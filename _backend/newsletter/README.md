# Team of Bots signup backend (double opt-in)

**Status: NOT DEPLOYED.** Nothing in this folder runs anywhere yet, and no Resend
contacts, secrets or Cloudflare resources have been created. This folder is removed
before every Pages deploy (`rm -rf … _backend …` in `.github/workflows/deploy.yml`),
so it never ships to tjnai.ai.

## What it does

1. `POST /subscribe` (called by the form on tjnai.ai, CORS locked to `https://tjnai.ai`)
   - Drops bots quietly: honeypot field `company` filled, or submitted < 2.5 s after page load.
   - Rate limits: 5 requests/min per IP and 1 confirm email/min per address (Workers Rate Limiting API, approximate).
   - Creates or updates the Resend contact with `optin_status = pending`. It is **not** added to the
     "Team of Bots" segment and the "Team of Bots weekly" topic stays at its opt_out default.
     A contact that is already confirmed is never downgraded and is not emailed again.
   - Sends one confirm email via Resend `/emails`: From `Tim <tim@tjnai.ai>`, Reply-To `info@tjnai.ai`,
     CAN-SPAM postal footer, `List-Unsubscribe` mailto header, and an idempotency key (one per address per UTC day).
2. `GET /confirm?t=…` verifies the token and shows a page that auto-POSTs (so email link scanners that only
   GET the URL can't subscribe anyone).
3. `POST /confirm` verifies the token again, then sets `optin_status = confirmed`, `unsubscribed = false`,
   adds the contact to segment "Team of Bots" and opts it in to the topic. Shows "You're subscribed."

**Token:** `v1.<b64url(email)>.<exp>.<HMAC-SHA256(SIGNING_SECRET, "v1|email|exp")>`, valid 7 days, verified with
`crypto.subtle.verify` (constant time). Without the secret it can't be forged, extended or moved to another
address (see `test/handler.test.js`).

The Resend API key lives only in the Worker's secrets. The browser only knows the Worker URL.

## Why Cloudflare Workers (not Supabase Edge Functions)

- The Supabase org "TJNAI.ai" is on the **Pro** plan, and its two projects are production backends for
  **Tovael** (App Store app) and **recipelift** (DishLift). A public, unauthenticated newsletter endpoint
  would share their secrets store, logs and blast radius, and would tie the newsletter to a paid plan
  that may be downgraded later. A new free Supabase project isn't an option inside a Pro org.
- Cloudflare Workers Free: 100,000 requests/day, built-in rate limiting binding, a free `*.workers.dev`
  URL (no DNS change at GoDaddy), $0, no card required.
- The handler uses only web-standard `Request`/`Response`/`crypto.subtle`, so it can be moved to a Supabase
  Edge Function later by wrapping `handle()` in `Deno.serve` (rate limiting would then need another store).

## Deploy steps (for Tim, or a bot once Tim says go)

Needs: a free Cloudflare account (Tim signs up; any bot asks before creating accounts), Node 20+.

```bash
cd _backend/newsletter
npx wrangler@latest login                     # opens the browser, Tim approves
npx wrangler@latest deploy                    # first deploy prints https://tob-newsletter.<account>.workers.dev
# edit wrangler.toml: PUBLIC_BASE_URL = that URL, then:
npx wrangler@latest secret put RESEND_API_KEY # a NEW Resend key (Full access: sending-only keys cannot manage contacts), name it tob-newsletter
openssl rand -base64 48 | npx wrangler@latest secret put SIGNING_SECRET
npx wrangler@latest deploy
```

Then in the site repo set `NEWSLETTER_ENDPOINT` in `assets/newsletter.js` to the Worker URL, and test with a
Tim-owned address before merging (submit → confirm email arrives → click → contact shows `confirmed`,
in segment "Team of Bots", topic opt_in).

### Secrets and vars

| Name | Kind | Value |
|---|---|---|
| `RESEND_API_KEY` | secret | New Resend API key (full access is needed for contacts; do not reuse another bot's key) |
| `SIGNING_SECRET` | secret | 48+ random bytes, base64 (min 32 chars). Rotating it invalidates unclicked links |
| `SEGMENT_ID` | var | `11b0fc63-f246-4274-b0f3-66165e61d9d7` (Resend segment "Team of Bots") |
| `TOPIC_ID` | var | `9f9bbe91-987a-4517-b208-5cc0a3640a29` ("Team of Bots weekly", default opt_out) |
| `PUBLIC_BASE_URL` | var | The Worker's own URL, used to build confirm links |
| `ALLOWED_ORIGIN` | var | `https://tjnai.ai` |
| `FROM_ADDRESS` / `REPLY_TO` | var | `Tim <tim@tjnai.ai>` / `info@tjnai.ai` |

## Cost and limits

- Cloudflare Workers Free: $0. Resend: the account's current plan; each signup uses 1 transactional email and
  1 contact. Resend Free (from Nov 3) is 100 emails/day shared with outreach and 1,000 contacts/month, so a spike
  could crowd out pitches (see `projects/newsletter/plan.md` §6). A 429 from Resend returns "try again later".
- Pending contacts that never confirm should be pruned after 7 days (existing plan); that routine is separate.

## Not included (needs separate approval)

- Welcome 1 on confirm (standing send type P2 is still "proposed").
- Unsubscribe handling: the weekly broadcasts already use Resend's managed unsubscribe link.

## Local tests

```bash
cd _backend/newsletter
node --test test/*.test.js      # mocked Resend, no network
node test/render-email-preview.js   # writes preview/confirm-email.html
```
