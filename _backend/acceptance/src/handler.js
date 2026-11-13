// Clickwrap acceptance log for tjnai.ai (spec: DRAFT_clickwrap-spec_v0.1_2026-11-13.md).
// Web-standard Request/Response + a D1 binding (env.DB).
//
// Routes
//   OPTIONS /accept        CORS preflight (ALLOWED_ORIGIN only)
//   POST    /accept        {flow, name, email, accepted:[{document_id, version, content_hash, checked, checkbox_text}],
//                           locale?, viewport?, form_template_id?}
//                          201 {acceptance_batch_id, engagement_id, account_id}
//                          400 missing/unchecked/extra box, bad name/email/flow, wrong checkbox text
//                          409 a box was accepted against a version or hash that is not current (re-render and re-accept)
//   GET     /manifest      current versions and hashes per flow (public, read-only)
//   GET     /reacceptance  ?account_id=&flow=   Bearer ADMIN_TOKEN. Says whether a material change requires
//                          re-acceptance before the next renewal/charge (spec section 8). Read-only.
//
// The server is the gate: the browser disabling the button is a convenience, not the control.
// Nothing here charges, invoices or provisions anything. Rows are INSERT-only; the schema blocks UPDATE/DELETE.
import MANIFEST from "./manifest.json";

const MAX_BODY = 16 * 1024;
const EMAIL_RE = /^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+$/;
const ID_RE = /^[A-Za-z0-9._-]{1,80}$/;

function origins(env) {
  return String(env.ALLOWED_ORIGIN || "https://tjnai.ai").split(",").map((s) => s.trim()).filter(Boolean);
}

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
}

function json(data, status, origin) {
  const headers = { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" };
  if (origin) Object.assign(headers, corsHeaders(origin));
  return new Response(JSON.stringify(data), { status, headers });
}

export function utcNow(date = new Date()) {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function accountIdFor(email) {
  return "acct_" + (await sha256hex(email.trim().toLowerCase())).slice(0, 24);
}

function versionOf(manifest, docId, version) {
  const doc = manifest.documents[docId];
  return doc ? doc.versions.find((v) => v.version === version) : undefined;
}

function currentOf(manifest, docId) {
  const doc = manifest.documents[docId];
  return versionOf(manifest, docId, doc.current_version);
}

export function currentSet(manifest, flow) {
  return manifest.flows[flow].required.map((id) => {
    const v = currentOf(manifest, id);
    return { document_id: id, version: v.version, content_hash: v.content_sha256, checkbox_text: v.checkbox_text };
  });
}

function clean(value, max) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
}

// Pure validation: returns {ok:true, ...} or {ok:false, status, error, ...}.
export function validateSubmission(body, manifest = MANIFEST) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, status: 400, error: "bad_request" };
  const flow = body.flow;
  if (typeof flow !== "string" || !Object.prototype.hasOwnProperty.call(manifest.flows, flow)) {
    return { ok: false, status: 400, error: "unknown_flow" };
  }
  const name = clean(body.name, 200);
  const email = clean(body.email, 254).toLowerCase();
  if (!name) return { ok: false, status: 400, error: "name_required" };
  if (!EMAIL_RE.test(email)) return { ok: false, status: 400, error: "email_invalid" };
  if (!Array.isArray(body.accepted)) return { ok: false, status: 400, error: "acceptance_missing", missing: manifest.flows[flow].required };

  const required = manifest.flows[flow].required;
  const byId = new Map();
  for (const item of body.accepted) {
    if (!item || typeof item !== "object" || typeof item.document_id !== "string") return { ok: false, status: 400, error: "bad_request" };
    if (byId.has(item.document_id)) return { ok: false, status: 400, error: "duplicate_document", document_id: item.document_id };
    byId.set(item.document_id, item);
  }
  const extra = [...byId.keys()].filter((id) => !required.includes(id));
  if (extra.length) return { ok: false, status: 400, error: "unexpected_document", extra };
  const missing = required.filter((id) => !byId.has(id) || byId.get(id).checked !== true);
  if (missing.length) return { ok: false, status: 400, error: "acceptance_missing", missing };

  const stale = [];
  const rows = [];
  for (const id of required) {
    const item = byId.get(id);
    const current = currentOf(manifest, id);
    if (item.version !== current.version || item.content_hash !== current.content_sha256) {
      stale.push(id);
      continue;
    }
    if (clean(item.checkbox_text, 2000) !== current.checkbox_text) {
      return { ok: false, status: 400, error: "checkbox_text_mismatch", document_id: id };
    }
    rows.push({ document_id: id, version: current });
  }
  if (stale.length) return { ok: false, status: 409, error: "acceptance_stale", stale, current: currentSet(manifest, flow) };

  const opt = (v, max) => (typeof v === "string" ? clean(v, max) || null : null);
  const template = opt(body.form_template_id, 80);
  return {
    ok: true,
    flow,
    name,
    email,
    rows,
    locale: opt(body.locale, 35),
    viewport: opt(body.viewport, 20),
    form_template_id: template && ID_RE.test(template) ? template : null,
  };
}

async function accept(request, env, manifest, origin) {
  const type = request.headers.get("Content-Type") || "";
  if (!type.toLowerCase().startsWith("application/json")) return json({ ok: false, error: "content_type" }, 415, origin);
  const ip = request.headers.get("CF-Connecting-IP") || "";
  if (env.RL_IP && ip) {
    const { success } = await env.RL_IP.limit({ key: ip });
    if (!success) return json({ ok: false, error: "rate_limited" }, 429, origin);
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY) return json({ ok: false, error: "too_large" }, 413, origin);
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ ok: false, error: "bad_json" }, 400, origin);
  }
  const v = validateSubmission(body, manifest);
  if (!v.ok) {
    const { ok, status, ...rest } = v;
    return json({ ok: false, ...rest }, status, origin);
  }

  // Integrity: a version string must never point at different bytes than the one already logged.
  for (const row of v.rows) {
    const existing = await env.DB.prepare(
      "SELECT content_hash FROM legal_document_versions WHERE document_id = ? AND version = ?",
    ).bind(row.document_id, row.version.version).first();
    if (existing && existing.content_hash !== row.version.content_sha256) {
      console.error(`version registry conflict for ${row.document_id} ${row.version.version}`);
      return json({ ok: false, error: "version_conflict" }, 500, origin);
    }
  }

  const now = utcNow();
  const accountId = await accountIdFor(v.email);
  const engagementId = "eng_" + crypto.randomUUID();
  const batchId = "acb_" + crypto.randomUUID();
  const ua = clean(request.headers.get("User-Agent") || "", 1024);
  const flowId = manifest.flows[v.flow].page_or_flow;
  const db = env.DB;
  const stmts = [
    db.prepare("INSERT OR IGNORE INTO accounts (account_id, email, created_at) VALUES (?, ?, ?)").bind(accountId, v.email, now),
    db.prepare("INSERT INTO engagements (engagement_id, account_id, flow_id, created_at) VALUES (?, ?, ?, ?)").bind(engagementId, accountId, v.flow, now),
  ];
  for (const row of v.rows) {
    const ver = row.version;
    stmts.push(
      db.prepare(
        "INSERT OR IGNORE INTO legal_document_versions (document_id, version, content_hash, artifact_uri, effective_at, is_material_change, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ).bind(row.document_id, ver.version, ver.content_sha256, ver.artifact_uri, ver.effective_at, ver.is_material_change ? 1 : 0, now),
    );
  }
  for (const row of v.rows) {
    const ver = row.version;
    stmts.push(
      db.prepare(
        `INSERT INTO acceptance_events (acceptance_id, acceptance_batch_id, account_id, name, email, document_id, document_version,
           content_hash, accepted_at, ip_address, user_agent, page_or_flow, checkbox_text_shown, billing_disclosure_shown,
           engagement_id, locale, viewport, form_template_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        "acc_" + crypto.randomUUID(), batchId, accountId, v.name, v.email, row.document_id, ver.version,
        ver.content_sha256, now, ip || "unknown", ua, flowId, ver.checkbox_text,
        ver.disclosure ? ver.disclosure.join("\n") : null,
        engagementId, v.locale, v.viewport, v.form_template_id,
      ),
    );
  }
  // D1 batch() runs as one transaction: either every row is written or none is.
  await db.batch(stmts);
  return json({ ok: true, acceptance_batch_id: batchId, engagement_id: engagementId, account_id: accountId, accepted_at: now }, 201, origin);
}

// Spec section 8: a stored acceptance is good until a newer version marked is_material_change is published.
export async function reacceptanceStatus(db, accountId, flow, manifest = MANIFEST) {
  const documents = [];
  for (const id of manifest.flows[flow].required) {
    const doc = manifest.documents[id];
    const order = doc.versions.map((x) => x.version);
    const last = await db.prepare(
      "SELECT document_version, accepted_at FROM acceptance_events WHERE account_id = ? AND document_id = ? ORDER BY accepted_at DESC, rowid DESC LIMIT 1",
    ).bind(accountId, id).first();
    const required = doc.current_material_version;
    let state = "ok";
    if (!last) state = "never_accepted";
    else if (order.indexOf(last.document_version) < order.indexOf(required)) state = "material_change";
    documents.push({ document_id: id, accepted_version: last ? last.document_version : null, required_version: required, state });
  }
  return { account_id: accountId, flow, needs_reacceptance: documents.some((d) => d.state !== "ok"), documents };
}

export async function handle(request, env, manifest = MANIFEST) {
  const url = new URL(request.url);
  const reqOrigin = request.headers.get("Origin");
  const allowed = reqOrigin && origins(env).includes(reqOrigin) ? reqOrigin : null;
  try {
    if (url.pathname === "/accept") {
      if (request.method === "OPTIONS") {
        return new Response(null, { status: allowed ? 204 : 403, headers: allowed ? corsHeaders(allowed) : {} });
      }
      if (request.method !== "POST") return json({ ok: false, error: "method" }, 405, allowed);
      if (!allowed) return json({ ok: false, error: "origin" }, 403);
      return await accept(request, env, manifest, allowed);
    }
    if (url.pathname === "/manifest" && request.method === "GET") {
      const flows = {};
      for (const flow of Object.keys(manifest.flows)) flows[flow] = currentSet(manifest, flow);
      return json({ flows }, 200, allowed);
    }
    if (url.pathname === "/reacceptance" && request.method === "GET") {
      const token = env.ADMIN_TOKEN;
      if (!token || token.length < 32 || request.headers.get("Authorization") !== `Bearer ${token}`) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
      const accountId = url.searchParams.get("account_id") || "";
      const flow = url.searchParams.get("flow") || "";
      if (!/^acct_[0-9a-f]{24}$/.test(accountId) || !Object.prototype.hasOwnProperty.call(manifest.flows, flow)) {
        return json({ ok: false, error: "bad_request" }, 400);
      }
      return json(await reacceptanceStatus(env.DB, accountId, flow, manifest), 200);
    }
    return json({ ok: false, error: "not_found" }, 404);
  } catch (e) {
    console.error(String(e));
    return json({ ok: false, error: "server" }, 500, allowed);
  }
}
