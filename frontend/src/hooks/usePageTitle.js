import { useEffect } from "react";

const SITE = "ReNest";

export function usePageTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} — ${SITE}` : `${SITE} — Campus Move-Out Rescue`;
    return () => {
      document.title = `${SITE} — Campus Move-Out Rescue`;
    };
  }, [title]);
}
