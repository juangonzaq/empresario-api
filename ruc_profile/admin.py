import json

from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join

from suppliers.services.ruc_client import RucLookupError

from .models import LegalRepresentative, RucSection, RucSnapshot, WorkerHeadcount
from .services import RucProfileSynchronizer

RISK_FIELDS = (
    ("has_coactive_debt", "Deuda coactiva"),
    ("has_tax_omissions", "Omisiones tributarias"),
    ("has_probatory_acts", "Actas probatorias"),
    ("reactiva_peru_debt", "Reactiva Perú"),
    ("covid_guarantee_debt", "Garantías COVID-19"),
)


class RucSectionInline(admin.TabularInline):
    model = RucSection
    extra = 0
    can_delete = False
    fields = ("label", "has_data", "answer", "preview", "error")
    readonly_fields = fields
    max_num = 0

    @admin.display(description="Rows")
    def preview(self, obj: RucSection) -> str:
        if not obj.tables:
            return "—"
        return format_html_join(
            "", "<div><b>{}</b>: {} row(s)</div>",
            ((" / ".join(t.get("headers") or [])[:70], len(t.get("rows") or []))
             for t in obj.tables),
        )

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class LegalRepresentativeInline(admin.TabularInline):
    model = LegalRepresentative
    extra = 0
    can_delete = False
    fields = ("document_type", "document_number", "full_name", "role", "since")
    readonly_fields = fields
    max_num = 0

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class WorkerHeadcountInline(admin.TabularInline):
    model = WorkerHeadcount
    extra = 0
    can_delete = False
    fields = ("period", "workers", "pensioners", "service_providers")
    readonly_fields = fields
    max_num = 0

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(RucSnapshot)
class RucSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "ruc", "business_name", "captured_on", "standing", "risk", "worker_count",
        "changed",
    )
    list_filter = (
        "has_risk_signals", "changed", "succeeded", "status", "condition",
        "has_coactive_debt", "has_tax_omissions", "captured_on",
    )
    search_fields = ("ruc", "business_name", "trade_name")
    date_hierarchy = "captured_on"
    inlines = (RucSectionInline, LegalRepresentativeInline, WorkerHeadcountInline)
    readonly_fields = tuple(
        field.name for field in RucSnapshot._meta.fields
    ) + ("raw_sections",)
    actions = ("capture_now",)

    @admin.display(description="Estado")
    def standing(self, obj: RucSnapshot) -> str:
        if not obj.succeeded:
            return format_html('<span style="color:#777">capture failed</span>')
        healthy = obj.status == "ACTIVO" and obj.condition == "HABIDO"
        return format_html(
            '<b style="color:{}">{}</b> / {}',
            "#146c2e" if healthy else "#b3261e", obj.status or "—", obj.condition or "—",
        )

    @admin.display(description="Alertas")
    def risk(self, obj: RucSnapshot) -> str:
        flagged = [label for field, label in RISK_FIELDS if getattr(obj, field)]
        if not flagged:
            return format_html('<span style="color:#146c2e">sin alertas</span>')
        return format_html('<b style="color:#b3261e">{}</b>', ", ".join(flagged))

    @admin.display(description="Sections (raw)")
    def raw_sections(self, obj: RucSnapshot) -> str:
        payload = {s.key: s.tables for s in obj.sections.all() if s.tables}
        return format_html(
            "<pre style='max-height:400px;overflow:auto'>{}</pre>",
            json.dumps(payload, ensure_ascii=False, indent=2)[:20000],
        )

    @admin.action(description="Capture these RUC profiles from SUNAT again")
    def capture_now(self, request, queryset) -> None:
        rucs = sorted(set(queryset.values_list("ruc", flat=True)))
        try:
            result = RucProfileSynchronizer().run(rucs, max_age_days=None)
        except RucLookupError as exc:
            self.message_user(request, f"Capture failed: {exc}", messages.ERROR)
            return
        level = messages.WARNING if result.failed else messages.SUCCESS
        self.message_user(request, f"RUC profile capture: {result}", level=level)

    def has_add_permission(self, request) -> bool:
        return False
