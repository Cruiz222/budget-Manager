from app.application.unit_of_work import UnitOfWork
from app.domain.money.wallet import Wallet
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountStatus import (
    VirtualAccountStatus,
)


VIRTUAL_ACCOUNT_PROVIDER = "paystack"


def record_wallet_with_pending_virtual_account(
    uow: UnitOfWork,
    wallet: Wallet,
) -> None:
    """Record a wallet and its pending bank account in one unit."""
    uow.wallets.save(wallet)
    uow.virtual_accounts.save(
        VirtualAccount(
            wallet_id=wallet.wallet_id,
            status=VirtualAccountStatus.PENDING,
            provider=VIRTUAL_ACCOUNT_PROVIDER,
        )
    )