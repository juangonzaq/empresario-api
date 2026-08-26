"""API de cuentas: registro, sesión, perfil, empresas y conexión con SUNAT.

Es el único módulo con endpoints abiertos (``AllowAny``); todo lo demás del
proyecto exige bearer y una empresa atribuible. Los cuatro abiertos son los
que por definición no pueden pedir sesión: registro, verificación de correo,
inicio de sesión y recuperación de contraseña.
"""

from __future__ import annotations

import logging

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    BusinessProfile, Invitation, InvitationStatus, Membership, OneTimeToken,
    Organization, Role, SunatAuthorization, SunatConnectionStatus, SunatCredential,
    TokenPurpose, User,
)
from .serializers import (
    BusinessProfileSerializer, LoginSerializer, MemberInviteSerializer,
    MemberRoleSerializer, OrganizationCreateSerializer, OrganizationSerializer,
    PasswordChangeSerializer, PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer, RegisterSerializer,
    SunatConnectSerializer, SunatCredentialSerializer, UserSerializer,
    tokens_for,
)
from .services import consent, mail, sol_portal, team
from .throttles import (
    CorreoThrottle, LoginPorCuentaThrottle, LoginPorIpThrottle,
    RegistroThrottle, SunatConexionThrottle, SunatPortalThrottle,
)
from .tenancy import (
    HasOrganization, ManagedOrganizationAPIView, OrganizationAPIView, user_memberships,
)

logger = logging.getLogger(__name__)

# Respuesta común a registro y recuperación: idéntica exista o no la cuenta.
NEUTRAL_EMAIL_REPLY = {
    "detail": "Si el correo es válido, te enviamos un mensaje con los pasos a seguir."
}


def _session_payload(user: User) -> dict:
    # Al iniciar sesión se saldan las invitaciones pendientes a este correo:
    # quien fue invitado antes de tener cuenta encuentra sus empresas listas.
    team.accept_pending_invitations(user)
    from billing.services import seat_summary

    memberships = list(user_memberships(user))
    return {
        **tokens_for(user),
        "user": UserSerializer(user).data,
        "seats": seat_summary(user),
        "organizations": OrganizationSerializer(
            [m.organization for m in memberships],
            many=True,
            context={"roles": {m.organization_id: m.role for m in memberships}},
        ).data,
    }


class PublicAuthView(APIView):
    """Endpoint público del flujo de autenticación.

    Sin autenticadores a propósito: aquí nadie llega ya autenticado, llega a
    autenticarse. Con los autenticadores por defecto, una cookie de sesión del
    admin de Django en el mismo navegador hacía que SessionAuthentication
    exigiera CSRF en el POST y el login del frontend —que habla JWT, sin
    token CSRF— muriera con «CSRF Failed» antes de mirar las credenciales.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]


class RegisterView(PublicAuthView):
    throttle_classes = [RegistroThrottle, CorreoThrottle]

    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        already_existed = User.objects.filter(
            email=serializer.validated_data["email"]
        ).exists()
        user = serializer.save()

        if already_existed:
            # No se confirma ni se niega que el correo esté registrado. La
            # verdad va al buzón, que solo ve su dueño: se le explica que ya
            # tiene cuenta y se le deja el enlace de restablecer la clave por
            # si llegó aquí por haberla olvidado.
            mail.send_already_registered(user)
        else:
            mail.send_verification(user)
        return Response(NEUTRAL_EMAIL_REPLY, status=status.HTTP_202_ACCEPTED)


class VerifyEmailView(PublicAuthView):
    def post(self, request: Request) -> Response:
        token = (request.data.get("token") or "").strip()
        row = OneTimeToken.objects.usable().filter(
            token=token, purpose=TokenPurpose.EMAIL_VERIFICATION
        ).select_related("user").first()
        if row is None:
            return Response(
                {"detail": "El enlace no es válido o ya venció. Pide uno nuevo."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = row.user
        if not user.email_verified:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at", "updated_at"])
        row.consume()
        # Verificar el correo deja la sesión iniciada: es el paso natural
        # antes del onboarding y evita pedir la contraseña otra vez.
        return Response(_session_payload(user))


class ResendVerificationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CorreoThrottle]

    def post(self, request: Request) -> Response:
        if request.user.email_verified:
            return Response({"detail": "Tu correo ya está verificado."})
        mail.send_verification(request.user)
        return Response({"detail": "Te enviamos un nuevo enlace de verificación."})


class LoginView(PublicAuthView):
    # Por cuenta y por IP a la vez: uno solo deja el hueco del otro.
    throttle_classes = [LoginPorCuentaThrottle, LoginPorIpThrottle]

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(_session_payload(serializer.validated_data["user"]))


class LogoutView(APIView):
    """Invalida el refresh recibido. El access vive lo que le quede de vida."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        raw = request.data.get("refresh")
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except TokenError:
                pass  # ya vencido o inválido: el objetivo se cumple igual
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(PublicAuthView):
    throttle_classes = [CorreoThrottle]

    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email=serializer.validated_data["email"], is_active=True
        ).first()
        if user:
            mail.send_password_reset(user)
        return Response(NEUTRAL_EMAIL_REPLY, status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(PublicAuthView):
    # Adivinar el token también es fuerza bruta.
    throttle_classes = [LoginPorIpThrottle]

    def post(self, request: Request) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = OneTimeToken.objects.usable().filter(
            token=serializer.validated_data["token"],
            purpose=TokenPurpose.PASSWORD_RESET,
        ).select_related("user").first()
        if row is None:
            return Response(
                {"detail": "El enlace no es válido o ya venció. Pide uno nuevo."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = row.user
        user.set_password(serializer.validated_data["password"])
        # Quien recupera la clave por correo demuestra control del buzón: se
        # aprovecha para dar el correo por verificado.
        if not user.email_verified:
            user.email_verified_at = timezone.now()
        user.save(update_fields=["password", "email_verified_at", "updated_at"])
        row.consume()
        mail.send_password_changed(user)
        return Response(_session_payload(user))


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["password"])
        request.user.save(update_fields=["password", "updated_at"])
        mail.send_password_changed(request.user)
        # Se devuelven tokens nuevos para que la sesión actual siga viva.
        return Response(_session_payload(request.user))


class ProfileView(APIView):
    """El usuario y las empresas a las que tiene acceso."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        from billing.services import seat_summary

        memberships = list(user_memberships(request.user))
        return Response({
            "user": UserSerializer(request.user).data,
            "seats": seat_summary(request.user),
            "organizations": OrganizationSerializer(
                [m.organization for m in memberships],
                many=True,
                context={"roles": {m.organization_id: m.role for m in memberships}},
            ).data,
        })

    def patch(self, request: Request) -> Response:
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class OrganizationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        memberships = list(user_memberships(request.user))
        return Response(
            OrganizationSerializer(
                [m.organization for m in memberships],
                many=True,
                context={"roles": {m.organization_id: m.role for m in memberships}},
            ).data
        )

    def post(self, request: Request) -> Response:
        if not request.user.email_verified:
            return Response(
                {"detail": "Verifica tu correo antes de registrar una empresa."},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Tope de empresas por cuenta: el plan incluye una base y el titular
        # puede tener asientos extra (los otorga el dueño del sistema). Se
        # devuelve el resumen para que el front ofrezca ampliar.
        from billing.services import can_add_company, seat_summary

        if not can_add_company(request.user):
            s = seat_summary(request.user)
            return Response(
                {
                    "code": "limite_empresas",
                    "detail": (
                        f"Tu plan permite {s['limit']} empresa(s) y ya administras "
                        f"{s['used']}. Para agregar más, habilita asientos "
                        f"adicionales (S/ {s['extra_price']} por empresa al mes)."
                    ),
                    "seats": s,
                },
                status=status.HTTP_409_CONFLICT,
            )
        serializer = OrganizationCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        return Response(
            OrganizationSerializer(
                organization, context={"roles": {organization.id: "owner"}}
            ).data,
            status=status.HTTP_201_CREATED,
        )


# ── Perfil del negocio ──

class BusinessProfileView(ManagedOrganizationAPIView):
    """El perfil breve del negocio de la empresa activa. Opcional; decide qué
    guías y obligaciones tienen sentido activar."""

    def get(self, request: Request) -> Response:
        profile, _ = BusinessProfile.objects.get_or_create(organization=request.organization)
        return Response(BusinessProfileSerializer(profile).data)

    def put(self, request: Request) -> Response:
        profile, _ = BusinessProfile.objects.get_or_create(organization=request.organization)
        serializer = BusinessProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(completed_at=profile.completed_at or timezone.now())
        # El perfil cambia qué obligaciones aplican; reevaluar en segundo plano.
        try:
            from obligations.services.engine import evaluate_company

            evaluate_company(request.organization)
        except Exception:
            logger.exception("no se pudo reevaluar cumplimiento tras el perfil")
        return Response(BusinessProfileSerializer(profile).data)


# ── Equipo: usuarios con acceso a la empresa ──

class TeamView(ManagedOrganizationAPIView):
    """Miembros e invitaciones de la empresa activa.

    Cualquier miembro puede ver la lista (GET); solo titular o contador pueden
    invitar (POST). El invitado que entra queda atado a esta empresa y solo la
    ve a ella. Sumar a otro **titular** exige que quien invita sea titular."""

    def get(self, request: Request) -> Response:
        return Response(team.team_payload(request.organization, you=request.user))

    def post(self, request: Request) -> Response:
        serializer = MemberInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data["role"]
        if role == Role.OWNER and request.membership.role != Role.OWNER:
            return Response(
                {"detail": "Solo un titular puede sumar a otro titular."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            result = team.invite_member(
                request.organization, serializer.validated_data["email"], role,
                invited_by=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if result["kind"] == "member":
            return Response(
                {"member": team.member_payload(result["membership"], you=request.user)},
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"invitation": team.invitation_payload(result["invitation"]),
             "detail": "Esa persona aún no tiene cuenta; la invitación queda "
                       "pendiente y se activará cuando se registre con ese correo."},
            status=status.HTTP_201_CREATED,
        )


class TeamMemberView(ManagedOrganizationAPIView):
    """Cambia el rol o quita el acceso de una persona de la empresa activa."""

    def _member(self, request: Request, member_id: str) -> Membership | None:
        return (
            Membership.objects.filter(id=member_id, organization=request.organization)
            .select_related("user").first()
        )

    def patch(self, request: Request, member_id: str) -> Response:
        membership = self._member(request, member_id)
        if membership is None:
            return Response({"detail": "No existe ese acceso en esta empresa."},
                            status=status.HTTP_404_NOT_FOUND)
        serializer = MemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_role = serializer.validated_data["role"]
        # Tocar a un titular —o convertir a alguien en titular— es cosa de
        # titulares.
        if (membership.role == Role.OWNER or new_role == Role.OWNER) \
                and request.membership.role != Role.OWNER:
            return Response({"detail": "Solo un titular puede gestionar a un titular."},
                            status=status.HTTP_403_FORBIDDEN)
        # Nunca dejar la empresa sin titular.
        if membership.role == Role.OWNER and new_role != Role.OWNER \
                and team.owners_count(request.organization) <= 1:
            return Response(
                {"detail": "La empresa necesita al menos un titular. Nombra a otro antes de cambiar este."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        membership.role = new_role
        membership.save(update_fields=["role", "updated_at"])
        return Response(team.member_payload(membership, you=request.user))

    def delete(self, request: Request, member_id: str) -> Response:
        membership = self._member(request, member_id)
        if membership is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        if membership.role == Role.OWNER and request.membership.role != Role.OWNER:
            return Response({"detail": "Solo un titular puede quitar a un titular."},
                            status=status.HTTP_403_FORBIDDEN)
        if membership.role == Role.OWNER and team.owners_count(request.organization) <= 1:
            return Response(
                {"detail": "No puedes quitar al único titular de la empresa."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Baja lógica: se conserva el rastro de que tuvo acceso.
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeamInvitationView(ManagedOrganizationAPIView):
    """Revoca una invitación pendiente de la empresa activa."""

    def delete(self, request: Request, invitation_id: str) -> Response:
        Invitation.objects.filter(
            id=invitation_id, organization=request.organization,
            status=InvitationStatus.PENDING,
        ).update(status=InvitationStatus.REVOKED, updated_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Conexión con SUNAT ──

PRIMARY_USER_WARNING = (
    "La clave del usuario SOL principal permite presentar declaraciones y "
    "solicitar devoluciones en tu nombre. Te recomendamos crear en SUNAT un "
    "usuario secundario con permisos de solo consulta y conectar ese."
)


class SunatConnectionView(ManagedOrganizationAPIView):
    throttle_classes = [SunatConexionThrottle]

    """Conecta, consulta o desconecta las credenciales SOL de la empresa.

    La clave se cifra al guardarla y la validación contra SUNAT no ocurre
    aquí: entrar al portal abre un navegador y tarda. Se deja la conexión en
    «pendiente» y se encola la verificación junto con la primera
    sincronización, que es lo que el usuario está esperando de todos modos.
    """

    def get(self, request: Request) -> Response:
        credential = getattr(request.organization, "sunat_credential", None)
        if credential is None:
            return Response({
                "ruc": request.ruc,
                "status": "sin_conectar",
                "recommendation": PRIMARY_USER_WARNING,
            })
        return Response({
            **SunatCredentialSerializer(credential).data,
            "recommendation": PRIMARY_USER_WARNING,
        })

    @transaction.atomic
    def post(self, request: Request) -> Response:
        if not request.user.email_verified:
            return Response(
                {"detail": "Verifica tu correo antes de conectar SUNAT."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SunatConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        credential, _ = SunatCredential.objects.get_or_create(
            organization=request.organization,
            defaults={"sol_username": data["sol_username"]},
        )
        credential.sol_username = data["sol_username"]
        credential.set_password(data["sol_password"])
        credential.uses_primary_user = data["is_primary_user"]
        credential.status = SunatConnectionStatus.PENDING
        credential.last_error = ""
        credential.connected_by = request.user
        credential.save()

        # La constancia de autorización, con todo lo que hace falta para
        # demostrarla: quién, qué usuario SOL, cuándo, desde dónde y qué texto.
        SunatAuthorization.objects.create(
            organization=request.organization, user=request.user,
            sol_username=data["sol_username"], version=consent.VERSION,
            text_sha256=consent.SHA256, scopes=consent.ALCANCES,
            ip_address=consent.client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:300],
        )

        from sync.models import JobKind
        from sync.services import start_sync

        job = start_sync(
            request.organization,
            kind=JobKind.INITIAL,
            requested_by=request.user,
        )
        logger.info(
            "SUNAT conectada para %s por %s", request.ruc, request.user.email
        )
        return Response(
            {
                **SunatCredentialSerializer(credential).data,
                "sync_job": str(job.id),
                "warning": PRIMARY_USER_WARNING if data["is_primary_user"] else None,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def delete(self, request: Request) -> Response:
        credential = getattr(request.organization, "sunat_credential", None)
        if credential is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        # Se borra la credencial pero no los datos ya sincronizados: siguen
        # siendo de la empresa, y volver a conectar no debería costar una
        # resincronización completa.
        credential.delete()
        SunatAuthorization.objects.filter(
            organization=request.organization, revoked_at__isnull=True,
        ).update(revoked_at=timezone.now(), revoked_by=request.user)
        logger.info("SUNAT desconectada para %s", request.ruc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SunatPortalView(ManagedOrganizationAPIView):
    """Abre SUNAT Operaciones en Línea con la sesión de la empresa iniciada.

    Devuelve el formulario de login SOL —acción y campos, clave incluida— para
    que el navegador lo envíe a SUNAT en una pestaña nueva y caiga dentro del
    menú sin teclear nada. Ver ``accounts.services.sol_portal``.

    Es POST y no GET a propósito: devuelve un secreto, y un GET se cachea, se
    prefetcha y queda en historiales. Solo titular y contador: quien mira en
    solo lectura no debe poder llevarse la llave con la que se declara.
    """

    throttle_classes = [SunatPortalThrottle]

    def post(self, request: Request) -> Response:
        credential = getattr(request.organization, "sunat_credential", None)
        if credential is None or not credential.encrypted_password:
            return Response(
                {"detail": "Conecta SUNAT primero para abrir el portal con tu sesión.",
                 "code": "sin_conectar"},
                status=status.HTTP_409_CONFLICT,
            )
        if credential.status == SunatConnectionStatus.INVALID:
            # Reenviar una clave que SUNAT ya rechazó solo acerca el bloqueo
            # del usuario SOL.
            return Response(
                {"detail": "SUNAT rechazó la clave guardada. Vuelve a conectar SUNAT "
                           "con la clave vigente.",
                 "code": "invalida"},
                status=status.HTTP_409_CONFLICT,
            )
        logger.info(
            "Portal SOL abierto para %s por %s", request.ruc, request.user.email
        )
        return Response(sol_portal.login_form(
            ruc=request.ruc,
            username=credential.sol_username,
            password=credential.password,
        ))


def _authorization_payload(auth: SunatAuthorization | None) -> dict | None:
    if auth is None:
        return None
    return {
        "id": str(auth.id), "version": auth.version, "text_sha256": auth.text_sha256,
        "accepted_at": auth.accepted_at, "revoked_at": auth.revoked_at,
        "user": {"email": auth.user.email, "full_name": auth.user.full_name} if auth.user else None,
        "sol_username": auth.sol_username, "ip_address": auth.ip_address,
        "user_agent": auth.user_agent, "scopes": auth.scopes,
        "current_version": auth.version == consent.VERSION,
    }


class SunatAuthorizationView(OrganizationAPIView):
    """La autorización vigente de la empresa (y el historial), más el texto
    actual para enseñarlo tal cual."""

    permission_classes = [IsAuthenticated, HasOrganization]

    def get(self, request: Request) -> Response:
        auths = list(SunatAuthorization.objects.filter(organization=request.organization).select_related("user"))
        current = next((a for a in auths if a.revoked_at is None), None)
        return Response({
            "current": _authorization_payload(current),
            "history": [_authorization_payload(a) for a in auths],
            "document": consent.documento(),
        })


class ConsentDocumentView(APIView):
    """El texto vigente de la autorización, público: se lee antes de conectar."""

    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(consent.documento())
