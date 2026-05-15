import { config } from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
config({ path: resolve(__dirname, "..", ".env") });

if (!process.env.ANTHROPIC_API_KEY) {
  // Don't throw — judge unit tests may stub the SDK. But warn loudly.
  // eslint-disable-next-line no-console
  console.warn(
    "[iga-evals] ANTHROPIC_API_KEY is not set. Live judge/SUT calls will fail. " +
      "Copy .env.example to .env and fill it in."
  );
}
