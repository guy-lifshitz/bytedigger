"""GH1200 fixture (AC39, gate findings M9 / N3) — a PARTIALLY literal constant.

`MIXED_HINT` concatenates one string literal with a call result, so only the
literal fragment is greppable. Spec §1.3 mandates three things here:

  1. capture the literal fragment as its own key;
  2. NEVER synthesise a concatenation across the non-literal operand — such a
     key exists in no test on earth (a phantom key), and
  3. emit `W_PARTIAL_LITERAL MIXED_HINT` so the auditor knows the value is only
     partly greppable.

This is the in-tree shape of `engine_py/lib/reference_backends/pydantic_anthropic.py:38-41`.
"""


def _suffix():
    return "computed-at-runtime"


MIXED_HINT = ("gh1200-mixed-literal-fragment " + _suffix())
