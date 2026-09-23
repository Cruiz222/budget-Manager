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
    
    

def test_get_by_account_number_finds_an_active_account(build_wallet):
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

    stored = repository.get_by_account_number("1234567890")

    assert stored == account
    

def test_missing_wallet_id_returns_none():
    from uuid import uuid4

    connection = open_sqlite_connection(":memory:")
    repository = SqliteVirtualAccountRepository(connection)

    assert repository.get_by_wallet_id(uuid4()) is None


def test_missing_account_number_returns_none():
    connection = open_sqlite_connection(":memory:")
    repository = SqliteVirtualAccountRepository(connection)

    assert repository.get_by_account_number("0000000000") is None     
    
    
def test_save_updates_a_pending_account_to_active(build_wallet):
    connection = open_sqlite_connection(":memory:")
    wallet_repository = SqliteWalletRepository(connection)
    repository = SqliteVirtualAccountRepository(connection)

    wallet = build_wallet()
    wallet_repository.save(wallet)

    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )
    repository.save(pending)

    active = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.ACTIVE,
        provider="paystack",
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )
    repository.save(active)

    stored = repository.get_by_wallet_id(wallet.wallet_id)

    assert stored == active

    row_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM virtual_accounts
        WHERE wallet_id = ?
        """,
        (str(wallet.wallet_id),),
    ).fetchone()[0]

    assert row_count == 1    
       