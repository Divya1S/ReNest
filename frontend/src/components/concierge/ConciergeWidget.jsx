import { AnimatePresence, motion } from "framer-motion";
import { Maximize2, RotateCcw, Sparkles, X } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import ConciergeConversation from "./ConciergeConversation";
import { useConciergeChat } from "./useConciergeChat";

/**
 * Nest Concierge — floating chat surface.
 *
 * A thin shell (launcher + panel) around the shared conversation core; the
 * full-page surface at /assistant drives the same thread. Mounted behind a
 * lazy import plus an ErrorBoundary in AppFrame, so nothing here can take the
 * app down with it.
 */
export default function ConciergeWidget() {
  const [open, setOpen] = useState(false);
  const chat = useConciergeChat(open);
  const launcherRef = useRef(null);
  const panelRef = useRef(null);

  // Keep Tab inside the dialog while it's open (matches the app drawer's
  // behaviour); Escape still closes via the window listener below.
  const trapFocus = (event) => {
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusables = panelRef.current.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  useEffect(() => {
    if (!open) launcherRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        ref={launcherRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={open ? "Close Nest Concierge" : "Open Nest Concierge"}
        aria-expanded={open}
        className="fixed bottom-[calc(1.5rem+env(safe-area-inset-bottom,0px))] left-4 z-40 flex h-13 w-13 items-center justify-center rounded-full bg-[color:var(--color-box)] p-3.5 text-white shadow-lg transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--color-box)] print:hidden sm:bottom-6 sm:left-6"
      >
        <Sparkles size={22} aria-hidden="true" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.section
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Nest Concierge"
            onKeyDown={trapFocus}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-x-0 bottom-0 z-40 flex h-[min(85dvh,34rem)] w-full flex-col overflow-hidden rounded-t-2xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface)] pb-[env(safe-area-inset-bottom,0px)] shadow-2xl print:hidden sm:inset-x-auto sm:bottom-24 sm:left-6 sm:h-[min(30rem,calc(100dvh-8rem))] sm:w-[min(24rem,calc(100vw-2rem))] sm:rounded-2xl sm:pb-0"
          >
            <header className="flex items-center gap-1.5 border-b border-[color:var(--color-line-strong)] px-4 py-3">
              <Sparkles size={16} className="text-[color:var(--color-box)]" aria-hidden="true" />
              <h2 className="flex-1 pl-0.5 text-sm font-bold tracking-tight text-[color:var(--color-night)]">
                Nest Concierge
              </h2>
              <Link
                to="/assistant"
                onClick={() => setOpen(false)}
                aria-label="Open full-page assistant"
                className="rounded-md p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--color-night)]"
              >
                <Maximize2 size={15} aria-hidden="true" />
              </Link>
              {chat.messages.length > 0 && (
                <button
                  type="button"
                  onClick={chat.reset}
                  aria-label="Clear conversation"
                  className="rounded-md p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--color-night)]"
                >
                  <RotateCcw size={15} aria-hidden="true" />
                </button>
              )}
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="rounded-md p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--color-night)]"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </header>

            <ConciergeConversation chat={chat} autoFocus />
          </motion.section>
        )}
      </AnimatePresence>
    </>
  );
}
