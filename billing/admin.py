from django.contrib import admin, messages

from . import services
from .models import (
    Payment, PaymentStatus, Plan, Referral, ReferralReward, Subscription, UsageCharge,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price", "currency", "interval",
                    "included_company_seats", "recurring", "is_active", "sort_order")
    list_editable = ("price", "included_company_seats", "recurring", "is_active", "sort_order")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "status", "plan", "auto_renew", "next_charge_at", "trial_end", "current_period_end", "access_until", "bonus_days")
    list_filter = ("auto_renew", "gateway", "gateway_status", "plan")
    search_fields = ("organization__ruc", "organization__name")
    list_select_related = ("organization", "plan")
    readonly_fields = ("access_until", "status", "days_left")
    actions = ("extender_30_dias",)

    @admin.action(description="Regalar 30 días")
    def extender_30_dias(self, request, queryset):
        for sub in queryset:
            sub.extend(30)
            sub.save(update_fields=["trial_end", "current_period_end", "bonus_days", "updated_at"])
        self.message_user(request, f"{queryset.count()} suscripciones extendidas.", messages.SUCCESS)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "subscription", "plan", "amount", "status", "kind", "gateway", "paid_at")
    list_filter = ("status", "kind", "gateway", "plan")
    search_fields = ("subscription__organization__ruc", "gateway_reference", "gateway_payment_id")
    readonly_fields = ("raw", "period_start", "period_end", "paid_at")
    actions = ("aprobar",)

    @admin.action(description="Aprobar (pago manual confirmado)")
    def aprobar(self, request, queryset):
        n = 0
        for payment in queryset.exclude(status=PaymentStatus.APPROVED):
            services.approve_payment(payment, gateway_payment_id="manual")
            n += 1
        self.message_user(request, f"{n} pagos aprobados.", messages.SUCCESS)


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("referrer", "referred", "created_at", "converted_at")
    search_fields = ("referrer__email", "referred__email")


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ("user", "days", "conversions_at_grant", "applied_to", "applied_at")
    actions = ("aplicar",)

    @admin.action(description="Aplicar pendientes")
    def aplicar(self, request, queryset):
        for r in queryset:
            services.apply_reward(r)


@admin.register(UsageCharge)
class UsageChargeAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "kind", "amount", "currency", "status", "created_by")
    list_filter = ("kind", "status", "currency")
    search_fields = ("organization__ruc", "organization__name", "reference")
    list_select_related = ("organization", "created_by")

    @admin.action(description="Marcar como cobrado")
    def marcar_cobrado(self, request, queryset):
        from .models import UsageChargeStatus
        n = queryset.update(status=UsageChargeStatus.SETTLED)
        self.message_user(request, f"{n} cargos marcados como cobrados.", messages.SUCCESS)

    actions = ("marcar_cobrado",)
