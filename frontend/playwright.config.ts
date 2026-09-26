import { defineConfig } from "@playwright/test";
import path from "node:path";
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8195",
    permissions: ["microphone"],
    viewport: { width: 1440, height: 1050 },
    launchOptions: {
      args: [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        `--use-file-for-fake-audio-capture=${path.resolve("../data/audio/smoke.wav")}`,
      ],
    },
  },
  webServer: {
    command: "../.venv/bin/python ../scripts/e2e_server.py",
    url: "http://127.0.0.1:8195/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
  reporter: "list",
});
