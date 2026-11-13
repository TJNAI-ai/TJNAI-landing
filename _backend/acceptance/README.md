# Clickwrap acceptance log (Cloudflare Worker + D1)

**Status: DRAFT, NOT DEPLOYED.** Nothing here runs anywhere yet. No Cloudflare account, D1 database,
Worker, secret or DNS record has been created. This folder is removed before every Pages deploy
(`rm -rf _backend …` in `.github/workflows/deploy.yml`), so it never ships to tjnai.ai.

Spec: Lena's `DRAFT_eva-handoff_v0.2_2026-11-13.md` (document IDs, labels, flows, log fields, tests T1–T20),
on top of `DRAFT_clickwrap-spec_v0.1_2026-11-13.md` (internal). Not legal advice; attorney review and Tim's
approval are required before go-live.

## What it does

`agree.html?flow=audit|build|care-plan` asks for the **Business legal name** (required; B2B only), the
signer's name and email, then shows one **unchecked** checkbox per item, each label in Lena's exact words and
linking the **frozen** version being accepted (`/legal/archive/<slug>/v0.2`):

| Flow | `page_or_flow` | Boxes, in order |
|---|---|---|
| audit | `checkout.audit.v1` | TOS, PRIV, REFUND, MSA, B2B-CONFIRM, SOW-AUDIT |
| build | `checkout.build.v1` | TOS, PRIV, REFUND, MSA, B2B-CONFIRM, SOW-BUILD |
| care-plan | `checkout.care-plan.v1` | TOS, PRIV, REFUND, MSA, B2B-CONFIRM, SOW-MAINTAIN, BILLING-CONSENT |

The care plan shows the **Key terms at a glance** box (SOW-MAINTAIN) above the boxes. The button stays
disabled until every box is checked and the business name is filled in. The page records agreement only; it
takes no payment.

**B2B-CONFIRM vs the MSA box.** The MSA box is Lena's short label (handoff §2.4); the business-purpose and
authority sentence lives only in the standalone B2B-CONFIRM box (handoff §2.4a, logged as `B2B-CONFIRM-v0.2`).
If the standalone box is ever removed, the sentence must go back into a new MSA label version.

The Worker is the real gate:

| Route | |
|---|---|
| `POST /accept` | Validates flow, business legal name, signer name, email and that **every** required item is present, `checked: true`, at the **current** `document_version` and `content_sha256`, with the exact label text. Missing/unchecked/extra box or blank business name → **400**; stale version or hash → **409** with the current versions (the page reloads). On success writes, in one D1 transaction, one `acceptance_events` row per box (plus account, engagement and version-registry rows) → **201** `{acceptance_batch_id, engagement_id, account_id}`. Any failure rolls back everything. CORS locked to `ALLOWED_ORIGIN`. |
| `GET /manifest` | Current versions, hashes and label text per flow (read-only). |
| `GET /reacceptance?account_id=&flow=` | `Authorization: Bearer $ADMIN_TOKEN`. Read-only: whether the account must re-accept before the next renewal/charge because a newer version marked `is_material_change` was published. |

Each `acceptance_events` row stores (handoff §4): acceptance_id, acceptance_batch_id, account_id,
business_legal_name, signer_name, email, document_id, document_version, display_id (e.g. `TOS-v0.2`),
content_sha256, accepted_at (UTC, RFC 3339 seconds), ip_address (`CF-Connecting-IP`), user_agent,
page_or_flow, checkbox_text_shown, billing_disclosure_shown (BILLING-CONSENT only), engagement_id,
acceptance_method (`checkbox`; `signed` is allowed by the schema for custom signed agreements, which need no
checkbox text), locale, viewport, form_template_id.

**Append-only:** SQLite triggers abort every `UPDATE`/`DELETE` on `acceptance_events` and
`legal_document_versions`. The Worker code only `INSERT`s and `SELECT`s.

## Retention

Keep acceptance records for **6 years after the customer relationship ends** (Tim's setting) —
**PENDING ATTORNEY CONFIRMATION** (PRIV-v0.2 §7 still brackets it). There is deliberately **no deletion
job**: the log is append-only, and any future purge must be a reviewed, separate change.

## Versions, hashes and prices

- `_build/legal_docs.py --import /workspace/legal` copies Lena's v0.2 drafts into `_build/legal_src/`,
  stripping internal notes per handoff §1: every `<!-- INTERNAL: ... -->` block (multi-line, non-greedy) first,
  then the older marked notes. It refuses to write if anything internal is left: `/workspace`, `INTERNAL:`, an
  HTML comment, or a teammate agent name (whole word, case-sensitive). `_checks` runs the same leak scan over
  every rendered legal page, frozen copy, legal source and the Worker manifest.
- `_build/legal_versions.json` is the registry; `_build/pricing.json` holds every price, as Lena's bracketed
  placeholders. Prices are rendered into the agree page and the BILLING-CONSENT text from that one file, and
  the Worker checks the same rendered label.
- `python3 _build/legal_manifest.py --write` renders `terms.html`, `privacy.html`, `refund.html` and
  `legal/{msa,sow-audit,sow-build,sow-maintain}.html` (noindex), writes each frozen artifact
  `legal/archive/<slug>/<version>.html` **once**, records the SHA-256 of its exact bytes, and regenerates
  `src/manifest.json` and the checkbox block in `agree.html`. Inline items (B2B-CONFIRM, BILLING-CONSENT) are
  hashed over their exact label text. `--check` (and `_checks`) fails if an artifact no longer re-hashes, if a
  page drifts from its source, or if text changes without a new version.

To publish a new version: import the new draft, add a `versions` entry (`vX.Y`, `is_material_change` set by
the legal owner, `content_sha256: null`), run `--write`, commit. Never edit an artifact in `legal/archive/`.

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

1. Attorney review of the flow and the v0.2 documents; Lena resolves every bracket and the effective date.
2. Final prices into `_build/pricing.json` (Vera), then a new label version for BILLING-CONSENT.
3. On the free Cloudflare account: `npx wrangler d1 create tjnai-acceptance` (put the id in `wrangler.toml`),
   `npx wrangler d1 migrations apply tjnai-acceptance --remote`, `npx wrangler secret put ADMIN_TOKEN`,
   `npx wrangler deploy`.
4. Set `ENDPOINT` in `agree.html` to the Worker URL. At relaunch, add the agree links to the services page
   buttons (placement agreed with Sage), and turn `MAINTENANCE_MODE` off only when Tim says so.
5. Not built yet: payment, the renewal job that checks `/reacceptance` before each charge, holds and
   notifications, staff tools for signed custom agreements.
