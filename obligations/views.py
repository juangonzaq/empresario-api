"""Compliance / obligations API.

Reads use :class:`OrganizationAPIView`; writes use
:class:`ManagedOrganizationAPIView` (GET still allowed for any member, writes
require owner/contador). Everything is scoped to ``request.ruc`` — obligations
of one company never leak into another's screen.
"""

from __future__ import annotations

from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.tenancy import ManagedOrganizationAPIView, OrganizationAPIView

from . import enums
from .models import ComplianceAction, CompanyObligation
from .serializers import (
    ActionCreateSerializer, ActionSerializer, ActionUpdateSerializer,
    EvidenceCreateSerializer, EvidenceSerializer, ObligationDetailSerializer,
    ObligationListSerializer, ObligationUpdateSerializer,
)
from .services.deadlines import time_state
from .services.engine import evaluate_company
from .services.overview import build_overview
from .services.snapshots import create_compliance_snapshot


def _resolve_owner(organization, email: str):
    """A member of this company by email, or None. Owners must belong here."""
    from accounts.models import Membership

    email = (email or "").strip().lower()
    if not email:
        return None
    membership = (
        Membership.objects.filter(organization=organization, user__email=email, is_active=True)
        .select_related("user").first()
    )
    return membership.user if membership else None


def _apply_filters(qs, params, today):
    domain = params.get("domain")
    if domain:
        qs = qs.filter(rule__domain__code=domain)
    for field in ("obligation_type", "frequency"):
        value = params.get(field)
        if value:
            qs = qs.filter(**{f"rule__{field}": value})
    for field in ("compliance_status", "workflow_status", "verification_status",
                  "applicability_status", "severity"):
        value = params.get(field)
        if value:
            qs = qs.filter(**{field: value})
    owner = params.get("owner")
    if owner:
        qs = qs.filter(owner__email__iexact=owner)
    due_before = params.get("due_before")
    if due_before:
        qs = qs.filter(due_date__lte=due_before)
    due_after = params.get("due_after")
    if due_after:
        qs = qs.filter(due_date__gte=due_after)
    has_evidence = params.get("has_evidence")
    if has_evidence in ("true", "1"):
        qs = qs.filter(evidence_count__gt=0)
    elif has_evidence in ("false", "0"):
        qs = qs.filter(evidence_count=0)
    search = params.get("search")
    if search:
        qs = qs.filter(rule__title__icontains=search) | qs.filter(rule__code__icontains=search)
    return qs


class ComplianceOverviewView(OrganizationAPIView):
    """The whole screen in one payload. Recomputes only if stale."""

    def get(self, request: Request) -> Response:
        force = request.query_params.get("force") in ("1", "true")
        return Response(build_overview(request.organization, force=force))


class ObligationListView(OrganizationAPIView):
    def get(self, request: Request) -> Response:
        today = timezone.localdate()
        qs = (
            CompanyObligation.objects.filter(account_ruc=request.ruc)
            .select_related("rule__domain", "owner")
            .annotate(evidence_count=Count("evidence"))
        )
        qs = _apply_filters(qs, request.query_params, today)

        rows = list(qs)
        # Estado temporal (vencida/próxima) se calcula, no se guarda: se filtra aquí.
        state = request.query_params.get("state")
        if state:
            rows = [ob for ob in rows if time_state(ob.due_date, today) == state]

        data = ObligationListSerializer(rows, many=True).data
        return Response({"count": len(data), "results": data})


class ObligationDetailView(ManagedOrganizationAPIView):
    """Read the full obligation, or set its human-owned fields (owner, workflow)."""

    def _get(self, request: Request, obligation_id: str):
        return (
            CompanyObligation.objects
            .filter(id=obligation_id, account_ruc=request.ruc)
            .select_related("rule__domain", "owner")
            .prefetch_related("evidence", "actions", "assessments")
            .first()
        )

    def get(self, request: Request, obligation_id: str) -> Response:
        ob = self._get(request, obligation_id)
        if ob is None:
            return Response({"detail": "No existe esa obligación."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ObligationDetailSerializer(ob).data)

    def patch(self, request: Request, obligation_id: str) -> Response:
        ob = self._get(request, obligation_id)
        if ob is None:
            return Response({"detail": "No existe esa obligación."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ObligationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updates = []
        if "workflow_status" in data:
            ob.workflow_status = data["workflow_status"]
            updates.append("workflow_status")
            if data["workflow_status"] == enums.WorkflowStatus.COMPLETED and ob.completed_at is None:
                ob.completed_at = timezone.now()
                updates.append("completed_at")
        if "owner_email" in data:
            ob.owner = _resolve_owner(request.organization, data["owner_email"])
            updates.append("owner")
        if updates:
            ob.save(update_fields=[*updates, "updated_at"])
        # Un cambio humano puede alterar el veredicto (marcar completada); reevaluar.
        evaluate_company(request.organization)
        ob.refresh_from_db()
        return Response(ObligationDetailSerializer(ob).data)


class ObligationEvidenceView(ManagedOrganizationAPIView):
    """Attach evidence (a pointer to data that already exists, or a URL/label)."""

    def post(self, request: Request, obligation_id: str) -> Response:
        ob = CompanyObligation.objects.filter(id=obligation_id, account_ruc=request.ruc).first()
        if ob is None:
            return Response({"detail": "No existe esa obligación."}, status=status.HTTP_404_NOT_FOUND)
        serializer = EvidenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        evidence = ob.evidence.create(
            evidence_type=data.get("evidence_type", enums.EvidenceType.DOCUMENT),
            label=data.get("label", ""),
            url=data.get("url", ""),
            reference=data.get("reference", {}) or {},
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            notes=data.get("notes", ""),
            verification_status=enums.VerificationStatus.SELF_REPORTED,
            verified_by=request.user,
            verified_at=timezone.now(),
        )
        evaluate_company(request.organization)
        return Response(EvidenceSerializer(evidence).data, status=status.HTTP_201_CREATED)


class ObligationActionsView(ManagedOrganizationAPIView):
    def post(self, request: Request, obligation_id: str) -> Response:
        ob = CompanyObligation.objects.filter(id=obligation_id, account_ruc=request.ruc).first()
        if ob is None:
            return Response({"detail": "No existe esa obligación."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ActionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        action = ob.actions.create(
            title=data["title"],
            description=data.get("description", ""),
            priority=data.get("priority", enums.ActionPriority.MEDIUM),
            due_date=data.get("due_date"),
            owner=_resolve_owner(request.organization, data.get("owner_email", "")),
            created_by=request.user,
        )
        return Response(ActionSerializer(action).data, status=status.HTTP_201_CREATED)


class ActionDetailView(ManagedOrganizationAPIView):
    def patch(self, request: Request, action_id: str) -> Response:
        action = (
            ComplianceAction.objects
            .filter(id=action_id, company_obligation__account_ruc=request.ruc).first()
        )
        if action is None:
            return Response({"detail": "No existe esa acción."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ActionUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        for field in ("status", "priority", "title", "description", "due_date"):
            if field in data:
                setattr(action, field, data[field])
        if "owner_email" in data:
            action.owner = _resolve_owner(request.organization, data["owner_email"])
        if data.get("status") == enums.ActionStatus.DONE and action.completed_at is None:
            action.completed_at = timezone.now()
        action.save()
        return Response(ActionSerializer(action).data)


class RecalculateView(ManagedOrganizationAPIView):
    def post(self, request: Request) -> Response:
        result = evaluate_company(request.organization, user=request.user, force=True)
        create_compliance_snapshot(request.ruc)
        return Response(result)
