"""Shared pytest fixtures.

The application-layer and repository tests each used to hand-roll their own
`build_wallet` helper (11 copies, each with slightly different hidden
defaults). A fixture factory is pytest's answer: one definition, requested by
name in whichever test wants it.

This factory exposes every dial a test could turn - status, both balances and
currency - with explicit defaults:

    build_wallet()                          # ACTIVE, 10000.00 NGN, nothing locked
    build_wallet(status=WalletStatus.CLOSED)
    build_wallet(available="500", locked="2500")
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationChannel import NotificationChannel
from app.domain.planning.cadence import Cadence
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.planning.schedule import Schedule
from app.domain.repositories.transaction_repository import TransactionRepository

#: A well-formed bank destination, reused by the plan fixture below.
BANK_DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)

#: The moment the ``build_wallet`` fixture opens and funds its pot at.
#:
#: Fixed rather than ``datetime.now()``, and the reason is now a domain one: a
#: pot records ``sealed_at`` and ``first_funded_at``, and a fixture that read the
#: clock would give every test a pot whose timeline moved with it. A test that
#: wanted to place a commitment relative to the pot's funding would then have to
#: know *when the suite ran* to write its expectation - which is the kind of
#: hidden dependency the rest of this suite avoids by passing moments in.
#:
#: One moment for the pot's opening, its sealing and its funding, which is
#: exactly the shape the pre-pots migration produces: a pot born already holding
#: money (see ``_migrate_locked_balance_into_funds``).
POT_MOMENT = datetime(2026, 1, 1)

#: Every variable ``email_settings`` reads. Cleared for every test in the suite.
#:
#: ``os.environ`` is a global that exactly one module reads, and leaving it
#: alone would make the tests depend on whoever is running them: a developer
#: with SMTP configured would have ``plan tick`` tests that queue nothing and
#: then reach out over the network, and the same suite would pass on a clean
#: machine and fail on a working one. Clearing them here is what makes "no test
#: opens a socket" a property of the suite rather than a property of the shell
#: it happens to run in. A test that wants a configured install sets the
#: variables it needs with ``monkeypatch.setenv``.
NOTIFICATION_VARIABLES = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_STARTTLS",
    "BUDGET_NOTIFY_TO",
    "BUDGET_NOTIFY_FROM",
)


@pytest.fixture(autouse=True)
def no_notification_environment(monkeypatch):
    for name in NOTIFICATION_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class FakeChannel(NotificationChannel):
    """A delivery channel that records instead of sending.

    This is the seam that means no test in this suite opens a socket. It is not
    a mock: it implements the real port, so anything that passes with this passes
    with the SMTP adapter as far as the calling code is concerned.

    ``failures`` is a queue of exceptions to raise, consumed one per attempt.
    Once it is empty, sends succeed - which is what makes "the second tick gets
    through" expressible without any further arrangement.
    """

    def __init__(self, failures=()):
        self.attempts: list = []
        self.sent: list = []
        self._failures = list(failures)

    def send(self, message) -> None:
        self.attempts.append(message)
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(message)


@pytest.fixture
def build_wallet():
    """Return a fresh Wallet in the requested state.

    ``locked`` builds one open pot named "Locked" rather than a locked balance,
    because a wallet no longer *has* a locked balance to set - it is the sum of
    its pots. The same name and the same openness are what the migration gives
    every pre-existing locked balance (see ``_migrate_locked_balance_into_funds``),
    so a test written against ``locked="4000"`` describes exactly the wallet it
    always did: 4000 reserved, and nothing preventing its release.

    That equivalence is what keeps this factory worth having. Roughly fifty tests
    reach the locked balance only through ``locked=``, and none of them should
    have to learn what a pot is to keep testing what they were testing.
    """

    def _build(
        available: str = "10000",
        locked: str = "0",
        status: WalletStatus = WalletStatus.ACTIVE,
        currency: Currency = Currency.NGN,
    ) -> Wallet:
        # Built ACTIVE and given its pot first, with the requested status applied
        # at the end. Not a detail: ``open_fund`` refuses a closed wallet, which
        # is correct domain behaviour, so a fixture that set the status up front
        # could not build the very states its callers ask for. Constructing a
        # state is also not the same as transitioning to it - ``freeze`` and
        # ``unfreeze`` are tested on their own, and this factory is entitled to
        # start where it likes.
        wallet = Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            status=WalletStatus.ACTIVE,
            _available_balance=Money(Decimal(available), currency),
            currency=currency,
        )
        if Decimal(locked) != 0:
            # Only when there is something to put in it: a wallet with no locked
            # money should have no pots, not an empty one. An empty pot would make
            # ``locked=0`` build a wallet differing in *shape* from
            # ``build_wallet()``, and shape is exactly what the payout draw order
            # depends on.
            fund = wallet.open_fund(
                "Locked", FundKind.PERSONAL, as_of=POT_MOMENT
            )
            wallet.deposit_into_fund(
                fund.fund_id, Money(Decimal(locked), currency), POT_MOMENT
            )
        wallet.status = status
        return wallet

    return _build


@pytest.fixture
def build_plan():
    """Return a fresh SavingsPlan in the requested state.

    Defaults describe the product's headline case: a locked-source monthly plan
    paying 2000 NGN to one named bank account, starting 1 January 2026.

    The anchor default is **midnight**, and that is a deliberate choice about
    what the suite can see. ``date(...) != datetime(...)``, so a midnight anchor
    still catches a value that came back as a plain date - the type regression.
    It does *not* catch a moment that lost its time of day on the way through,
    because midnight is the one answer that losing the time cannot change; the
    noon-based tests in ``test_schedule.py`` and ``test_savings_plan.py`` are
    what cover that. Keeping midnight here buys readable expectations everywhere
    else in exchange for those few tests carrying the weight.
    """

    def _build(
        source: PlanSource = PlanSource.LOCKED,
        cadence: Cadence = Cadence.MONTHLY,
        anchor: datetime = datetime(2026, 1, 1),
        instructions: tuple[Instruction, ...] | None = None,
        status: PlanStatus = PlanStatus.ACTIVE,
        completed_runs: int = 0,
        ends_on: date | None = None,
        wallet_id: UUID | None = None,
        name: str = "salary",
        fund_id: UUID | None = None,
        created_at: datetime = datetime(2026, 1, 1),
    ) -> SavingsPlan:
        """Build a plan, defaulting to one that names no pot.

        ``fund_id`` defaults to ``None``, and that default is the honest one
        rather than a convenience: ``None`` is what a plan saved before pots
        could be named looks like, which is the shape most of this suite is
        about. A test that wants the commitment rule exercised names a pot - and
        it has to want that deliberately, which is the point.

        ``created_at`` is fixed for the mirror-image reason ``POT_MOMENT`` is:
        it is one half of the ``sealed_at <= committed_at <= first_funded_at``
        comparison, so a plan built with ``datetime.now()`` would make that
        ordering depend on when the suite ran.
        """
        if instructions is None:
            instructions = (
                Instruction(
                    action=PlannedAction.PAYOUT,
                    amount=Money(Decimal("2000"), Currency.NGN),
                    label="salary",
                    destination=BANK_DESTINATION,
                ),
            )
        return SavingsPlan(
            wallet_id=wallet_id if wallet_id is not None else uuid4(),
            name=name,
            source=source,
            schedule=Schedule(cadence=cadence, anchor=anchor),
            _instructions=instructions,
            status=status,
            completed_runs=completed_runs,
            ends_on=ends_on,
            fund_id=fund_id,
            created_at=created_at,
        )

    return _build


@pytest.fixture
def build_channel():
    """Return a fresh FakeChannel, optionally told to fail the first N sends.

        build_channel()                                  # every send succeeds
        build_channel(failures=[OSError("refused")])      # the first send raises
        build_channel(failures=[OSError("a"), OSError("b")])   # the first two
    """

    def _build(failures=()) -> FakeChannel:
        return FakeChannel(failures=failures)

    return _build


class RecordingTransactionRepository(TransactionRepository):
    """A ledger that keeps every save, in order, and nothing else.

    ``InMemoryTransactionRepository`` keeps only the latest row per transaction,
    which is what most tests want - they ask what the *final* row says. This one
    exists for the tests that ask what the *sequence* said, which is how the
    PENDING-then-SUCCESSFUL shape of ``WalletOperation.execute`` is observable at
    all: by the time an operation returns, PENDING has been overwritten and the
    in-memory repository has forgotten it ever existed.

    Five test modules used to define their own copy of this class. The three fund
    modules share the fixture instead; consolidating the older five onto it is a
    cleanup that belongs with whatever touches them next, not with this change.

    **The statuses are snapshots, taken at save time, and that is load-bearing.**
    ``save`` is called twice for one transaction, and the second call is after
    the *same object* has been marked FAILED or SUCCESSFUL - so a property
    reading ``[transaction.status for transaction in self.saved]`` would report
    the final status twice and never show PENDING at all. The only way to observe
    the PENDING-then-SUCCESSFUL sequence is to record what the status was when
    the save happened, which is what the original per-module copies did and what
    this one has to keep doing.
    """

    def __init__(self):
        self.saved = []
        self.saved_statuses = []

    def save(self, transaction):
        self.saved.append(transaction)
        self.saved_statuses.append(transaction.status)
        return transaction

    def get_by_id(self, transaction_id):
        raise NotImplementedError

    def get_by_internal_reference(self, internal_reference):
        return None

    def get_by_wallet_id(self, wallet_id):
        raise NotImplementedError

    def get_by_provider_reference(self, provider_reference):
        raise NotImplementedError


@pytest.fixture
def recording_transactions():
    """A ``RecordingTransactionRepository``, fresh for each test."""
    return RecordingTransactionRepository()
