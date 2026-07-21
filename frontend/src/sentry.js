import * as Sentry from "@sentry/react";

const dsn = import.meta.env.VITE_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_APP_ENV ?? (import.meta.env.PROD ? "production" : "development"),
    integrations: [
      Sentry.browserTracingIntegration(),
    ],
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? "0.1"),
    // Only send errors, not PII
    sendDefaultPii: false,
  });
}

export { Sentry };
