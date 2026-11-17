from django.urls import path

from .views import (
    DonationHubDetailView,
    DonationHubListView,
    HubManagerUpdateView,
    MyManagedHubsView,
)

urlpatterns = [
    path("hubs", DonationHubListView.as_view(), name="hubs-list"),
    path("hubs/mine", MyManagedHubsView.as_view(), name="hubs-mine"),
    path("hubs/<int:pk>", DonationHubDetailView.as_view(), name="hubs-detail"),
    path("hubs/<int:pk>/manage", HubManagerUpdateView.as_view(), name="hubs-manage"),
]
