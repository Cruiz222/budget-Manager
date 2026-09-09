import sqlite3

from app.domain.money.currency import Currency
from app.domain.money.exception import TransactionNotFoundError
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.repositories.transaction_repository import TransactionRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    metadata_to_text,
    money_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_metadata,
    text_to_money,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = (
    "transaction_id, wallet_id, type, amount, currency, internal_reference, "
    "provider_reference, narration, metadata, status, created_at, completed_at, "
    "reversed_at"
)


class SqliteTransactionRepository(TransactionRepository):
    """Transaction store over a single SQLite connection.

    Like the wallet repository, this never commits: it issues SQL inside the
    connection's transaction and lets the Unit of Work commit or roll back.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, transaction: Transaction) -> Transaction:
        # UPSERT: execute() writes the transaction as PENDING first, then
        # updates the same row to SUCCESSFUL / FAILED. ON CONFLICT updates the
        # existing row rather than inserting a duplicate.
        self._connection.execute(
            f"""
            INSERT INTO transactions ({_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                wallet_id          = excluded.wallet_id,
                type               = excluded.type,
                amount             = excluded.amount,
                currency           = excluded.currency,
                internal_reference = excluded.internal_reference,
                provider_reference = excluded.provider_reference,
                narration          = excluded.narration,
                metadata           = excluded.metadata,
                status             = excluded.status,
                created_at         = excluded.created_at,
                completed_at       = excluded.completed_at,
                reversed_at        = excluded.reversed_at
            """,
            (
                uuid_to_text(transaction.transaction_id),
                uuid_to_text(transaction.wallet_id),
                enum_to_text(transaction.type),
                money_to_text(transaction.amount),
                enum_to_text(transaction.amount.currency),
                transaction.internal_reference,
                transaction.provider_reference,
                transaction.narration,
                metadata_to_text(transaction.metadata),
                enum_to_text(transaction.status),
                datetime_to_text(transaction.created_at),
                datetime_to_text(transaction.completed_at),
                datetime_to_text(transaction.reversed_at),
            ),
        )
        return transaction

    def get_by_id(self, transaction_id) -> Transaction:
        row = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM transactions
            WHERE transaction_id = ?
            """,
            (uuid_to_text(transaction_id),),
        ).fetchone()
        if row is None:
            raise TransactionNotFoundError
        return self._row_to_transaction(row)

    def get_by_internal_reference(self, internal_reference: str) -> Transaction | None:
        row = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM transactions
            WHERE internal_reference = ?
            """,
            (internal_reference,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_transaction(row)

    def _row_to_transaction(self, row) -> Transaction:
        currency = text_to_enum(Currency, row["currency"])
        return Transaction(
            wallet_id=text_to_uuid(row["wallet_id"]),
            type=text_to_enum(TransactionType, row["type"]),
            amount=text_to_money(row["amount"], currency),
            internal_reference=row["internal_reference"],
            provider_reference=row["provider_reference"],
            narration=row["narration"],
            metadata=text_to_metadata(row["metadata"]),
            transaction_id=text_to_uuid(row["transaction_id"]),
            status=text_to_enum(TransactionStatus, row["status"]),
            created_at=text_to_datetime(row["created_at"]),
            completed_at=text_to_datetime(row["completed_at"]),
            reversed_at=text_to_datetime(row["reversed_at"]),
        )
