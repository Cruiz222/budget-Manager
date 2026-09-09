from app.application.unit_of_work import UnitOfWorkFactory
from app.application.wallet_service import WalletService
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)


def build_wallet_service(
    unit_of_work_factory: UnitOfWorkFactory | None = None,
) -> WalletService:
    """Composition root: the one place concrete persistence is chosen.

    The application layer only knows the abstract UnitOfWorkFactory; this
    factory is where the SQLite-backed implementation is wired in, writing to
    "budget.db" in the working directory. Tests inject a factory pointed at a
    temp file. Swapping storage happens here and nowhere else.
    """
    return WalletService(
        unit_of_work_factory=unit_of_work_factory or SqliteUnitOfWorkFactory()
    )
