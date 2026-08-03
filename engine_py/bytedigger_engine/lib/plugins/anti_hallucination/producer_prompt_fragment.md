ANTI-FABRICATION — producer rules in injection/producer-rules.md
(## Anti-Fabrication — Producer Rules) apply. Generic to ALL producers:
  - PATH:LINE citations MUST come from grep/glob/Read results you actually
    ran. If a search returned nothing, park it in ## Open Questions — do
    NOT invent a citation to back a claim.
  - SELF-VERIFY before writing each `path:line: <code>` citation:
    re-confirm the path exists and the line content matches.
  - SCOPE: stay within the FEATURE REQUEST + SPEC + previous-phase
    artifacts. Drive-by refactors, tangents, and "for future flexibility"
    additions go to ## Out of Scope, not your main output.
  - INVENTION: input silent on something → ## Open Questions or ## Unknowns.
    Never invent an answer to fill a section.
  - FAILURE-MODE EXAMPLE (build 3E8E3A2A): producer fabricated a code
    citation for a function that did not exist. Reviewer caught it but
    the fabrication wasted spec + review cycles. Do not repeat this.
