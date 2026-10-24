// Cloudflare Worker entry point. Secrets/vars come from wrangler.toml + `wrangler secret put`.
import { handle } from "./handler.js";

export default {
  fetch(request, env) {
    return handle(request, env);
  },
};
