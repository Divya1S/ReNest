import { motion } from "framer-motion";
import { ArrowRight, CheckCircle, XCircle } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import PageSection from "../components/PageSection";
import { useAuth } from "../context/AuthContext";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function VerifyEmailConfirmPage() {
  usePageTitle("Email Verified");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const { refreshUser } = useAuth();

  const [state, setState] = useState("loading"); // "loading" | "success" | "error"
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setErrorMessage("No verification token found in the link.");
      return;
    }

    let cancelled = false;

    apiFetch("/auth/verify-email/confirm", { method: "POST", body: { token } })
      .then(async () => {
        if (cancelled) return;
        await refreshUser();
        setState("success");
        // Auto-redirect after 3 seconds
        setTimeout(() => {
          if (!cancelled) navigate("/dashboard", { replace: true });
        }, 3000);
      })
      .catch((error) => {
        if (cancelled) return;
        setState("error");
        setErrorMessage(error.message);
      });

    return () => {
      cancelled = true;
    };
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <PageSection className="flex min-h-[80vh] items-center justify-center py-16">
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="paper-panel mx-auto w-full max-w-xl !rounded-[3rem] p-10 sm:p-16 text-center"
      >
        {state === "loading" && (
          <>
            <div className="mx-auto h-16 w-16 animate-spin rounded-full border-4 border-[color:var(--color-tag)] border-t-transparent" />
            <p className="mt-8 text-lg font-bold text-[color:var(--text-muted)]">
              Verifying your email…
            </p>
          </>
        )}

        {state === "success" && (
          <>
            <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-emerald-100 dark:bg-emerald-900/30">
              <CheckCircle size={40} className="text-emerald-600 dark:text-emerald-400" />
            </div>
            <h1 className="mt-8 text-[28px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              Email verified!
            </h1>
            <p className="mt-4 text-lg text-[color:var(--text-muted)]">
              You can now post listings and start room rescue scans. Redirecting
              to your dashboard…
            </p>
            <Link to="/dashboard" replace className="primary-button mt-10 w-full !py-4">
              Go to Dashboard
              <ArrowRight size={18} />
            </Link>
          </>
        )}

        {state === "error" && (
          <>
            <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-red-100 dark:bg-red-900/30">
              <XCircle size={40} className="text-red-500" />
            </div>
            <h1 className="mt-8 text-[28px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              Verification failed.
            </h1>
            <p className="mt-4 text-lg text-[color:var(--text-muted)]">{errorMessage}</p>
            <div className="mt-10 flex flex-col gap-3">
              <Link to="/verify-email" className="primary-button w-full !py-4">
                Request a new link
                <ArrowRight size={18} />
              </Link>
              <Link
                to="/dashboard"
                className="text-sm font-bold text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] dark:hover:text-white transition-colors"
              >
                Back to Dashboard
              </Link>
            </div>
          </>
        )}
      </motion.section>
    </PageSection>
  );
}
