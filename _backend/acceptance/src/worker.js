// Cloudflare Worker entry point. Bindings/vars come from wrangler.toml (+ `wrangler secret put ADMIN_TOKEN`).
import { handle } from "./handler.js";

export default {
  fetch(request, env) {
    return handle(request, env);
  },
};
