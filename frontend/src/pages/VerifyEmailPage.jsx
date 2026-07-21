import { motion } from "framer-motion";
import { ArrowRight, Mail, MailCheck, Wrench } from "lucide-react";
import React, { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function VerifyEmailPage() {
  usePageTitle("Verify Email");
  const location = useLocation();
  const [sending, setSending] = useState(false);
  const [resent, setResent] = useState(false);
  // In DEBUG the backend returns the verification link directly (emails only
  // go to the server console in dev). Registration hands it over via route
  // state; a resend refreshes it from the API response.
  const [devVerifyUrl, setDevVerifyUrl] = useState(location.state?.devVerifyUrl ?? null);

  async function handleResend() {
    setSending(true);
    try {
      const data = await apiFetch("/auth/verify-email/send", { method: "POST", body: {} });
      if (data?.dev_verify_url) setDevVerifyUrl(data.dev_verify_url);
      setResent(true);
      toast.success("Verification email sent.");
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <PageSection className="flex min-h-[80vh] items-center justify-center py-16">
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="paper-panel mx-auto w-full max-w-xl !rounded-[3rem] p-10 sm:p-16 text-center"
      >
        <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-[color:var(--color-tag-soft)]">
          <Mail size={36} className="text-[color:var(--color-tag)]" />
        </div>

        <h1 className="mt-8 text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
          Check your inbox.
        </h1>
        <p className="mt-4 text-lg leading-8 text-[color:var(--text-muted)]">
          We sent a verification link to your email address. Click it to unlock
          the ability to post listings and start room rescue scans.
        </p>
        <p className="mt-2 text-sm text-[color:var(--text-muted)]">
          The link expires in 24 hours.
        </p>

        {devVerifyUrl ? (
          <div className="mt-8 rounded-[1.5rem] border-2 border-dashed border-[color:var(--color-tag)] bg-[color:var(--color-tag-soft)] p-5 text-left">
            <p className="inline-flex items-center gap-2 text-xs font-black uppercase tracking-[0.14em] text-[color:var(--color-tag)]">
              <Wrench size={13} aria-hidden="true" />
              Development mode
            </p>
            <p className="mt-2 text-sm leading-6 text-[color:var(--text-muted)]">
              Emails print to the backend terminal in local dev, so here is your
              verification link directly:
            </p>
            <a href={devVerifyUrl} className="secondary-button mt-4 w-full !py-3">
              Open verification link
              <ArrowRight size={16} />
            </a>
          </div>
        ) : null}

        <div className="mt-10 space-y-4">
          <Link to="/dashboard" className="primary-button w-full !py-4">
            Go to Dashboard
            <ArrowRight size={18} />
          </Link>

          <button
            type="button"
            onClick={handleResend}
            disabled={sending || resent}
            className="secondary-button w-full !py-4"
          >
            {resent ? (
              <>
                <MailCheck size={18} />
                Email sent
              </>
            ) : sending ? (
              "Sending..."
            ) : (
              "Resend verification email"
            )}
          </button>
        </div>

        <p className="mt-8 text-xs text-[color:var(--text-muted)]">
          Can&apos;t find it? Check your spam folder. You can also browse listings
          while you wait — you just need to verify before posting.
        </p>
      </motion.section>
    </PageSection>
  );
}
