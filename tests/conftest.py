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

#: A well-formed bank destination, reused by the plan fixture below.
BANK_DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)

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
    """Return a fresh Wallet in the requested state."""

    def _build(
        available: str = "10000",
        locked: str = "0",
        status: WalletStatus = WalletStatus.ACTIVE,
        currency: Currency = Currency.NGN,
    ) -> Wallet:
        return Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            status=status,
            _available_balance=Money(Decimal(available), currency),
            _locked_balance=Money(Decimal(locked), currency),
            currency=currency,
        )

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
    ) -> SavingsPlan:
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
