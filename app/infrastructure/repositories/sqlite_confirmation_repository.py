import sqlite3
import uuid
from datetime import datetime

from app.domain.money.confirmation import Confirmation
from app.domain.money.confirmationKind import ConfirmationKind
from app.domain.money.confirmationStatus import ConfirmationStatus
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    ConfirmationAlreadyUsedError,
    ConfirmationExpiredError,
    ConfirmationNotFoundError,
)
from app.domain.repositories.confirmation_repository import ConfirmationRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    destination_to_text,
    enum_to_text,
    money_to_text,
    optional_uuid_to_text,
    text_to_datetime,
    text_to_destination,
    text_to_enum,
    text_to_money,
    text_to_optional_uuid,
    text_to_uuid,
    uuid_to_text,
)


class SqliteConfirmationRepository(ConfirmationRepository):
    """Confirmation store over a single SQLite connection.

    Like every other repository here, this never commits: it issues SQL inside
    the connection's transaction and lets the Unit of Work commit or roll back.
    That is load-bearing for this table in particular - the claim below and the
    ledger row the claimed request authorises must land together, or a crash
    between them would leave a request recorded as carried out with nothing to
    show for it.
    """

    #: The columns, in one place, so the three statements below cannot drift.
    _COLUMNS = (
        "confirmation_id, user_id, wallet_id, kind, amount, currency, destination, "
        "fund_name, internal_reference, status, created_at, expires_at, transaction_id"
    )

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def add(self, confirmation: Confirmation) -> bool:
        """Insert the request, and report whether this call is the one that did.

        ``DO NOTHING`` rather than ``DO UPDATE``, unlike ``save`` below, and the
        difference is the point: this is a claim, not a write. A request is
        created once, with the window it was given, and a second attempt under the
        same key is not a correction - it is the same request arriving twice,
        which a client retrying a lost response does quite legitimately. It must
        change nothing, and the caller is expected to answer it with ``find``.
        """
        cursor = self._connection.execute(
            f"""
            INSERT INTO confirmations ({self._COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_id, internal_reference) DO NOTHING
            """,
            self._values(confirmation),
        )
        return cursor.rowcount == 1

    def claim(
        self,
        confirmation_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: ConfirmationKind,
        as_of: datetime,
    ) -> Confirmation:
        """Spend the request in one statement; see the port for the reasoning.

        ``expires_at > ?`` and not ``>=``, which is the boundary worth pinning
        against ``Confirmation.is_expired``: that method reads ``as_of >=
        expires_at`` as expired, so the window is closed *at* the instant it
        closes. The two must agree or the store and the aggregate would disagree
        about the last instant of a request's life - a disagreement in the
        direction of moving money for one extra moment.

        The comparison is a string comparison on ISO-8601 text, which is exact
        rather than lucky: every component is zero-padded to a fixed width, so
        lexicographic order is chronological order.
        """
        cursor = self._connection.execute(
            """
            UPDATE confirmations
               SET status = ?
             WHERE confirmation_id = ?
               AND user_id = ?
               AND kind = ?
               AND status = ?
               AND expires_at > ?
            """,
            (
                enum_to_text(ConfirmationStatus.CONFIRMED),
                uuid_to_text(confirmation_id),
                uuid_to_text(user_id),
                enum_to_text(kind),
                enum_to_text(ConfirmationStatus.AWAITING),
                datetime_to_text(as_of),
            ),
        )
        if cursor.rowcount == 1:
            return self.get_owned(confirmation_id, user_id)

        raise self._refusal(confirmation_id, user_id, kind, as_of)

    def find(
        self, wallet_id: uuid.UUID, internal_reference: str
    ) -> Confirmation | None:
        row = self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM confirmations
            WHERE wallet_id = ?
              AND internal_reference = ?
            """,
            (uuid_to_text(wallet_id), internal_reference),
        ).fetchone()
        return None if row is None else self._row_to_confirmation(row)

    def get_owned(
        self, confirmation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Confirmation:
        confirmation = self._find_owned(confirmation_id, user_id)
        if confirmation is None:
            # Raised bare, and that is the point rather than an omission. A
            # message naming the id would make this 404 differ from the same 404
            # against an id that names nothing - the two answers would be
            # distinguishable by a client comparing bodies, and the whole
            # authorisation for answering a request is knowing its id, so an
            # oracle here would be worth more than an oracle anywhere else in the
            # API. Bare matches ``WalletNotFoundError``, which is raised the same
            # way for the same reason; ``errors._detail`` turns the empty message
            # into the class name, so the client still gets something to read.
            raise ConfirmationNotFoundError
        return confirmation

    def save(self, confirmation: Confirmation) -> Confirmation:
        """Write the request's current state, overwriting what was there.

        The opposite conflict clause to ``add``, and both are correct. A request
        that has just been spent really does have a truer second version - and
        this is not an alternative route to spending one, because only
        ``status`` and ``transaction_id`` move here. A caller cannot promote an
        expired or already-spent request back to ``AWAITING`` through this
        method: it writes whatever status the aggregate holds, and the only
        transition the domain models is the one ``claim`` performs.
        """
        self._connection.execute(
            f"""
            INSERT INTO confirmations ({self._COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(confirmation_id) DO UPDATE SET
                status         = excluded.status,
                transaction_id = excluded.transaction_id
            """,
            self._values(confirmation),
        )
        return confirmation

    # --- internals -----------------------------------------------------------

    def _find_owned(
        self, confirmation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Confirmation | None:
        """The row, if it is this actor's. Absent and foreign are both None.

        One lookup for both questions, so no caller can accidentally answer them
        differently - which is the same reason ``get_owned`` and ``_refusal``
        both go through it.
        """
        row = self._connection.execute(
            f"""
            SELECT {self._COLUMNS}
            FROM confirmations
            WHERE confirmation_id = ?
              AND user_id = ?
            """,
            (uuid_to_text(confirmation_id), uuid_to_text(user_id)),
        ).fetchone()
        return None if row is None else self._row_to_confirmation(row)

    def _refusal(
        self,
        confirmation_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: ConfirmationKind,
        as_of: datetime,
    ) -> Exception:
        """Why the claim above matched no row - as an exception, not raised yet.

        Returned rather than raised so the caller reads as one statement: the
        ``raise self._refusal(...)`` at the end of ``claim`` says what is
        happening without a second indentation level hiding the decision.

        The four conditions the UPDATE tested are checked here in the same order,
        so the fallthrough is exact rather than a catch-all. A row that is
        present, is this actor's, is of this kind, and did not match can only
        have failed the window test.

        ``kind`` is checked *before* the status, and that ordering is the point
        of passing it in at all. Without it a mismatched kind would fall through
        to ``ConfirmationExpiredError`` - a name that sends a caller to look at a
        clock, when what is wrong is that they asked the wrong question. It is
        reported as not-found because that is what it is: the withdrawal request
        with this id does not exist, whatever else does.
        """
        confirmation = self._find_owned(confirmation_id, user_id)
        if confirmation is None or confirmation.kind is not kind:
            # Absent, foreign and wrong-kind alike, for the reason in the port -
            # and bare, for the reason ``get_owned`` gives: the message must not
            # name the id, or the answer to "this is not yours" and the answer to
            # "this never was" would differ by more than their shared status code.
            return ConfirmationNotFoundError
        if confirmation.status is ConfirmationStatus.CONFIRMED:
            return ConfirmationAlreadyUsedError(
                f"confirmation {confirmation_id} has already been answered"
            )
        return ConfirmationExpiredError(
            f"confirmation {confirmation_id} expired at "
            f"{confirmation.expires_at.isoformat()} and can no longer be answered"
        )

    @staticmethod
    def _values(confirmation: Confirmation) -> tuple:
        """The column values, in the order the two statements above expect.

        Shared so that ``add`` and ``save`` cannot disagree about which column
        holds which field - a mistake that would be invisible until a round trip,
        and would then look like a serialization bug.

        The amount's currency travels in its own column rather than being assumed
        from the wallet, because the wallet is loaded separately and a Money is
        meaningless without its currency. It is redundant with the wallet for
        every row this codebase writes - the pair cannot disagree, because the
        aggregate refuses a currency mismatch at the moment the money would move -
        and it is written anyway so the row is readable on its own, exactly as
        ``instruction_to_dict`` writes one.
        """
        return (
            uuid_to_text(confirmation.confirmation_id),
            uuid_to_text(confirmation.user_id),
            uuid_to_text(confirmation.wallet_id),
            enum_to_text(confirmation.kind),
            money_to_text(confirmation.amount) if confirmation.amount else None,
            enum_to_text(confirmation.amount.currency) if confirmation.amount else None,
            destination_to_text(confirmation.destination),
            confirmation.fund_name,
            confirmation.internal_reference,
            enum_to_text(confirmation.status),
            datetime_to_text(confirmation.created_at),
            datetime_to_text(confirmation.expires_at),
            optional_uuid_to_text(confirmation.transaction_id),
        )

    @staticmethod
    def _row_to_confirmation(row) -> Confirmation:
        # ``text_to_money`` needs the currency, and it is NULL exactly when the
        # amount is - a CLOSE carries neither. Reading the pair together is what
        # keeps that coupling in one place rather than at each field.
        amount = (
            text_to_money(row["amount"], text_to_enum(Currency, row["currency"]))
            if row["amount"] is not None
            else None
        )
        return Confirmation(
            confirmation_id=text_to_uuid(row["confirmation_id"]),
            user_id=text_to_uuid(row["user_id"]),
            wallet_id=text_to_uuid(row["wallet_id"]),
            kind=text_to_enum(ConfirmationKind, row["kind"]),
            internal_reference=row["internal_reference"],
            status=text_to_enum(ConfirmationStatus, row["status"]),
            created_at=text_to_datetime(row["created_at"]),
            expires_at=text_to_datetime(row["expires_at"]),
            amount=amount,
            destination=text_to_destination(row["destination"]),
            fund_name=row["fund_name"],
            transaction_id=text_to_optional_uuid(row["transaction_id"]),
        )
