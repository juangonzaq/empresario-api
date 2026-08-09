from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    LoginView, LogoutView, OrganizationListView, PasswordChangeView,
    PasswordResetConfirmView, PasswordResetRequestView, ProfileView,
    RegisterView, ResendVerificationView, SunatConnectionView, VerifyEmailView,
)

app_name = "accounts"

urlpatterns = [
    # Abiertos: los que por definición no pueden exigir sesión.
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/password/reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path("auth/password/reset/confirm/", PasswordResetConfirmView.as_view(),
         name="password-reset-confirm"),
    # Con sesión.
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/resend-verification/", ResendVerificationView.as_view(),
         name="resend-verification"),
    path("auth/password/change/", PasswordChangeView.as_view(), name="password-change"),
    path("me/", ProfileView.as_view(), name="profile"),
    path("organizations/", OrganizationListView.as_view(), name="organizations"),
    path("organizations/sunat/", SunatConnectionView.as_view(), name="sunat-connection"),
]
