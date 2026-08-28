from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .search_views import GlobalSearchView
from .status_views import CompanyStatusView
from .views import (
    BusinessProfileView, LoginView, LogoutView, OrganizationListView,
    PasswordChangeView, PasswordResetConfirmView, PasswordResetRequestView,
    ProfileView, ConsentDocumentView, RegisterView, ResendVerificationView,
    SunatAuthorizationView, SunatConnectionView, SunatPortalView, SunafilPortalView,
    TeamInvitationView, TeamMemberView, TeamView, VerifyEmailView,
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
    path("organizations/business-profile/", BusinessProfileView.as_view(), name="business-profile"),
    path("organizations/members/", TeamView.as_view(), name="team"),
    path("organizations/members/<uuid:member_id>/", TeamMemberView.as_view(), name="team-member"),
    path("organizations/invitations/<uuid:invitation_id>/", TeamInvitationView.as_view(), name="team-invitation"),
    path("organizations/sunat/", SunatConnectionView.as_view(), name="sunat-connection"),
    path("organizations/sunat/portal/", SunatPortalView.as_view(), name="sunat-portal"),
    path("organizations/sunafil/portal/", SunafilPortalView.as_view(), name="sunafil-portal"),
    path("organizations/sunat/authorization/", SunatAuthorizationView.as_view(), name="sunat-authorization"),
    path("legal/autorizacion-sunat/", ConsentDocumentView.as_view(), name="consent-document"),
    path("status/", CompanyStatusView.as_view(), name="company-status"),
    path("search/", GlobalSearchView.as_view(), name="global-search"),
]
