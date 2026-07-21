import { AlertTriangle, RefreshCw } from "lucide-react";
import React from "react";

import { Sentry } from "../sentry";

export class ErrorBoundary extends React.Component {
  state = { hasError: false, error: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, { componentStack }) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error.message, componentStack);
    Sentry?.captureException(error, { contexts: { react: { componentStack } } });
  }

  reset = () => this.setState({ hasError: false, error: null });

  render() {
    const { hasError, error } = this.state;
    const { children, fallback: Fallback } = this.props;

    if (!hasError) return children;

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
