from uuid import uuid4

import pytest
from app.domain.money.currency import Currency
from app.application.wallet_service import WalletService
from app.domain.money.exception import WalletNotFoundError
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountStatus import (
    VirtualAccountStatus,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_ID


def build_service(tmp_path):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "wallet-virtual-account.db")
    )
    return (
        WalletService(factory, actor=TEST_USER_ID),
        factory,
    )


def test_opening_a_wallet_records_a_pending_virtual_account(
    tmp_path,
):
    service, factory = build_service(tmp_path)

    wallet = service.open_wallet(Currency.NGN)

    stored = factory.start()
    try:
        account = stored.virtual_accounts.get_by_wallet_id(
            wallet.wallet_id
        )
    finally:
        stored.rollback()

    assert account is not None
    assert account.wallet_id == wallet.wallet_id
    assert account.status is VirtualAccountStatus.PENDING
    assert account.provider == "paystack"
    assert account.provider_customer_code is None
    assert account.account_number is None
    assert account.account_name is None
    assert account.bank_name is None
    

def seed(factory, wallet, account):
    uow = factory.start()
    try:
        uow.wallets.save(wallet)
        uow.virtual_accounts.save(account)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def test_it_reads_the_virtual_account_for_an_owned_wallet(
    tmp_path,
    build_wallet,
):
    service, factory = build_service(tmp_path)
    wallet = build_wallet(user_id=TEST_USER_ID)
    account = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )
    seed(factory, wallet, account)

    assert (
        service.virtual_account_for_wallet(wallet.wallet_id)
        == account
    )


def test_it_hides_another_actors_virtual_account(
    tmp_path,
    build_wallet,
):
    _, factory = build_service(tmp_path)
    wallet = build_wallet(user_id=TEST_USER_ID)
    account = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )
    seed(factory, wallet, account)

    stranger = WalletService(factory, actor=uuid4())

    with pytest.raises(WalletNotFoundError):
        stranger.virtual_account_for_wallet(wallet.wallet_id)