"""Views for economy.

Rule: views parse → call service → serialize → return. Zero business logic here.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent
from libs.pagination.cursor import CursorPagination

from . import services
from .serializers import (
    CreditRechargeCreateSerializer,
    CreditRechargeSubmitTxidSerializer,
    CreditRechargeVerifySerializer,
)


def _uid(request: Request) -> str:
    return str(request.user.id)


def _parse_entry_types(request: Request) -> list[str] | None:
    raw = request.query_params.get("entry_type")
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------


class PointWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.get_wallet(user_id=_uid(request), currency="MP"))


class CreditWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.get_wallet(user_id=_uid(request), currency="MC"))


class AggregateWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.get_aggregate_balance(user_id=_uid(request)))


# ---------------------------------------------------------------------------
# Ledgers (cursor paginated)
# ---------------------------------------------------------------------------


class _LedgerView(APIView):
    permission_classes = [IsAuthenticated]
    currency = ""

    def get(self, request: Request) -> Response:
        qs = services.ledger_queryset(
            user_id=_uid(request),
            currency=self.currency,
            entry_types=_parse_entry_types(request),
            date_from=request.query_params.get("date_from"),
            date_to=request.query_params.get("date_to"),
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = [services.serialize_ledger_entry(e, self.currency) for e in page]
        return paginator.get_paginated_response(data)


class PointLedgerView(_LedgerView):
    currency = "MP"


class CreditLedgerView(_LedgerView):
    currency = "MC"


# ---------------------------------------------------------------------------
# Credit packages
# ---------------------------------------------------------------------------


class CreditPackagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.list_credit_packages())


# ---------------------------------------------------------------------------
# Daily login reward
# ---------------------------------------------------------------------------


class DailyRewardClaimView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        return Response(services.claim_daily_reward(user_id=_uid(request)))


class DailyRewardStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.daily_reward_status(user_id=_uid(request)))


# ---------------------------------------------------------------------------
# Credit recharge (skeleton — verification wired in with payments, W9)
# ---------------------------------------------------------------------------


class CreditRechargeInfoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        package_code = request.query_params.get("package_code", "")
        return Response(services.recharge_info(package_code=package_code))


class CreditRechargeCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreditRechargeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        result = services.create_credit_recharge(
            user_id=_uid(request),
            package_code=serializer.validated_data["package_code"],
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class CreditRechargeSubmitTxidView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = CreditRechargeSubmitTxidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.submit_recharge_txid(
            user_id=_uid(request),
            package_code=serializer.validated_data["package_code"],
            txid=serializer.validated_data["txid"],
        )
        return Response(result)


class CreditRechargeVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        serializer = CreditRechargeVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.verify_recharge(
            user_id=_uid(request),
            order_no=order_no,
            txid=serializer.validated_data["txid"],
        )
        return Response(result)
