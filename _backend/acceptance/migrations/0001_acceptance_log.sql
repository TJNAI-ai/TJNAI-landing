-- Clickwrap acceptance log (spec: /workspace/legal/DRAFT_clickwrap-spec_v0.1_2026-11-13.md, sections 6, 7, 9.1).
-- acceptance_events and legal_document_versions are append-only: triggers abort any UPDATE or DELETE.
-- Corrections are new rows, never edits. The Worker only ever INSERTs and SELECTs.

CREATE TABLE accounts (
  account_id  TEXT PRIMARY KEY,               -- acct_ + first 24 hex of SHA-256(lowercased email)
  email       TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL                   -- UTC, RFC 3339 with seconds
);

CREATE TABLE engagements (
  engagement_id TEXT PRIMARY KEY,
  account_id    TEXT NOT NULL REFERENCES accounts(account_id),
  flow_id       TEXT NOT NULL CHECK (flow_id IN ('audit', 'build', 'care-plan')),
  created_at    TEXT NOT NULL
);

CREATE TABLE legal_document_versions (
  document_id        TEXT NOT NULL,
  version            TEXT NOT NULL,
  content_hash       TEXT NOT NULL CHECK (length(content_hash) = 64),
  artifact_uri       TEXT,
  effective_at       TEXT,
  is_material_change INTEGER NOT NULL CHECK (is_material_change IN (0, 1)),
  created_at         TEXT NOT NULL,
  PRIMARY KEY (document_id, version)
);

CREATE TABLE acceptance_events (
  acceptance_id            TEXT PRIMARY KEY,
  acceptance_batch_id      TEXT NOT NULL,     -- ties the rows from one submit together
  account_id               TEXT NOT NULL REFERENCES accounts(account_id),
  name                     TEXT NOT NULL,
  email                    TEXT NOT NULL,
  document_id              TEXT NOT NULL,
  document_version         TEXT NOT NULL,
  content_hash             TEXT NOT NULL CHECK (length(content_hash) = 64),
  accepted_at              TEXT NOT NULL,     -- UTC, RFC 3339 with seconds
  ip_address               TEXT NOT NULL,     -- CF-Connecting-IP
  user_agent               TEXT NOT NULL,
  page_or_flow             TEXT NOT NULL,
  checkbox_text_shown      TEXT NOT NULL,
  billing_disclosure_shown TEXT,              -- required for BILLING-CONSENT
  engagement_id            TEXT NOT NULL REFERENCES engagements(engagement_id),
  locale                   TEXT,
  viewport                 TEXT,
  form_template_id         TEXT,
  FOREIGN KEY (document_id, document_version) REFERENCES legal_document_versions(document_id, version),
  CHECK (document_id <> 'BILLING-CONSENT' OR billing_disclosure_shown IS NOT NULL)
);

CREATE INDEX acceptance_events_account ON acceptance_events (account_id, document_id, accepted_at);
CREATE INDEX acceptance_events_batch ON acceptance_events (acceptance_batch_id);

CREATE TRIGGER acceptance_events_no_update BEFORE UPDATE ON acceptance_events
BEGIN
  SELECT RAISE(ABORT, 'acceptance_events is append-only');
END;

CREATE TRIGGER acceptance_events_no_delete BEFORE DELETE ON acceptance_events
BEGIN
  SELECT RAISE(ABORT, 'acceptance_events is append-only');
END;

CREATE TRIGGER legal_document_versions_no_update BEFORE UPDATE ON legal_document_versions
BEGIN
  SELECT RAISE(ABORT, 'legal_document_versions is append-only');
END;

CREATE TRIGGER legal_document_versions_no_delete BEFORE DELETE ON legal_document_versions
BEGIN
  SELECT RAISE(ABORT, 'legal_document_versions is append-only');
END;
