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

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus


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
