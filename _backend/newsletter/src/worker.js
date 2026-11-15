// Cloudflare Worker entry point. Bindings come from wrangler.toml (D1 "DB", rate limit "RL_IP").
import { handle } from "./handler.js";

export default {
  fetch(request, env) {
    return handle(request, env);
  },
};
