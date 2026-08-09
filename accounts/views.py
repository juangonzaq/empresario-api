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
    Membership, OneTimeToken, Organization, SunatConnectionStatus,
    SunatCredential, TokenPurpose, User,
)
from .serializers import (
    LoginSerializer, OrganizationCreateSerializer, OrganizationSerializer,
    PasswordChangeSerializer, PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer, RegisterSerializer,
    SunatConnectSerializer, SunatCredentialSerializer, UserSerializer,
    tokens_for,
)
from .services import mail
from .throttles import (
    CorreoThrottle, LoginPorCuentaThrottle, LoginPorIpThrottle,
    RegistroThrottle, SunatConexionThrottle,
)
from .tenancy import ManagedOrganizationAPIView, user_memberships

logger = logging.getLogger(__name__)

# Respuesta común a registro y recuperación: idéntica exista o no la cuenta.
NEUTRAL_EMAIL_REPLY = {
    "detail": "Si el correo es válido, te enviamos un mensaje con los pasos a seguir."
}


def _session_payload(user: User) -> dict:
    memberships = list(user_memberships(user))
    return {
        **tokens_for(user),
        "user": UserSerializer(user).data,
        "organizations": OrganizationSerializer(
            [m.organization for m in memberships],
            many=True,
            context={"roles": {m.organization_id: m.role for m in memberships}},
        ).data,
    }


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RegistroThrottle, CorreoThrottle]

    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        already_existed = User.objects.filter(
            email=serializer.validated_data["email"]
        ).exists()
        user = serializer.save()

        if already_existed:
            # No se confirma ni se niega que el correo esté registrado. A quien
            # ya tiene cuenta se le manda el enlace de recuperación, que es lo
            # útil si llegó aquí por haber olvidado que se registró.
            mail.send_password_reset(user)
        else:
            mail.send_verification(user)
        return Response(NEUTRAL_EMAIL_REPLY, status=status.HTTP_202_ACCEPTED)


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

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


class LoginView(APIView):
    permission_classes = [AllowAny]
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


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
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


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
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
        memberships = list(user_memberships(request.user))
        return Response({
            "user": UserSerializer(request.user).data,
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
        logger.info("SUNAT desconectada para %s", request.ruc)
        return Response(status=status.HTTP_204_NO_CONTENT)
