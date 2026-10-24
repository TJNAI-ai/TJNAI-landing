// Team of Bots signup backend (double opt-in) for tjnai.ai.
// Web-standard Request/Response only, so it runs on Cloudflare Workers (src/worker.js)
// and can be ported to Supabase Edge Functions / Deno with a few lines.
//
// Routes
//   OPTIONS /subscribe   CORS preflight (only https://tjnai.ai)
//   POST    /subscribe   {email, first_name?, company (honeypot), elapsed_ms}
//                        -> contact created/updated with optin_status=pending, NOT in the segment,
//                           then one confirm email via Resend /emails.
//   GET     /confirm?t=  shows a "confirming…" page that auto-POSTs (so link scanners that only
//                        GET the URL can't subscribe anyone)
//   POST    /confirm     t=<token> -> verify HMAC + expiry, then optin_status=confirmed,
//                        add to segment "Team of Bots", topic opt_in. Shows "You're subscribed".

import { signToken, verifyToken } from "./token.js";
import { renderConfirmEmail, escapeHtml } from "./email.js";

const RESEND = "https://api.resend.com";
const TOKEN_TTL_SECONDS = 7 * 24 * 3600;
const MIN_ELAPSED_MS = 2500; // "time to submit" check: faster than this is treated as a bot
const EMAIL_RE = /^[^\s@<>()[\]\\,;:"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

export function normalizeEmail(v) {
  if (typeof v !== "string") return null;
  const e = v.trim().toLowerCase();
  return e.length <= 254 && EMAIL_RE.test(e) ? e : null;
}

export function cleanFirstName(v) {
  if (typeof v !== "string") return "";
  return v.replace(/[\u0000-\u001f\u007f<>"]/g, "").replace(/\s+/g, " ").trim().slice(0, 60);
}

function cfg(env) {
  const missing = ["RESEND_API_KEY", "SIGNING_SECRET", "SEGMENT_ID", "PUBLIC_BASE_URL"].filter((k) => !env[k]);
  if (missing.length) throw new Error(`missing config: ${missing.join(", ")}`);
  return {
    key: env.RESEND_API_KEY,
    secret: env.SIGNING_SECRET,
    segmentId: env.SEGMENT_ID,
    topicId: env.TOPIC_ID || "",
    base: env.PUBLIC_BASE_URL.replace(/\/+$/, ""),
    origin: env.ALLOWED_ORIGIN || "https://tjnai.ai",
    from: env.FROM_ADDRESS || "Tim <tim@tjnai.ai>",
    replyTo: env.REPLY_TO || "info@tjnai.ai",
  };
}

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...corsHeaders(origin) },
  });
}

async function resend(c, method, path, body, extraHeaders = {}) {
  const res = await fetch(RESEND + path, {
    method,
    headers: { Authorization: `Bearer ${c.key}`, "Content-Type": "application/json", ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  return { ok: res.ok, status: res.status, data };
}

async function getContact(c, email) {
  const r = await resend(c, "GET", `/contacts/${encodeURIComponent(email)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`resend get contact ${r.status}`);
  return r.data;
}

async function inSegment(c, email) {
  const r = await resend(c, "GET", `/contacts/${encodeURIComponent(email)}/segments`);
  if (!r.ok) return false;
  const list = Array.isArray(r.data?.data) ? r.data.data : [];
  return list.some((s) => s.id === c.segmentId);
}

// Resend returns properties as { key: { value, type } }; tolerate a plain value too.
function optinStatus(contact) {
  const p = contact?.properties?.optin_status;
  return p && typeof p === "object" ? p.value : p;
}

async function limited(limiter, key) {
  if (!limiter || typeof limiter.limit !== "function") return false; // binding not configured: skip
  try { return !(await limiter.limit({ key })).success; } catch { return false; }
}

// ---------- POST /subscribe ----------
async function subscribe(request, env, c) {
  const origin = request.headers.get("Origin");
  if (origin !== c.origin) return json({ ok: false, error: "forbidden" }, 403, c.origin);

  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (await limited(env.RL_IP, `ip:${ip}`)) return json({ ok: false, error: "rate_limited" }, 429, c.origin);

  const raw = await request.text();
  if (raw.length > 2048) return json({ ok: false, error: "too_large" }, 413, c.origin);
  let body;
  try { body = JSON.parse(raw); } catch { return json({ ok: false, error: "bad_json" }, 400, c.origin); }

  // Bot traps: answer like a success so bots learn nothing, but do nothing.
  const elapsed = Number(body.elapsed_ms);
  if ((typeof body.company === "string" && body.company.trim() !== "") || !Number.isFinite(elapsed) || elapsed < MIN_ELAPSED_MS) {
    return json({ ok: true }, 200, c.origin);
  }

  const email = normalizeEmail(body.email);
  if (!email) return json({ ok: false, error: "invalid_email" }, 400, c.origin);
  const firstName = cleanFirstName(body.first_name);

  // One confirm email per address per minute (per Cloudflare location).
  if (await limited(env.RL_EMAIL, `email:${email}`)) return json({ ok: true }, 200, c.origin);

  const existing = await getContact(c, email);
  if (existing) {
    // Already confirmed and on the list: don't email again, same answer (no account enumeration).
    if (optinStatus(existing) === "confirmed" && existing.unsubscribed !== true && (await inSegment(c, email))) {
      return json({ ok: true }, 200, c.origin);
    }
    // Never downgrade a confirmed contact or touch `unsubscribed` here; only fill in a missing first name
    // and mark pending if it isn't confirmed.
    const patch = {};
    if (firstName && !existing.first_name) patch.first_name = firstName;
    if (optinStatus(existing) !== "confirmed") patch.properties = { optin_status: "pending" };
    if (Object.keys(patch).length) {
      const r = await resend(c, "PATCH", `/contacts/${encodeURIComponent(email)}`, patch);
      if (!r.ok) throw new Error(`resend update contact ${r.status}`);
    }
  } else {
    // New contact: no segments, so it is NOT in "Team of Bots" until the confirm click.
    const r = await resend(c, "POST", "/contacts", {
      email,
      ...(firstName ? { first_name: firstName } : {}),
      unsubscribed: false,
      properties: { optin_status: "pending" },
    });
    if (!r.ok && r.status !== 409) throw new Error(`resend create contact ${r.status}`);
  }

  const token = await signToken(email, c.secret, TOKEN_TTL_SECONDS);
  const confirmUrl = `${c.base}/confirm?t=${encodeURIComponent(token)}`;
  const mail = renderConfirmEmail({ firstName: firstName || existing?.first_name || "", confirmUrl });
  const day = new Date().toISOString().slice(0, 10);
  const sent = await resend(c, "POST", "/emails", {
    from: c.from,
    to: [email],
    reply_to: c.replyTo,
    subject: mail.subject,
    html: mail.html,
    text: mail.text,
    headers: { "List-Unsubscribe": "<mailto:info@tjnai.ai?subject=Unsubscribe%20Team%20of%20Bots>" },
    tags: [{ name: "category", value: "tob_confirm" }],
  }, { "Idempotency-Key": `tob-confirm-${email}-${day}`.slice(0, 256) });

  if (sent.status === 429) return json({ ok: false, error: "busy" }, 503, c.origin); // Resend quota: try later
  if (!sent.ok && sent.status !== 409) throw new Error(`resend send ${sent.status}`); // 409 = same idempotency key today
  return json({ ok: true }, 200, c.origin);
}

// ---------- /confirm ----------
const PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'";

function page(title, inner, status = 200) {
  const html = `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta name="robots" content="noindex"><title>${escapeHtml(title)} · Team of Bots</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#020209;color:#f4f7fb;font:16px/1.6 Inter,system-ui,sans-serif}
.card{width:min(520px,100%);padding:36px 28px;border-radius:22px;border:1px solid rgba(255,255,255,.12);background:radial-gradient(80% 120% at 0% 100%,rgba(0,212,255,.12),transparent 50%),radial-gradient(80% 120% at 100% 0%,rgba(139,92,246,.16),transparent 46%),#10101c}
.k{margin:0 0 10px;color:#00d4ff;font-size:.75rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase}h1{margin:0 0 12px;font:700 clamp(1.8rem,5vw,2.4rem)/1.1 "Space Grotesk",system-ui,sans-serif;letter-spacing:-.03em}
p{color:#b7c4d6;margin:0 0 14px}a{color:#00d4ff}.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:12px 18px;border-radius:12px;border:0;background:#00d4ff;color:#041018;font:600 .98rem Inter,system-ui,sans-serif;text-decoration:none;cursor:pointer}
</style></head><body><main class="card"><p class="k">Team of Bots</p>${inner}</main></body></html>`;
  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP, "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex" },
  });
}

const BAD_LINK = () => page("Link expired", `<h1>That link didn't work</h1><p>It may have expired (links last 7 days) or been copied incompletely.</p><p><a class="btn" href="https://tjnai.ai/newsletter">Sign up again</a></p><p>Questions? <a href="mailto:info@tjnai.ai">info@tjnai.ai</a></p>`, 400);

async function confirmGet(url, c) {
  const t = url.searchParams.get("t") || "";
  if (!(await verifyToken(t, c.secret))) return BAD_LINK();
  // Auto-submit for real browsers; link scanners that only GET never reach POST.
  return page("Confirm", `<h1>Confirming your signup…</h1><form method="post" action="/confirm" id="f"><input type="hidden" name="t" value="${escapeHtml(t)}"><p>If nothing happens, tap the button.</p><button class="btn" type="submit">Confirm my subscription</button></form><script>document.getElementById("f").submit()</script>`);
}

async function confirmPost(request, env, c) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (await limited(env.RL_IP, `ip:${ip}`)) return page("Slow down", `<h1>Too many tries</h1><p>Please wait a minute and try the link again.</p>`, 429);
  const form = await request.formData().catch(() => null);
  const email = await verifyToken(form?.get("t") || "", c.secret);
  if (!email) return BAD_LINK();

  let contact = await getContact(c, email);
  if (!contact) {
    // Pending contact was pruned before the click: recreate it.
    const r = await resend(c, "POST", "/contacts", { email, unsubscribed: false, properties: { optin_status: "confirmed" } });
    if (!r.ok && r.status !== 409) throw new Error(`resend create contact ${r.status}`);
  } else {
    // The click is an explicit opt-in, so it also clears an old global unsubscribe.
    const r = await resend(c, "PATCH", `/contacts/${encodeURIComponent(email)}`, { unsubscribed: false, properties: { optin_status: "confirmed" } });
    if (!r.ok) throw new Error(`resend update contact ${r.status}`);
  }
  const seg = await resend(c, "POST", `/contacts/${encodeURIComponent(email)}/segments/${c.segmentId}`);
  if (!seg.ok && seg.status !== 409) throw new Error(`resend add to segment ${seg.status}`);
  if (c.topicId) {
    const tp = await resend(c, "PATCH", `/contacts/${encodeURIComponent(email)}/topics`, [{ id: c.topicId, subscription: "opt_in" }]);
    if (!tp.ok) throw new Error(`resend topic opt-in ${tp.status}`);
  }
  return page("Subscribed", `<h1>You're subscribed.</h1><p>Thanks for confirming. Team of Bots lands in your inbox every other Tuesday at 7 AM ET.</p><p>Unsubscribe anytime from any issue, or write to <a href="mailto:info@tjnai.ai">info@tjnai.ai</a>.</p><p><a class="btn" href="https://tjnai.ai/">Back to tjnai.ai</a></p>`);
}

export async function handle(request, env) {
  const url = new URL(request.url);
  let c;
  try { c = cfg(env); } catch (e) { console.error(String(e)); return new Response("Not configured", { status: 500 }); }
  try {
    if (url.pathname === "/subscribe") {
      if (request.method === "OPTIONS") {
        const ok = request.headers.get("Origin") === c.origin;
        return new Response(null, { status: ok ? 204 : 403, headers: corsHeaders(c.origin) });
      }
      if (request.method === "POST") return await subscribe(request, env, c);
      return json({ ok: false, error: "method" }, 405, c.origin);
    }
    if (url.pathname === "/confirm") {
      if (request.method === "GET") return await confirmGet(url, c);
      if (request.method === "POST") return await confirmPost(request, env, c);
      return new Response("Method not allowed", { status: 405 });
    }
    return new Response("Not found", { status: 404 });
  } catch (e) {
    console.error(String(e)); // never log the API key or tokens
    if (url.pathname === "/confirm") return page("Error", `<h1>Something went wrong</h1><p>Please try the link again in a minute, or write to <a href="mailto:info@tjnai.ai">info@tjnai.ai</a>.</p>`, 502);
    return json({ ok: false, error: "server" }, 502, c.origin);
  }
}
