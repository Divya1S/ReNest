from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .views import (
    FrontendAppView,
    HealthDlqView,
    HealthLiveView,
    HealthReadyView,
    HealthWorkerView,
    MaintenanceRunView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("listings.urls")),
    path("api/", include("hubs.urls")),
    path("api/concierge/", include("concierge.urls")),
    path("api/risk/", include("risk.urls")),
    path("api/health/live", HealthLiveView.as_view(), name="health-live"),
    path("api/health/ready", HealthReadyView.as_view(), name="health-ready"),
    path("api/health/worker", HealthWorkerView.as_view(), name="health-worker"),
    path("api/health/dlq", HealthDlqView.as_view(), name="health-dlq"),
    path("api/internal/maintenance/", MaintenanceRunView.as_view(), name="internal-maintenance"),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
]

# Media serving. With object storage configured (AWS_S3_BUCKET_NAME) uploads
# are served by S3/R2 and Django never sees these URLs. Without it, uploaded
# photos live on the local disk and something has to serve them: WhiteNoise
# only indexes files that exist at startup, so it cannot serve user uploads.
# Django's static serve view is used instead: adequate for the single-service
# free-tier deploy this project targets, and the reason the README
# recommends S3 or Cloudflare R2 (both have free tiers) for anything larger.
if settings.DEBUG or not settings.AWS_S3_BUCKET_NAME:
    # django.conf.urls.static.static() is a no-op outside DEBUG, so the route is
    # declared directly. serve() resolves paths through safe_join, so it cannot
    # escape MEDIA_ROOT.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]

if settings.SERVE_FRONTEND:
    # SPA catch-all — every non-API, non-admin, non-file route gets index.html
    # so client-side routing works on hard refresh and deep links.
    urlpatterns += [
        re_path(r"^(?!api/|admin/|static/|media/).*$", FrontendAppView.as_view(), name="spa"),
    ]
