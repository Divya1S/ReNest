import { AlertTriangle, RefreshCw } from "lucide-react";
import React from "react";

import { Sentry } from "../sentry";

const RELOAD_FLAG = "renest:chunk-reload";

function isChunkLoadError(error) {
  return /dynamically imported module|Importing a module script failed|Loading chunk|Failed to fetch/i.test(
    error?.message ?? "",
  );
}

export class ErrorBoundary extends React.Component {
  state = { hasError: false, error: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, { componentStack }) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error.message, componentStack);
    Sentry?.captureException(error, { contexts: { react: { componentStack } } });

    // A tab opened before a deploy still holds the old index.html and asks for
    // chunk filenames that no longer exist. React.lazy caches the rejection, so
    // "Try Again" can never succeed; only a reload can. The sessionStorage
    // flag makes this a one-shot so a genuine load failure cannot loop.
    if (isChunkLoadError(error) && !sessionStorage.getItem(RELOAD_FLAG)) {
      try {
        sessionStorage.setItem(RELOAD_FLAG, "1");
        window.location.reload();
      } catch {
        // Storage unavailable: fall through to the error UI.
      }
    }
  }

  reset = () => this.setState({ hasError: false, error: null });

  render() {
    const { hasError, error } = this.state;
    const { children, fallback: Fallback } = this.props;

    if (!hasError) {
      try {
        sessionStorage.removeItem(RELOAD_FLAG);
      } catch {
        // Storage unavailable; the flag simply expires with the session.
      }
      return children;
    }

    if (Fallback) return <Fallback error={error} reset={this.reset} />;

    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center gap-5 py-24 text-center"
      >
        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-amber-50 dark:bg-amber-900/20">
          <AlertTriangle size={36} className="text-amber-500" />
        </div>
        <div>
          <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">Something went wrong</h2>
          <p className="mt-2 max-w-sm text-slate-500 dark:text-slate-400">{error?.message}</p>
        </div>
        <button onClick={this.reset} className="primary-button mt-2">
          <RefreshCw size={16} />
          Try Again
        </button>
      </div>
    );
  }
}
