function cacheRouteLoader(loader) {
  let promise;

  return () => {
    if (!promise) {
      promise = loader().catch((error) => {
        promise = null;
        throw error;
      });
    }
    return promise;
  };
}

export const routeLoaders = {
  campusAnalytics: cacheRouteLoader(() => import("../pages/CampusAnalyticsPage")),
  campusLanding: cacheRouteLoader(() => import("../pages/CampusLandingPage")),
  campusOnboard: cacheRouteLoader(() => import("../pages/CampusOnboardPage")),
  leaderboard: cacheRouteLoader(() => import("../pages/LeaderboardPage")),
  assistant: cacheRouteLoader(() => import("../pages/AssistantPage")),
  hubDispatch: cacheRouteLoader(() => import("../pages/HubDispatchPage")),
  savedSearches: cacheRouteLoader(() => import("../pages/SavedSearchesPage")),
  notificationPreferences: cacheRouteLoader(() => import("../pages/NotificationPreferencesPage")),
  partnerPortal: cacheRouteLoader(() => import("../pages/PartnerPortalPage")),
  browse: cacheRouteLoader(() => import("../pages/BrowsePage")),
  clearoutBoard: cacheRouteLoader(() => import("../pages/ClearoutBoardPage")),
  dashboard: cacheRouteLoader(() => import("../pages/DashboardPage")),
  handoffHub: cacheRouteLoader(() => import("../pages/HandoffHubPage")),
  hubManager: cacheRouteLoader(() => import("../pages/HubManagerPage")),
  hubs: cacheRouteLoader(() => import("../pages/HubsPage")),
  listingDetail: cacheRouteLoader(() => import("../pages/ListingDetailPage")),
  listingForm: cacheRouteLoader(() => import("../pages/ListingFormPage")),
  moveOutPlan: cacheRouteLoader(() => import("../pages/MoveOutPlanPage")),
  myListings: cacheRouteLoader(() => import("../pages/MyListingsPage")),
  myReservations: cacheRouteLoader(() => import("../pages/MyReservationsPage")),
  notifications: cacheRouteLoader(() => import("../pages/NotificationsPage")),
  publishQueue: cacheRouteLoader(() => import("../pages/PublishQueuePage")),
  requests: cacheRouteLoader(() => import("../pages/RequestsPage")),
  saved: cacheRouteLoader(() => import("../pages/SavedPage")),
  scan: cacheRouteLoader(() => import("../pages/ScanPage")),
  scanStudio: cacheRouteLoader(() => import("../pages/ScanStudioPage")),
  settings: cacheRouteLoader(() => import("../pages/SettingsPage")),
  privacy: cacheRouteLoader(() => import("../pages/PrivacyPage")),
  trust: cacheRouteLoader(() => import("../pages/TrustPage")),
  riskOverview: cacheRouteLoader(() => import("../pages/RiskOverviewPage")),
  riskTransactions: cacheRouteLoader(() => import("../pages/RiskTransactionsPage")),
  riskTransactionDetail: cacheRouteLoader(() => import("../pages/RiskTransactionDetailPage")),
  fraudLab: cacheRouteLoader(() => import("../pages/FraudLabPage")),
  riskInvestigator: cacheRouteLoader(() => import("../pages/RiskInvestigatorPage")),
  agentSandbox: cacheRouteLoader(() => import("../pages/AgentSandboxPage")),
  riskHealth: cacheRouteLoader(() => import("../pages/RiskHealthPage")),
};

export function prefetchRoute(routeKey) {
  const loader = routeLoaders[routeKey];
  if (!loader) {
    return Promise.resolve(null);
  }
  return loader();
}
