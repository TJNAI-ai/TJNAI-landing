// Offline tests: Resend is mocked, nothing leaves the machine. Run: node --test test/
import test from "node:test";
import assert from "node:assert/strict";
import { handle } from "../src/handler.js";
import { signToken, verifyToken } from "../src/token.js";

const SECRET = "x".repeat(48);
const ENV = {
  RESEND_API_KEY: "re_test_not_real",
  SIGNING_SECRET: SECRET,
  SEGMENT_ID: "seg-tob",
  TOPIC_ID: "topic-tob",
  PUBLIC_BASE_URL: "https://tob.example.workers.dev",
};

function mockResend(contacts = {}) {
  const calls = [];
  const segs = {};
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method: init.method, path: u.pathname, body, headers: init.headers });
    const m = u.pathname.match(/^\/contacts\/([^/]+)(?:\/(segments|topics))?(?:\/([^/]+))?$/);
    const r = (status, data) => new Response(data === undefined ? "" : JSON.stringify(data), { status });
    if (u.pathname === "/contacts" && init.method === "POST") { contacts[body.email] = { ...body, properties: { optin_status: { value: body.properties.optin_status } } }; return r(200, { id: "c1" }); }
    if (u.pathname === "/emails") return r(200, { id: "e1" });
    if (m) {
      const email = decodeURIComponent(m[1]);
      if (!m[2] && init.method === "GET") return contacts[email] ? r(200, contacts[email]) : r(404, { name: "not_found" });
      if (!m[2] && init.method === "PATCH") { Object.assign(contacts[email], body); if (body.properties) contacts[email].properties = { optin_status: { value: body.properties.optin_status } }; return r(200, {}); }
      if (m[2] === "segments" && init.method === "GET") return r(200, { data: (segs[email] || []).map((id) => ({ id })) });
      if (m[2] === "segments" && init.method === "POST") { (segs[email] ||= []).push(m[3]); return r(200, { id: m[3] }); }
      if (m[2] === "topics") return r(200, {});
    }
    return r(500, {});
  };
  return { calls, contacts, segs };
}

const sub = (body, origin = "https://tjnai.ai") =>
  new Request("https://tob.example.workers.dev/subscribe", { method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify(body) });

test("token round-trips, rejects tampering, other secrets and expiry", async () => {
  const t = await signToken("a@b.co", SECRET, 60);
  assert.equal(await verifyToken(t, SECRET), "a@b.co");
  assert.equal(await verifyToken(t, "y".repeat(48)), null);
  const [v, e, x, s] = t.split(".");
  const other = Buffer.from("evil@b.co").toString("base64url");
  assert.equal(await verifyToken([v, other, x, s].join("."), SECRET), null, "swapped email");
  assert.equal(await verifyToken([v, e, String(Number(x) + 999999), s].join("."), SECRET), null, "extended expiry");
  assert.equal(await verifyToken(t, SECRET, Number(x) + 1), null, "expired");
  assert.equal(await verifyToken("garbage", SECRET), null);
});

test("CORS: preflight only for https://tjnai.ai; other origins get 403", async () => {
  mockResend();
  const ok = await handle(new Request("https://w/subscribe", { method: "OPTIONS", headers: { Origin: "https://tjnai.ai" } }), ENV);
  assert.equal(ok.status, 204);
  assert.equal(ok.headers.get("Access-Control-Allow-Origin"), "https://tjnai.ai");
  const bad = await handle(sub({ email: "a@b.co", elapsed_ms: 9000 }, "https://evil.example"), ENV);
  assert.equal(bad.status, 403);
});

test("honeypot and too-fast submits look successful but call nothing", async () => {
  const m = mockResend();
  assert.equal((await handle(sub({ email: "a@b.co", company: "x", elapsed_ms: 9000 }), ENV)).status, 200);
  assert.equal((await handle(sub({ email: "a@b.co", elapsed_ms: 300 }), ENV)).status, 200);
  assert.equal(m.calls.length, 0);
});

test("invalid email is rejected", async () => {
  mockResend();
  assert.equal((await handle(sub({ email: "nope", elapsed_ms: 9000 }), ENV)).status, 400);
});

test("full double opt-in: pending + not in segment, then confirm adds segment + topic", async () => {
  const m = mockResend();
  const res = await handle(sub({ email: " New@Example.com ", first_name: "Ann<b>", elapsed_ms: 9000 }), ENV);
  assert.equal(res.status, 200);
  const create = m.calls.find((c) => c.path === "/contacts" && c.method === "POST");
  assert.deepEqual(create.body, { email: "new@example.com", first_name: "Annb", unsubscribed: false, properties: { optin_status: "pending" } });
  assert.equal(create.body.segments, undefined, "not added to any segment on submit");
  assert.equal(m.calls.some((c) => c.path.includes("/segments/") && c.method === "POST"), false);
  const mail = m.calls.find((c) => c.path === "/emails");
  assert.equal(mail.body.from, "Tim <tim@tjnai.ai>");
  assert.equal(mail.body.reply_to, "info@tjnai.ai");
  assert.match(mail.body.html, /5540 Centerview Dr Ste 204, PMB #588341, Raleigh, NC 27606-8012/);
  assert.ok(!JSON.stringify(mail.body).includes("re_test_not_real"), "key never in the email");
  const link = mail.body.text.match(/https:\/\/tob\.example\.workers\.dev\/confirm\?t=\S+/)[0];

  // GET only renders an auto-submit page; it must not change anything.
  const before = m.calls.length;
  const g = await handle(new Request(link), ENV);
  assert.equal(g.status, 200);
  assert.match(await g.text(), /method="post"/);
  assert.equal(m.calls.length, before);

  const t = new URL(link).searchParams.get("t");
  const p = await handle(new Request("https://tob.example.workers.dev/confirm", { method: "POST", body: new URLSearchParams({ t }) }), ENV);
  assert.equal(p.status, 200);
  assert.match(await p.text(), /You're subscribed/);
  assert.deepEqual(m.segs["new@example.com"], ["seg-tob"]);
  assert.equal(m.contacts["new@example.com"].properties.optin_status.value, "confirmed");
  assert.ok(m.calls.some((c) => c.path.endsWith("/topics") && c.body[0].subscription === "opt_in"));
});

test("forged token on POST /confirm changes nothing", async () => {
  const m = mockResend({ "v@x.co": { email: "v@x.co", properties: { optin_status: { value: "pending" } } } });
  const forged = "v1." + Buffer.from("v@x.co").toString("base64url") + ".9999999999." + Buffer.alloc(32).toString("base64url");
  const p = await handle(new Request("https://w/confirm", { method: "POST", body: new URLSearchParams({ t: forged }) }), ENV);
  assert.equal(p.status, 400);
  assert.equal(m.calls.length, 0);
});

test("already-confirmed subscriber is not emailed again and not downgraded", async () => {
  const m = mockResend({ "c@x.co": { email: "c@x.co", unsubscribed: false, properties: { optin_status: { value: "confirmed" } } } });
  await handle(new Request("https://w/confirm", { method: "POST", body: new URLSearchParams({ t: await signToken("c@x.co", SECRET, 60) }) }), ENV);
  m.calls.length = 0;
  assert.equal((await handle(sub({ email: "c@x.co", elapsed_ms: 9000 }), ENV)).status, 200);
  assert.equal(m.calls.some((c) => c.path === "/emails"), false);
  assert.equal(m.calls.some((c) => c.method === "PATCH"), false);
});

test("rate limiter binding returning failure yields 429", async () => {
  mockResend();
  const env = { ...ENV, RL_IP: { limit: async () => ({ success: false }) } };
  assert.equal((await handle(sub({ email: "a@b.co", elapsed_ms: 9000 }), env)).status, 429);
});
