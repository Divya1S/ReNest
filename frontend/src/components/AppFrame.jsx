import { motion, AnimatePresence } from "framer-motion";
import {
  Bell,
  Bookmark,
  Camera,
  ChevronDown,
  LayoutDashboard,
  ListChecks,
  LogIn,
  LogOut,
  Mail,
  MapPinned,
  Search,
  Shield,
  Sparkles,
  Trophy,
  Menu,
  X,
} from "lucide-react";
import React, { useState, useEffect, useRef, Suspense, useEffectEvent } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { toast } from "sonner";

import { useAuth } from "../context/AuthContext";
import { useIntentPrefetch } from "../hooks/useIntentPrefetch";
import { useNotificationBadge } from "../hooks/useNotificationBadge";
import { apiFetch } from "../lib/api";

import DarkModeToggle from "./DarkModeToggle";
import { ErrorBoundary } from "./ErrorBoundary";
import QuickActionFab from "./QuickActionFab";

// Lazy so the concierge ships as its own chunk and costs nothing until opened;
// its ErrorBoundary below renders nothing on a crash — the widget can never
// take the app shell down with it.
const ConciergeWidget = React.lazy(() => import("./concierge/ConciergeWidget"));
const silentFallback = () => null;

function NavItem({ to, icon: Icon, children, onClick, prefetchRouteKey, prefetchData }) {
  const prefetchBind = useIntentPrefetch({
    route: prefetchRouteKey,
    data: prefetchData,
    enabled: Boolean(prefetchRouteKey || prefetchData),
  });

  return (
    <NavLink
      to={to}
      onClick={onClick}
      {...prefetchBind}
      className={({ isActive }) =>
        `inline-flex items-center gap-1.5 rounded-full px-3.5 py-2 text-[0.8125rem] font-medium transition-all duration-150 ${
          isActive
            ? "bg-[color:var(--color-tag)] text-white"
            : "text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] hover:bg-[color:var(--bg-tag-soft)]"
        }`
      }
    >
      {Icon ? <Icon size={15} aria-hidden="true" /> : null}
      {children}
    </NavLink>
  );
}

const MORE_MENU_ITEMS = [
  { to: "/requests", label: "Requests", icon: ListChecks, routeKey: "requests", data: ["/requests", "/match-center"] },
  { to: "/saved", label: "Saved Items", icon: Bookmark, routeKey: "saved", data: "/saved-listings" },
  { to: "/hubs", label: "Donation Hubs", icon: MapPinned, routeKey: "hubs", data: "/hubs" },
  { to: "/trust", label: "Trust Center", icon: Shield, routeKey: "trust", data: "/trust/me" },
  { to: "/leaderboard", label: "Leaderboard", icon: Trophy, routeKey: "leaderboard" },
  { to: "/assistant", label: "Assistant", icon: Sparkles, routeKey: "assistant" },
];

const MoreMenuLink = React.forwardRef(function MoreMenuLink({ item, onNavigate }, ref) {
  const prefetchBind = useIntentPrefetch({ route: item.routeKey, data: item.data, enabled: true });
  const Icon = item.icon;
  return (
    <NavLink
      ref={ref}
      to={item.to}
      onClick={onNavigate}
      {...prefetchBind}
      className={({ isActive }) =>
        `flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[0.8125rem] font-medium transition-colors ${
          isActive
            ? "bg-[color:var(--bg-tag-soft)] text-[color:var(--color-tag)]"
            : "text-[color:var(--color-ink)] hover:bg-[color:var(--bg-surface-2)]"
        }`
      }
    >
      <Icon size={15} aria-hidden="true" />
      {item.label}
    </NavLink>
  );
});

/** Disclosure dropdown holding secondary nav destinations. */
function MoreMenu() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef(null);
  const triggerRef = useRef(null);
  const firstItemRef = useRef(null);
  const location = useLocation();

  // Close when navigating
  useEffect(() => {
    setOpen(false);
  }, [location]);

  useEffect(() => {
    if (!open) return undefined;
    function handleOutside(event) {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setOpen(false);
      }
    }
    function handleKey(event) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  useEffect(() => {
    if (open) firstItemRef.current?.focus();
  }, [open]);

  const isCurrentSection = MORE_MENU_ITEMS.some((item) => location.pathname.startsWith(item.to));

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls="more-nav-panel"
        onClick={() => setOpen((o) => !o)}
        className={`inline-flex items-center gap-1 rounded-full px-3.5 py-2 text-[0.8125rem] font-medium transition-all duration-150 ${
          isCurrentSection
            ? "bg-[color:var(--color-tag)] text-white"
            : "text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] hover:bg-[color:var(--bg-tag-soft)]"
        }`}
      >
        More
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={`transition-transform duration-150 ${open ? "rotate-180" : ""}`}
        />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            id="more-nav-panel"
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.12 }}
            className="absolute right-0 top-full z-50 mt-2 w-56 rounded-2xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface)] p-2 shadow-xl"
          >
            {MORE_MENU_ITEMS.map((item, index) => (
              <MoreMenuLink
                key={item.to}
                item={item}
                ref={index === 0 ? firstItemRef : undefined}
                onNavigate={() => setOpen(false)}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Minimal spinner shown while a lazy-loaded route chunk is downloading. */
function PageLoader() {
  return (
    <div
      role="status"
      aria-label="Loading page"
      className="flex min-h-[60vh] items-center justify-center"
    >
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-[color:var(--color-tag)] border-t-transparent" />
    </div>
  );
}

function VerificationBanner() {
  const [dismissed, setDismissed] = useState(false);
  const [sending, setSending] = useState(false);

  async function resend() {
    setSending(true);
    try {
      await apiFetch("/auth/verify-email/send", { method: "POST", body: {} });
      toast.success("Verification email sent — check your inbox.");
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSending(false);
    }
  }

  if (dismissed) return null;

  return (
    <div className="border-b border-amber-200 bg-amber-50 dark:border-amber-900/40 dark:bg-amber-900/20">
      <div className="mx-auto flex max-w-[92rem] items-center justify-between gap-4 px-4 py-2.5 sm:px-6 lg:px-8">
        <div className="flex items-center gap-2.5 text-sm font-bold text-amber-800 dark:text-amber-200">
          <Mail size={15} className="shrink-0" />
          <span>
            Verify your email to post listings.{" "}
            <Link to="/verify-email" className="underline underline-offset-2 hover:no-underline">
              Verify now
            </Link>
            {" · "}
            <button
              type="button"
              onClick={resend}
              disabled={sending}
              className="underline underline-offset-2 hover:no-underline disabled:opacity-60"
            >
              {sending ? "Sending…" : "Resend link"}
            </button>
          </span>
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label="Dismiss"
          className="shrink-0 rounded-full p-1 text-amber-700 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900/40"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

export default function AppFrame() {
  const { user, logout } = useAuth();
  const [isScrolled, setIsScrolled] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const location = useLocation();
  const unreadNotifications = useNotificationBadge(!!user);

  const closeButtonRef = useRef(null);
  const menuButtonRef = useRef(null);
  const headerRef = useRef(null);

  // Publish the header's real rendered height as --nav-h so sticky elements
  // that sit beneath it (the browse search bar) stay flush against it. A
  // hardcoded token drifts with fonts/zoom/viewport and opens a see-through
  // seam where page content scrolls past between nav and bar.
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return undefined;
    const apply = () =>
      document.documentElement.style.setProperty("--nav-h", `${el.offsetHeight}px`);
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const handleScroll = useEffectEvent(() => {
    setIsScrolled(window.scrollY > 20);
  });

  // Shrink nav on scroll
  useEffect(() => {
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  // Close mobile menu on route change
  useEffect(() => {
    setIsMobileMenuOpen(false);
  }, [location]);

  // ESC key closes the mobile drawer
  const handleEscape = useEffectEvent((event) => {
    if (event.key === "Escape") {
      setIsMobileMenuOpen(false);
      menuButtonRef.current?.focus();
    }
  });

  useEffect(() => {
    if (!isMobileMenuOpen) return;
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleEscape, isMobileMenuOpen]);

  // Move focus into the drawer when it opens; restore when it closes
  useEffect(() => {
    if (isMobileMenuOpen) {
      closeButtonRef.current?.focus();
    }
  }, [isMobileMenuOpen]);

  return (
    <div className="min-h-screen bg-transparent transition-colors duration-300">
      {/* Everything except the drawer goes inert while the drawer is open, so
          keyboard/screen-reader focus cannot escape the dialog. The header
          carries `inert` itself rather than sitting in a wrapper div: a
          wrapper would be exactly header-height, which strands the sticky
          header with no scroll room (its containing block must span the page). */}
      {/* Skip navigation — invisible until focused, lets keyboard users jump straight to content */}
      <a
        inert={isMobileMenuOpen}
        href="#main-content"
        className="fixed left-4 top-4 z-[200] -translate-y-20 rounded-2xl bg-[color:var(--color-tag)] px-5 py-3 text-sm font-bold text-white shadow-2xl transition-transform focus:translate-y-0 focus:outline-none"
      >
        Skip to main content
      </a>

      <header
        ref={headerRef}
        inert={isMobileMenuOpen}
        className={`sticky top-0 z-40 nav-shell ${isScrolled ? "shrink" : ""}`}
      >
        <div className="mx-auto max-w-[92rem]">
          <div
            className={`flex items-center justify-between gap-6 px-4 sm:px-6 lg:px-8 transition-all duration-200 ${
              isScrolled ? "py-3" : "py-4"
            }`}
          >
            <Link to="/" aria-label="ReNest — Home" className="flex items-center gap-3 group">
              <div className="brand-mark" aria-hidden="true">RN</div>
              <p className="text-[1.1rem] font-bold leading-none tracking-tight text-[color:var(--color-night)]">
                ReNest
              </p>
            </Link>

            <nav aria-label="Main navigation" className="hidden items-center gap-1 lg:flex">
              {user ? (
                <>
                  <NavItem to="/dashboard" prefetchRouteKey="dashboard" prefetchData="/dashboard">Dashboard</NavItem>
                  <NavItem to="/browse" prefetchRouteKey="browse">Browse</NavItem>
                  <NavItem to="/scan" prefetchRouteKey="scan" prefetchData="/scan-sessions">Scan</NavItem>
                  <NavItem to="/notifications" prefetchRouteKey="notifications" prefetchData="/notifications">
                    Alerts
                    {unreadNotifications ? (
                      <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-[color:var(--color-box)] px-1.5 py-0.5 text-[0.55rem] font-black text-[color:var(--color-night)]">
                        {unreadNotifications}
                      </span>
                    ) : null}
                  </NavItem>
                  <MoreMenu />
                </>
              ) : (
                <>
                  <NavItem to="/browse" prefetchRouteKey="browse">Browse</NavItem>
                  <NavItem to="/hubs" prefetchRouteKey="hubs" prefetchData="/hubs">Hubs</NavItem>
                </>
              )}
            </nav>

            <div className="flex items-center gap-3">
              <div className="hidden lg:block">
                <DarkModeToggle />
              </div>

              {user ? (
                <>
                  <Link
                    to="/settings"
                    className="hidden sm:block text-right rounded-xl px-2 py-1 transition-colors hover:bg-[color:var(--bg-tag-soft)]"
                    aria-label="Profile settings"
                  >
                    <p className="text-[0.8125rem] font-semibold text-[color:var(--color-night)] leading-tight">
                      {user.display_name}
                    </p>
                    <p className="text-[0.7rem] text-[color:var(--text-muted)]">
                      {user.campus_name || "ReNest member"}
                    </p>
                  </Link>
                  <button type="button" onClick={logout} className="secondary-button !px-4 !py-2.5">
                    <LogOut size={15} aria-hidden="true" />
                    <span className="hidden sm:inline">Log Out</span>
                  </button>
                </>
              ) : (
                <>
                  <Link to="/login" className="secondary-button !px-4 !py-2.5">
                    <LogIn size={15} aria-hidden="true" />
                    <span className="hidden sm:inline">Log In</span>
                  </Link>
                  <Link to="/register" className="primary-button !px-4 !py-2.5">
                    <Sparkles size={15} aria-hidden="true" />
                    <span className="hidden sm:inline">Join</span>
                  </Link>
                </>
              )}

              <button
                ref={menuButtonRef}
                type="button"
                aria-label="Open navigation menu"
                aria-expanded={isMobileMenuOpen}
                aria-controls="mobile-menu"
                className="lg:hidden flex h-11 w-11 items-center justify-center rounded-full p-0 text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--bg-surface-2)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-tag)] focus-visible:ring-offset-2"
                onClick={() => setIsMobileMenuOpen(true)}
              >
                <Menu size={24} aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Mobile drawer — kept outside the inert region */}
      <AnimatePresence>
        {isMobileMenuOpen && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsMobileMenuOpen(false)}
              className="fixed inset-0 z-[100] bg-black/30 backdrop-blur-sm"
              aria-hidden="true"
            />

            {/* Drawer panel */}
            <motion.div
              id="mobile-menu"
              role="dialog"
              aria-modal="true"
              aria-label="Navigation menu"
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="fixed inset-y-0 right-0 z-[101] w-full max-w-xs overflow-y-auto p-8 shadow-2xl"
              style={{ background: "var(--bg-surface)" }}
            >
              <div className="flex items-center justify-between mb-8">
                <div className="brand-mark" aria-hidden="true">RN</div>
                <button
                  ref={closeButtonRef}
                  type="button"
                  onClick={() => setIsMobileMenuOpen(false)}
                  aria-label="Close navigation menu"
                  className="flex h-11 w-11 items-center justify-center rounded-full text-[color:var(--text-muted)] transition-colors hover:bg-[color:var(--bg-surface-2)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-tag)] focus-visible:ring-offset-2"
                >
                  <X size={24} aria-hidden="true" />
                </button>
              </div>

              <div className="flex items-center justify-between mb-8 pb-8 border-b border-[color:var(--color-line-strong)]">
                <span className="text-sm font-semibold text-[color:var(--text-muted)]">
                  Appearance
                </span>
                <DarkModeToggle />
              </div>

              <nav aria-label="Mobile navigation" className="flex flex-col gap-2">
                {user ? (
                  <>
                    <NavItem to="/dashboard" icon={LayoutDashboard} prefetchRouteKey="dashboard" prefetchData="/dashboard">Dashboard</NavItem>
                    <NavItem to="/browse" icon={Search} prefetchRouteKey="browse">Browse</NavItem>
                    <NavItem to="/scan" icon={Camera} prefetchRouteKey="scan" prefetchData="/scan-sessions">Scan</NavItem>
                    <NavItem to="/notifications" icon={Bell} prefetchRouteKey="notifications" prefetchData="/notifications">
                      Alerts
                      {unreadNotifications ? (
                        <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-[color:var(--color-box)] px-1.5 py-0.5 text-[0.55rem] font-black text-[color:var(--color-night)]">
                          {unreadNotifications}
                        </span>
                      ) : null}
                    </NavItem>
                    <p className="mt-4 mb-1 px-3 text-[0.66rem] font-semibold uppercase tracking-[0.16em] text-[color:var(--text-muted)]">
                      More
                    </p>
                    <NavItem to="/requests" icon={ListChecks} prefetchRouteKey="requests" prefetchData={["/requests", "/match-center"]}>Requests</NavItem>
                    <NavItem to="/saved" icon={Bookmark} prefetchRouteKey="saved" prefetchData="/saved-listings">Saved</NavItem>
                    <NavItem to="/hubs" icon={MapPinned} prefetchRouteKey="hubs" prefetchData="/hubs">Hubs</NavItem>
                    <NavItem to="/trust" icon={Shield} prefetchRouteKey="trust" prefetchData="/trust/me">Trust Center</NavItem>
                    <NavItem to="/leaderboard" icon={Trophy} prefetchRouteKey="leaderboard">Leaderboard</NavItem>
                    <NavItem to="/assistant" icon={Sparkles} prefetchRouteKey="assistant">Assistant</NavItem>
                  </>
                ) : (
                  <>
                    <NavItem to="/browse" icon={Search} prefetchRouteKey="browse">Browse</NavItem>
                    <NavItem to="/hubs" icon={MapPinned} prefetchRouteKey="hubs" prefetchData="/hubs">Hubs</NavItem>
                  </>
                )}
              </nav>

              {user && (
                <div className="mt-8 pt-8 border-t border-[color:var(--color-line-strong)]">
                  <Link to="/settings" className="block rounded-xl -mx-2 px-2 py-1 transition-colors hover:bg-[color:var(--bg-tag-soft)]">
                    <p className="text-sm font-semibold text-[color:var(--color-night)]">
                      {user.display_name}
                    </p>
                    <p className="text-xs text-[color:var(--text-muted)]">{user.campus_name || "Profile settings"}</p>
                  </Link>
                  <button onClick={logout} className="secondary-button w-full mt-4">
                    Log Out
                  </button>
                </div>
              )}
            </motion.div>
          </>
        )}
      </AnimatePresence>

      <div inert={isMobileMenuOpen}>
      {/* Email verification banner — shown only to authenticated unverified users */}
      {user && !user.email_verified && <VerificationBanner />}

      {/* Page content — ErrorBoundary catches render errors, Suspense handles lazy chunks */}
      {/* key resets the boundary on every route change so a crash on one page doesn't bleed into others */}
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-[92rem] px-4 py-8 outline-none sm:px-6 lg:px-8 xl:py-10">
        <ErrorBoundary key={location.pathname}>
          <Suspense fallback={<PageLoader />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>

      {user && <QuickActionFab />}

      {/* Hidden on /assistant — that page IS the concierge, full-screen. */}
      {user && location.pathname !== "/assistant" && (
        <ErrorBoundary fallback={silentFallback}>
          <Suspense fallback={null}>
            <ConciergeWidget />
          </Suspense>
        </ErrorBoundary>
      )}

      <footer className="mt-24 border-t border-[color:var(--color-line-strong)]" style={{ background: "var(--bg-paper-deep)" }}>
        <div className="mx-auto max-w-[92rem] px-4 sm:px-6 lg:px-8 py-16">
          <div className="grid gap-12 md:grid-cols-2 lg:grid-cols-4">
            <div className="md:col-span-2 lg:col-span-1">
              <Link to="/" className="flex items-center gap-3 mb-5" aria-label="ReNest — Home">
                <div className="brand-mark" aria-hidden="true">RN</div>
                <span className="text-lg font-bold tracking-tight text-[color:var(--color-night)]">ReNest</span>
              </Link>
              <p className="text-sm leading-relaxed text-[color:var(--text-muted)] max-w-xs">
                Campus move-out, reimagined. Rescue, reuse, and reduce waste together.
              </p>
            </div>

            <div>
              <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.16em] text-[color:var(--text-muted)] mb-5">
                Platform
              </h4>
              <ul className="space-y-3.5 text-sm text-[color:var(--text-muted)]">
                <li><Link to="/browse" className="hover:text-[color:var(--color-tag)] transition-colors">Browse Marketplace</Link></li>
                <li><Link to="/scan" className="hover:text-[color:var(--color-tag)] transition-colors">Room Scan Studio</Link></li>
                <li><Link to="/hubs" className="hover:text-[color:var(--color-tag)] transition-colors">Donation Hubs</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.16em] text-[color:var(--text-muted)] mb-5">
                Account
              </h4>
              <ul className="space-y-3.5 text-sm text-[color:var(--text-muted)]">
                <li><Link to="/settings" className="hover:text-[color:var(--color-tag)] transition-colors">Profile Settings</Link></li>
                <li><Link to="/settings/notifications" className="hover:text-[color:var(--color-tag)] transition-colors">Notification Preferences</Link></li>
                <li><Link to="/saved-searches" className="hover:text-[color:var(--color-tag)] transition-colors">Saved Searches</Link></li>
              </ul>
            </div>

            <div>
              <h4 className="text-[0.7rem] font-semibold uppercase tracking-[0.16em] text-[color:var(--text-muted)] mb-5">
                Community
              </h4>
              <ul className="space-y-3.5 text-sm text-[color:var(--text-muted)]">
                <li><Link to="/register" className="hover:text-[color:var(--color-tag)] transition-colors">Join ReNest</Link></li>
                <li><Link to="/requests" className="hover:text-[color:var(--color-tag)] transition-colors">Post a Request</Link></li>
                <li><Link to="/browse" className="hover:text-[color:var(--color-tag)] transition-colors">Browse Listings</Link></li>
              </ul>
            </div>
          </div>

          <div className="mt-12 pt-8 border-t border-[color:var(--color-line-strong)] flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-[color:var(--text-muted)]">Copyright © 2026 ReNest. All rights reserved.</p>
            <div className="flex items-center gap-4 text-xs text-[color:var(--text-muted)]">
              <Link to="/privacy" className="hover:text-[color:var(--color-tag)] transition-colors">Privacy</Link>
              <Link to="/privacy#terms" className="hover:text-[color:var(--color-tag)] transition-colors">Terms</Link>
              <span>Built for campus communities everywhere.</span>
            </div>
          </div>
        </div>
      </footer>
      </div>
    </div>
  );
}
