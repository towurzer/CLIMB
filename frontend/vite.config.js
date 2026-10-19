import {defineConfig, loadEnv} from "vite";
import react from "@vitejs/plugin-react";
import {fileURLToPath} from "url";
import path from "path";

const dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({mode}) => {
    const rootDir = path.resolve(dirname, "..");
    const env = loadEnv(mode, rootDir, '');

    return {
        plugins: [react()],
        // BACKEND_URL / BACKEND_PORT from the root .env reach import.meta.env in App.jsx
        envDir: rootDir,
        envPrefix: ["VITE_", "BACKEND_"],
        server: {
            port: parseInt(env.FRONTEND_PORT, 10) || 3000,
        },
    };
});
