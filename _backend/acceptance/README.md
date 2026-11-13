# Clickwrap acceptance log (Cloudflare Worker + D1)

**Status: DRAFT, NOT DEPLOYED.** Nothing here runs anywhere yet. No Cloudflare account, D1 database,
Worker, secret or DNS record has been created. This folder is removed before every Pages deploy
(`rm -rf _backend …` in `.github/workflows/deploy.yml`), so it never ships to tjnai.ai.

Spec: Lena's `DRAFT_clickwrap-spec_v0.1_2026-11-13.md` (internal). Not legal advice; attorney review and
Tim's approval are required before go-live.

## What it does

`agree.html?flow=audit|build|care-plan` shows one **unchecked** checkbox per required document (Terms of
Service, Privacy Policy, Refund Policy), each label linking the **frozen** version being accepted
(`/legal/archive/<doc>/<version>`), plus a separate recurring-billing authorization box for the care plan.
The button stays disabled until every box is checked. The page records agreement only; it takes no payment
(payments stay on Gumroad and Stripe invoices).

The Worker is the real gate:

| Route | |
|---|---|
| `POST /accept` | Validates flow, name, email and that **every** required document is present, `checked: true`, at the **current** version and SHA-256 hash, with the exact label text. Missing/unchecked/extra box → **400**; stale version or hash → **409** (page must be reloaded). On success writes, in one D1 transaction, one `acceptance_events` row per box (plus account, engagement and version-registry rows) → **201** `{acceptance_batch_id, engagement_id, account_id}`. CORS locked to `ALLOWED_ORIGIN`. |
| `GET /manifest` | Current versions, hashes and label text per flow (read-only). |
| `GET /reacceptance?account_id=&flow=` | `Authorization: Bearer $ADMIN_TOKEN`. Read-only: whether the account must re-accept before the next renewal/charge because a newer version marked `is_material_change` was published (spec §8). |

Each `acceptance_events` row stores: acceptance_id, acceptance_batch_id, account_id, name, email,
document_id, document_version, content_hash (SHA-256), accepted_at (UTC, RFC 3339 seconds), ip_address
(`CF-Connecting-IP`), user_agent, page_or_flow, checkbox_text_shown, billing_disclosure_shown (care plan),
engagement_id, locale, viewport, form_template_id.

**Append-only:** SQLite triggers abort every `UPDATE`/`DELETE` on `acceptance_events` and
`legal_document_versions`. The Worker code only `INSERT`s and `SELECT`s.

## Versions and hashes

`_build/legal_versions.json` is the source of truth. `python3 _build/legal_manifest.py --write` hashes each
legal page's canonical text (visible text of `<main>`), freezes a copy to `legal/archive/…`, and regenerates
`src/manifest.json` (what this Worker enforces) and the checkbox block in `agree.html`.
`python3 _checks/test_public_site.py` fails if a legal page's wording changes without a new version entry.

To publish a new version: add an entry (`version` like `2026-12-01-1`, `is_material_change` set by the legal
owner, `content_sha256: null`) to the document's `versions` list, run `--write`, commit. A material version
makes `/reacceptance` report `material_change` for everyone who accepted an older one.

## Run locally (no account, no network)

```bash
cd _backend/acceptance
npm ci
npm test                                   # vitest + Miniflare + local D1
export WRANGLER_SEND_METRICS=false
npx wrangler d1 migrations apply tjnai-acceptance --local --persist-to .wrangler/state
npx wrangler dev --local --ip 127.0.0.1 --port 8787 --persist-to .wrangler/state \
  --var ALLOWED_ORIGIN:http://127.0.0.1:8765 --var ADMIN_TOKEN:local-dev-admin-token-0123456789abcdef
# second terminal, repo root:
python3 _checks/test_public_site.py --serve 8765
# open http://127.0.0.1:8765/agree.html?flow=care-plan&endpoint=http://127.0.0.1:8787
# (the ?endpoint= override only works on localhost/127.0.0.1)
npx wrangler d1 execute tjnai-acceptance --local --persist-to .wrangler/state \
  --command "SELECT * FROM acceptance_events ORDER BY accepted_at"
```

## Go-live (needs Tim's approval first; $0 on Cloudflare Free)

1. Attorney review of the flow and the v0.2 Terms/Privacy/Refund; Lena's v0.2 text wired in as new versions.
2. Tim confirms the care-plan billing text (start date, renewal date, cancellation method), and whether MSA/SOW
   documents join the flows (spec §2).
3. Privacy Policy mentions the limited IP/user-agent logging for acceptance (spec §6).
4. On the free Cloudflare account: `npx wrangler d1 create tjnai-acceptance` (put the id in `wrangler.toml`),
   `npx wrangler d1 migrations apply tjnai-acceptance --remote`, `npx wrangler secret put ADMIN_TOKEN`,
   `npx wrangler deploy`.
5. Set `ENDPOINT` in `agree.html` to the Worker URL, decide where the agree link appears (services page /
   invoice email), and turn `MAINTENANCE_MODE` off only when Tim says so.
