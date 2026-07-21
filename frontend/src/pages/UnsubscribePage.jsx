import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiFetch } from "../lib/api";

export default function UnsubscribePage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [state, setState] = useState("loading"); // loading | done | error

  useEffect(() => {
    if (!token) {
      setState("error");
      return;
    }
    apiFetch(`/auth/unsubscribe?token=${encodeURIComponent(token)}`, { method: "GET" })
      .then(() => setState("done"))
      .catch(() => setState("error"));
  }, [token]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <img src="/images/icon-192.png" alt="ReNest" className="h-14 w-14 rounded-2xl" />

      {state === "loading" && (
        <p className="text-[color:var(--text-muted)]">Processing your request…</p>
      )}

      {state === "done" && (
        <>
          <h1 className="text-2xl font-bold text-[color:var(--text-primary)]">
            You&apos;ve been unsubscribed
          </h1>
          <p className="max-w-sm text-[color:var(--text-muted)]">
            You won&apos;t receive marketing or notification emails from ReNest.
            You can re-enable them anytime in your account settings.
          </p>
          <Link
            to="/dashboard"
            className="btn-primary mt-2"
          >
            Back to ReNest
          </Link>
        </>
      )}

      {state === "error" && (
        <>
          <h1 className="text-2xl font-bold text-[color:var(--text-primary)]">
            Invalid unsubscribe link
          </h1>
          <p className="max-w-sm text-[color:var(--text-muted)]">
            This link may have expired or already been used. If you&apos;d like to
            unsubscribe, please contact us.
          </p>
          <Link to="/dashboard" className="btn-primary mt-2">
            Back to ReNest
          </Link>
        </>
      )}
    </div>
  );
}
