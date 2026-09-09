from decimal import Decimal
from uuid import uuid4

from app.composition_root import build_wallet_service
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NGN = Currency.NGN


def test_two_service_instances_share_state_over_one_database(tmp_path, build_wallet):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "compose.db"))

    # Seed a wallet through a unit of work, exactly like the service would.
    seed = factory.start()
    wallet = build_wallet()
    seed.wallets.save(wallet)
    seed.commit()

    service_one = build_wallet_service(unit_of_work_factory=factory)
    service_one.deposit(
        wallet.wallet_id,
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    # A second, independently-constructed service reads the committed deposit
    # and withdraws from it - state persisted across instances.
    service_two = build_wallet_service(unit_of_work_factory=factory)
    service_two.withdraw(
        wallet.wallet_id,
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    read = factory.start()
    try:
        stored = read.wallets.get_by_id(wallet.wallet_id)
    finally:
        read.rollback()
    assert stored.available_balance == Money(Decimal("12000"), NGN)
    assert stored.locked_balance == Money(Decimal("0"), NGN)
