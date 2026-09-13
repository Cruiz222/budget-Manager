import sqlite3
from datetime import datetime

from app.domain.identity.emailChange import EmailChange
from app.domain.identity.emailChangeStatus import EmailChangeStatus
from app.domain.identity.exception import (
    EmailChangeAlreadyUsedError,
    EmailChangeExpiredError,
    InvalidEmailChangeTokenError,
)
from app.domain.repositories.email_change_repository import EmailChangeRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_uuid,
    uuid_to_text,
)


class SqliteEmailChangeRepository(EmailChangeRepository):
    """Email-change store over a single SQLite connection.

    Like every other repository here, this never commits: it issues SQL inside the
    connection's transaction and lets the Unit of Work commit or roll back. That is
    load-bearing for this table in particular - the claim below and the address
    change it authorises must land together, or a crash between them would leave a
    token that has already been spent while the account still holds its old
    address.

    Note what this class does *not* do: it never touches ``users``. Moving the
    address is the use case's job, through ``UserRepository``, in the same unit -
    which is what keeps this file about one table and the atomicity about one
    transaction.
    """

    #: The columns, in one place, so the two statements below cannot drift.
    _COLUMNS = (
        "email_change_id, user_id, new_email, token_hash, status, "
        "requested_at, expires_at, settled_at"
    )

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, change: EmailChange) -> EmailChange:
        """Write this request, superseding any request this account already has.

        ``DO UPDATE`` on ``user_id``, so the account's row is *replaced* rather
        than added to - and every column moves, including the primary key, because
        the newer request is a different request and not a correction to the old
        one. The old token is dead the instant this commits, which is what makes
        asking again the remedy for a mistyped address.

        A consequence worth naming: this cannot promote an account's *spent*
        request back to ``AWAITING``, but not because it refuses to. It writes
        whatever the aggregate holds, and the only way an aggregate arrives here
        holding ``CONFIRMED`` is a caller that has read one and is writing it back
        unchanged. Nothing does that - a new request is a new ``EmailChange``, and
        ``EmailChange.issue`` always builds one ``AWAITING``.
        """
        self._connection.execute(
            f"""
            INSERT INTO email_changes ({self._COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email_change_id = excluded.email_change_id,
                new_email       = excluded.new_email,
                token_hash      = excluded.token_hash,
                status          = excluded.status,
                requested_at    = excluded.requested_at,
                expires_at      = excluded.expires_at,
                settled_at      = excluded.settled_at
            """,
            self._values(change),
        )
        return change

    def claim_by_token_hash(self, token_hash: str, as_of: datetime) -> EmailChange:
        """Spend the request in one statement; see the port for the reasoning.

        ``expires_at > ?`` and not ``>=``, which is the boundary worth pinning
        against ``EmailChange.is_expired``: that method reads ``as_of >=
        expires_at`` as expired, so the window is closed *at* the instant it
        closes. The two must agree or the store and the aggregate would disagree
        about the last instant of a request's life - a disagreement in the
        direction of a credential working for one moment longer than it was mailed
        for.

        Both ``status`` and ``settled_at`` are set here rather than by the
        aggregate, and that placement is the same argument the port makes: the
        check and the write have to be one statement, so the transition cannot be a
        method on the object being transitioned. The two move together in one
        UPDATE, which is what makes ``__post_init__``'s pairing rule a check on
        *this statement* rather than on a caller's discipline.

        The comparison is a string comparison on ISO-8601 text, which is exact
        rather than lucky: every component is zero-padded to a fixed width, so
        lexicographic order is chronological order.
        """
        cursor = self._connection.execute(
            """
            UPDATE email_changes
               SET status = ?, settled_at = ?
             WHERE token_hash = ?
               AND status = ?
               AND expires_at > ?
            """,
            (
                enum_to_text(EmailChangeStatus.CONFIRMED),
                datetime_to_text(as_of),
                token_hash,
                enum_to_text(EmailChangeStatus.AWAITING),
                datetime_to_text(as_of),
            ),
        )
        if cursor.rowcount == 1:
            # Re-read rather than build the settled object in Python, so the value
            # returned is the row that is now on disk - the same reason
            # ``SqliteConfirmationRepository.claim`` re-reads, and it matters more
            # here: the use case takes the new address from what this returns, so a
            # locally-assembled object could differ from the stored one and the
            # account would be moved to an address the row does not name.
            return self._find_by_token_hash(token_hash)

        raise self._refusal(token_hash, as_of)

    # --- internals -----------------------------------------------------------

    def _find_by_token_hash(self, token_hash: str) -> EmailChange | None:
        row = self._connection.execute(
            f"SELECT {self._COLUMNS} FROM email_changes WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return None if row is None else self._row_to_change(row)

    def _refusal(self, token_hash: str, as_of: datetime) -> Exception:
        """Why the claim above matched no row - as an exception, not raised yet.

        Returned rather than raised so the caller reads as one statement: the
        ``raise self._refusal(...)`` at the end of ``claim_by_token_hash`` says what
        is happening without a second indentation level hiding the decision. The
        same shape ``SqliteConfirmationRepository._refusal`` has.

        The three conditions the UPDATE tested are checked here in the same order,
        so the fallthrough is exact rather than a catch-all. A row that is present,
        is awaiting, and did not match can only have failed the window test.

        **Bare for the unknown case, and that is deliberate rather than lazy.** A
        message naming the token hash would make this answer differ from the same
        answer about a hash that names nothing - and the whole authorisation for
        changing an address is holding the token, so an oracle here would tell a
        guesser which of their guesses was a real token. ``errors._detail`` turns
        the empty message into the class name, so a client still gets something to
        read.
        """
        change = self._find_by_token_hash(token_hash)
        if change is None:
            return InvalidEmailChangeTokenError
        if change.status is EmailChangeStatus.CONFIRMED:
            return EmailChangeAlreadyUsedError(
                "this email change has already been answered"
            )
        return EmailChangeExpiredError(
            f"this email change expired at {change.expires_at.isoformat()} "
            f"and can no longer be answered"
        )

    @staticmethod
    def _values(change: EmailChange) -> tuple:
        """The column values, in the order the INSERT above expects.

        Written out rather than derived from the dataclass, matching every other
        repository here: the column order is a contract with the SQL, and a
        reflection-driven version would make a field rename silently reorder the
        statement.
        """
        return (
            uuid_to_text(change.email_change_id),
            uuid_to_text(change.user_id),
            change.new_email,
            change.token_hash,
            enum_to_text(change.status),
            datetime_to_text(change.requested_at),
            datetime_to_text(change.expires_at),
            datetime_to_text(change.settled_at) if change.settled_at else None,
        )

    @staticmethod
    def _row_to_change(row) -> EmailChange:
        """Rebuild a request from its row.

        The constructor runs on the way back in, as it does in every repository
        here, so a row corrupted by hand - an address that is not one, a settled
        row with no moment, a window that runs backwards - fails loudly at load
        rather than travelling further wearing a valid shape.
        """
        return EmailChange(
            email_change_id=text_to_uuid(row["email_change_id"]),
            user_id=text_to_uuid(row["user_id"]),
            new_email=row["new_email"],
            token_hash=row["token_hash"],
            status=text_to_enum(EmailChangeStatus, row["status"]),
            requested_at=text_to_datetime(row["requested_at"]),
            expires_at=text_to_datetime(row["expires_at"]),
            settled_at=(
                text_to_datetime(row["settled_at"])
                if row["settled_at"] is not None
                else None
            ),
        )
