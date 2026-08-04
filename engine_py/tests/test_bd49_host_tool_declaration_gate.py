"""bd#49 — производный гейт объявления на хост-инструмент.

Спека: `docs/decisions/2026-08-04-bd49-host-tool-declaration-gate.md`.

ЧТО ЭТОТ ГЕЙТ ПРИНУЖДАЕТ, И ПОЧЕМУ ИМЕННО ЭТО.

`helpers/host_tools.py::pytest_runtest_makereport` уже **тотален** по обычному
пути: любой тест, упавший `FileNotFoundError` на объявленном и действительно
отсутствующем инструменте, автоматически становится `skipped` — независимо от
того, как он звал инструмент. Замерено на корпусе `b95e48a`: **610** тел
`test_*` достигают хост-инструмент литералом argv (`subprocess.run(["git", …])`)
вообще без `which`, и все они этим механизмом покрыты (`docs/host-requirements.md`
пишет о ~500 таких на одном только `git`).

Уйти из-под тотального механизма можно ровно одним способом — **опросить
доступность самому** и принять решение до того, как полетит `FileNotFoundError`.
Таких тел **37**, и это ПОЛНОЕ множество побегов, а не выборка. Домен гейта —
именно они.

Отсюда политика, которую гейт принуждает:

    тело теста, которое САМО опрашивает доступность хост-инструмента, обязано
    объявить, что делает с ответом — либо `skip_without(tool)`, либо
    `# host-tool-hard-fail: <довод>`. Молчаливого третьего пути нет.

ГРАНИЦА ЧЕСТНОСТИ (AC11). Сканер ловит пре-эмпцию, сделанную через `which`, и
НЕ ловит сделанную иначе — `os.path.exists("/usr/bin/git")`, собственный
`except FileNotFoundError: pytest.skip(...)`. Это не недосмотр, а объявленная
граница: замерено, что в корпусе таких форм **ноль** (`except FileNotFoundError`
рядом со `skip` — 0 попаданий, `exists()` по путям бинарей — 0). Появится
первая — граница обязана двинуться, и этот абзац её место в исходнике.

ЧЕГО ГЕЙТ НЕ УТВЕРЖДАЕТ: что корпус целиком инертен-безопасен без инструмента
(тотальность даёт хукврапер, гейт закрывает только побеги), и что
`_C4/_C5/_C6_*_CALL_SITES` в `test_bd102_host_tool_contract.py` больше не нужны
— те принуждают более сильное свойство (форма обязана быть именно
`skip_without`) на своих 8 сайтах.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def _probes(source: str) -> list[tuple[str, str]]:
    """Импорт внутри тела — идиома этого корпуса для символа, которого ещё нет
    (ср. `test_bd102_host_tool_contract.py::test_ac6`). Модульный импорт ронял
    бы СБОР, и RED был бы collection-error, а не assert-time (§1q)."""
    from helpers.host_tool_probes import (  # noqa: PLC0415
        undeclared_host_tool_probes,
    )

    return undeclared_host_tool_probes(source)


def _scan(source: str) -> list[tuple[str, str]]:
    return _probes(textwrap.dedent(source))


# ─── AC1: §1l — якорь на живом корпусе, не на фикстуре ─────────────────────

def test_ac1_live_corpus_has_no_undeclared_host_tool_probes() -> None:
    """Каждое тело в РЕАЛЬНОМ `engine_py/tests/**`, опрашивающее доступность
    хост-инструмента само, объявляет выбранную форму.

    Это утверждение о поставляемом корпусе, а не о синтетической строке —
    §1l-якорь спеки. На момент RED оно ложно ровно 37 раз.
    """
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for func_name, tool in _probes(source):
            offenders.append(f"{path.relative_to(TESTS_DIR).as_posix()}::{func_name} probes {tool!r}")

    assert offenders == [], (
        "тела опрашивают доступность хост-инструмента, не объявив формы "
        f"({len(offenders)}):\n  " + "\n  ".join(offenders) + "\n"
        "Каждое обязано либо звать skip_without(<tool>), либо нести комментарий "
        "`# host-tool-hard-fail: <довод>` в собственном теле."
    )


# ─── AC2/AC3: положительные ноги — обе формы принимаются ───────────────────

def test_ac2_skip_without_form_is_accepted() -> None:
    assert _scan('''
        import shutil
        from helpers.host_tools import skip_without

        def test_thing():
            skip_without("bun")
            bun = shutil.which("bun")
            assert bun
    ''') == []


def test_ac3_declared_hard_fail_marker_is_accepted() -> None:
    assert _scan('''
        import shutil

        def test_thing():
            # host-tool-hard-fail: паритет корпуса нельзя сертифицировать,
            # ни разу его не прогнав — тихий пропуск здесь дороже красноты.
            bun = shutil.which("bun")
            assert bun is not None, "hard AC failure, not a skip"
    ''') == []


# ─── AC4/AC5: ОТРИЦАТЕЛЬНЫЕ НОГИ — гейт, не падающий на новом сайте, инертен ─

def test_ac4_undeclared_direct_probe_is_reported() -> None:
    """Новый вызывающий сайт, не объявивший ничего, ОБЯЗАН вернуться записью.

    Без этого AC гейт декоративен: он бы одинаково молчал и на чистом корпусе,
    и на корпусе, полном необъявленных проб.
    """
    assert _scan('''
        import shutil

        def test_thing():
            bun = shutil.which("bun")
            assert bun is not None
    ''') == [("test_thing", "bun")]


def test_ac5_undeclared_probe_reached_through_helper_is_reported() -> None:
    """Проба, спрятанная в хелпере, который зовёт тело теста.

    Это НЕ гипотетика: `test_gh1338_corpus_parity_gate.py` держит
    `shutil.which("bun")` в `_build_parity_fixture`, а не в телах, и именно
    так восемь его ACs — ac11/12/13/23/26/37/38/39 — уходят из-под прямого
    сканера. Сканер, ключующийся только на прямой вызов, пропустил бы ровно
    тот предмет, ради которого bd#49 заведён.
    """
    assert _scan('''
        import shutil

        def _build_parity_fixture(tmp_path):
            bun = shutil.which("bun")
            assert bun is not None
            return bun

        def test_thing(tmp_path):
            _build_parity_fixture(tmp_path)
    ''') == [("test_thing", "bun")]


# ─── AC6/AC7/AC8: объявление обязано быть настоящим, своим и про тот инструмент ─

def test_ac6_marker_without_a_reason_is_not_a_declaration() -> None:
    """Пустой маркер — молчаливое освобождение под видом объявления."""
    assert _scan('''
        import shutil

        def test_thing():
            # host-tool-hard-fail:
            bun = shutil.which("bun")
            assert bun is not None
    ''') == [("test_thing", "bun")]


def test_ac7_declaration_does_not_leak_between_bodies_in_one_file() -> None:
    """Ловит сканер, ищущий маркер/вызов по файлу целиком вместо тела."""
    assert _scan('''
        import shutil
        from helpers.host_tools import skip_without

        def test_declared():
            skip_without("bun")
            assert shutil.which("bun")

        def test_undeclared():
            assert shutil.which("bun")
    ''') == [("test_undeclared", "bun")]


def test_ac8_declaration_is_bound_to_the_probed_tool() -> None:
    """Ловит сканер, сверяющий факт вызова `skip_without` вместо аргумента."""
    assert _scan('''
        import shutil
        from helpers.host_tools import skip_without

        def test_thing():
            skip_without("git")
            assert shutil.which("bun")
    ''') == [("test_thing", "bun")]


# ─── AC10: алиасы — в корпусе живут _shutil, _sh, _shutil_real ─────────────

def test_ac10_aliased_and_bare_which_are_both_recognised() -> None:
    assert _scan('''
        import shutil as _sh

        def test_aliased_module():
            assert _sh.which("bun")
    ''') == [("test_aliased_module", "bun")]

    assert _scan('''
        from shutil import which

        def test_bare_name():
            assert which("bun")
    ''') == [("test_bare_name", "bun")]


# ─── AC9: §1a sibling — bd#102 AC5b не задет конформансом M2 ───────────────

def test_ac9_bd102_call_site_contract_is_untouched_by_this_pr() -> None:
    """M2 правит только формы 3/4/5; ни один из 8 сайтов bd#102 в них не входит.

    Сверяется на исходнике, а не прогоном: файлы, перечисленные в
    `_C4/_C5/_C6`, обязаны по-прежнему нести `skip_without` в своих телах.
    """
    from test_bd102_host_tool_contract import (  # noqa: PLC0415
        _C4_BUN_CALL_SITES,
        _C5_SEMGREP_CALL_SITES,
        _C6_DOCTOR_GIT_CALL_SITES,
    )

    sites = (
        [(f, n, "bun") for f, n in _C4_BUN_CALL_SITES]
        + [(f, n, "semgrep") for f, n in _C5_SEMGREP_CALL_SITES]
        + [(f, n, "git") for f, n in _C6_DOCTOR_GIT_CALL_SITES]
    )
    assert len(sites) == 8, f"bd#102 перечисляет не 8 сайтов, а {len(sites)}"

    for filename, func_name, tool in sites:
        source = (TESTS_DIR / filename).read_text(encoding="utf-8")
        reported = dict(_probes(source))
        assert reported.get(func_name) != tool, (
            f"{filename}::{func_name} потерял объявление на {tool!r} — "
            "конформанс M2 задел сайт bd#102, чего не должен был"
        )
