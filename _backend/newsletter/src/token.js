// Signed, expiring confirm tokens: v1.<b64url(email)>.<expUnixSeconds>.<b64url(HMAC-SHA256)>
// The HMAC covers "v1|<email>|<exp>" with SIGNING_SECRET, so a token can't be forged
// or re-pointed at another address without the server secret.

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64urlEncode(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(str) {
  if (!/^[A-Za-z0-9_-]*$/.test(str)) throw new Error("bad b64url");
  const pad = str.length % 4 === 0 ? "" : "=".repeat(4 - (str.length % 4));
  const bin = atob(str.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function hmacKey(secret) {
  if (!secret || secret.length < 32) throw new Error("SIGNING_SECRET missing or shorter than 32 chars");
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function signToken(email, secret, ttlSeconds, nowSeconds = Math.floor(Date.now() / 1000)) {
  const exp = nowSeconds + ttlSeconds;
  const key = await hmacKey(secret);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(`v1|${email}|${exp}`)));
  return `v1.${b64urlEncode(enc.encode(email))}.${exp}.${b64urlEncode(sig)}`;
}

// Returns the email on success, or null for any malformed, forged or expired token.
export async function verifyToken(token, secret, nowSeconds = Math.floor(Date.now() / 1000)) {
  try {
    if (typeof token !== "string" || token.length > 600) return null;
    const parts = token.split(".");
    if (parts.length !== 4 || parts[0] !== "v1") return null;
    const email = dec.decode(b64urlDecode(parts[1]));
    if (!/^\d{1,12}$/.test(parts[2])) return null;
    const exp = Number(parts[2]);
    const sig = b64urlDecode(parts[3]);
    if (sig.length !== 32) return null;
    const key = await hmacKey(secret);
    // crypto.subtle.verify compares in constant time.
    const ok = await crypto.subtle.verify("HMAC", key, sig, enc.encode(`v1|${email}|${exp}`));
    if (!ok || exp < nowSeconds) return null;
    return email;
  } catch {
    return null;
  }
}
