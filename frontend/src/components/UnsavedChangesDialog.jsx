import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, ArrowRight, X } from "lucide-react";
import React, { useEffect, useId, useRef } from "react";

export default function UnsavedChangesDialog({
  open,
  kicker = "Unsaved work",
  title = "Leave this page?",
  message = "You have unsaved changes that have not been applied yet.",
  confirmLabel = "Leave page",
  cancelLabel = "Stay here",
  onConfirm,
  onCancel,
}) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef(null);
  const confirmRef = useRef(null);
  const restoreFocusRef = useRef(null);

  // Focus moves into the dialog on open and returns to the trigger on close,
  // Escape cancels, and Tab cycles inside: the behaviours a native <dialog>
  // would provide and that a confirmation prompt needs to be usable without a
  // mouse.
  useEffect(() => {
    if (!open) return undefined;
    restoreFocusRef.current = document.activeElement;
    confirmRef.current?.focus();

    const onKey = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCancel?.();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const previous = restoreFocusRef.current;
      if (previous && typeof previous.focus === "function" && document.contains(previous)) {
        previous.focus();
      }
    };
  }, [open, onCancel]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/60 px-4 backdrop-blur-sm"
          onClick={onCancel}
        >
          <motion.div
            ref={panelRef}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 12 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="paper-panel relative w-full max-w-lg !rounded-[2rem] p-8"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={onCancel}
              aria-label="Close dialog"
              className="absolute right-4 top-4 flex h-10 w-10 items-center justify-center rounded-full text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--bg-surface-2)]"
            >
              <X size={18} />
            </button>

            <div className="flex items-center gap-4">
              <div className="flex h-14 w-14 items-center justify-center rounded-[1.2rem] bg-[rgba(201,100,68,0.12)] text-[color:var(--color-urgent)]">
                <AlertTriangle size={26} />
              </div>
              <div>
                <p className="label-title">{kicker}</p>
                <h2 id={titleId} className="mt-1 text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">{title}</h2>
              </div>
            </div>

            <p id={descriptionId} className="mt-5 text-sm leading-7 text-[color:var(--text-muted)]">{message}</p>

            <div className="mt-7 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <button type="button" onClick={onCancel} className="secondary-button">
                {cancelLabel}
              </button>
              <button ref={confirmRef} type="button" onClick={onConfirm} className="primary-button">
                {confirmLabel}
                <ArrowRight size={15} />
              </button>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
