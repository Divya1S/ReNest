import { Copy, Key, Plus, RefreshCw, Trash2, Webhook } from "lucide-react";
import React from "react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { cn } from "../lib/cn";

export default function PartnerPortalPage() {
  usePageTitle("Partner Portal");
  const [usage, setUsage] = useState(null);
  const [usageError, setUsageError] = useState(null);
  const [webhooks, setWebhooks] = useState([]);
  const [loadingUsage, setLoadingUsage] = useState(true);
  const [loadingWebhooks, setLoadingWebhooks] = useState(true);
  const [showNewKey, setShowNewKey] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [newScopes, setNewScopes] = useState(["listing_read"]);
  const [createdKey, setCreatedKey] = useState(null);
  const [showWebhookForm, setShowWebhookForm] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookEvents, setWebhookEvents] = useState(["listing_created"]);

  // The raw bearer key is held for this browser tab only. localStorage kept it
  // readable by any script on the origin, survived logout, and let anyone at
  // the machine copy it again long after provisioning, for a credential the
  // server itself only stores hashed and promises to show once.
  const [storedKey, setStoredKey] = useState(() =>
    typeof window !== "undefined" ? sessionStorage.getItem("renest_partner_key") : null,
  );

  useEffect(() => {
    if (!storedKey) { setLoadingUsage(false); return; }
    apiFetch("/partner/usage", { headers: { Authorization: `Bearer ${storedKey}` } })
      .then(setUsage)
      .catch((e) => setUsageError(e?.message ?? "Failed to load usage"))
      .finally(() => setLoadingUsage(false));
    apiFetch("/partner/webhooks", { headers: { Authorization: `Bearer ${storedKey}` } })
      .then((d) => setWebhooks(d.results ?? []))
      .catch(() => {})
      .finally(() => setLoadingWebhooks(false));
  }, [storedKey]);

  async function provisionKey() {
    if (!newKeyName.trim()) return;
    try {
      const data = await apiFetch("/partner/keys", {
        method: "POST",
        body: { partner_name: newKeyName, scopes: newScopes },
      });
      setCreatedKey(data.key);
      sessionStorage.setItem("renest_partner_key", data.key);
      setStoredKey(data.key);
      setShowNewKey(false);
      setNewKeyName("");
      toast.success("API key created — copy it now, it won't be shown again.");
    } catch (e) {
      toast.error(e?.message ?? "Failed to create key");
    }
  }

  async function addWebhook() {
    if (!webhookUrl.trim() || !storedKey) return;
    try {
      const data = await apiFetch("/partner/webhooks", {
        method: "POST",
        headers: { Authorization: `Bearer ${storedKey}` },
        body: { url: webhookUrl, events: webhookEvents },
      });
      setWebhooks((prev) => [...prev, data]);
      setShowWebhookForm(false);
      setWebhookUrl("");
      toast.success("Webhook registered. Signing secret shown below — store it securely.");
    } catch {
      toast.error("Failed to register webhook");
    }
  }

  async function removeWebhook(id) {
    if (!storedKey) return;
    await apiFetch(`/partner/webhooks/${id}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${storedKey}` },
    }).catch(() => {});
    setWebhooks((prev) => prev.filter((w) => w.id !== id));
  }

  function copy(text) {
    navigator.clipboard.writeText(text).then(() => toast.success("Copied!"));
  }

  const SCOPE_OPTIONS = ["listing_read", "reservation_read", "webhook"];
  const EVENT_OPTIONS = ["listing_created", "reservation_confirmed", "listing_donated"];

  return (
    <div className="mx-auto max-w-[780px] px-4 py-10 space-y-8">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-xl bg-[color:var(--color-surface-2)] flex items-center justify-center">
          <Key size={18} className="text-[color:var(--color-tag)]" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-[color:var(--color-ink)] dark:text-white">Partner Portal</h1>
          <p className="text-sm text-[color:var(--text-muted)]">API keys, webhook endpoints, and usage stats.</p>
        </div>
      </div>

      {/* Usage stats */}
      {usageError && storedKey && (
        <div className="flex items-center gap-3 rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] p-4 text-sm">
          <p className="flex-1 text-[color:var(--text-muted)]">{usageError}</p>
          <button
            onClick={() => {
              setUsageError(null);
              setLoadingUsage(true);
              apiFetch("/partner/usage", { headers: { Authorization: `Bearer ${storedKey}` } })
                .then(setUsage)
                .catch((e) => setUsageError(e?.message ?? "Failed to load usage"))
                .finally(() => setLoadingUsage(false));
            }}
            className="flex items-center gap-1 text-xs font-semibold text-[color:var(--color-tag)] hover:opacity-80"
          >
            <RefreshCw size={12} /> Retry
          </button>
        </div>
      )}
      {loadingUsage && storedKey && (
        <div className="h-28 rounded-2xl animate-pulse bg-[color:var(--color-surface)]" />
      )}
      {usage && (
        <section className="rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] p-5">
          <h2 className="font-semibold text-sm text-[color:var(--color-ink)] dark:text-white mb-4">Usage — {usage.campus}</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: "Listings", value: usage.listing_count },
              { label: "Reservations", value: usage.reservation_count },
              { label: "Webhooks", value: usage.webhook_endpoints },
              { label: "Daily limit", value: usage.rate_limit_per_day },
            ].map((s) => (
              <div key={s.label} className="rounded-xl bg-[color:var(--color-surface-2)] p-3 text-center">
                <p className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">{s.value}</p>
                <p className="text-xs text-[color:var(--text-muted)] mt-0.5">{s.label}</p>
              </div>
            ))}
          </div>
          {usage.scopes?.length > 0 && (
            <p className="mt-3 text-xs text-[color:var(--text-muted)]">
              Scopes: {usage.scopes.join(", ")}
            </p>
          )}
        </section>
      )}

      {/* Key provisioning */}
      <section className="rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-sm text-[color:var(--color-ink)] dark:text-white">API Keys</h2>
          <button
            onClick={() => setShowNewKey((v) => !v)}
            className="flex items-center gap-1.5 text-xs font-semibold text-[color:var(--color-tag)] hover:opacity-80"
          >
            <Plus size={13} /> New key
          </button>
        </div>

        {showNewKey && (
          <div className="space-y-3 mb-4 p-4 rounded-xl bg-[color:var(--color-surface-2)]">
            <input
              placeholder="Partner name (e.g. Housing Portal)"
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              className="w-full text-sm border border-black/10 dark:border-white/10 rounded-xl px-3 py-2 bg-transparent focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]"
            />
            <div>
              <p className="text-xs font-medium text-[color:var(--text-muted)] mb-2">Scopes</p>
              <div className="flex flex-wrap gap-2">
                {SCOPE_OPTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => setNewScopes((prev) => prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s])}
                    className={cn(
                      "px-3 py-1 rounded-full text-xs font-semibold border transition-all",
                      newScopes.includes(s)
                        ? "bg-[color:var(--color-tag)] text-white border-transparent"
                        : "border-black/10 dark:border-white/10 text-[color:var(--text-muted)]"
                    )}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <button
              onClick={provisionKey}
              className="px-4 py-2 bg-[color:var(--color-tag)] text-white rounded-xl text-xs font-semibold hover:opacity-90"
            >
              Create key
            </button>
          </div>
        )}

        {createdKey && (
          <div className="flex items-center gap-2 p-3 rounded-xl bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-700 mb-4">
            <code className="text-xs text-green-800 dark:text-green-300 flex-1 truncate">{createdKey}</code>
            <button onClick={() => copy(createdKey)} className="text-green-700 hover:text-green-900">
              <Copy size={14} />
            </button>
          </div>
        )}

        {storedKey ? (
          <div className="flex items-center gap-2 p-3 rounded-xl bg-[color:var(--color-surface-2)]">
            <Key size={13} className="text-[color:var(--text-muted)]" />
            <code className="text-xs text-[color:var(--text-muted)] flex-1">
              {storedKey.slice(0, 12)}{"•".repeat(20)}
            </code>
            <button onClick={() => copy(storedKey)} className="text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]">
              <Copy size={13} />
            </button>
          </div>
        ) : (
          !showNewKey && (
            <p className="text-xs text-[color:var(--text-muted)]">No API key provisioned yet. Create one above.</p>
          )
        )}
      </section>

      {/* Webhook endpoints */}
      <section className="rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-sm text-[color:var(--color-ink)] dark:text-white">Webhook Endpoints</h2>
          {storedKey && (
            <button
              onClick={() => setShowWebhookForm((v) => !v)}
              className="flex items-center gap-1.5 text-xs font-semibold text-[color:var(--color-tag)] hover:opacity-80"
            >
              <Plus size={13} /> Add endpoint
            </button>
          )}
        </div>

        {showWebhookForm && (
          <div className="space-y-3 mb-4 p-4 rounded-xl bg-[color:var(--color-surface-2)]">
            <input
              placeholder="https://your-portal.com/webhooks/renest"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              className="w-full text-sm border border-black/10 dark:border-white/10 rounded-xl px-3 py-2 bg-transparent focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]"
            />
            <div>
              <p className="text-xs font-medium text-[color:var(--text-muted)] mb-2">Events</p>
              <div className="flex flex-wrap gap-2">
                {EVENT_OPTIONS.map((ev) => (
                  <button
                    key={ev}
                    onClick={() => setWebhookEvents((prev) => prev.includes(ev) ? prev.filter((x) => x !== ev) : [...prev, ev])}
                    className={cn(
                      "px-3 py-1 rounded-full text-xs font-semibold border transition-all",
                      webhookEvents.includes(ev)
                        ? "bg-[color:var(--color-tag)] text-white border-transparent"
                        : "border-black/10 dark:border-white/10 text-[color:var(--text-muted)]"
                    )}
                  >
                    {ev}
                  </button>
                ))}
              </div>
            </div>
            <button
              onClick={addWebhook}
              className="px-4 py-2 bg-[color:var(--color-tag)] text-white rounded-xl text-xs font-semibold hover:opacity-90"
            >
              Register
            </button>
          </div>
        )}

        {loadingWebhooks ? (
          <div className="space-y-2">
            {[1, 2].map((i) => <div key={i} className="h-12 rounded-xl animate-pulse bg-[color:var(--color-surface-2)]" />)}
          </div>
        ) : webhooks.length === 0 ? (
          <p className="text-xs text-[color:var(--text-muted)]">No webhook endpoints registered.</p>
        ) : (
          <ul className="space-y-2">
            {webhooks.map((wh) => (
              <li key={wh.id} className="flex items-center gap-3 p-3 rounded-xl bg-[color:var(--color-surface-2)]">
                <Webhook size={13} className="text-[color:var(--text-muted)] flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate text-[color:var(--color-ink)] dark:text-white">{wh.url}</p>
                  <p className="text-[10px] text-[color:var(--text-muted)]">{(wh.events || []).join(", ")}</p>
                  {wh.secret && (
                    <div className="flex items-center gap-1.5 mt-1">
                      <code className="text-[10px] text-green-700 dark:text-green-400">{wh.secret.slice(0, 16)}…</code>
                      <button onClick={() => copy(wh.secret)} className="text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]">
                        <Copy size={11} />
                      </button>
                    </div>
                  )}
                </div>
                <button onClick={() => removeWebhook(wh.id)} className="text-[color:var(--text-muted)] hover:text-red-500">
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Embed snippet */}
      <section className="rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] p-5">
        <h2 className="font-semibold text-sm text-[color:var(--color-ink)] dark:text-white mb-3">Embed Widget</h2>
        <p className="text-xs text-[color:var(--text-muted)] mb-3">
          Drop this into any page to show a live listing grid — no authentication required.
        </p>
        <div className="relative">
          <pre className="text-[11px] bg-[color:var(--color-surface-2)] rounded-xl p-4 overflow-x-auto text-[color:var(--color-ink)] dark:text-white whitespace-pre-wrap break-all">
{`<div data-renest-campus="${usage?.campus ? usage.campus.toLowerCase().replace(/\s+/g, "-") : "your-campus-slug"}" data-renest-limit="6"></div>
<script src="${window.location.origin}/embed.js" defer></script>`}
          </pre>
          <button
            onClick={() => copy(`<div data-renest-campus="${usage?.campus ?? "your-slug"}" data-renest-limit="6"></div>\n<script src="${window.location.origin}/embed.js" defer></script>`)}
            className="absolute top-2 right-2 p-1.5 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] bg-[color:var(--color-surface)]"
          >
            <Copy size={13} />
          </button>
        </div>
      </section>
    </div>
  );
}
