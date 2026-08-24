from django.urls import path

from .views import (
    CancelAutoRenewView, ChargesView, CheckoutView, MercadoPagoWebhookView,
    PaymentsView, PlansView, ReferralsView, SubscriptionView,
)

app_name = "billing"

urlpatterns = [
    path("billing/plans/", PlansView.as_view(), name="plans"),
    path("billing/subscription/", SubscriptionView.as_view(), name="subscription"),
    path("billing/checkout/", CheckoutView.as_view(), name="checkout"),
    path("billing/cancel/", CancelAutoRenewView.as_view(), name="cancel"),
    path("billing/payments/", PaymentsView.as_view(), name="payments"),
    path("billing/charges/", ChargesView.as_view(), name="charges"),
    path("billing/referrals/", ReferralsView.as_view(), name="referrals"),
    path("billing/webhook/mercadopago/", MercadoPagoWebhookView.as_view(), name="mp-webhook"),
]
