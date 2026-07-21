import { useEffect } from "react";
import { useBlocker } from "react-router-dom";

export function useUnsavedChangesGuard({ when, title, message, confirmLabel, cancelLabel } = {}) {
  const blocker = useBlocker(Boolean(when));

  useEffect(() => {
    if (!when) {
      return undefined;
    }

    const handleBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
      return "";
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [when]);

  useEffect(() => {
    if (!when && blocker.state === "blocked") {
      blocker.reset();
    }
  }, [blocker, when]);

  return {
    isBlocked: blocker.state === "blocked",
    dialogProps: {
      open: blocker.state === "blocked",
      title,
      message,
      confirmLabel,
      cancelLabel,
      onConfirm: () => blocker.proceed(),
      onCancel: () => blocker.reset(),
    },
  };
}
