-- learning-schema.sql — DDL for learning_entries table (test fixture)
-- Source: findings-hal-schema.md + build-spec.md §4 (13-col INSERT shape)
-- Usage: sqlite3 "$TEST_DB" < tests/fixtures/learning-schema.sql
--
-- Only defines the columns required for the 13-col INSERT; remaining HAL
-- schema columns (decay_rate, decay_type, half_life, minimum_confidence,
-- last_outcome, cost_savings, quality_score, distilled_text) carry defaults
-- and are not inserted by the delegate — not included here to keep the
-- fixture minimal and self-contained.
CREATE TABLE IF NOT EXISTS learning_entries (
  id                TEXT     PRIMARY KEY,
  pattern           TEXT     NOT NULL,
  approach          TEXT     NOT NULL,
  domain            TEXT,
  confidence        REAL     NOT NULL DEFAULT 0.5,
  attempts          INTEGER  NOT NULL DEFAULT 0,
  successes         INTEGER  NOT NULL DEFAULT 0,
  failures          INTEGER  NOT NULL DEFAULT 0,
  partial_successes INTEGER  NOT NULL DEFAULT 0,
  created_at        INTEGER  NOT NULL,
  first_used        INTEGER  NOT NULL,
  last_used         INTEGER  NOT NULL,
  source            TEXT     NOT NULL,
  UNIQUE(pattern, approach)
);
