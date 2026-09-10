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

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
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
    """

    def _build(
        source: PlanSource = PlanSource.LOCKED,
        cadence: Cadence = Cadence.MONTHLY,
        anchor: date = date(2026, 1, 1),
        instructions: tuple[Instruction, ...] | None = None,
        status: PlanStatus = PlanStatus.ACTIVE,
        completed_runs: int = 0,
        ends_on: date | None = None,
        wallet_id: UUID | None = None,
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
            source=source,
            schedule=Schedule(cadence=cadence, anchor=anchor),
            _instructions=instructions,
            status=status,
            completed_runs=completed_runs,
            ends_on=ends_on,
        )

    return _build
