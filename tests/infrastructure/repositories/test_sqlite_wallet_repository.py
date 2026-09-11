from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NGN = Currency.NGN

#: The moment the deposits in this file arrive at.
#:
#: Fixed rather than "now", because a deposit stamps ``first_funded_at`` and this
#: file asserts the *round trip* - so the value has to be one a test can write
#: down as an expectation. Reading the clock would make the assertion "the pot
#: was funded whenever this suite happened to run".
MOMENT = datetime(2026, 1, 1)


def ngn(amount: str) -> Money:
    return Money(Decimal(amount), NGN)


def build_repository():
    return SqliteWalletRepository(open_sqlite_connection(":memory:"))


def test_save_and_get_by_id_round_trips_the_wallet(build_wallet):
    wallet = build_wallet()
    repository = build_repository()

    repository.save(wallet)

    stored = repository.get_by_id(wallet.wallet_id)
    assert stored.wallet_id == wallet.wallet_id
    assert stored.user_id == wallet.user_id
    assert stored.status is WalletStatus.ACTIVE
    assert stored.currency is NGN
    assert stored.available_balance == wallet.available_balance
    assert stored.locked_balance == wallet.locked_balance


def test_save_overwrites_an_existing_wallet(build_wallet):
    wallet = build_wallet()
    repository = build_repository()
    repository.save(wallet)

    wallet.apply_deposit(Money(Decimal("5000"), NGN))
    repository.save(wallet)

    stored = repository.get_by_id(wallet.wallet_id)
    assert stored.available_balance == Money(Decimal("15000"), NGN)


def test_get_by_id_of_missing_wallet_raises():
    repository = build_repository()

    with pytest.raises(WalletNotFoundError):
        repository.get_by_id(uuid4())


# --- pots ------------------------------------------------------------------


def test_a_wallets_pots_round_trip(build_wallet):
    """Everything about a pot survives the database, field for field.

    Written out rather than asserted against the wallet's own ``locked_balance``,
    because a repository that persisted the *sum* and re-derived empty pots would
    satisfy an aggregate comparison while losing every name and date.
    """
    wallet = build_wallet(available="1000")
    wallet.open_fund("Vacation", FundKind.PERSONAL, maturity_date=date(2026, 6, 1))
    wallet.open_fund("Float", FundKind.BUSINESS, maturity_date=None)
    wallet.deposit_into_fund(wallet.fund_by_name("Vacation").fund_id, ngn("4000"), MOMENT)
    wallet.deposit_into_fund(wallet.fund_by_name("Float").fund_id, ngn("2500"), MOMENT)
    repository = build_repository()

    repository.save(wallet)
    stored = repository.get_by_id(wallet.wallet_id)

    assert [fund.name for fund in stored.funds] == ["Vacation", "Float"]
    assert [fund.kind for fund in stored.funds] == [
        FundKind.PERSONAL,
        FundKind.BUSINESS,
    ]
    assert [fund.balance for fund in stored.funds] == [ngn("4000"), ngn("2500")]
    assert stored.fund_by_name("Vacation").maturity_date == date(2026, 6, 1)
    assert stored.fund_by_name("Float").maturity_date is None
    assert stored.locked_balance == ngn("6500")


def test_a_pots_identity_survives_the_database(build_wallet):
    """The ``fund_id`` is what the ledger rows and (later) plans point at.

    A round trip that regenerated the id would leave every stored ``fund_id``
    dangling while looking superficially fine - the name would still be right.
    """
    wallet = build_wallet(available="1000")
    fund = wallet.open_fund("Vacation", FundKind.PERSONAL)
    repository = build_repository()

    repository.save(wallet)
    stored = repository.get_by_id(wallet.wallet_id)

    assert stored.fund_by_name("Vacation").fund_id == fund.fund_id


def test_pots_come_back_in_the_order_they_were_opened(build_wallet):
    """The order is load-bearing - it is the order a payout draws on them.

    Three pots opened with no ``as_of``, so they share a ``created_at`` to the
    microsecond more often than not. That is the case ``ORDER BY created_at``
    cannot resolve, and it is why the repository orders by ``rowid`` instead -
    see ``_funds_for``.
    """
    wallet = build_wallet()
    for name in ("First", "Second", "Third"):
        wallet.open_fund(name, FundKind.PERSONAL)
    repository = build_repository()

    repository.save(wallet)
    stored = repository.get_by_id(wallet.wallet_id)

    assert [fund.name for fund in stored.funds] == ["First", "Second", "Third"]


def test_saving_again_does_not_duplicate_pots(build_wallet):
    """``save`` is an upsert, and pots are the half of it that is not free.

    The wallets row is a single upsert; the funds rows have to be reconciled, and
    the simplest correct reconciliation is delete-then-insert. Doing nothing
    would duplicate on the second save; inserting without deleting would collide
    on ``UNIQUE (wallet_id, name)``.
    """
    wallet = build_wallet(available="1000")
    wallet.open_fund("Vacation", FundKind.PERSONAL)
    repository = build_repository()
    repository.save(wallet)

    wallet.deposit_into_fund(wallet.fund_by_name("Vacation").fund_id, ngn("500"), MOMENT)
    repository.save(wallet)
    stored = repository.get_by_id(wallet.wallet_id)

    assert len(stored.funds) == 1
    assert stored.funds[0].balance == ngn("500")
    assert stored.locked_balance == ngn("500")


def test_a_pot_removed_from_the_aggregate_is_removed_from_the_database(build_wallet):
    """The delete half of delete-then-insert, asserted rather than assumed.

    Nothing in the domain removes a pot today, so this test constructs the state
    directly. It is here because the delete is the part of ``_sync_funds`` whose
    absence would be invisible until some future "close a pot" feature left ghost
    rows behind - and a test now is cheaper than that bug later.
    """
    wallet = build_wallet(available="1000")
    wallet.open_fund("Vacation", FundKind.PERSONAL)
    wallet.open_fund("Salary", FundKind.PERSONAL)
    repository = build_repository()
    repository.save(wallet)

    wallet._funds = tuple(fund for fund in wallet.funds if fund.name == "Salary")
    repository.save(wallet)

    assert [fund.name for fund in repository.get_by_id(wallet.wallet_id).funds] == [
        "Salary"
    ]


def test_a_wallet_with_no_pots_round_trips_as_no_pots(build_wallet):
    wallet = build_wallet()
    repository = build_repository()

    repository.save(wallet)
    stored = repository.get_by_id(wallet.wallet_id)

    assert stored.funds == ()
    assert stored.locked_balance == ngn("0")
