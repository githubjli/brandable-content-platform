"""Payment provider adapters (payments.md §5)."""

from .blockchain import (
    BLOCKCHAIN_BACKEND_REGISTRY,
    BlockchainAdapter,
    BlockchainBackend,
    LttBackend,
    VerifyResult,
)
from .stripe_adapter import StripeAdapter

__all__ = [
    "BLOCKCHAIN_BACKEND_REGISTRY",
    "BlockchainAdapter",
    "BlockchainBackend",
    "LttBackend",
    "StripeAdapter",
    "VerifyResult",
]
