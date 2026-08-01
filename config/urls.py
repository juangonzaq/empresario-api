"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("sunat_mailbox.urls")),
    path("api/", include("suppliers.urls")),
    path("api/", include("remype.urls")),
    path("api/", include("ruc_profile.urls")),
    path("api/auth/", include("rest_framework.urls")),  # login for the browsable API
]
