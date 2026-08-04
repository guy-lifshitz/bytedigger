"""bd#36 — `phase_artifacts.written`: манифест как ИСТОЧНИК, а не фильтр.

Спека: `docs/decisions/2026-08-04-bd36-phase-artifacts-written-source.md`.

Class: прибор отвечает на вопрос, которого ему не задавали, и его отказ выглядит
как успех. Сегодня `written` наполняется РОВНО из git-дельты `git_cwd`
(`engine.py:493`), а подпись `write_tracking: "git-delta"` читается как «я
наблюдал записи фазы». Замер: шаг, реально записавший файл ВНЕ `git_cwd` и
объявивший его манифестом, даёт `written: []` при `write_tracking: "git-delta"`
— то есть «фаза ничего не написала» вместо «мои записи были вне окна».
`EMISSIONS_SPEC.md [G18r3:EDGE-4]` называет ровно эту пару «overclaim shape» и
запрещает её; AC-E3b, однако, связывает правило с вопросом «была ли дельта
посчитана», а не «покрыло ли окно записи» — поэтому буква соблюдена, а смысл нет.

UUT НЕ мокан: во всех ногах крутится настоящий `WorkflowEngine`, шаг физически
пишет файл, манифест отдаётся штатной формой `StepResult.data`
(`worker_written_paths` + `manifest_source`), которую разбирает
`llm_subprocess.manifest_from_result`.

ПОЧЕМУ ИМЕННО `harness_tool_record`, а не референс-бэкенд. Замерено: все
`lib/reference_backends/*` строят манифест через `_manifest_since`, то есть
`git diff --name-only` внутри `root` — их манифест САМ есть git-дельта, и
объединение с ней не добавляет ничего. Пути вне репозитория несёт только продовый
путь (`llm_subprocess._written_paths_from_events`, `file_path` из транскрипта
Write/Edit, вербатим). RED, написанный на референс-бэкенде, был бы зелёным и до
починки, и после — то есть не RED вовсе.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine

from test_bd18_emissions import make_log, make_ctx, events_of, git_repo  # noqa: F401

MANIFEST_SOURCE = "harness_tool_record"


def _step(name: str, writes: list[Path], declared: list[str] | None) -> StepContract:
    """Шаг, который РЕАЛЬНО пишет `writes` и объявляет `declared`.

    `declared is None` ⇒ StepResult без манифеста (ветка DEFER, §2 D2).
    """
    def _run(_ctx, _prev):
        for p in writes:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("bd36\n")
        data = None
        if declared is not None:
            data = {
                "worker_written_paths": declared,
                "manifest_source": MANIFEST_SOURCE,
            }
        return StepResult(status="ok", data=data, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _phase_artifacts(tmp_path, label, git_cwd, writes, declared):
    """Прогнать одну фазу и вернуть payload её `phase_artifacts`."""
    log_dir = tmp_path / label
    log_dir.mkdir(parents=True, exist_ok=True)
    log = make_log(log_dir)
    eng = WorkflowEngine(event_log=log)
    eng.register(label, WorkflowDefinition(
        name=label, steps=[_step("s1", writes, declared)],
    ))
    eng.execute(label, make_ctx(git_cwd=git_cwd), run_id="r")
    pa = events_of(log, "phase_artifacts")
    assert len(pa) == 1, f"expected exactly one phase_artifacts, got {len(pa)}"
    return pa[0]["payload"]


# ─── AC1: предмет — объявленный путь вне репо обязан попасть в written ──────

def test_ac1_manifest_declared_path_outside_git_cwd_lands_in_written(tmp_path, git_repo):
    """§1l: файл создаётся физически, вне `git_cwd`, и объявлен манифестом.

    Сегодня красный: `written == []`. Это ровно замер §0 (случай C) спеки.
    """
    outside = tmp_path / "scratchpad" / "run1" / "note.md"
    payload = _phase_artifacts(
        tmp_path, "ac1", str(git_repo), [outside], [str(outside)],
    )

    assert outside.exists(), "фикстура обязана реально создать файл"
    # §1j macOS realpath: tmp_path приходит как /var/..., а resolve() даёт
    # /private/var/... Сверять надо с РАЗРЕШЁННОЙ формой — иначе корректный
    # GREEN, нормализующий пути (D3), не смог бы удовлетворить этот AC.
    assert str(outside.resolve()) in payload["written"], (
        f"объявленный манифестом путь вне git_cwd не попал в written: "
        f"written={payload['written']!r}. Манифест обязан быть ИСТОЧНИКОМ, "
        f"а не фильтром git-дельты."
    )


# ─── AC2: контрольная нога — без манифеста ровно git-дельта (D2) ────────────

def test_ac2_without_manifest_written_is_exactly_the_git_delta(tmp_path, git_repo):
    """Ловит «починку», удовлетворённую тем, что пересекать перестали вообще.

    Без этой ноги достаточно было бы снести манифестную логику, и AC1 позеленел
    бы, а поведение шагов без манифеста молча поехало.
    """
    payload = _phase_artifacts(
        tmp_path, "ac2", str(git_repo), [git_repo / "c.txt"], None,
    )
    assert payload["written"] == ["c.txt"], (
        f"шаг без манифеста обязан давать РОВНО git-дельту, получено "
        f"{payload['written']!r}"
    )
    assert payload["write_tracking"] == "git-delta"


# ─── AC3: ОТРИЦАТЕЛЬНАЯ НОГА — not-observed не смягчается манифестом (D6) ───

def test_ac3_absent_git_cwd_stays_not_observed_even_with_manifest(tmp_path):
    """Гейт, разрешающий здесь наблюдение, инертен.

    Дельта не посчитана вовсе (нет `git_cwd`), поэтому непустой манифест НЕ
    имеет права превратить `not-observed` в наблюдение — это прямой запрет
    issue и `[G18r3:EDGE-4]`. Без этой ноги «манифест как источник» тихо
    переписал бы смысл подписи на всех путях, где движок не смотрел.
    """
    outside = tmp_path / "scratchpad" / "run2" / "x.md"
    payload = _phase_artifacts(tmp_path, "ac3", None, [outside], [str(outside)])

    assert payload["write_tracking"] == "not-observed", (
        f"без git_cwd дельта не считалась — подпись обязана остаться "
        f"'not-observed', получено {payload['write_tracking']!r}"
    )


# ─── AC4: новое значение появляется ПО ДЕЛУ, обе стороны (D5) ──────────────

def test_ac4_new_tracking_value_appears_only_when_manifest_added_something(tmp_path, git_repo):
    """Обе стороны: иначе значение либо не появляется, либо появляется всегда."""
    outside = tmp_path / "scratchpad" / "run3" / "y.md"
    added = _phase_artifacts(
        tmp_path, "ac4_added", str(git_repo), [outside], [str(outside)],
    )
    assert added["write_tracking"] == "git-delta+manifest", (
        f"манифест добавил путь сверх дельты — подпись обязана это назвать, "
        f"получено {added['write_tracking']!r}"
    )

    nothing_new = _phase_artifacts(
        tmp_path, "ac4_plain", str(git_repo), [git_repo / "d.txt"], ["d.txt"],
    )
    assert nothing_new["write_tracking"] == "git-delta", (
        f"манифест не добавил ничего сверх дельты — подпись обязана остаться "
        f"'git-delta', получено {nothing_new['write_tracking']!r}"
    )


# ─── AC5/AC6: нормализация — один алфавит (D3) ─────────────────────────────

def test_ac5_absolute_manifest_path_inside_repo_collapses_with_relative_delta(tmp_path, git_repo):
    """Один файл, два алфавита: git-дельта даёт `e.txt`, манифест — абсолютный.

    Ловит склейку двух пространств имён: без нормализации в `written` окажутся
    ДВЕ записи об одном файле.
    """
    inside = git_repo / "e.txt"
    payload = _phase_artifacts(
        tmp_path, "ac5", str(git_repo), [inside], [str(inside)],
    )
    assert payload["written"] == ["e.txt"], (
        f"один файл обязан дать одну репо-относительную запись, получено "
        f"{payload['written']!r}"
    )


def test_ac6_outside_path_normalisation_is_idempotent(tmp_path, git_repo):
    """Путь вне репо, объявленный дважды в разных формах, даёт одну запись."""
    outside = tmp_path / "scratchpad" / "run4" / "z.md"
    messy = str(outside.parent / "." / outside.name)
    payload = _phase_artifacts(
        tmp_path, "ac6", str(git_repo), [outside], [str(outside), messy],
    )
    assert payload["written"] == [str(outside.resolve())], (
        f"две формы одного пути обязаны схлопнуться в одну, получено "
        f"{payload['written']!r}"
    )


# ─── AC7: принятая цена доверия к манифесту (D4) ───────────────────────────

def test_ac7_declared_but_nonexistent_path_is_trusted_and_included(tmp_path, git_repo):
    """D4 пинуется НАМЕРЕННО, чтобы будущая проверка существования не прошла молча.

    Манифест — запись харнесса о собственных tool-call'ах (`4961254A`), а не
    самоотчёт воркера; проверка существования ввела бы гонку с последующим
    шагом, законно удалившим файл. Цена — путь, о котором отчитались ошибочно,
    попадёт в `written`.
    """
    ghost = tmp_path / "scratchpad" / "run5" / "never-written.md"
    payload = _phase_artifacts(
        tmp_path, "ac7", str(git_repo), [], [str(ghost)],
    )
    assert not ghost.exists(), "фикстура НЕ должна создавать этот файл"
    # §1j: та же разрешённая форма, что и в AC1.
    assert str(ghost.resolve()) in payload["written"], (
        f"D4: путь из манифеста включается без проверки существования, "
        f"получено {payload['written']!r}"
    )


# ─── AC8: §1a sibling — потребители подписи не задеты ──────────────────────

def test_ac8_bd8_oracle_pinned_pair_still_holds(tmp_path, git_repo):
    """`test_bd8_l1_oracle.py:19` пинует пару `git-delta` + `written: []` буквально.

    Его случай — фаза с посчитанной дельтой и БЕЗ манифеста, то есть ветка D2.
    Здесь та же форма воспроизведена напрямую: шаг ничего не пишет, манифеста
    нет ⇒ подпись обязана остаться `git-delta`, а набор пустым.
    """
    payload = _phase_artifacts(tmp_path, "ac8", str(git_repo), [], None)
    assert payload["written"] == []
    assert payload["write_tracking"] == "git-delta", (
        f"пара, на которую опирается оракул bd#8, поехала: "
        f"{payload['write_tracking']!r}"
    )
