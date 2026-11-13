// Local only: Miniflare + local D1. Nothing is deployed and nothing leaves the machine.
import { env as realEnv, applyD1Migrations } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { handle, currentSet, accountIdFor, reacceptanceStatus } from "../src/handler.js";
import MANIFEST from "../src/manifest.json";

// The real rate-limit binding would throttle this suite; it gets its own test below.
const env = { ...realEnv, RL_IP: undefined };
const ORIGIN = "https://tjnai.ai";
const UA = "Mozilla/5.0 (test) AcceptanceSuite/1.0";
const AUTH = { Authorization: `Bearer ${"t".repeat(40)}` };

function body(flow, overrides = {}) {
  const accepted = currentSet(MANIFEST, flow).map((d) => ({
    document_id: d.document_id,
    version: d.version,
    content_hash: d.content_hash,
    checked: true,
    checkbox_text: d.checkbox_text,
  }));
  return { flow, name: "Ada Lovelace", email: "Ada@Example.com", accepted, locale: "en-US", viewport: "1440x900", form_template_id: "agree.v1", ...overrides };
}

function post(payload, { origin = ORIGIN, ip = "203.0.113.7" } = {}) {
  return new Request("https://acceptance.test/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: origin, "User-Agent": UA, "CF-Connecting-IP": ip },
    body: JSON.stringify(payload),
  });
}

async function count(table = "acceptance_events") {
  return (await env.DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first()).n;
}

// A copy of the manifest where `docId` gets a newer version (material or not).
function bumped(docId, material) {
  const m = structuredClone(MANIFEST);
  const doc = m.documents[docId];
  const prev = doc.versions[doc.versions.length - 1];
  const next = { ...prev, version: "2099-01-01-1", content_sha256: "f".repeat(64), is_material_change: material,
    checkbox_text: prev.checkbox_text.replace(prev.version, "2099-01-01-1") };
  doc.versions.push(next);
  doc.current_version = next.version;
  if (material) doc.current_material_version = next.version;
  return m;
}

// Fresh schema for every test. (DROP is a test-only reset; the Worker never issues DDL.)
beforeEach(async () => {
  for (const table of ["acceptance_events", "legal_document_versions", "engagements", "accounts", "d1_migrations"]) {
    await realEnv.DB.prepare(`DROP TABLE IF EXISTS ${table}`).run();
  }
  await applyD1Migrations(realEnv.DB, realEnv.TEST_MIGRATIONS);
  expect(await count()).toBe(0);
});

describe("POST /accept: server-side gate", () => {

  it("rejects a crafted POST with no acceptance payload (400) and writes nothing", async () => {
    const res = await handle(post({ flow: "audit", name: "A", email: "a@b.co" }), env);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("acceptance_missing");
    expect(await count()).toBe(0);
    expect(await count("engagements")).toBe(0);
  });

  it.each(["TOS", "PRIVACY", "REFUND"])("rejects when %s is missing (400)", async (id) => {
    const b = body("build");
    b.accepted = b.accepted.filter((a) => a.document_id !== id);
    const res = await handle(post(b), env);
    expect(res.status).toBe(400);
    expect((await res.json()).missing).toEqual([id]);
    expect(await count()).toBe(0);
  });

  it("rejects when a box is present but unchecked (400)", async () => {
    const b = body("audit");
    b.accepted[1].checked = false;
    const res = await handle(post(b), env);
    expect(res.status).toBe(400);
    expect(await count()).toBe(0);
  });

  it("care plan: rejects without the recurring-billing box (400)", async () => {
    const b = body("care-plan");
    b.accepted = b.accepted.filter((a) => a.document_id !== "BILLING-CONSENT");
    const res = await handle(post(b), env);
    expect(res.status).toBe(400);
    expect((await res.json()).missing).toEqual(["BILLING-CONSENT"]);
  });

  it("rejects a stale version (409) and a wrong hash (409)", async () => {
    const b1 = body("audit");
    b1.accepted[0].version = "2026-01-01-1";
    const r1 = await handle(post(b1), env);
    expect(r1.status).toBe(409);
    const j1 = await r1.json();
    expect(j1.error).toBe("acceptance_stale");
    expect(j1.stale).toEqual(["TOS"]);
    const b2 = body("audit");
    b2.accepted[2].content_hash = "0".repeat(64);
    expect((await handle(post(b2), env)).status).toBe(409);
    expect(await count()).toBe(0);
  });

  it("rejects after a new version is published mid-session (409)", async () => {
    const res = await handle(post(body("audit")), env, bumped("PRIVACY", false));
    expect(res.status).toBe(409);
    expect((await res.json()).stale).toEqual(["PRIVACY"]);
  });

  it("rejects altered checkbox text, extra documents, bad origin, bad email", async () => {
    const b = body("audit");
    b.accepted[0].checkbox_text = "I agree to whatever";
    expect((await handle(post(b), env)).status).toBe(400);
    const c = body("audit");
    c.accepted.push({ ...body("care-plan").accepted[3] });
    expect((await handle(post(c), env)).status).toBe(400);
    expect((await handle(post(body("audit"), { origin: "https://evil.example" }), env)).status).toBe(403);
    expect((await handle(post(body("audit", { email: "nope" })), env)).status).toBe(400);
    expect((await handle(post(body("audit", { flow: "other" })), env)).status).toBe(400);
    expect(await count()).toBe(0);
  });

  it("rate limiter saying no yields 429 and writes nothing", async () => {
    const limited = { ...env, RL_IP: { limit: async () => ({ success: false }) } };
    expect((await handle(post(body("audit")), limited)).status).toBe(429);
    expect(await count()).toBe(0);
  });

  it("CORS preflight only for the allowed origin", async () => {
    const ok = await handle(new Request("https://acceptance.test/accept", { method: "OPTIONS", headers: { Origin: ORIGIN } }), env);
    expect(ok.status).toBe(204);
    expect(ok.headers.get("Access-Control-Allow-Origin")).toBe(ORIGIN);
    const bad = await handle(new Request("https://acceptance.test/accept", { method: "OPTIONS", headers: { Origin: "https://evil.example" } }), env);
    expect(bad.status).toBe(403);
  });
});

describe("POST /accept: valid submit writes the log", () => {
  it("writes one row per document with every required field", async () => {
    const res = await handle(post(body("care-plan")), env);
    expect(res.status).toBe(201);
    const out = await res.json();
    const { results } = await env.DB.prepare("SELECT * FROM acceptance_events ORDER BY document_id").all();
    expect(results.map((r) => r.document_id)).toEqual(["BILLING-CONSENT", "PRIVACY", "REFUND", "TOS"]);
    const expected = Object.fromEntries(currentSet(MANIFEST, "care-plan").map((d) => [d.document_id, d]));
    for (const r of results) {
      expect(r.acceptance_id).toMatch(/^acc_[0-9a-f-]{36}$/);
      expect(r.acceptance_batch_id).toBe(out.acceptance_batch_id);
      expect(r.account_id).toBe(await accountIdFor("ada@example.com"));
      expect(r.name).toBe("Ada Lovelace");
      expect(r.email).toBe("ada@example.com");
      expect(r.document_version).toBe(expected[r.document_id].version);
      expect(r.content_hash).toBe(expected[r.document_id].content_hash);
      expect(r.content_hash).toMatch(/^[0-9a-f]{64}$/);
      expect(r.accepted_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
      expect(r.ip_address).toBe("203.0.113.7");
      expect(r.user_agent).toBe(UA);
      expect(r.page_or_flow).toBe("checkout.care-plan.v1");
      expect(r.checkbox_text_shown).toBe(expected[r.document_id].checkbox_text);
      expect(r.engagement_id).toBe(out.engagement_id);
      expect(r.form_template_id).toBe("agree.v1");
    }
    const billing = results.find((r) => r.document_id === "BILLING-CONSENT");
    expect(billing.billing_disclosure_shown).toContain("$150 (USD) every month");
    expect(results.filter((r) => r.document_id !== "BILLING-CONSENT").every((r) => r.billing_disclosure_shown === null)).toBe(true);
    expect(await count("engagements")).toBe(1);
    expect(await count("legal_document_versions")).toBe(4);
  });

  it("audit flow writes exactly three rows", async () => {
    expect((await handle(post(body("audit")), env)).status).toBe(201);
    expect(await count()).toBe(3);
  });

  it("a repeat acceptance appends new rows and keeps the old ones", async () => {
    await handle(post(body("audit")), env);
    await handle(post(body("audit")), env);
    expect(await count()).toBe(6);
    expect(await count("accounts")).toBe(1);
  });
});

describe("append-only storage", () => {
  it("blocks UPDATE and DELETE on acceptance_events", async () => {
    await handle(post(body("audit")), env);
    await expect(env.DB.prepare("UPDATE acceptance_events SET name = 'x'").run()).rejects.toThrow(/append-only/);
    await expect(env.DB.prepare("DELETE FROM acceptance_events").run()).rejects.toThrow(/append-only/);
    expect(await count()).toBe(3);
    const { results } = await env.DB.prepare("SELECT DISTINCT name FROM acceptance_events").all();
    expect(results).toEqual([{ name: "Ada Lovelace" }]);
  });

  it("blocks UPDATE and DELETE on legal_document_versions", async () => {
    await handle(post(body("audit")), env);
    await expect(env.DB.prepare("UPDATE legal_document_versions SET content_hash = ?").bind("0".repeat(64)).run()).rejects.toThrow(/append-only/);
    await expect(env.DB.prepare("DELETE FROM legal_document_versions").run()).rejects.toThrow(/append-only/);
  });

  it("refuses a version string that now points at a different hash", async () => {
    await handle(post(body("audit")), env);
    const m = structuredClone(MANIFEST);
    m.documents.TOS.versions[0].content_sha256 = "e".repeat(64);
    const b = body("audit");
    b.accepted[0].content_hash = "e".repeat(64);
    const res = await handle(post(b), env, m);
    expect(res.status).toBe(500);
    expect(await count()).toBe(3);
  });
});

describe("re-acceptance on material change", () => {
  const status = (accountId, flow, manifest) =>
    handle(new Request(`https://acceptance.test/reacceptance?account_id=${accountId}&flow=${flow}`, { headers: AUTH }), env, manifest);

  it("requires a token", async () => {
    const res = await handle(new Request("https://acceptance.test/reacceptance?account_id=acct_x&flow=audit"), env);
    expect(res.status).toBe(401);
  });

  it("current acceptance is fine; a material bump requires re-acceptance; a non-material bump does not", async () => {
    const out = await (await handle(post(body("care-plan")), env)).json();
    const now = await (await status(out.account_id, "care-plan", MANIFEST)).json();
    expect(now.needs_reacceptance).toBe(false);

    const minor = await reacceptanceStatus(env.DB, out.account_id, "care-plan", bumped("REFUND", false));
    expect(minor.needs_reacceptance).toBe(false);

    const material = bumped("REFUND", true);
    const after = await (await status(out.account_id, "care-plan", material)).json();
    expect(after.needs_reacceptance).toBe(true);
    expect(after.documents.find((d) => d.document_id === "REFUND")).toMatchObject({ state: "material_change", required_version: "2099-01-01-1" });

    // Re-accepting the new set clears it, and the old rows remain.
    const b = body("care-plan");
    const refund = b.accepted.find((a) => a.document_id === "REFUND");
    refund.version = "2099-01-01-1";
    refund.content_hash = "f".repeat(64);
    refund.checkbox_text = material.documents.REFUND.versions[1].checkbox_text;
    expect((await handle(post(b), env, material)).status).toBe(201);
    expect((await reacceptanceStatus(env.DB, out.account_id, "care-plan", material)).needs_reacceptance).toBe(false);
    expect(await count()).toBe(8);
  });

  it("an account that never accepted needs acceptance", async () => {
    const s = await reacceptanceStatus(env.DB, await accountIdFor("new@x.co"), "audit");
    expect(s.needs_reacceptance).toBe(true);
    expect(s.documents.every((d) => d.state === "never_accepted")).toBe(true);
  });
});
