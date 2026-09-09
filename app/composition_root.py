from app.application.wallet_service import WalletService
from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.repositories.wallet_repository import WalletRepository
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)
from app.infrastructure.repositories.in_memory_wallet_repository import (
    InMemoryWalletRepository,
)


def build_wallet_service(
    wallet_repository: WalletRepository | None = None,
    transaction_repository: TransactionRepository | None = None,
) -> WalletService:
    """Composition root: the one place concrete implementations are chosen.

    The application layer only knows the abstract repositories; this factory is
    where the in-memory implementations are wired in. When a Postgres-backed
    repository exists, swapping it in happens here and nowhere else.
    """
    return WalletService(
        wallet_repository=wallet_repository or InMemoryWalletRepository(),
        transaction_repository=transaction_repository
        or InMemoryTransactionRepository(),
    )
