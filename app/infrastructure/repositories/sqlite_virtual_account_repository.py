import sqlite3
from uuid import UUID

from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus
from app.domain.repositories.virtual_account_repository import (
    VirtualAccountRepository,
)
from app.infrastructure.persistence.serialization import (
    enum_to_text,
    text_to_enum,
    text_to_uuid,
    uuid_to_text,
)


_COLUMNS = (
    "wallet_id, status, provider, provider_customer_code, "
    "account_number, account_name, bank_name"
)


class SqliteVirtualAccountRepository(VirtualAccountRepository):
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, account: VirtualAccount) -> VirtualAccount:
        self._connection.execute(
            """
            INSERT INTO virtual_accounts (
                wallet_id,
                status,
                provider,
                provider_customer_code,
                account_number,
                account_name,
                bank_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_id) DO UPDATE SET
                status                 = excluded.status,
                provider               = excluded.provider,
                provider_customer_code = excluded.provider_customer_code,
                account_number         = excluded.account_number,
                account_name           = excluded.account_name,
                bank_name              = excluded.bank_name
            """,
            (
                uuid_to_text(account.wallet_id),
                enum_to_text(account.status),
                account.provider,
                account.provider_customer_code,
                account.account_number,
                account.account_name,
                account.bank_name,
            ),
        )
        return account

    def get_by_wallet_id(self, wallet_id: UUID) -> VirtualAccount | None:
        row = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM virtual_accounts
            WHERE wallet_id = ?
            """,
            (uuid_to_text(wallet_id),),
        ).fetchone()

        if row is None:
            return None

        return self._row_to_virtual_account(row)

    def get_by_account_number(
        self,
        account_number: str,
    ) -> VirtualAccount | None:
        row = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM virtual_accounts
            WHERE account_number = ?
            """,
            (account_number,),
        ).fetchone()

        if row is None:
            return None

        return self._row_to_virtual_account(row)

    @staticmethod
    def _row_to_virtual_account(row: sqlite3.Row) -> VirtualAccount:
        return VirtualAccount(
            wallet_id=text_to_uuid(row["wallet_id"]),
            status=text_to_enum(VirtualAccountStatus, row["status"]),
            provider=row["provider"],
            provider_customer_code=row["provider_customer_code"],
            account_number=row["account_number"],
            account_name=row["account_name"],
            bank_name=row["bank_name"],
        )