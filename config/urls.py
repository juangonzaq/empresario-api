"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

from accounts.admin_guard import admin_login

urlpatterns = [
    # Sombrea el login del admin ANTES de montar el sitio: reCAPTCHA + código
    # al correo (accounts.admin_guard). El admin redirige aquí solo.
    path("admin/login/", admin_login, name="admin-otp-login"),
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("sync.urls")),
    path("api/", include("sunat_mailbox.urls")),
    path("api/", include("suppliers.urls")),
    path("api/", include("remype.urls")),
    path("api/", include("ruc_profile.urls")),
    path("api/", include("sunafil.urls")),
    path("api/", include("compliance_profile.urls")),
    path("api/", include("sunat_itf.urls")),
    path("api/", include("sunat_cpe.urls")),
    path("api/", include("sunat_rhe.urls")),
    path("api/", include("sensor_sunat.urls")),
    path("api/", include("sunat_intel.urls")),
    path("api/", include("finance_analytics.urls")),
    path("api/", include("afpnet.urls")),
    path("api/", include("colaboradores.urls")),
    path("api/", include("payroll.urls")),
    path("api/", include("financials.urls")),
    path("api/", include("leads.urls")),
    path("api/", include("billing.urls")),
    path("api/", include("reconciliation.urls")),
    path("api/", include("obligations.urls")),
    path("api/", include("sunat_declaraciones.urls")),
    path("api/auth/", include("rest_framework.urls")),  # login for the browsable API
]
