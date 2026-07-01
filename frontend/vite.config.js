import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "url";
import path from "path";

const dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const rootDir = path.resolve(dirname, "..");
  const env = loadEnv(mode, rootDir, '');

  return {
    plugins: [react()],
    server: {
      port: parseInt(env.FRONTEND_PORT, 10) || 3000,
    },
  };
});
