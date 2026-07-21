import { motion } from "framer-motion";
import { ArrowRight, Camera, HeartHandshake, Sparkles, User, Mail, School, Lock } from "lucide-react";
import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import PageSection from "../components/PageSection";
import { useAuth } from "../context/AuthContext";
import { usePageTitle } from "../hooks/usePageTitle";

export default function RegisterPage() {
  usePageTitle("Create Account");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { register } = useAuth();
  const [form, setForm] = useState({
    display_name: "",
    email: "",
    campus_name: "",
    password: "",
    confirm_password: "",
    referral_code: searchParams.get("ref") ?? "",
  });
  const [error, setError] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function validatePasswords() {
    if (form.password !== form.confirm_password) {
      setPasswordError("Passwords do not match.");
      return false;
    }
    setPasswordError("");
    return true;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!validatePasswords()) return;
    setSubmitting(true);
    setError("");
    try {
      const data = await register(form);
      navigate("/verify-email", {
        replace: true,
        state: data.dev_verify_url ? { devVerifyUrl: data.dev_verify_url } : undefined,
      });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageSection className="auth-grid min-h-[80vh] items-center gap-12">
      <motion.aside 
        initial={{ opacity: 0, x: -30 }}
        animate={{ opacity: 1, x: 0 }}
        className="auth-aside auth-aside--register !rounded-[3rem] p-10 sm:p-16"
      >
        <p className="label-title !text-amber-300">Why students join</p>
        <h1 className="mt-4 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] sm:text-[42px] text-white">Build a cheaper, less wasteful move-in.</h1>
        <p className="mt-8 max-w-xl text-lg leading-relaxed text-white/80">
          ReNest is built to solve one overlooked campus pain point: useful dorm gear gets trashed during move-out, then bought again weeks later.
        </p>

        <div className="mt-12 space-y-8">
          <div className="auth-point group">
            <div className="flex items-center gap-4 transition-transform group-hover:translate-x-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/10 backdrop-blur-md">
                <Camera size={24} className="text-emerald-300" />
              </div>
              <p className="text-[17px] font-semibold text-white">Room Rescue Scan</p>
            </div>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              Upload room photos, tag hotspots, and build rescue items visually instead of filling out repetitive forms.
            </p>
          </div>
          
          <div className="auth-point group">
            <div className="flex items-center gap-4 transition-transform group-hover:translate-x-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/10 backdrop-blur-md">
                <HeartHandshake size={24} className="text-pink-300" />
              </div>
              <p className="text-[17px] font-semibold text-white">Community handoff</p>
            </div>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              Share essentials through lobbies and donation hubs so valuable items reach students before dumpsters.
            </p>
          </div>
        </div>
      </motion.aside>

      <motion.section
        initial={{ opacity: 0, x: 30 }}
        animate={{ opacity: 1, x: 0 }}
        className="paper-panel mx-auto w-full max-w-2xl !rounded-[3rem] p-10 sm:p-16"
      >
        <div className="sticker bg-[color:var(--color-box)] text-white font-bold">
          <Sparkles size={14} />
          Join ReNest
        </div>
        <h2 className="mt-6 text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Start rescuing smarter today.</h2>
        
        <form onSubmit={handleSubmit} className="mt-12 grid gap-6 sm:grid-cols-2">
          <div className="floating-label-group sm:col-span-2">
            <User className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="text"
              className="field pl-14"
              placeholder=" "
              autoComplete="name"
              value={form.display_name}
              onChange={(event) => setForm((current) => ({ ...current, display_name: event.target.value }))}
              required
              id="display_name"
            />
            <label htmlFor="display_name">Full Name</label>
          </div>

          <div className="floating-label-group">
            <Mail className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="email"
              className="field pl-14"
              placeholder=" "
              autoComplete="email"
              value={form.email}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
              required
              id="email"
            />
            <label htmlFor="email">Email Address</label>
          </div>

          <div className="floating-label-group">
            <School className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="text"
              className="field pl-14"
              placeholder=" "
              value={form.campus_name}
              onChange={(event) => setForm((current) => ({ ...current, campus_name: event.target.value }))}
              id="campus"
            />
            <label htmlFor="campus">Campus Name</label>
          </div>

          <div className="floating-label-group">
            <Lock className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="password"
              className="field pl-14"
              placeholder=" "
              autoComplete="new-password"
              value={form.password}
              onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
              required
              id="password"
            />
            <label htmlFor="password">Password</label>
          </div>

          <div className="floating-label-group">
            <Lock className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="password"
              className={`field pl-14 ${passwordError ? "border-red-400 focus:border-red-400 focus:ring-red-200" : ""}`}
              placeholder=" "
              autoComplete="new-password"
              value={form.confirm_password}
              onChange={(event) => {
                setForm((current) => ({ ...current, confirm_password: event.target.value }));
                if (passwordError) setPasswordError("");
              }}
              onBlur={validatePasswords}
              required
              id="confirm_password"
              aria-describedby={passwordError ? "password-match-error" : undefined}
            />
            <label htmlFor="confirm_password">Confirm Password</label>
          </div>

          {passwordError && (
            <motion.p
              id="password-match-error"
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              role="alert"
              className="sm:col-span-2 text-sm font-bold text-red-500 bg-red-50 p-3 rounded-xl dark:bg-red-900/20"
            >
              {passwordError}
            </motion.p>
          )}

          {error && (
            <motion.p 
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="sm:col-span-2 text-sm font-bold text-red-500 bg-red-50 p-3 rounded-xl dark:bg-red-900/20"
            >
              {error}
            </motion.p>
          )}

          <button type="submit" disabled={submitting} className="primary-button sm:col-span-2 !py-5 !text-lg">
            {submitting ? "Creating Account..." : "Create Account & Start"}
            <ArrowRight size={20} />
          </button>
        </form>

        <div className="mt-10 pt-10 border-t border-slate-100 dark:border-slate-800">
          <p className="text-sm font-bold text-[color:var(--text-muted)]">Already registered?</p>
          <div className="mt-4 flex items-center justify-between gap-4">
            <p className="text-sm text-[color:var(--text-muted)] leading-relaxed">
              Sign in to continue your active scans and rescue listings.
            </p>
            <Link to="/login" className="secondary-button whitespace-nowrap !px-6">
              Sign In
            </Link>
          </div>
        </div>
      </motion.section>
    </PageSection>
  );
}

