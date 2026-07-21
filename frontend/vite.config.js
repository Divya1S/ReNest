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
          manualChunks(id) {
            if (!id.includes("node_modules")) {
              return undefined;
            }

            if (id.includes("recharts")) return "charts";
            if (id.includes("framer-motion")) return "motion";
            if (id.includes("lucide-react")) return "icons";
            if (id.includes("@sentry")) return "sentry";
            if (id.includes("sonner") || id.includes("clsx") || id.includes("tailwind-merge")) {
              return "ui";
            }
            if (id.includes("react") || id.includes("react-router-dom")) return "react";
            return "vendor";
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
