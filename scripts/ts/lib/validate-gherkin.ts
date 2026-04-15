/**
 * validate-gherkin.ts — Pure-text Gherkin BDD scenario validator.
 *
 * ByteDigger API: takes raw text, returns { valid, errors }.
 * Checks:
 *   1. Non-empty input
 *   2. Contains a Scenario: header
 *   3. Contains Given / When / Then keywords
 */

export interface GherkinValidationResult {
  valid: boolean;
  errors: string[];
}

export function validateGherkin(text: string): GherkinValidationResult {
  const errors: string[] = [];

  if (!text || !text.trim()) {
    errors.push("input is empty — Gherkin BDD scenarios required");
    return { valid: false, errors };
  }

  if (!/Scenario:/.test(text)) {
    errors.push("No 'Scenario:' header found");
  }
  if (!/(^|\n)\s*Given\s/.test(text)) {
    errors.push("No 'Given' step found");
  }
  if (!/(^|\n)\s*When\s/.test(text)) {
    errors.push("No 'When' step found");
  }
  if (!/(^|\n)\s*Then\s/.test(text)) {
    errors.push("No 'Then' step found");
  }

  return { valid: errors.length === 0, errors };
}
