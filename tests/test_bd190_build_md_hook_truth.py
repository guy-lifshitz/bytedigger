"""RED tests for bd#190 -- commands/build.md must describe the hooks that really exist.

Spec: bd#190 (FROZEN rev 2): the "Tool Guard" paragraph of commands/build.md claims an
orchestrator Edit/Write block, env-var agent detection (CLAUDE_AGENT_ID, SIDECHAIN)
and a pid-file that arms the guard. No hook implements any of that. The heading of the
Gates section also names a non-existent hooks/build-gate.sh.

ACs covered (T = build.md, whitespace-normalised, lower-cased):
  AC1: each env-var token build.md mentions (CLAUDE_AGENT_ID, SIDECHAIN) must occur in
       at least one *.sh / *.py / *.json file under hooks/.
  AC2: every script basename registered under hooks.PreToolUse[*].hooks[*].command in
       hooks/hooks.json (json-parsed) is named in build.md.
  AC3: on T: no `arm(s|ed|ing)` word, no `env(ironment) var`, every
       "blocks (the )?orchestrator from" is preceded by "no hook ", T contains
       "agent_id" and the exact sentence "no hook blocks the orchestrator from
       editing code; that rule is in the prompt only."
  AC4: this test does not read or assert anything about phases/.
  AC5: every (hooks|scripts)/<name> path token in build.md exists in the repo
       (at least 5 such tokens must be found).

Reads files only. Nothing is imported from the repo; no sys.path changes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_MD = REPO_ROOT / "commands" / "build.md"
HOOKS_DIR = REPO_ROOT / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"

ENV_TOKENS = ["CLAUDE_AGENT_ID", "SIDECHAIN"]
EXACT_SENTENCE = (
    "no hook blocks the orchestrator from editing code; "
    "that rule is in the prompt only."
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _build_md() -> str:
    return BUILD_MD.read_text(encoding="utf-8")


def _hooks_text() -> str:
    parts = []
    for p in sorted(HOOKS_DIR.rglob("*")):
        if p.is_file() and p.suffix in (".sh", ".py", ".json"):
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_ac1_build_md_env_tokens_exist_in_hooks():
    doc = _build_md()
    hooks_text = _hooks_text()
    hooks_nonempty = bool(hooks_text.strip())
    assert hooks_nonempty, "hooks/ has no readable content; check is vacuous"
    for token in ENV_TOKENS:
        in_doc = token in doc
        in_hooks = token in hooks_text
        ok = (not in_doc) or in_hooks
        assert ok, (
            f"commands/build.md mentions {token} but no *.sh/*.py/*.json file under "
            f"hooks/ references it; the doc describes detection no hook implements"
        )


def test_ac2_every_registered_pretooluse_script_is_named_in_build_md():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    names = set()
    for entry in data["hooks"]["PreToolUse"]:
        for h in entry["hooks"]:
            names.add(h["command"].replace("\\", "/").rsplit("/", 1)[-1])
    assert names, "parsed no PreToolUse script names from hooks/hooks.json"
    for required in ("build-state-guard.sh", "worker-write-guard.sh"):
        assert required in names, (
            f"{required} not found among PreToolUse commands in hooks.json: {sorted(names)}"
        )
    doc = _build_md()
    missing = sorted(n for n in names if n not in doc)
    assert not missing, (
        f"commands/build.md does not name registered PreToolUse hook script(s): {missing}"
    )


def test_ac3_build_md_has_no_false_guard_claims_and_names_agent_id():
    t = _norm(_build_md())

    arm_hits = [m.group(0) for m in re.finditer(r"\barm(s|ed|ing)?\b", t)]
    assert not arm_hits, f"build.md says the guard is armed: {arm_hits}"

    env_hits = [m.group(0) for m in re.finditer(r"env(ironment)?[ -]?var", t)]
    assert not env_hits, f"build.md describes env-var detection: {env_hits}"

    bad_blocks = []
    for m in re.finditer(r"blocks (the )?orchestrator from", t):
        if not t[: m.start()].endswith("no hook "):
            bad_blocks.append(t[max(0, m.start() - 20): m.end() + 20])
    assert not bad_blocks, (
        f"build.md claims a hook blocks the orchestrator (not preceded by 'no hook '): "
        f"{bad_blocks}"
    )

    has_agent_id = "agent_id" in t
    assert has_agent_id, (
        "build.md does not say subagents are recognised by the stdin agent_id"
    )

    has_sentence = EXACT_SENTENCE in t
    assert has_sentence, f"build.md lacks the exact sentence: {EXACT_SENTENCE!r}"


def test_ac5_build_md_hook_and_script_paths_exist():
    doc = _build_md()
    tokens = sorted(set(re.findall(r"(?:hooks|scripts)/[A-Za-z0-9_.-]+", doc)))
    assert len(tokens) >= 5, (
        f"expected at least 5 hooks/ or scripts/ path tokens in build.md, got {tokens}"
    )
    missing = [t for t in tokens if not (REPO_ROOT / t).exists()]
    assert not missing, f"build.md references paths that do not exist: {missing}"
