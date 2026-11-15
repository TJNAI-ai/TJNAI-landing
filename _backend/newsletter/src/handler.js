// Team of Bots sign-up Worker for tjnai.ai: STORE-ONLY.
//
// Routes
//   OPTIONS /subscribe  CORS preflight (ALLOWED_ORIGIN only)
//   POST    /subscribe  {email, first_name?, company (honeypot), elapsed_ms, source_page?, consent_text}
//                       -> one row in D1 newsletter_signups (status 'pending'), 200 {ok:true}
//
// This Worker sends no email and calls no outside service: there is no Resend code, no fetch() and no
// secret. A sign-up is only stored. Sending (one confirmation email, then the newsletter to confirmed
// addresses only) is a separate, future change that needs Tim's approval.

export const CONSENT_TEXT =
  "Team of Bots hasn't started sending yet. Leave your email and, when it does, we'll send you one email to confirm before anything else. Unsubscribe anytime.";
const LIVE_ORIGIN = "https://tjnai.ai";
const MIN_ELAPSED_MS = 2500; // faster than this is treated as a bot
const MAX_BODY = 2048;
const EMAIL_RE = /^[^\s@<>()[\]\\,;:"]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;
const SOURCE_RE = /^\/[A-Za-z0-9/_-]{0,80}$/;

export function normalizeEmail(v) {
  if (typeof v !== "string") return null;
  const e = v.trim().toLowerCase();
  return e.length <= 254 && EMAIL_RE.test(e) ? e : null;
}

export function cleanFirstName(v) {
  if (typeof v !== "string") return "";
  return v.replace(/[\u0000-\u001f\u007f<>"]/g, "").replace(/\s+/g, " ").trim().slice(0, 60);
}

export function utcNow(date = new Date()) {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function origins(env) {
  return String(env.ALLOWED_ORIGIN || "https://tjnai.ai").split(",").map((s) => s.trim()).filter(Boolean);
}

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
}

function json(body, status, origin) {
  const headers = { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" };
  if (origin) Object.assign(headers, cors(origin));
  return new Response(JSON.stringify(body), { status, headers });
}

async function limited(limiter, key) {
  if (!limiter || typeof limiter.limit !== "function") return false;
  try { return !(await limiter.limit({ key })).success; } catch { return false; }
}

async function subscribe(request, env, origin) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown"; // used for rate limiting only, never stored
  if (await limited(env.RL_IP, `ip:${ip}`)) return json({ ok: false, error: "rate_limited" }, 429, origin);
  const type = request.headers.get("Content-Type") || "";
  if (!type.toLowerCase().startsWith("application/json")) return json({ ok: false, error: "content_type" }, 415, origin);
  const raw = await request.text();
  if (raw.length > MAX_BODY) return json({ ok: false, error: "too_large" }, 413, origin);
  let body;
  try { body = JSON.parse(raw); } catch { return json({ ok: false, error: "bad_json" }, 400, origin); }
  if (!body || typeof body !== "object") return json({ ok: false, error: "bad_json" }, 400, origin);

  // Bot traps: answer like a success, store nothing.
  const elapsed = Number(body.elapsed_ms);
  if ((typeof body.company === "string" && body.company.trim() !== "") || !Number.isFinite(elapsed) || elapsed < MIN_ELAPSED_MS) {
    return json({ ok: true }, 200, origin);
  }
  const email = normalizeEmail(body.email);
  if (!email) return json({ ok: false, error: "invalid_email" }, 400, origin);
  if (body.consent_text !== CONSENT_TEXT) return json({ ok: false, error: "consent_text" }, 400, origin);
  const firstName = cleanFirstName(body.first_name) || null;
  const source = typeof body.source_page === "string" && SOURCE_RE.test(body.source_page) ? body.source_page : "/newsletter";
  // Rows from any site other than tjnai.ai (the relaunch preview) and explicit example.com test sign-ups
  // are flagged is_test=1 so they can be excluded or deleted.
  const isTest = origin !== LIVE_ORIGIN || (body.is_test === true && /@example\.(com|org|net)$/.test(email)) ? 1 : 0;

  // Same answer whether or not the address is already on the list (no enumeration). An existing row is
  // never changed here, so a later unsubscribe can't be undone by re-submitting the form.
  await env.DB.prepare(
    `INSERT INTO newsletter_signups (signup_id, email, first_name, status, consent_text, source_page, signed_up_at, is_test)
     VALUES (?1, ?2, ?3, 'pending', ?4, ?5, ?6, ?7) ON CONFLICT(email) DO NOTHING`
  ).bind(crypto.randomUUID(), email, firstName, CONSENT_TEXT, source, utcNow(), isTest).run();
  return json({ ok: true }, 200, origin);
}

export async function handle(request, env) {
  const url = new URL(request.url);
  const reqOrigin = request.headers.get("Origin");
  const allowed = reqOrigin && origins(env).includes(reqOrigin) ? reqOrigin : null;
  if (url.pathname === "/subscribe") {
    if (request.method === "OPTIONS") {
      return allowed ? new Response(null, { status: 204, headers: cors(allowed) }) : json({ ok: false, error: "origin" }, 403);
    }
    if (request.method === "POST") {
      if (!allowed) return json({ ok: false, error: "origin" }, 403);
      try {
        return await subscribe(request, env, allowed);
      } catch {
        return json({ ok: false, error: "server" }, 500, allowed);
      }
    }
    return json({ ok: false, error: "method" }, 405, allowed);
  }
  return new Response("Not found", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8", "X-Robots-Tag": "noindex" } });
}
