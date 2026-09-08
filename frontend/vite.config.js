import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { sentryVitePlugin } from "@sentry/vite-plugin";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  // Source-map upload is opt-in: only runs when SENTRY_AUTH_TOKEN is provided.
  const sentryPlugin =
    env.SENTRY_AUTH_TOKEN && env.SENTRY_ORG && env.SENTRY_PROJECT
      ? sentryVitePlugin({
          org: env.SENTRY_ORG,
          project: env.SENTRY_PROJECT,
          authToken: env.SENTRY_AUTH_TOKEN,
          telemetry: false,
        })
      : null;

  return {
    plugins: [react(), tailwindcss(), sentryPlugin].filter(Boolean),
    server: {
      proxy: {
        "/api": "http://127.0.0.1:8000",
        "/media": "http://127.0.0.1:8000",
      },
    },
    build: {
      // Emit source maps for Sentry; harmless when Sentry isn't configured.
      sourcemap: true,
      chunkSizeWarningLimit: 650,
      rollupOptions: {
        output: {
          // Match on package boundaries, not substrings. `id.includes("react")`
          // also matched react-leaflet, which dragged all of Leaflet into the
          // eagerly-loaded react chunk. recharts is deliberately NOT given its
          // own chunk: a manual chunk is reachable from the entry, so the
          // 400 kB charts bundle was preloaded on the landing and login pages.
          // Left alone it stays inside the lazy Dashboard/Analytics chunks.
          manualChunks(id) {
            if (!id.includes("node_modules")) {
              return undefined;
            }

            if (/node_modules\/framer-motion\//.test(id)) return "motion";
            if (/node_modules\/lucide-react\//.test(id)) return "icons";
            if (/node_modules\/@sentry\//.test(id)) return "sentry";
            if (/node_modules\/(sonner|clsx|tailwind-merge)\//.test(id)) return "ui";
            if (/node_modules\/(react|react-dom|scheduler|react-router|react-router-dom)\//.test(id)) {
              return "react";
            }
            return undefined;
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.js",
      exclude: ["**/node_modules/**", "**/e2e/**"],
    },
  };
});
