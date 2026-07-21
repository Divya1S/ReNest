import { motion, AnimatePresence } from "framer-motion";
import { Plus, Camera, Search, LayoutDashboard } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { useIntentPrefetch } from "../hooks/useIntentPrefetch";

const actions = [
  {
    icon: Camera,
    label: "Scan Room",
    to: "/scan",
    color: "bg-[color:var(--color-teal)]",
    routeKey: "scan",
    data: "/scan-sessions",
  },
  {
    icon: Plus,
    label: "New Listing",
    to: "/listings/new",
    color: "bg-[color:var(--color-box)]",
    routeKey: "listingForm",
  },
  {
    icon: Search,
    label: "Browse",
    to: "/browse",
    color: "bg-[color:var(--color-tag)]",
    routeKey: "browse",
  },
  {
    icon: LayoutDashboard,
    label: "Dashboard",
    to: "/dashboard",
    color: "bg-slate-600 dark:bg-slate-500",
    routeKey: "dashboard",
    data: "/dashboard",
  },
];

function QuickActionLink({ action, actionRef, onNavigate }) {
  const prefetchBind = useIntentPrefetch({
    route: action.routeKey,
    data: action.data,
    enabled: Boolean(action.routeKey || action.data),
  });

  return (
    <Link
      ref={actionRef}
      to={action.to}
      role="menuitem"
      onClick={onNavigate}
      className="flex items-center gap-3 focus-visible:outline-none"
      {...prefetchBind}
    >
      <span className="rounded-lg bg-white px-3 py-1 text-sm font-bold shadow-lg dark:bg-slate-800 text-[color:var(--color-ink)] dark:text-white">
        {action.label}
      </span>
      <div className={`flex h-12 w-12 items-center justify-center rounded-full text-white shadow-lg transition-transform hover:scale-110 ${action.color}`}>
        <action.icon size={20} aria-hidden="true" />
      </div>
    </Link>
  );
}

export default function QuickActionFab() {
  const [isOpen, setIsOpen] = useState(false);
  const fabRef = useRef(null);
  const firstActionRef = useRef(null);
  const location = useLocation();

  // Close on route change
  useEffect(() => {
    setIsOpen(false);
  }, [location]);

  // Move focus into menu when it opens; return to FAB when it closes
  useEffect(() => {
    if (isOpen) {
      firstActionRef.current?.focus();
    }
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        setIsOpen(false);
        fabRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen]);

  return (
    <div className="fixed bottom-8 right-8 z-50 flex flex-col items-end gap-4 sm:bottom-10 sm:right-10" style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}>
      <AnimatePresence>
        {isOpen && (
          <div role="menu" aria-label="Quick actions" className="flex flex-col items-end gap-3">
            {actions.map((action, index) => (
              <motion.div
                key={action.to}
                initial={{ opacity: 0, y: 20, scale: 0.8 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 20, scale: 0.8 }}
                transition={{ delay: (actions.length - 1 - index) * 0.05 }}
              >
                <QuickActionLink
                  action={action}
                  actionRef={index === 0 ? firstActionRef : undefined}
                  onNavigate={() => setIsOpen(false)}
                />
              </motion.div>
            ))}
          </div>
        )}
      </AnimatePresence>

      <button
        ref={fabRef}
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-label={isOpen ? "Close quick actions" : "Open quick actions"}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        className="flex h-16 w-16 items-center justify-center rounded-full bg-[color:var(--color-tag)] text-white shadow-2xl transition-transform hover:scale-110 active:scale-95 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-[color:var(--color-tag)] focus-visible:ring-offset-2"
      >
        <motion.div
          animate={{ rotate: isOpen ? 45 : 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 20 }}
          aria-hidden="true"
        >
          <Plus size={28} />
        </motion.div>
      </button>
    </div>
  );
}
