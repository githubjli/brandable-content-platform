"""Blockchain payment backends (payments.md §5).

V1 ships only the **LTT** backend (THB-LTT stablecoin). Adding a chain = one new
BlockchainBackend subclass + a config block + registry entry; no contract change.
Actual on-chain verification against an LTT node is deferred — until a node is
wired, verify_txid reports the order as still pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from libs.errors.exceptions import AppError, ValidationError


@dataclass
class VerifyResult:
    verified: bool
    confirmations: int
    required_confirmations: int
    pending: bool = False
    error: str | None = None


class BlockchainBackend:
    """One backend per chain. Each declares the currencies it settles."""

    network: str = ""
    supported_currencies: list[str] = []

    def get_pay_to_address(self, currency: str) -> str:
        raise NotImplementedError

    def get_required_confirmations(self) -> int:
        raise NotImplementedError

    def verify_txid(
        self, txid: str, expected_amount: Decimal, expected_currency: str, address: str
    ) -> VerifyResult:
        raise NotImplementedError


class LttBackend(BlockchainBackend):
    network = "ltt"
    supported_currencies = ["THB-LTT"]

    def get_pay_to_address(self, currency: str) -> str:
        address = settings.LTT_RECEIVE_ADDRESS
        if not address:
            raise AppError(
                code="PAYMENT_ADDRESS_NOT_CONFIGURED",
                message="LTT receive address is not configured.",
                http_status=503,
            )
        return address

    def get_required_confirmations(self) -> int:
        return settings.LTT_REQUIRED_CONFIRMATIONS

    def verify_txid(
        self, txid: str, expected_amount: Decimal, expected_currency: str, address: str
    ) -> VerifyResult:
        # Real LTT node verification is deferred (Week 9 decision). Without a node
        # the order stays pending rather than falsely confirming.
        return VerifyResult(
            verified=False,
            confirmations=0,
            required_confirmations=self.get_required_confirmations(),
            pending=True,
            error="LTT on-chain verification is not yet available.",
        )


# Adding a chain is one line here (plus the backend class + config block).
BLOCKCHAIN_BACKEND_REGISTRY: dict[str, BlockchainBackend] = {
    LttBackend.network: LttBackend(),
}


class BlockchainAdapter:
    """Generic adapter that dispatches to the per-network backend."""

    def __init__(self, network: str) -> None:
        backend = BLOCKCHAIN_BACKEND_REGISTRY.get(network)
        if backend is None:
            raise ValidationError(
                code="BLOCKCHAIN_NETWORK_UNSUPPORTED",
                message=f"Blockchain network '{network}' is not registered.",
                http_status=422,
            )
        self.network = network
        self.backend = backend

    def get_pay_to_address(self, currency: str) -> str:
        return self.backend.get_pay_to_address(currency)

    def get_required_confirmations(self) -> int:
        return self.backend.get_required_confirmations()

    def verify_txid(
        self, txid: str, expected_amount: Decimal, expected_currency: str, address: str
    ) -> VerifyResult:
        return self.backend.verify_txid(txid, expected_amount, expected_currency, address)
