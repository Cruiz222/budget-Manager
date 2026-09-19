import sqlite3
from datetime import datetime
from decimal import Decimal

from app.domain.money.currency import Currency
from app.domain.money.exception import TransactionNotFoundError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.repositories.transaction_repository import TransactionRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    destination_to_text,
    enum_to_text,
    metadata_to_text,
    money_to_text,
    optional_uuid_to_text,
    text_to_datetime,
    text_to_destination,
    text_to_enum,
    text_to_metadata,
    text_to_money,
    text_to_optional_uuid,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = (
    "transaction_id, wallet_id, type, amount, currency, internal_reference, "
    "provider_reference, narration, metadata, destination, fund_id, status, "
    "created_at, completed_at, reversed_at"
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                wallet_id          = excluded.wallet_id,
                type               = excluded.type,
                amount             = excluded.amount,
                currency           = excluded.currency,
                internal_reference = excluded.internal_reference,
                provider_reference = excluded.provider_reference,
                narration          = excluded.narration,
                metadata           = excluded.metadata,
                destination        = excluded.destination,
                fund_id            = excluded.fund_id,
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
                destination_to_text(transaction.destination),
                optional_uuid_to_text(transaction.fund_id),
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

    def get_by_wallet_id(self, wallet_id) -> list[Transaction]:
        rows = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM transactions
            WHERE wallet_id = ?
            ORDER BY created_at
            """,
            (uuid_to_text(wallet_id),),
        ).fetchall()
        return [self._row_to_transaction(row) for row in rows]

    def get_by_provider_reference(self, provider_reference: str) -> Transaction | None:
        row = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM transactions
            WHERE provider_reference = ?
            """,
            (provider_reference,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_transaction(row)

    def list_awaiting_provider(self) -> list[Transaction]:
        """See the port for why the filter is two conditions rather than one.

        ``provider_reference IS NOT NULL`` is the half that is easy to leave out
        and the half that keeps a CLI withdrawal from being asked about. The
        tiebreak on ``transaction_id`` is not decoration: ``created_at`` is
        stored at second resolution, so two rows written in the same second
        would otherwise come back in whatever order SQLite felt like, and a
        bounded batch would then be free to starve one of them indefinitely -
        the exact failure the ordering contract exists to prevent.
        """
        rows = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM transactions
            WHERE status = ?
              AND provider_reference IS NOT NULL
            ORDER BY created_at, transaction_id
            """,
            (enum_to_text(TransactionStatus.PENDING),),
        ).fetchall()
        return [self._row_to_transaction(row) for row in rows]

    def outflow_total_between(
        self,
        wallet_id,
        start: datetime,
        end: datetime,
        currency: Currency,
    ) -> Money:
        """See the port for what counts and why the boundary is half-open.

        **The sum is done in Python over a selected column, and not by ``SUM()``
        in SQL.** This is the one decision in this method worth stating, because
        ``SELECT SUM(amount)`` is the obvious line and it is wrong: amounts are
        stored as TEXT - ``money_to_text`` exists precisely so that they are,
        because "binary floats cannot represent money exactly" - and SQLite's
        ``SUM`` coerces a text operand to REAL. So the arithmetic would happen in
        binary floating point and a total of a hundred small movements would come
        back as 999.9999999999999 rather than 1000.00. The error is small and the
        rule it breaks is not: the daily cap is compared against this total with
        ``>``, so a total that is a hair under the ceiling admits a movement that
        is a hair over.

        Selecting the rows and summing ``Decimal`` in Python keeps the arithmetic
        in the type that can hold it. The rows are one wallet's, one day's, and
        one direction's, which is a handful at most; the read is indexed by
        ``wallet_id`` and bounded by ``created_at``, both of which every ledger
        query here already uses. This is a case where the correct version is also
        the cheap one.

        ``text_to_money`` is reused rather than ``Decimal(row["amount"])`` typed
        out again, so the exact round-trip a loaded transaction gets is the
        round-trip each addend gets, and the sum cannot disagree with the rows it
        came from.
        """
        rows = self._connection.execute(
            """
            SELECT amount
            FROM transactions
            WHERE wallet_id = ?
              AND currency = ?
              AND type IN (?, ?)
              AND status IN (?, ?)
              AND created_at >= ?
              AND created_at <  ?
            """,
            (
                uuid_to_text(wallet_id),
                enum_to_text(currency),
                enum_to_text(TransactionType.WITHDRAWAL),
                enum_to_text(TransactionType.PAYOUT),
                # PENDING and SUCCESSFUL, in that order so that the two statuses
                # read in the order the port's docstring argues them.
                enum_to_text(TransactionStatus.PENDING),
                enum_to_text(TransactionStatus.SUCCESSFUL),
                # ``datetime_to_text`` on both bounds, which is the same
                # serialization the column is written with - so the comparison is
                # between two ISO-8601 strings in one frame, and it is why
                # ``limit_day_bounds`` returns naive local moments rather than
                # offset-carrying ones.
                datetime_to_text(start),
                datetime_to_text(end),
            ),
        ).fetchall()

        return sum(
            (text_to_money(row["amount"], currency) for row in rows),
            start=Money(Decimal("0.00"), currency),
        )

    def pending_credit_total(self, wallet_id, currency: Currency) -> Money:
        """See the port for what counts and why there is no time window.

        **The sum is done in Python and not by ``SUM()``, for the reason above
        rather than by analogy.** Amounts are stored as TEXT and SQLite's
        ``SUM`` coerces a text operand to REAL, so ``SELECT SUM(amount)`` would
        do the arithmetic in binary floating point - and this total is added to
        a wallet's balances and compared against the balance cap with ``>``, so
        a hair under admits a deposit that is a hair over. This is the same
        hazard in the same units, one method over.

        **There is no ``created_at`` bound**, unlike the method above, and the
        absence is the port's argument rather than an omission: a day bounds an
        outflow because its ceiling is a daily allowance, and a pending
        collection has no day. It is in flight until it settles or is abandoned,
        whenever that happens - so a payment waiting across midnight is counted,
        which is precisely the row that most needs counting.

        ``PENDING`` is the only status, because it is the only one whose money
        is neither in the wallet nor gone. ``SUCCESSFUL`` would be double
        counting - the caller adds the wallet's balances separately - and
        ``FAILED`` and ``REVERSED`` are finished facts about money that is not
        coming.
        """
        rows = self._connection.execute(
            """
            SELECT amount
            FROM transactions
            WHERE wallet_id = ?
              AND currency = ?
              AND type = ?
              AND status = ?
            """,
            (
                uuid_to_text(wallet_id),
                enum_to_text(currency),
                enum_to_text(TransactionType.DEPOSIT),
                enum_to_text(TransactionStatus.PENDING),
            ),
        ).fetchall()

        return sum(
            (text_to_money(row["amount"], currency) for row in rows),
            start=Money(Decimal("0.00"), currency),
        )

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
            destination=text_to_destination(row["destination"]),
            fund_id=text_to_optional_uuid(row["fund_id"]),
            transaction_id=text_to_uuid(row["transaction_id"]),
            status=text_to_enum(TransactionStatus, row["status"]),
            created_at=text_to_datetime(row["created_at"]),
            completed_at=text_to_datetime(row["completed_at"]),
            reversed_at=text_to_datetime(row["reversed_at"]),
        )
