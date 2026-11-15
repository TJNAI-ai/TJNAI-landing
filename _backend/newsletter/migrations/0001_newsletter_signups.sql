-- Team of Bots sign-up list (store-only). No email is sent by this Worker.
-- Fields are the minimum needed to send one confirmation email later, if and when sending is approved.
-- IP address and User-Agent are NOT stored (the Worker reads the IP only for in-memory rate limiting).
-- Retention: kept until the person asks to be removed (email info@tjnai.ai) or the list is retired.
-- There is no automatic deletion job.

CREATE TABLE newsletter_signups (
  signup_id     TEXT PRIMARY KEY,                       -- random UUID
  email         TEXT NOT NULL UNIQUE,                   -- trimmed, lowercased
  first_name    TEXT,                                   -- optional, as entered (max 60 chars)
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'confirmed', 'unsubscribed')),
  consent_text  TEXT NOT NULL,                          -- exact sign-up notice shown next to the form
  source_page   TEXT NOT NULL,                          -- e.g. /newsletter
  signed_up_at  TEXT NOT NULL,                          -- UTC, RFC 3339 with seconds, set by the server
  is_test       INTEGER NOT NULL DEFAULT 0 CHECK (is_test IN (0, 1))
);
