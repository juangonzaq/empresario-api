"""Miembros de una empresa: invitar, aceptar, cambiar rol y quitar.

Un usuario invitado queda atado a **una** empresa (una ``Membership``) y solo
ve esa: el alcance se resuelve siempre desde sus memberships en
``accounts.tenancy``. Aquí no se toca ninguna otra empresa.

Reglas que se repiten:

* Reactivar antes que duplicar. Una ``Membership`` es única por (usuario,
  empresa); si ya existía —aunque estuviera dada de baja— se reusa.
* Nunca dejar a una empresa sin titular. Quitar o degradar al último ``OWNER``
  se rechaza arriba (en las vistas), donde hay contexto para explicarlo.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..models import Invitation, InvitationStatus, Membership, Organization, Role, User


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def attach_member(
    organization: Organization, user: User, role: str, invited_by: User | None = None
) -> Membership:
    """Da acceso a ``user`` sobre ``organization`` con ``role``. Si ya tenía una
    membership (activa o no), la reactiva y actualiza el rol en vez de duplicar."""
    membership, created = Membership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={"role": role, "invited_by": invited_by},
    )
    if not created:
        membership.role = role
        membership.is_active = True
        if invited_by and membership.invited_by is None:
            membership.invited_by = invited_by
        membership.save(update_fields=["role", "is_active", "invited_by", "updated_at"])
    return membership


@transaction.atomic
def invite_member(
    organization: Organization, email: str, role: str, invited_by: User
) -> dict:
    """Invita por correo. Si el correo ya tiene cuenta, entra de inmediato; si
    no, la invitación queda pendiente hasta que esa persona se registre.

    Devuelve ``{"kind": "member"|"invitation", ...}`` para que la vista sepa
    qué pasó, o lanza ``ValueError`` con un mensaje presentable."""
    email = _norm_email(email)
    if not email:
        raise ValueError("Indica el correo de la persona que quieres invitar.")

    user = User.objects.filter(email=email).first()
    if user:
        existing = Membership.objects.filter(
            user=user, organization=organization, is_active=True
        ).first()
        if existing:
            raise ValueError("Esa persona ya tiene acceso a esta empresa.")
        membership = attach_member(organization, user, role, invited_by)
        # Si había una invitación pendiente para ese correo, queda saldada.
        Invitation.objects.filter(
            organization=organization, email=email, status=InvitationStatus.PENDING
        ).update(
            status=InvitationStatus.ACCEPTED, accepted_at=timezone.now(),
            accepted_user=user, updated_at=timezone.now(),
        )
        return {"kind": "member", "membership": membership}

    invitation, created = Invitation.objects.get_or_create(
        organization=organization, email=email, status=InvitationStatus.PENDING,
        defaults={"role": role, "invited_by": invited_by},
    )
    if not created:
        invitation.role = role
        invitation.invited_by = invited_by
        invitation.save(update_fields=["role", "invited_by", "updated_at"])
    return {"kind": "invitation", "invitation": invitation}


def accept_pending_invitations(user: User) -> int:
    """Convierte en acceso las invitaciones pendientes al correo de ``user``.

    Se llama en cada inicio de sesión, así que un correo invitado antes de
    tener cuenta encuentra sus empresas listas apenas entra. Devuelve cuántas
    se aplicaron."""
    pending = list(
        Invitation.objects.filter(
            email=_norm_email(user.email), status=InvitationStatus.PENDING
        ).select_related("organization")
    )
    applied = 0
    for invitation in pending:
        with transaction.atomic():
            attach_member(
                invitation.organization, user, invitation.role, invitation.invited_by
            )
            invitation.status = InvitationStatus.ACCEPTED
            invitation.accepted_at = timezone.now()
            invitation.accepted_user = user
            invitation.save(
                update_fields=["status", "accepted_at", "accepted_user", "updated_at"]
            )
            applied += 1
    return applied


def owners_count(organization: Organization) -> int:
    return Membership.objects.filter(
        organization=organization, is_active=True, role=Role.OWNER
    ).count()


def member_payload(membership: Membership, *, you: User | None = None) -> dict:
    return {
        "id": str(membership.id),
        "role": membership.role,
        "role_label": membership.get_role_display(),
        "is_active": membership.is_active,
        "is_you": bool(you and membership.user_id == you.id),
        "user": {
            "email": membership.user.email,
            "full_name": membership.user.full_name,
        },
        "invited_by": membership.invited_by.email if membership.invited_by else None,
        "created_at": membership.created_at,
    }


def invitation_payload(invitation: Invitation) -> dict:
    return {
        "id": str(invitation.id),
        "email": invitation.email,
        "role": invitation.role,
        "role_label": invitation.get_role_display(),
        "status": invitation.status,
        "invited_by": invitation.invited_by.email if invitation.invited_by else None,
        "created_at": invitation.created_at,
    }


def team_payload(organization: Organization, *, you: User | None = None) -> dict:
    members = (
        Membership.objects.filter(organization=organization, is_active=True)
        .select_related("user", "invited_by").order_by("role", "user__email")
    )
    invitations = (
        Invitation.objects.filter(
            organization=organization, status=InvitationStatus.PENDING
        ).select_related("invited_by")
    )
    from billing.services import member_seat_summary

    return {
        "members": [member_payload(m, you=you) for m in members],
        "invitations": [invitation_payload(i) for i in invitations],
        "seats": member_seat_summary(organization),
    }
