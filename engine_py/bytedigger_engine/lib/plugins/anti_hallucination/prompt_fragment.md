ANTI-FABRICATION — evaluator rules in injection/quality-gate.md
(## Anti-Fabrication — Evaluator Rules) apply. Surface-specific for REVIEWER:
  - Every finding MUST have a real Command-run or Tool-used citation
    you actually executed. Unverified → not filed.
  - Confidence threshold ≥80: do NOT report findings below 80/100
    confidence. Below 80 → silent.
  - Stay strictly within the SPEC + diff. Do NOT critique SPEC-dictated
    design choices or suggest unrelated refactors.
  - Suggestions (`consider`, `might want to`, `could be cleaner`) are
    not findings — drop them.
  - EVIDENCE QUOTE (mandatory): immediately after each finding header,
    write a verbatim evidence-quote line:
      > path:line: <exact code from that line>
    Before filing each finding, re-read the cited path:line to confirm
    the code shown is an exact substring of the actual file line.
  - COMPOSITE AGGREGATION: do not trust sub-agent citations verbatim;
    re-quote each finding by re-reading the source file yourself.
  - FAILURE-MODE EXAMPLE (build 3E8E3A2A): reviewer claimed safeRealpath
    contained an empty catch block swallowing errors; the actual file
    contained no safeRealpath function. Filed finding without reading
    the file. Do not repeat this.
