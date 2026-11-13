// Local only: Miniflare + local D1. Nothing is deployed and nothing leaves the machine.
// Test IDs T1–T20 refer to Lena's DRAFT_eva-handoff_v0.2 §10; browser-side ones (T1–T3, T20) live in
// _checks/test_public_site.py, and T11 (re-hash frozen artifacts) is there too because it reads repo files.
import { env as realEnv, applyD1Migrations } from "cloudflare:test";
import { describe, it, expect, beforeEach } from "vitest";
import { handle, currentSet, accountIdFor, reacceptanceStatus } from "../src/handler.js";
import MANIFEST from "../src/manifest.json";

// The real rate-limit binding would throttle this suite; it gets its own test below.
const env = { ...realEnv, RL_IP: undefined };
const ORIGIN = "https://tjnai.ai";
const UA = "Mozilla/5.0 (test) AcceptanceSuite/1.0";
const AUTH = { Authorization: `Bearer ${"t".repeat(40)}` };

// Lena's exact labels (handoff §2.1–2.6). BILLING-CONSENT is worded as in SOW-MAINTAIN-v0.2 ("starting on the
// Build handoff date"), which _checks also asserts against the SOW source.
const PRICE_CARE = "$225.00";
const FIRST_CHARGE = "on the Build handoff date";
const EXPECTED_LABELS = {
  TOS: "I have read and agree to the Terms of Service (TOS-v0.2).",
  PRIV: "I have read and acknowledge the Privacy Policy (PRIV-v0.2).",
  REFUND: "I have read and agree to the Refund Policy (REFUND-v0.2).",
  MSA: "I have read and agree to the Master Services Agreement (MSA-v0.2) on behalf of the business named above.",
  "B2B-CONFIRM": "I confirm the business named above is buying for business purposes, not personal, family or household use, and that I have authority to bind it.",
  "SOW-AUDIT": "I have read and agree to the Statement of Work — AI Agent Audit (SOW-AUDIT-v0.2).",
  "SOW-BUILD": "I have read and agree to the Statement of Work — Build (SOW-BUILD-v0.2).",
  "SOW-MAINTAIN": "I have read and agree to the Statement of Work — Maintain (Monthly Care Plan) (SOW-MAINTAIN-v0.2).",
  "BILLING-CONSENT": `I authorize TJNAI.ai LLC to charge the business's payment method ${PRICE_CARE} USD every month, starting ${FIRST_CHARGE}, until I cancel. I understand I can cancel at any time by emailing info@tjnai.ai, that cancellation takes effect at the end of the current billing month, and that there are no partial refunds.`,
};

function body(flow, overrides = {}) {
  const accepted = currentSet(MANIFEST, flow).map((d) => ({
    document_id: d.document_id,
    document_version: d.document_version,
    content_sha256: d.content_sha256,
    checked: true,
    checkbox_text: d.checkbox_text,
  }));
  return {
    flow, business_legal_name: "Example Plumbing LLC", signer_name: "Ada Lovelace", email: "Ada@Example.com",
    accepted, locale: "en-US", viewport: "1440x900", form_template_id: "agree.v2", ...overrides,
  };
}
const without = (b, id) => ({ ...b, accepted: b.accepted.filter((a) => a.document_id !== id) });

function post(payload, { origin = ORIGIN, ip = "203.0.113.7" } = {}) {
  return new Request("https://acceptance.test/accept", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: origin, "User-Agent": UA, "CF-Connecting-IP": ip },
    body: JSON.stringify(payload),
  });
}

async function count(table = "acceptance_events") {
  return (await realEnv.DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first()).n;
}
async function nothingWritten() {
  expect(await count()).toBe(0);
  expect(await count("engagements")).toBe(0);
  expect(await count("accounts")).toBe(0);
}

async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// A copy of the manifest where `docId` gets a newer version (material or not).
function bumped(docId, material) {
  const m = structuredClone(MANIFEST);
  const doc = m.documents[docId];
  const prev = doc.versions[doc.versions.length - 1];
  const next = { ...prev, version: "v0.3", display_id: `${docId}-v0.3`, content_sha256: "f".repeat(64), is_material_change: material,
    checkbox_text: prev.checkbox_text.replace(`${docId}-v0.2`, `${docId}-v0.3`) };
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
  await nothingWritten();
});

describe("manifest matches Lena's handoff", () => {
  it("flows require exactly the handoff boxes in order (plus B2B-CONFIRM after MSA)", () => {
    const base = ["TOS", "PRIV", "REFUND", "MSA", "B2B-CONFIRM"];
    expect(MANIFEST.flows.audit.required).toEqual([...base, "SOW-AUDIT"]);
    expect(MANIFEST.flows.build.required).toEqual([...base, "SOW-BUILD"]);
    expect(MANIFEST.flows["care-plan"].required).toEqual([...base, "SOW-MAINTAIN", "BILLING-CONSENT"]);
    expect(MANIFEST.flows.audit.page_or_flow).toBe("checkout.audit.v1");
  });

  it("labels are Lena's exact text and display IDs are document_id-version", () => {
    for (const [id, label] of Object.entries(EXPECTED_LABELS)) {
      const v = MANIFEST.documents[id].versions.at(-1);
      expect(v.checkbox_text).toBe(label);
      expect(v.version).toBe("v0.2");
      expect(v.display_id).toBe(`${id}-v0.2`);
    }
  });

  it("T11 (inline boxes): content_sha256 is the SHA-256 of the exact label text", async () => {
    for (const id of ["B2B-CONFIRM", "BILLING-CONSENT"]) {
      const v = MANIFEST.documents[id].versions.at(-1);
      expect(v.content_sha256).toBe(await sha256hex(v.checkbox_text));
    }
  });

  it("care-plan billing terms: first charge on the Build handoff date; cancel by email; end of billing month; no partial refunds", () => {
    const d = MANIFEST.documents["BILLING-CONSENT"].versions.at(-1).billing_disclosure;
    expect(d).toContain(`${PRICE_CARE} USD per month`);
    expect(d).toContain("month-to-month, with the first monthly charge on the Build handoff date, then billed monthly in advance.");
    expect(d).toContain("Cancel any time by emailing info@tjnai.ai.");
    expect(d).toContain("end of your current billing month");
    expect(d).toContain("No partial refunds");
    expect(d).not.toMatch(/PLACEHOLDER|\[on signup|\[DATE\]/);
  });
});

describe("POST /accept: server-side gate (criterion 4)", () => {
  it("T4: no SOW box → 400; no rows, no engagement", async () => {
    for (const flow of ["audit", "build", "care-plan"]) {
      const sow = { audit: "SOW-AUDIT", build: "SOW-BUILD", "care-plan": "SOW-MAINTAIN" }[flow];
      const res = await handle(post(without(body(flow), sow)), env);
      expect(res.status).toBe(400);
      expect((await res.json()).missing).toEqual([sow]);
    }
    await nothingWritten();
  });

  it("crafted POST with no acceptances at all → 400", async () => {
    const res = await handle(post({ flow: "audit", business_legal_name: "B", signer_name: "A", email: "a@b.co" }), env);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("acceptance_missing");
    await nothingWritten();
  });

  it.each(["TOS", "PRIV", "REFUND", "MSA", "B2B-CONFIRM"])("any one box missing (%s) → 400", async (id) => {
    const res = await handle(post(without(body("build"), id)), env);
    expect(res.status).toBe(400);
    expect((await res.json()).missing).toEqual([id]);
    await nothingWritten();
  });

  it("care plan without BILLING-CONSENT → 400", async () => {
    const res = await handle(post(without(body("care-plan"), "BILLING-CONSENT")), env);
    expect(res.status).toBe(400);
    await nothingWritten();
  });

  it("a box present but unchecked → 400", async () => {
    const b = body("audit");
    b.accepted[4].checked = false;
    expect((await handle(post(b), env)).status).toBe(400);
    await nothingWritten();
  });

  it("T5: document_version v0.1 → 409; wrong hash → 409", async () => {
    const b1 = body("audit");
    b1.accepted[0].document_version = "v0.1";
    const r1 = await handle(post(b1), env);
    expect(r1.status).toBe(409);
    expect((await r1.json()).stale).toEqual(["TOS"]);
    const b2 = body("audit");
    b2.accepted[2].content_sha256 = "0".repeat(64);
    expect((await handle(post(b2), env)).status).toBe(409);
    await nothingWritten();
  });

  it("T6: a new TOS version published while the page is open → 409 with the new versions to re-render", async () => {
    const res = await handle(post(body("audit")), env, bumped("TOS", true));
    expect(res.status).toBe(409);
    const j = await res.json();
    expect(j.stale).toEqual(["TOS"]);
    expect(j.current[0]).toMatchObject({ document_id: "TOS", document_version: "v0.3", display_id: "TOS-v0.3" });
    await nothingWritten();
  });

  it("T18: empty Business legal name → 400 (also blocked client-side, see _checks)", async () => {
    for (const business_legal_name of ["", "   ", undefined]) {
      const res = await handle(post(body("audit", { business_legal_name })), env);
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("business_legal_name_required");
    }
    expect((await handle(post(body("audit", { signer_name: "" })), env)).status).toBe(400);
    await nothingWritten();
  });

  it("altered label, extra document, bad origin, bad email, unknown flow → 4xx", async () => {
    const b = body("audit");
    b.accepted[0].checkbox_text = "I agree to whatever";
    expect((await handle(post(b), env)).status).toBe(400);
    const c = body("audit");
    c.accepted.push(body("build").accepted[5]);
    expect((await handle(post(c), env)).status).toBe(400);
    expect((await handle(post(body("audit"), { origin: "https://evil.example" }), env)).status).toBe(403);
    expect((await handle(post(body("audit", { email: "nope" })), env)).status).toBe(400);
    expect((await handle(post(body("audit", { flow: "custom" })), env)).status).toBe(400);
    await nothingWritten();
  });

  it("rate limiter saying no → 429 and nothing written", async () => {
    const limited = { ...env, RL_IP: { limit: async () => ({ success: false }) } };
    expect((await handle(post(body("audit")), limited)).status).toBe(429);
    await nothingWritten();
  });

  it("CORS preflight only for the allowed origin", async () => {
    const ok = await handle(new Request("https://acceptance.test/accept", { method: "OPTIONS", headers: { Origin: ORIGIN } }), env);
    expect(ok.status).toBe(204);
    expect(ok.headers.get("Access-Control-Allow-Origin")).toBe(ORIGIN);
    const bad = await handle(new Request("https://acceptance.test/accept", { method: "OPTIONS", headers: { Origin: "https://evil.example" } }), env);
    expect(bad.status).toBe(403);
  });
});

describe("POST /accept: valid submits write the log (criterion 5)", () => {
  async function checkRows(flow, expectedIds) {
    const res = await handle(post(body(flow)), env);
    expect(res.status).toBe(201);
    const out = await res.json();
    const { results } = await realEnv.DB.prepare("SELECT * FROM acceptance_events ORDER BY rowid").all();
    expect(results.map((r) => r.document_id)).toEqual(expectedIds);
    const expected = Object.fromEntries(currentSet(MANIFEST, flow).map((d) => [d.document_id, d]));
    for (const r of results) {
      expect(r.acceptance_id).toMatch(/^acc_[0-9a-f-]{36}$/);
      expect(r.acceptance_batch_id).toBe(out.acceptance_batch_id);
      expect(r.account_id).toBe(await accountIdFor("ada@example.com"));
      expect(r.business_legal_name).toBe("Example Plumbing LLC");
      expect(r.signer_name).toBe("Ada Lovelace");
      expect(r.email).toBe("ada@example.com");
      expect(r.document_version).toBe("v0.2");
      expect(r.display_id).toBe(`${r.document_id}-v0.2`);
      expect(r.content_sha256).toBe(expected[r.document_id].content_sha256);
      expect(r.accepted_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
      expect(r.ip_address).toBe("203.0.113.7");
      expect(r.user_agent).toBe(UA);
      expect(r.page_or_flow).toBe(`checkout.${flow}.v1`);
      expect(r.checkbox_text_shown).toBe(EXPECTED_LABELS[r.document_id]);
      expect(r.engagement_id).toBe(out.engagement_id);
      expect(r.acceptance_method).toBe("checkbox");
    }
    expect(await count("engagements")).toBe(1);
    return results;
  }

  it("T7: audit → 6 rows (TOS, PRIV, REFUND, MSA, B2B-CONFIRM, SOW-AUDIT), one batch, every field", async () => {
    await checkRows("audit", ["TOS", "PRIV", "REFUND", "MSA", "B2B-CONFIRM", "SOW-AUDIT"]);
  });

  it("build → 6 rows ending in SOW-BUILD", async () => {
    await checkRows("build", ["TOS", "PRIV", "REFUND", "MSA", "B2B-CONFIRM", "SOW-BUILD"]);
  });

  it("T8: care plan → 7 rows including BILLING-CONSENT with billing_disclosure_shown (criterion 8)", async () => {
    const rows = await checkRows("care-plan", ["TOS", "PRIV", "REFUND", "MSA", "B2B-CONFIRM", "SOW-MAINTAIN", "BILLING-CONSENT"]);
    const billing = rows.find((r) => r.document_id === "BILLING-CONSENT");
    expect(billing.billing_disclosure_shown).toBe(MANIFEST.documents["BILLING-CONSENT"].versions.at(-1).billing_disclosure);
    expect(rows.filter((r) => r.document_id !== "BILLING-CONSENT").every((r) => r.billing_disclosure_shown === null)).toBe(true);
  });

  it("a repeat acceptance appends new rows and keeps the old ones", async () => {
    await handle(post(body("audit")), env);
    await handle(post(body("audit")), env);
    expect(await count()).toBe(12);
    expect(await count("accounts")).toBe(1);
  });
});

describe("integrity", () => {
  it("T9: a log insert that fails rolls back everything (no rows, engagement or account)", async () => {
    await realEnv.DB.prepare(
      "CREATE TRIGGER test_fail_refund BEFORE INSERT ON acceptance_events WHEN NEW.document_id = 'REFUND' BEGIN SELECT RAISE(ABORT, 'forced failure'); END",
    ).run();
    const res = await handle(post(body("care-plan")), env);
    expect(res.status).toBe(500);
    await nothingWritten();
    expect(await count("legal_document_versions")).toBe(0);
  });

  it("T10 / criterion 7: UPDATE and DELETE on acceptance_events are blocked", async () => {
    await handle(post(body("audit")), env);
    await expect(realEnv.DB.prepare("UPDATE acceptance_events SET signer_name = 'x'").run()).rejects.toThrow(/append-only/);
    await expect(realEnv.DB.prepare("DELETE FROM acceptance_events").run()).rejects.toThrow(/append-only/);
    expect(await count()).toBe(6);
  });

  it("UPDATE and DELETE on legal_document_versions are blocked", async () => {
    await handle(post(body("audit")), env);
    await expect(realEnv.DB.prepare("UPDATE legal_document_versions SET content_sha256 = ?").bind("0".repeat(64)).run()).rejects.toThrow(/append-only/);
    await expect(realEnv.DB.prepare("DELETE FROM legal_document_versions").run()).rejects.toThrow(/append-only/);
  });

  it("a version string that now points at different bytes is refused", async () => {
    await handle(post(body("audit")), env);
    const m = structuredClone(MANIFEST);
    m.documents.TOS.versions[0].content_sha256 = "e".repeat(64);
    const b = body("audit");
    b.accepted[0].content_sha256 = "e".repeat(64);
    expect((await handle(post(b), env, m)).status).toBe(500);
    expect(await count()).toBe(6);
  });

  it("T19 (schema only): a signed row needs no checkbox text; a checkbox row must have it", async () => {
    await handle(post(body("audit")), env);
    const r = (await realEnv.DB.prepare("SELECT * FROM acceptance_events LIMIT 1").first());
    const insert = (method, text) => realEnv.DB.prepare(
      `INSERT INTO acceptance_events (acceptance_id, acceptance_batch_id, account_id, business_legal_name, signer_name, email,
        document_id, document_version, display_id, content_sha256, accepted_at, ip_address, user_agent, page_or_flow,
        checkbox_text_shown, engagement_id, acceptance_method) VALUES (?, 'b', ?, 'B', 'S', 'e@x.co', 'MSA', 'v0.2', 'MSA-v0.2', ?, ?, '', '', 'custom.signed.v1', ?, ?, ?)`,
    ).bind("acc_" + crypto.randomUUID(), r.account_id, "a".repeat(64), r.accepted_at, text, r.engagement_id, method).run();
    await insert("signed", null);
    await expect(insert("checkbox", null)).rejects.toThrow();
  });
});

describe("re-acceptance on material change (T13/T14, partial: no renewal job yet)", () => {
  const status = (accountId, flow, manifest) =>
    handle(new Request(`https://acceptance.test/reacceptance?account_id=${accountId}&flow=${flow}`, { headers: AUTH }), env, manifest);

  it("requires the admin token", async () => {
    expect((await handle(new Request("https://acceptance.test/reacceptance?account_id=acct_x&flow=audit"), env)).status).toBe(401);
  });

  it("material SOW-MAINTAIN bump → needs re-acceptance; non-material → not; re-accepting clears it and keeps old rows", async () => {
    const out = await (await handle(post(body("care-plan")), env)).json();
    expect((await (await status(out.account_id, "care-plan", MANIFEST)).json()).needs_reacceptance).toBe(false);
    expect((await reacceptanceStatus(realEnv.DB, out.account_id, "care-plan", bumped("SOW-MAINTAIN", false))).needs_reacceptance).toBe(false);

    const material = bumped("SOW-MAINTAIN", true);
    const after = await (await status(out.account_id, "care-plan", material)).json();
    expect(after.needs_reacceptance).toBe(true);
    expect(after.documents.find((d) => d.document_id === "SOW-MAINTAIN")).toMatchObject({ state: "material_change", required_version: "v0.3" });

    const b = body("care-plan");
    const sow = b.accepted.find((a) => a.document_id === "SOW-MAINTAIN");
    sow.document_version = "v0.3";
    sow.content_sha256 = "f".repeat(64);
    sow.checkbox_text = material.documents["SOW-MAINTAIN"].versions[1].checkbox_text;
    expect((await handle(post(b), env, material)).status).toBe(201);
    expect((await reacceptanceStatus(realEnv.DB, out.account_id, "care-plan", material)).needs_reacceptance).toBe(false);
    expect(await count()).toBe(14);
  });

  it("an account that never accepted needs acceptance", async () => {
    const s = await reacceptanceStatus(realEnv.DB, await accountIdFor("new@x.co"), "audit");
    expect(s.needs_reacceptance).toBe(true);
    expect(s.documents.every((d) => d.state === "never_accepted")).toBe(true);
  });
});
