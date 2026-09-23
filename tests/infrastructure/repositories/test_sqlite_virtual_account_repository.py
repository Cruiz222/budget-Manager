from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    open_sqlite_connection,
)
from app.infrastructure.repositories.sqlite_virtual_account_repository import (
    SqliteVirtualAccountRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)


def test_save_and_get_by_wallet_id_round_trips_an_active_account(build_wallet):
    connection = open_sqlite_connection(":memory:")
    wallet_repository = SqliteWalletRepository(connection)
    repository = SqliteVirtualAccountRepository(connection)

    wallet = build_wallet()
    wallet_repository.save(wallet)

    account = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.ACTIVE,
        provider="paystack",
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    repository.save(account)

    stored = repository.get_by_wallet_id(wallet.wallet_id)

    assert stored == account