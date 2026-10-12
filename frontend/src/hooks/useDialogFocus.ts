import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface DialogFocusOptions {
  open: boolean;
  /** The dialog panel: Tab is kept inside it. */
  panelRef: RefObject<HTMLElement | null>;
  /** What to focus on open. Defaults to the panel's first focusable element. */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Called on Escape. */
  onClose?: () => void;
}

/**
 * Keyboard behaviour for a custom modal dialog, the parts a native <dialog>
 * would provide: focus moves into the panel when it opens, Tab and Shift+Tab
 * cycle inside it, Escape closes it, and focus returns to whatever opened it.
 *
 * `onClose` may change identity between renders without disturbing an open
 * dialog: only `open` flipping runs the focus hand-off.
 */
export function useDialogFocus({ open, panelRef, initialFocusRef, onClose }: DialogFocusOptions): void {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const initial =
      initialFocusRef?.current ?? panelRef.current?.querySelector<HTMLElement>(FOCUSABLE) ?? null;
    initial?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current?.();
        return;
      }
      const panel = panelRef.current;
      if (event.key !== "Tab" || !panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!panel.contains(active)) {
        // Focus escaped (a click on the backdrop, a control that unmounted).
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (
        previouslyFocused &&
        typeof previouslyFocused.focus === "function" &&
        document.contains(previouslyFocused)
      ) {
        previouslyFocused.focus();
      }
    };
  }, [open, panelRef, initialFocusRef]);
}
