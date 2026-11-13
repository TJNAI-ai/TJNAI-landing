import path from "node:path";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest(async () => {
      const migrations = await readD1Migrations(path.join(import.meta.dirname, "migrations"));
      return {
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations, ADMIN_TOKEN: "t".repeat(40), ALLOWED_ORIGIN: "https://tjnai.ai" },
        },
      };
    }),
  ],
  test: {
    setupFiles: ["./test/apply-migrations.js"],
  },
});
