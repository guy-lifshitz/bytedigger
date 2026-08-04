"""bd#49 — сканер необъявленных проб доступности хост-инструмента.

Прибор для `tests/test_bd49_host_tool_declaration_gate.py`; довод, зачем домен
именно такой, и граница честности — в докстроке того файла. Здесь только
механика.

Принимает ИСХОДНЫЙ ТЕКСТ, а не путь: отрицательные ноги гейта кормятся
синтетическими строками, и сканер, умеющий только путь, был бы непроверяем на
том самом множестве, ради которого написан.

`HOST_TOOLS` НЕ дублируется — импортируется из `helpers.host_tools`, иначе
появился бы второй источник истины на тот же список (§1g), и добавленный
инструмент молча оставался бы вне гейта.
"""
from __future__ import annotations

import ast

from .host_tools import HOST_TOOLS

#: Комментарий, которым тело объявляет сознательный отказ от пропуска.
#: Хвост после двоеточия обязан быть непустым — см. AC6.
HARD_FAIL_MARKER = "# host-tool-hard-fail:"

_SUBPROCESS_ENTRYPOINTS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _callee_name(node: ast.Call) -> str | None:
    """Имя вызываемого — по атрибуту либо по голому имени.

    Ключуем на ИМЯ, а не на модуль: в корпусе живут три алиаса `shutil`
    (`_shutil`, `_sh`, `_shutil_real`), плюс `from shutil import which`.
    Сверка с модулем требовала бы разрешения алиасов и всё равно текла бы на
    `from ... import which` (AC10).
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _const_str_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _body_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int] | None:
    """Строки СОБСТВЕННОГО тела (без сигнатуры и декораторов).

    Через `node.body`, а не через `node.lineno`: у многострочной сигнатуры
    строка `def` и строка `)` -> `None` дают отступ <= def-строки, и наивный
    построчный обход вернул бы список аргументов вместо тела. Это ровно тот
    дефект, который bd#102 ловил у себя (`_extract_function_body`, gate MINOR-3).
    """
    if not node.body:
        return None
    end = node.body[-1].end_lineno
    if end is None:
        return None
    # Начало — def-строка, а НЕ `body[0].lineno`: комментарий не является узлом
    # AST, поэтому маркер, стоящий первой строкой перед первым оператором,
    # выпадал бы из пролёта и объявление не засчитывалось (поймано AC3).
    # Утечки между телами это не даёт: пролёт всё равно свой у каждой функции,
    # а декораторы выше def-строки не захватываются (AC7).
    return node.lineno, end


class _FunctionFacts:
    """Факты, снятые с одной функции: пробы, объявления, исходящие вызовы."""

    __slots__ = ("probes", "declared", "calls", "hard_fail")

    def __init__(self) -> None:
        self.probes: set[str] = set()
        self.declared: set[str] = set()
        self.calls: set[str] = set()
        self.hard_fail: bool = False


def _collect(tree: ast.AST, lines: list[str]) -> dict[str, _FunctionFacts]:
    facts: dict[str, _FunctionFacts] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fact = facts.setdefault(node.name, _FunctionFacts())

        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            name = _callee_name(sub)
            if name is None:
                continue
            arg = _const_str_arg(sub)
            if name == "which":
                if arg in HOST_TOOLS:
                    fact.probes.add(arg)
            elif name == "skip_without":
                if arg is not None:
                    fact.declared.add(arg)
            elif name not in _SUBPROCESS_ENTRYPOINTS:
                # исходящий вызов — ребро для транзитивного замыкания
                fact.calls.add(name)

        # фикстуры приходят параметрами, а не вызовами: тело, берущее
        # `tmp_path`-подобную фикстуру, наследует её пробы ровно так же.
        for arg_node in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            fact.calls.add(arg_node.arg)

        span = _body_span(node)
        if span is not None:
            start, end = span
            for line in lines[start - 1:end]:
                head, sep, tail = line.partition(HARD_FAIL_MARKER)
                if sep and tail.strip():
                    fact.hard_fail = True
                    break

    return facts


def _close_over_calls(facts: dict[str, _FunctionFacts], attr: str) -> dict[str, set[str]]:
    """Транзитивное замыкание множества `attr` по рёбрам `calls`.

    Замыкание обязательно: `test_gh1338_corpus_parity_gate.py` держит
    `which("bun")` в `_build_parity_fixture`, а не в телах восьми своих ACs.
    Сканер без замыкания пропустил бы предмет bd#49 целиком.
    """
    closed = {name: set(getattr(fact, attr)) for name, fact in facts.items()}
    changed = True
    while changed:
        changed = False
        for name, fact in facts.items():
            for callee in fact.calls:
                target = closed.get(callee)
                if target is not None and not target <= closed[name]:
                    closed[name] |= target
                    changed = True
    return closed


def undeclared_host_tool_probes(source: str) -> list[tuple[str, str]]:
    """Тела `test_*`, опрашивающие доступность хост-инструмента без объявления.

    Возвращает отсортированный список `(имя_теста, инструмент)`. Пусто —
    значит каждая проба объявлена: либо `skip_without(<тот же инструмент>)`,
    либо `# host-tool-hard-fail: <непустой довод>` в собственном теле.

    Объявление `skip_without` наследуется по вызовам (хелпер, скрывающий и
    пробу, и пропуск, законен), а маркер отказа — НЕТ: он довод о конкретном
    теле, и протекание его на всех вызывающих вернуло бы молчаливое
    освобождение через общий хелпер.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    facts = _collect(tree, lines)
    probes = _close_over_calls(facts, "probes")
    declared = _close_over_calls(facts, "declared")

    offenders: list[tuple[str, str]] = []
    for name, fact in facts.items():
        if not name.startswith("test_"):
            continue
        if fact.hard_fail:
            continue
        for tool in sorted(probes[name] - declared[name]):
            offenders.append((name, tool))

    return sorted(offenders)
