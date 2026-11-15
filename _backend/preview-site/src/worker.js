// Relaunch preview for tjnai.ai, served at https://tjnai-relaunch-preview.tjnai.workers.dev.
// Serves the public files of one pinned commit of TJNAI-ai/TJNAI-landing (a public repo) the way GitHub Pages
// would with MAINTENANCE_MODE off: /page -> page.html, /dir/ -> dir/index.html, unknown -> 404.html (404).
// It never touches tjnai.ai. Every response is noindex, and a thin banner marks every page as a preview.
// Change PREVIEW_REF (wrangler.toml var) to preview another commit.

const NON_PUBLIC = /^\/(_backend|_build|_checks|_templates|_archive|\.github|\.git|node_modules)(\/|$)|^\/(CLAUDE\.md|\.gitignore|\.nojekyll|maintenance\.html)$/;
const TYPES = {
  html: "text/html; charset=utf-8", css: "text/css; charset=utf-8", js: "text/javascript; charset=utf-8",
  json: "application/json", xml: "application/xml", txt: "text/plain; charset=utf-8", svg: "image/svg+xml",
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", gif: "image/gif", ico: "image/x-icon",
  woff2: "font/woff2", woff: "font/woff", pdf: "application/pdf",
};

function candidates(path) {
  if (path.endsWith("/")) return [path + "index.html"];
  const last = path.split("/").pop();
  if (last.includes(".")) return [path];
  return [path + ".html", path + "/index.html"];
}

async function raw(env, path) {
  const url = `https://raw.githubusercontent.com/${env.REPO}/${env.PREVIEW_REF}${path}`;
  const res = await fetch(url, { cf: { cacheTtl: 60, cacheEverything: true } });
  return res.ok ? res : null;
}

function typeOf(path) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  return TYPES[ext] || "application/octet-stream";
}

class Banner {
  constructor(ref) { this.ref = ref; }
  element(el) {
    el.prepend(
      `<div style="position:sticky;top:0;z-index:2147483647;background:#7c2d12;color:#fff;font:600 13px/1.4 system-ui,sans-serif;padding:6px 12px;text-align:center">PREVIEW of the tjnai.ai relaunch (commit ${this.ref.slice(0, 7)}). Not the live site. Not indexed.</div>`,
      { html: true },
    );
  }
}

function respond(env, body, path, status) {
  const headers = new Headers({
    "Content-Type": typeOf(path),
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
  });
  const res = new Response(body, { status, headers });
  return typeOf(path).startsWith("text/html") ? new HTMLRewriter().on("body", new Banner(env.PREVIEW_REF)).transform(res) : res;
}

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
    const url = new URL(request.url);
    if (url.pathname === "/robots.txt") return new Response("User-agent: *\nDisallow: /\n", { headers: { "Content-Type": "text/plain", "X-Robots-Tag": "noindex" } });
    let path;
    try { path = decodeURIComponent(url.pathname); } catch { path = "/"; }
    if (path.includes("..")) path = "/";
    if (!NON_PUBLIC.test(path)) {
      for (const candidate of candidates(path)) {
        if (NON_PUBLIC.test(candidate)) continue;
        const res = await raw(env, candidate);
        if (res) return respond(env, res.body, candidate, 200);
      }
    }
    const notFound = await raw(env, "/404.html");
    return respond(env, notFound ? notFound.body : "Not found", "/404.html", 404);
  },
};
