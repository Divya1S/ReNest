import { AnimatePresence, motion } from "framer-motion";
import { Megaphone, X } from "lucide-react";
import React, { useEffect, useState } from "react";

import { apiFetch } from "../lib/api";

export default function AnnouncementBanner() {
  const [announcements, setAnnouncements] = useState([]);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return JSON.parse(sessionStorage.getItem("dismissed_announcements") ?? "[]");
    } catch {
      return [];
    }
  });

  useEffect(() => {
    apiFetch("/announcements")
      .then((data) => setAnnouncements(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  const visible = announcements.filter(
    (a) => !dismissed.includes(a.id) && (a.is_pinned || true)
  );

  function dismiss(id) {
    const next = [...dismissed, id];
    setDismissed(next);
    try {
      sessionStorage.setItem("dismissed_announcements", JSON.stringify(next));
    } catch {
      // sessionStorage unavailable (private mode) — dismissal just won't persist
    }
  }

  if (visible.length === 0) return null;

  // Show pinned first, then just the most recent one if multiple
  const toShow = visible.slice(0, 2);

  return (
    <div className="mx-auto max-w-[1200px] px-4 md:px-8 pt-3 space-y-2">
      <AnimatePresence mode="popLayout">
        {toShow.map((a) => (
          <motion.div
            key={a.id}
            initial={{ opacity: 0, y: -8, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -8, height: 0 }}
            transition={{ duration: 0.2 }}
            className={`flex items-start gap-3 rounded-xl px-4 py-3 text-sm ${
              a.is_pinned
                ? "bg-[color:var(--color-teal)]/10 border border-[color:var(--color-teal)]/25"
                : "bg-[color:var(--color-surface)] border border-[color:var(--color-border)]"
            }`}
          >
            <Megaphone
              className={`mt-0.5 h-4 w-4 shrink-0 ${
                a.is_pinned ? "text-[color:var(--color-teal)]" : "text-[color:var(--color-muted)]"
              }`}
            />
            <div className="flex-1 min-w-0">
              <span className="font-semibold">{a.title}</span>
              {a.body && (
                <span className="ml-2 text-[color:var(--color-muted)]">{a.body}</span>
              )}
            </div>
            <button
              onClick={() => dismiss(a.id)}
              aria-label="Dismiss announcement"
              className="shrink-0 rounded-full p-1 text-[color:var(--color-muted)] hover:text-[color:var(--color-fg)] hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
