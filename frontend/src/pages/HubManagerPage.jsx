import { AlertCircle, CheckCircle2, Package, Warehouse } from "lucide-react";
import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import PageSection from "../components/PageSection";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function HubManagerPage() {
  usePageTitle("Manage Hub");
  const { hubId } = useParams();

  const [hub, setHub] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [openInstructions, setOpenInstructions] = useState("");
  const [capacity, setCapacity] = useState("");
  const [active, setActive] = useState(true);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let mounted = true;
    apiFetch(`/hubs/${hubId}/manage`)
      .then((data) => {
        if (!mounted) return;
        setHub(data);
        setOpenInstructions(data.open_instructions ?? "");
        setCapacity(data.capacity != null ? String(data.capacity) : "");
        setActive(data.active);
      })
      .catch((err) => {
        if (mounted) setError(err.message);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => { mounted = false; };
  }, [hubId]);

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setSaveError("");
    setSaved(false);

    const body = {
      open_instructions: openInstructions,
      capacity: capacity === "" ? null : parseInt(capacity, 10),
      active,
    };

    try {
      const data = await apiFetch(`/hubs/${hubId}/manage`, { method: "PATCH", body });
      setHub(data);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <div className="paper-panel p-10 animate-pulse">
          <div className="h-3 w-24 rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="mt-4 h-8 w-1/2 rounded-2xl bg-[color:var(--color-surface-2)]" />
        </div>
        <div className="paper-panel p-10 h-64 animate-pulse bg-[color:var(--color-surface-2)]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-red-100 text-red-500 dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle size={40} />
        </div>
        <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
          Could not load hub
        </h2>
        <p className="mt-4 text-[color:var(--text-muted)]">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageSection className="paper-panel p-10 sm:p-14">
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[1.25rem] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]">
            <Warehouse size={28} />
          </div>
          <div>
            <p className="label-title">Hub manager</p>
            <h1 className="mt-1 text-[28px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              {hub?.name}
            </h1>
            <p className="mt-0.5 text-sm text-[color:var(--text-muted)]">
              {hub?.campus_name} · {hub?.zone_label}
            </p>
          </div>
        </div>
      </PageSection>

      <PageSection className="paper-panel p-10 sm:p-12" delay={0.04}>
        <form onSubmit={handleSubmit} className="space-y-8 max-w-2xl">
          <div>
            <label className="mb-2 block text-sm font-semibold text-[color:var(--color-ink)] dark:text-white">
              Pickup instructions
            </label>
            <p className="mb-3 text-xs text-[color:var(--text-muted)]">
              Shown to students on the hub listing — explain access hours, entry codes, or drop-off rules.
            </p>
            <textarea
              value={openInstructions}
              onChange={(e) => setOpenInstructions(e.target.value)}
              rows={5}
              className="w-full rounded-2xl border border-black/12 dark:border-white/12 bg-[color:var(--color-surface)] px-5 py-4 text-sm text-[color:var(--color-ink)] dark:text-white placeholder:text-[color:var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)] resize-none"
              placeholder="e.g. Open Mon–Fri 9am–5pm. Use the keypad at the side entrance: code 1234#."
            />
          </div>

          <div>
            <label className="mb-2 block text-sm font-semibold text-[color:var(--color-ink)] dark:text-white">
              Capacity <span className="font-normal text-[color:var(--text-muted)]">(optional)</span>
            </label>
            <p className="mb-3 text-xs text-[color:var(--text-muted)]">
              Maximum items the hub can accept at once. Leave blank for unlimited.
            </p>
            <div className="flex items-center gap-3">
              <Package size={18} className="text-[color:var(--text-muted)] shrink-0" />
              <input
                type="number"
                min="0"
                value={capacity}
                onChange={(e) => setCapacity(e.target.value)}
                placeholder="e.g. 50"
                className="w-40 rounded-2xl border border-black/12 dark:border-white/12 bg-[color:var(--color-surface)] px-4 py-3 text-sm text-[color:var(--color-ink)] dark:text-white placeholder:text-[color:var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)]"
              />
              <span className="text-sm text-[color:var(--text-muted)]">items</span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={active}
              onClick={() => setActive((v) => !v)}
              className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)] focus:ring-offset-2 ${active ? "bg-[color:var(--color-accent)]" : "bg-[color:var(--color-surface-2)]"}`}
            >
              <span
                className={`inline-block h-6 w-6 rounded-full bg-white shadow-lg transition-transform ${active ? "translate-x-5" : "translate-x-0"}`}
              />
            </button>
            <div>
              <p className="text-sm font-semibold text-[color:var(--color-ink)] dark:text-white">
                Hub is {active ? "open" : "closed"}
              </p>
              <p className="text-xs text-[color:var(--text-muted)]">
                {active ? "Visible to students on the hubs page." : "Hidden from the public hub listing."}
              </p>
            </div>
          </div>

          {saveError && (
            <p className="rounded-2xl bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-600 dark:text-red-400">
              {saveError}
            </p>
          )}

          {saved && (
            <div className="flex items-center gap-2 rounded-2xl bg-emerald-50 dark:bg-emerald-900/20 px-4 py-3 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
              <CheckCircle2 size={16} />
              Changes saved.
            </div>
          )}

          <div className="pt-2">
            <button
              type="submit"
              disabled={saving}
              className="primary-button disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>
      </PageSection>
    </div>
  );
}
