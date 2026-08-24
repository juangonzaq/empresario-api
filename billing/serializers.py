from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from .models import Payment, Plan, UsageCharge


class PlanSerializer(serializers.ModelSerializer):
    months = serializers.IntegerField(read_only=True)
    monthly_equivalent = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    savings_pct = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = ("code", "name", "description", "price", "currency", "interval",
                  "recurring", "months", "monthly_equivalent", "savings_pct")

    def get_savings_pct(self, plan: Plan) -> int | None:
        """Cuánto se ahorra frente a pagar el mensual durante el mismo tiempo."""
        monthly = self.context.get("monthly")
        if plan.months == 1 or monthly is None or monthly.price <= 0:
            return None
        full = monthly.price * plan.months
        return int(((full - plan.price) / full * 100).quantize(Decimal("1")))


class PaymentSerializer(serializers.ModelSerializer):
    plan = serializers.CharField(source="plan.code", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)

    class Meta:
        model = Payment
        fields = ("id", "plan", "plan_name", "amount", "currency", "status", "kind", "gateway",
                  "checkout_url", "paid_at", "period_start", "period_end", "created_at")
        read_only_fields = fields


class CheckoutSerializer(serializers.Serializer):
    plan = serializers.SlugField()

    def validate_plan(self, value: str) -> Plan:
        plan = Plan.objects.filter(code=value, is_active=True).first()
        if plan is None:
            raise serializers.ValidationError("Ese plan no existe o ya no está a la venta.")
        return plan


class UsageChargeSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = UsageCharge
        fields = ("id", "kind", "kind_label", "amount", "currency", "description",
                  "status", "status_label", "created_at")
