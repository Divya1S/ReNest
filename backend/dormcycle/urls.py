from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .views import FrontendAppView, HealthDlqView, HealthLiveView, HealthReadyView, HealthWorkerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("listings.urls")),
    path("api/", include("hubs.urls")),
    path("api/concierge/", include("concierge.urls")),
    path("api/health/live", HealthLiveView.as_view(), name="health-live"),
    path("api/health/ready", HealthReadyView.as_view(), name="health-ready"),
    path("api/health/worker", HealthWorkerView.as_view(), name="health-worker"),
    path("api/health/dlq", HealthDlqView.as_view(), name="health-dlq"),
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.SERVE_FRONTEND:
    # SPA catch-all — every non-API, non-admin, non-file route gets index.html
    # so client-side routing works on hard refresh and deep links.
    urlpatterns += [
        re_path(r"^(?!api/|admin/|static/|media/).*$", FrontendAppView.as_view(), name="spa"),
    ]
