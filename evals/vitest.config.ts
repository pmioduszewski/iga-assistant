import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    testTimeout: 120_000, // judge calls + scenario runs are slow
    hookTimeout: 60_000,
    reporters: ["verbose"],
    env: {
      // loaded from .env via dotenv in setup
    },
    setupFiles: ["./src/testSetup.ts"],
  },
});
