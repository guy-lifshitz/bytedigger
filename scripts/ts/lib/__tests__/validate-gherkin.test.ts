// RED — Phase 5.1. Tests fail until validate-gherkin.ts is ported from HAL.
import { describe, expect, test } from "bun:test";
import { validateGherkin } from "../validate-gherkin.ts";

describe("validateGherkin", () => {
  test("returns valid:true for well-formed Given/When/Then", () => {
    const text = [
      "Scenario: foo",
      "  Given a precondition",
      "  When something happens",
      "  Then an outcome occurs",
    ].join("\n");
    const r = validateGherkin(text);
    expect(r.valid).toBe(true);
    expect(r.errors).toEqual([]);
  });

  test("returns valid:false when Then is missing", () => {
    const text = "Scenario: foo\n  Given x\n  When y\n";
    const r = validateGherkin(text);
    expect(r.valid).toBe(false);
    expect(r.errors.length).toBeGreaterThan(0);
  });

  test("returns valid:false on empty input", () => {
    const r = validateGherkin("");
    expect(r.valid).toBe(false);
  });
});
