"""Views for identity.

Rule: views parse → call service → serialize → return.
Zero business logic here.
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent

from . import services
from .serializers import (
    ChangePasswordSerializer,
    CreatorProfileUpdateSerializer,
    KycDocumentUploadSerializer,
    KycUpdateSerializer,
    LoginRequestSerializer,
    LogoutRequestSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PreferencesSerializer,
    ProfileUpdateSerializer,
    RefreshRequestSerializer,
    RegisterRequestSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterView(APIView):
    permission_classes = [AllowAny]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = RegisterRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        result = services.register(
            email=data["email"],
            password=data["password"],
            display_name=data["display_name"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get(
            "REMOTE_ADDR"
        )
        result = services.login(
            email=data["email"],
            password=data["password"],
            device_label=data.get("device_label", ""),
            ip_address=ip or None,
        )
        return Response(result, status=status.HTTP_200_OK)


class RefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = RefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.refresh_token(refresh_jwt=serializer.validated_data["refresh"])
        return Response(result, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = LogoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.logout(
            refresh_jwt=serializer.validated_data["refresh"],
            user_id=str(request.user.id),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        session_id = request.auth.get("jti") if isinstance(request.auth, dict) else None
        result = services.get_me(
            user_id=str(request.user.id),
            session_id=session_id,
        )
        return Response(result, status=status.HTTP_200_OK)


class SessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        session_id = request.auth.get("jti") if isinstance(request.auth, dict) else None
        result = services.list_sessions(
            user_id=str(request.user.id),
            current_session_id=session_id,
        )
        return Response({"results": result}, status=status.HTTP_200_OK)


class SessionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request: Request, session_id: str) -> Response:
        services.revoke_session(user_id=str(request.user.id), session_id=str(session_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(email=serializer.validated_data["email"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_password_reset(
            reset_token=serializer.validated_data["reset_token"],
            new_password=serializer.validated_data["new_password"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.change_password(
            user_id=str(request.user.id),
            current_password=data["current_password"],
            new_password=data["new_password"],
            revoke_other_sessions=data.get("revoke_other_sessions", False),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Account / Profile
# ---------------------------------------------------------------------------


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request: Request) -> Response:
        result = services.get_profile(user_id=str(request.user.id))
        return Response(result, status=status.HTTP_200_OK)

    @idempotent
    def patch(self, request: Request) -> Response:
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_profile(
            user_id=str(request.user.id),
            **serializer.validated_data,
        )
        return Response(result, status=status.HTTP_200_OK)


class PreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        result = services.get_preferences(user_id=str(request.user.id))
        return Response(result, status=status.HTTP_200_OK)

    def patch(self, request: Request) -> Response:
        serializer = PreferencesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_preferences(
            user_id=str(request.user.id),
            **serializer.validated_data,
        )
        return Response(result, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------


class KycView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        result = services.get_kyc(user_id=str(request.user.id))
        return Response(result, status=status.HTTP_200_OK)

    @idempotent
    def put(self, request: Request) -> Response:
        serializer = KycUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_kyc(
            user_id=str(request.user.id),
            **serializer.validated_data,
        )
        return Response(result, status=status.HTTP_200_OK)


class KycDocumentView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request: Request) -> Response:
        serializer = KycDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # In production, upload the image to object storage and get back a URL.
        # For now, store as a placeholder (real implementation plugs in S3/GCS adapter).
        image_file = serializer.validated_data["image"]
        image_url = getattr(image_file, "url", f"/media/kyc/{image_file.name}")

        result = services.upload_kyc_document(
            user_id=str(request.user.id),
            document_type=serializer.validated_data["document_type"],
            image_url=image_url,
        )
        return Response(result, status=status.HTTP_200_OK)


class KycSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        result = services.submit_kyc(user_id=str(request.user.id))
        return Response(result, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Creator profile
# ---------------------------------------------------------------------------


class CreatorProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        result = services.get_creator_profile(user_id=str(request.user.id))
        return Response(result, status=status.HTTP_200_OK)

    def patch(self, request: Request) -> Response:
        serializer = CreatorProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_creator_profile(
            user_id=str(request.user.id),
            **serializer.validated_data,
        )
        return Response(result, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Public users
# ---------------------------------------------------------------------------


class PublicUserView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, user_id: str) -> Response:
        viewer_id = str(request.user.id) if request.user.is_authenticated else None
        result = services.get_public_user(viewer_id=viewer_id, user_id=str(user_id))
        return Response(result, status=status.HTTP_200_OK)


class FollowView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, user_id: str) -> Response:
        result = services.follow_user(
            follower_id=str(request.user.id),
            target_id=str(user_id),
        )
        return Response(result, status=status.HTTP_200_OK)

    def delete(self, request: Request, user_id: str) -> Response:
        services.unfollow_user(
            follower_id=str(request.user.id),
            target_id=str(user_id),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicCreatorView(APIView):
    permission_classes = [AllowAny]

    def get(self, request: Request, creator_id: str) -> Response:
        viewer_id = str(request.user.id) if request.user.is_authenticated else None
        result = services.get_public_user(viewer_id=viewer_id, user_id=str(creator_id))
        if not result.get("is_creator"):
            from libs.errors.exceptions import NotFoundError

            raise NotFoundError(code="USER_NOT_FOUND", message="Creator not found.")
        return Response(result, status=status.HTTP_200_OK)


class PublicCreatorVideosView(APIView):
    """Stub — returns empty results until content.video app is built."""

    permission_classes = [AllowAny]

    def get(self, request: Request, creator_id: str) -> Response:
        return Response({"results": [], "next": None}, status=status.HTTP_200_OK)
