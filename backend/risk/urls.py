from django.urls import path

from . import views

app_name = "risk"

urlpatterns = [
    # Payments
    path("payments", views.PaymentListCreateView.as_view(), name="payments"),
    path("payments/<str:public_id>", views.PaymentDetailView.as_view(), name="payment-detail"),
    path("payments/<str:public_id>/review", views.PaymentReviewView.as_view(), name="payment-review"),
    # Dashboard read models
    path("overview", views.OverviewView.as_view(), name="overview"),
    path("feed", views.FeedView.as_view(), name="feed"),
    path("distribution", views.DistributionView.as_view(), name="distribution"),
    path("policy", views.PolicyView.as_view(), name="policy"),
    path("rules", views.RulesView.as_view(), name="rules"),
    path("health", views.SystemHealthView.as_view(), name="health"),
    path("metrics", views.MetricsView.as_view(), name="metrics"),
    path("metrics/prometheus", views.PrometheusView.as_view(), name="metrics-prometheus"),
    # Fraud Lab
    path("scenarios", views.ScenarioListView.as_view(), name="scenarios"),
    path("simulations", views.SimulationListCreateView.as_view(), name="simulations"),
    path("simulations/<str:public_id>", views.SimulationDetailView.as_view(), name="simulation-detail"),
    path("simulations/<str:public_id>/rescore", views.SimulationRescoreView.as_view(), name="simulation-rescore"),
    # Investigator
    path("investigator", views.InvestigatorInfoView.as_view(), name="investigator-info"),
    path("investigations", views.InvestigationListCreateView.as_view(), name="investigations"),
    path("investigations/<str:public_id>", views.InvestigationDetailView.as_view(), name="investigation-detail"),
    # Agents
    path("agents", views.AgentListCreateView.as_view(), name="agents"),
    path("agents/<str:public_id>", views.AgentDetailView.as_view(), name="agent-detail"),
    path("agents/<str:public_id>/transactions", views.AgentTransactionView.as_view(), name="agent-transactions"),
    # Traceability
    path("events", views.EventListView.as_view(), name="events"),
    path("events/dispatch", views.EventDispatchView.as_view(), name="events-dispatch"),
    path("events/<str:event_id>/replay", views.EventReplayView.as_view(), name="event-replay"),
    path("audit", views.AuditListView.as_view(), name="audit"),
]
