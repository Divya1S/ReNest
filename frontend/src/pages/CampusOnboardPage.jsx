import { ArrowRight, Building2, CheckCircle2 } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";

import PageSection from "../components/PageSection";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

const initialForm = {
  name: "",
  slug: "",
  email_domains: "",
  contact_name: "",
  contact_email: "",
};

function slugify(value) {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

/**
 * Self-service campus registration, backing the "Request yours" links on the
 * campus landing page. The request is created inactive and pending approval;
 * a platform admin approves it before anyone can register with that domain.
 */
export default function CampusOnboardPage() {
  usePageTitle("Bring ReNest to your campus");
  const [form, setForm] = useState(initialForm);
  // The slug auto-fills from the name until the operator edits it themselves.
  const [slugTouched, setSlugTouched] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [submitted, setSubmitted] = useState(false);

  function update(field, value) {
    setForm((current) => {
      const next = { ...current, [field]: value };
      if (field === "name" && !slugTouched) next.slug = slugify(value);
      return next;
    });
    setFieldErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setFieldErrors({});
    try {
      await apiFetch("/auth/campus/onboard", { method: "POST", body: form });
      setSubmitted(true);
    } catch (requestError) {
      const data = requestError?.data;
      if (data && typeof data === "object" && !Array.isArray(data)) {
        const errors = {};
        for (const [field, message] of Object.entries(data)) {
          errors[field] = Array.isArray(message) ? message.join(" ") : String(message);
        }
        setFieldErrors(errors);
      } else {
        setFieldErrors({ detail: requestError?.message || "Could not send your request." });
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-20 text-center">
        <CheckCircle2 size={40} className="mx-auto text-[color:var(--color-free)]" />
        <h1 className="mt-5 text-[28px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
          Request received
        </h1>
        <p className="mt-3 text-[color:var(--text-muted)]">
          We will email {form.contact_email} once {form.name} is approved. Students at your campus
          can browse ReNest in the meantime.
        </p>
        <Link to="/browse" className="primary-button mt-8 inline-flex">
          Browse listings
          <ArrowRight size={15} />
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 pb-20">
      <PageSection className="pt-10 pb-6">
        <div className="flex items-center gap-2 text-[color:var(--color-tag)]">
          <Building2 size={16} />
          <span className="text-[12px] font-semibold uppercase tracking-[0.1em]">New campus</span>
        </div>
        <h1 className="mt-3 text-[34px] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
          Bring ReNest to your campus
        </h1>
        <p className="mt-2 text-[color:var(--text-muted)]">
          Tell us about your school. We review each request by hand, then switch on a campus page,
          email-domain matching and analytics for your team.
        </p>
      </PageSection>

      <form onSubmit={handleSubmit} className="paper-panel space-y-6 p-8">
        {fieldErrors.detail && (
          <p role="alert" className="rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400">
            {fieldErrors.detail}
          </p>
        )}

        <Field
          id="campus-name"
          label="Campus name"
          value={form.name}
          onChange={(value) => update("name", value)}
          error={fieldErrors.name}
          placeholder="University of Southern California"
          required
        />
        <Field
          id="campus-slug"
          label="URL slug"
          hint="Your campus page will live at /campus/<slug>."
          value={form.slug}
          onChange={(value) => {
            setSlugTouched(true);
            update("slug", slugify(value));
          }}
          error={fieldErrors.slug}
          placeholder="usc"
          required
        />
        <Field
          id="campus-domains"
          label="Student email domains"
          hint="Comma-separated. Students with these addresses join your campus automatically."
          value={form.email_domains}
          onChange={(value) => update("email_domains", value)}
          error={fieldErrors.email_domains}
          placeholder="usc.edu, alumni.usc.edu"
          required
        />
        <Field
          id="campus-contact-name"
          label="Your name"
          value={form.contact_name}
          onChange={(value) => update("contact_name", value)}
          error={fieldErrors.contact_name}
          placeholder="Jordan Alvarez"
          required
        />
        <Field
          id="campus-contact-email"
          label="Your email"
          type="email"
          value={form.contact_email}
          onChange={(value) => update("contact_email", value)}
          error={fieldErrors.contact_email}
          placeholder="jordan@usc.edu"
          required
        />

        <button type="submit" className="primary-button w-full justify-center" disabled={submitting}>
          {submitting ? "Sending…" : "Request a campus"}
          {!submitting && <ArrowRight size={15} />}
        </button>
      </form>
    </div>
  );
}

function Field({ id, label, hint, value, onChange, error, type = "text", placeholder, required }) {
  const errorId = `${id}-error`;
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white">
        {label}
      </label>
      {hint && <p className="mb-2 text-xs text-[color:var(--text-muted)]">{hint}</p>}
      <input
        id={id}
        type={type}
        className="field"
        value={value}
        required={required}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error && (
        <p id={errorId} className="mt-1.5 text-xs font-semibold text-red-500">
          {error}
        </p>
      )}
    </div>
  );
}
