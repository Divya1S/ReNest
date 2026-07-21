import { MotionConfig } from "framer-motion";
import React, { lazy, useEffect } from "react";
import {
  createBrowserRouter,
  createRoutesFromElements,
  Navigate,
  Outlet,
  Route,
  RouterProvider,
} from "react-router-dom";
import { Toaster } from "sonner";

import AppFrame from "./components/AppFrame";
import RequireAuth from "./components/RequireAuth";
import { AuthProvider } from "./context/AuthContext";
import { apiFetch } from "./lib/api";
import { routeLoaders } from "./lib/routeLoaders";
import { initOfflineReplay } from "./lib/scanQueue";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import NotFoundPage from "./pages/NotFoundPage";
import PasswordResetConfirmPage from "./pages/PasswordResetConfirmPage";
import PasswordResetPage from "./pages/PasswordResetPage";
import RegisterPage from "./pages/RegisterPage";
import UnsubscribePage from "./pages/UnsubscribePage";
import VerifyEmailConfirmPage from "./pages/VerifyEmailConfirmPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";

// Lazy-loaded — split into separate chunks so the initial JS bundle stays lean.
// Suspense + ErrorBoundary are applied in AppFrame around the Outlet.
const BrowsePage = lazy(routeLoaders.browse);
const CampusAnalyticsPage = lazy(routeLoaders.campusAnalytics);
const CampusLandingPage = lazy(routeLoaders.campusLanding);
const LeaderboardPage = lazy(routeLoaders.leaderboard);
const AssistantPage = lazy(routeLoaders.assistant);
const HubDispatchPage = lazy(routeLoaders.hubDispatch);
const SavedSearchesPage = lazy(routeLoaders.savedSearches);
const NotificationPreferencesPage = lazy(routeLoaders.notificationPreferences);
const PartnerPortalPage = lazy(routeLoaders.partnerPortal);
const ClearoutBoardPage = lazy(routeLoaders.clearoutBoard);
const DashboardPage = lazy(routeLoaders.dashboard);
const HandoffHubPage = lazy(routeLoaders.handoffHub);
const HubManagerPage = lazy(routeLoaders.hubManager);
const HubsPage = lazy(routeLoaders.hubs);
const ListingDetailPage = lazy(routeLoaders.listingDetail);
const ListingFormPage = lazy(routeLoaders.listingForm);
const MoveOutPlanPage = lazy(routeLoaders.moveOutPlan);
const MyListingsPage = lazy(routeLoaders.myListings);
const MyReservationsPage = lazy(routeLoaders.myReservations);
const NotificationsPage = lazy(routeLoaders.notifications);
const PublishQueuePage = lazy(routeLoaders.publishQueue);
const RequestsPage = lazy(routeLoaders.requests);
const SavedPage = lazy(routeLoaders.saved);
const ScanPage = lazy(routeLoaders.scan);
const ScanStudioPage = lazy(routeLoaders.scanStudio);
const SettingsPage = lazy(routeLoaders.settings);
const PrivacyPage = lazy(routeLoaders.privacy);
const TrustPage = lazy(routeLoaders.trust);

function ProtectedLayout() {
  return <Outlet />;
}

const router = createBrowserRouter(
  createRoutesFromElements(
    <>
      <Route element={<AppFrame />}>
        <Route index element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/password-reset" element={<PasswordResetPage />} />
        <Route path="/password-reset/confirm" element={<PasswordResetConfirmPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/verify-email/confirm" element={<VerifyEmailConfirmPage />} />
        <Route path="/unsubscribe" element={<UnsubscribePage />} />
        <Route path="/hubs" element={<HubsPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="/campus/:slug" element={<CampusLandingPage />} />
        {/* Public marketplace — browsing and listing details need no account;
            reserve/save/report actions inside are gated on auth in-page */}
        <Route path="/browse" element={<BrowsePage />} />
        <Route path="/listings/:listingId" element={<ListingDetailPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<ProtectedLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/requests" element={<RequestsPage />} />
            <Route path="/notifications" element={<NotificationsPage />} />
            <Route path="/trust" element={<TrustPage />} />
            <Route path="/scan" element={<ScanPage />} />
            <Route path="/scan/:sessionId" element={<ScanStudioPage />} />
            <Route path="/plan/:sessionId" element={<MoveOutPlanPage />} />
            <Route path="/clearout/:sessionId" element={<ClearoutBoardPage />} />
            <Route path="/publish/:sessionId" element={<PublishQueuePage />} />
            <Route path="/saved" element={<SavedPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/listings/new" element={<ListingFormPage />} />
            <Route path="/listings/:listingId/edit" element={<ListingFormPage />} />
            <Route path="/my-listings" element={<MyListingsPage />} />
            <Route path="/my-reservations" element={<MyReservationsPage />} />
            <Route path="/handoffs/:reservationId" element={<HandoffHubPage />} />
            <Route path="/hubs/:hubId/manage" element={<HubManagerPage />} />
            <Route path="/analytics" element={<CampusAnalyticsPage />} />
            <Route path="/leaderboard" element={<LeaderboardPage />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/dispatch" element={<HubDispatchPage />} />
            <Route path="/saved-searches" element={<SavedSearchesPage />} />
            <Route path="/settings/notifications" element={<NotificationPreferencesPage />} />
            <Route path="/partner" element={<PartnerPortalPage />} />
          </Route>
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="/app" element={<Navigate to="/dashboard" replace />} />
    </>,
  ),
);

export default function App() {
  useEffect(() => {
    return initOfflineReplay(apiFetch, (count) => {
      import("sonner").then(({ toast }) =>
        toast.success(`Synced ${count} queued item${count !== 1 ? "s" : ""} from offline mode`)
      );
    });
  }, []);

  return (
    // reducedMotion="user": every framer-motion animation collapses to a
    // no-op for people with prefers-reduced-motion set — one switch, app-wide.
    <MotionConfig reducedMotion="user">
      <AuthProvider>
        <Toaster richColors position="top-right" />
        <RouterProvider router={router} />
      </AuthProvider>
    </MotionConfig>
  );
}
