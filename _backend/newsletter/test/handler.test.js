// Local only: Miniflare + local D1. Nothing is deployed and no email is sent.
import { env as realEnv } from "cloudflare:test";
import { describe, it, expect, beforeEach, vi } from "vitest";
import HANDLER_SRC from "../src/handler.js?raw";
import WORKER_SRC from "../src/worker.js?raw";
import TOML from "../wrangler.toml?raw";
import { handle, CONSENT_TEXT, normalizeEmail } from "../src/handler.js";

const env = { ...realEnv, RL_IP: undefined };
const ORIGIN = "https://tjnai.ai";

function post(body, { origin = ORIGIN, type = "application/json" } = {}) {
  return new Request("https://tob-newsletter.example.workers.dev/subscribe", {
    method: "POST",
    headers: { "Content-Type": type, ...(origin ? { Origin: origin } : {}), "CF-Connecting-IP": "203.0.113.9", "User-Agent": "UA-test" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}
const good = (o = {}) => ({ email: " Ada@Example.com ", first_name: "Ada", company: "", elapsed_ms: 5000, source_page: "/newsletter", consent_text: CONSENT_TEXT, ...o });
const rows = async () => (await env.DB.prepare("SELECT * FROM newsletter_signups ORDER BY signed_up_at").all()).results;

describe("tob-newsletter (store-only)", () => {
  beforeEach(async () => { await env.DB.prepare("DELETE FROM newsletter_signups").run(); });

  it("stores one pending row with only the listed fields", async () => {
    const res = await handle(post(good()), env);
    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe(ORIGIN);
    const r = await rows();
    expect(r).toHaveLength(1);
    expect(Object.keys(r[0]).sort()).toEqual(["consent_text", "email", "first_name", "is_test", "signed_up_at", "signup_id", "source_page", "status"]);
    expect(r[0]).toMatchObject({ email: "ada@example.com", first_name: "Ada", status: "pending", source_page: "/newsletter", consent_text: CONSENT_TEXT, is_test: 0 });
    expect(r[0].signed_up_at).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
    expect(JSON.stringify(r[0])).not.toContain("203.0.113.9");
    expect(JSON.stringify(r[0])).not.toContain("UA-test");
  });

  it("never calls fetch (no email, no outside service)", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    await handle(post(good()), env);
    await handle(post(good({ email: "b@example.com" })), env);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("source has no email or Resend code path and no secrets", () => {
    const src = HANDLER_SRC + WORKER_SRC;
    const code = src.replace(/\/\/.*$/gm, "");
    expect(code.length).toBeGreaterThan(1000);
    expect(code).not.toMatch(/(await|=|return|\.then\()\s*fetch\(|resend|api\.resend\.com|RESEND_API_KEY|send_email|SIGNING_SECRET|MailChannels/i);
    expect(TOML).not.toMatch(/RESEND|send_email|FROM_ADDRESS|SIGNING_SECRET/);
  });

  it("re-submitting the same address keeps one row and does not change it", async () => {
    await handle(post(good()), env);
    await env.DB.prepare("UPDATE newsletter_signups SET status='unsubscribed'").run();
    const res = await handle(post(good({ first_name: "Other" })), env);
    expect(res.status).toBe(200);
    const r = await rows();
    expect(r).toHaveLength(1);
    expect(r[0]).toMatchObject({ status: "unsubscribed", first_name: "Ada" });
  });

  it("rejects bad input and other origins; bots get ok but nothing is stored", async () => {
    expect((await handle(post(good({ email: "nope" })), env)).status).toBe(400);
    expect((await handle(post(good({ consent_text: "x" })), env)).status).toBe(400);
    expect((await handle(post(good(), { origin: "https://evil.example" }), env)).status).toBe(403);
    expect((await handle(post(good(), { origin: null }), env)).status).toBe(403);
    expect((await handle(post("{", {}), env)).status).toBe(400);
    expect((await handle(post(good(), { type: "text/plain" }), env)).status).toBe(415);
    expect((await handle(post(good({ company: "Acme" })), env)).status).toBe(200);
    expect((await handle(post(good({ elapsed_ms: 100 })), env)).status).toBe(200);
    expect(await rows()).toHaveLength(0);
  });

  it("flags test rows only for example.com addresses", async () => {
    await handle(post(good({ email: "relaunch-test@example.com", is_test: true })), env);
    await handle(post(good({ email: "real@tjnai-customer.test", is_test: true })), env);
    const r = await rows();
    expect(r.find((x) => x.email === "relaunch-test@example.com").is_test).toBe(1);
    expect(r.find((x) => x.email === "real@tjnai-customer.test").is_test).toBe(0);
  });

  it("flags every row from the preview origin as a test row", async () => {
    const PREVIEW = "https://tjnai-relaunch-preview.tjnai.workers.dev";
    const penv = { ...env, ALLOWED_ORIGIN: `${ORIGIN},${PREVIEW}` };
    await handle(post(good({ email: "someone@tjnai-customer.test" }), { origin: PREVIEW }), penv);
    expect((await rows())[0].is_test).toBe(1);
  });

  it("CORS preflight only for allowed origins; other paths 404", async () => {
    const ok = await handle(new Request("https://x/subscribe", { method: "OPTIONS", headers: { Origin: ORIGIN } }), env);
    expect(ok.status).toBe(204);
    const bad = await handle(new Request("https://x/subscribe", { method: "OPTIONS", headers: { Origin: "https://evil.example" } }), env);
    expect(bad.status).toBe(403);
    expect((await handle(new Request("https://x/"), env)).status).toBe(404);
    expect((await handle(new Request("https://x/confirm?t=1"), env)).status).toBe(404);
  });

  it("normalizes email", () => {
    expect(normalizeEmail("  A@B.co ")).toBe("a@b.co");
    expect(normalizeEmail("a@b")).toBe(null);
  });
});
