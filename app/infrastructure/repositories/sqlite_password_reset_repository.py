import sqlite3
from datetime import datetime

from app.domain.identity.exception import (
    InvalidPasswordResetTokenError,
    PasswordResetAlreadyUsedError,
    PasswordResetExpiredError,
)
from app.domain.identity.passwordReset import PasswordReset
from app.domain.identity.passwordResetStatus import PasswordResetStatus
from app.domain.repositories.password_reset_repository import PasswordResetRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_uuid,
    uuid_to_text,
)


class SqlitePasswordResetRepository(PasswordResetRepository):
    """Password-reset store over a single SQLite connection.

    Like every other repository here, this never commits: it issues SQL inside the
    connection's transaction and lets the Unit of Work commit or roll back. That is
    load-bearing for this table more than for any other, because the claim below is
    only one of the three writes a confirm performs - the password replacement and
    the session deletions are the other two, in different repositories - and all
    three have to land together.

    Note what this class does *not* do: it never sees a password. There is no
    column for one and no parameter through which one could arrive, so this file
    cannot be the thing that breaks "a password is written down exactly once, as an
    argon2 hash". That is a rule the types make impossible to break rather than a
    rule this class remembers - the same shape of guarantee
    ``SqliteSessionRepository`` gives about tokens.
    """

    #: The columns, in one place, so the statements below cannot drift.
    _COLUMNS = (
        "password_reset_id, user_id, token_hash, status, "
        "requested_at, expires_at, settled_at"
    )

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, reset: PasswordReset) -> PasswordReset:
        """Write this request, superseding any request this account already has.

        ``DO UPDATE`` on ``user_id``, so the account's row is *replaced* rather
        than added to - and every column moves, including the primary key, because
        a second request is a different request and not a correction to the first.
        The old code is dead the instant this commits, which is what makes asking
        again the remedy for a mail that never arrived.

        A consequence worth naming, and it is the same one ``email_changes``'
        column names: this cannot promote an account's *spent* request back to
        ``AWAITING``, but not because it refuses to. It writes whatever the
        aggregate holds, and the only way an aggregate arrives here holding
        ``CONFIRMED`` is a caller that has read one and is writing it back
        unchanged. Nothing does that - a new request is a new ``PasswordReset``,
        and ``PasswordReset.issue`` always builds one ``AWAITING``.

        The seven values match the seven columns of ``_COLUMNS`` exactly, which is
        why the INSERT names the columns rather than relying on table order: the
        payload column ``email_changes`` has between ``user_id`` and ``token_hash``
        is absent here, so a statement copied from that repository and adjusted by
        eye is the one mistake this signature invites.
        """
        self._connection.execute(
            f"""
            INSERT INTO password_resets ({self._COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                password_reset_id = excluded.password_reset_id,
                token_hash        = excluded.token_hash,
                status            = excluded.status,
                requested_at      = excluded.requested_at,
                expires_at        = excluded.expires_at,
                settled_at        = excluded.settled_at
            """,
            self._values(reset),
        )
        return reset

    def claim_by_token_hash(self, token_hash: str, as_of: datetime) -> PasswordReset:
        """Spend the request in one statement; see the port for the reasoning.

        ``expires_at > ?`` and not ``>=``, which is the boundary worth pinning
        against ``PasswordReset.is_expired``: that method reads ``as_of >=
        expires_at`` as expired, so the window is closed *at* the instant it
        closes. The two must agree or the store and the aggregate would disagree
        about the last instant of a request's life - and the direction of the
        disagreement would be a code that replaces an account's password one
        instant after it was promised to have stopped working.

        Both ``status`` and ``settled_at`` are set here rather than by the
        aggregate, and that placement is the same argument the port makes: the
        check and the write have to be one statement, so the transition cannot be a
        method on the object being transitioned. The two move together in one
        UPDATE, which is what makes ``__post_init__``'s pairing rule a check on
        *this statement* rather than on a caller's discipline.

        The comparison is a string comparison on ISO-8601 text, which is exact
        rather than lucky: every component is zero-padded to a fixed width, so
        lexicographic order is chronological order.

        **What is not in the ``WHERE`` clause matters as much as what is.** There is
        no ``user_id`` and no address: the account whose password this authorises is
        the one the claimed row names, and nothing a request supplied can widen
        that. So the single fact a caller has to hold is the token.
        """
        cursor = self._connection.execute(
            """
            UPDATE password_resets
               SET status = ?, settled_at = ?
             WHERE token_hash = ?
               AND status = ?
               AND expires_at > ?
            """,
            (
                enum_to_text(PasswordResetStatus.CONFIRMED),
                datetime_to_text(as_of),
                token_hash,
                enum_to_text(PasswordResetStatus.AWAITING),
                datetime_to_text(as_of),
            ),
        )
        if cursor.rowcount == 1:
            # Re-read rather than build the settled object in Python, so the value
            # returned is the row that is now on disk - the same reason
            # ``SqliteConfirmationRepository.claim`` and ``SqliteEmailChange
            # Repository.claim_by_token_hash`` re-read. It matters here for the
            # reason it matters there and one more: the use case takes the account
            # to act on from ``reset.user_id``, so a locally-assembled object could
            # name a different account from the one the stored row names - and the
            # thing being replaced would be a password.
            return self._find_by_token_hash(token_hash)

        raise self._refusal(token_hash, as_of)

    # --- internals -----------------------------------------------------------

    def _find_by_token_hash(self, token_hash: str) -> PasswordReset | None:
        row = self._connection.execute(
            f"SELECT {self._COLUMNS} FROM password_resets WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return None if row is None else self._row_to_reset(row)

    def _refusal(self, token_hash: str, as_of: datetime) -> Exception:
        """Why the claim above matched no row - as an exception, not raised yet.

        Returned rather than raised so the caller reads as one statement: the
        ``raise self._refusal(...)`` at the end of ``claim_by_token_hash`` says what
        is happening without a second indentation level hiding the decision. The
        same shape ``SqliteEmailChangeRepository._refusal`` has.

        The three conditions the UPDATE tested are checked here in the same order,
        so the fallthrough is exact rather than a catch-all. A row that is present,
        is awaiting, and did not match can only have failed the window test.

        **Bare for the unknown case, and that is deliberate rather than lazy.** A
        message naming the token hash would make this answer differ from the same
        answer about a hash that names nothing - and the whole authorisation for
        replacing a password is holding the token, so an oracle here would tell a
        guesser which of their guesses was a real token. ``errors._detail`` turns
        the empty message into the class name, so a client still gets something to
        read.

        **The three classes are not interchangeable here**, which is why there are
        three rather than one refusal for the whole claim. "That code means nothing"
        and "you already used that code" and "that code has expired" send a person
        to three different actions, and a caller who can tell them apart learns
        nothing they could not have learned by holding the token in the first place.
        """
        reset = self._find_by_token_hash(token_hash)
        if reset is None:
            return InvalidPasswordResetTokenError
        if reset.status is PasswordResetStatus.CONFIRMED:
            return PasswordResetAlreadyUsedError(
                "this password reset has already been answered"
            )
        return PasswordResetExpiredError(
            f"this password reset expired at {reset.expires_at.isoformat()} "
            f"and can no longer be answered"
        )

    @staticmethod
    def _values(reset: PasswordReset) -> tuple:
        """The column values, in the order the INSERT above expects.

        Written out rather than derived from the dataclass, matching every other
        repository here: the column order is a contract with the SQL, and a
        reflection-driven version would make a field rename silently reorder the
        statement. Note the absence of a payload here that ``_values`` of the
        email-change store has - this tuple is one shorter than that one, and the
        only field it could have carried is the password, which is not a field.
        """
        return (
            uuid_to_text(reset.password_reset_id),
            uuid_to_text(reset.user_id),
            reset.token_hash,
            enum_to_text(reset.status),
            datetime_to_text(reset.requested_at),
            datetime_to_text(reset.expires_at),
            datetime_to_text(reset.settled_at) if reset.settled_at else None,
        )

    @staticmethod
    def _row_to_reset(row) -> PasswordReset:
        """Rebuild a request from its row.

        The constructor runs on the way back in, as it does in every repository
        here, so a row corrupted by hand - a settled row with no moment, an
        awaiting one carrying one, a window that runs backwards - fails loudly at
        load rather than travelling further wearing a valid shape.
        """
        return PasswordReset(
            password_reset_id=text_to_uuid(row["password_reset_id"]),
            user_id=text_to_uuid(row["user_id"]),
            token_hash=row["token_hash"],
            status=text_to_enum(PasswordResetStatus, row["status"]),
            requested_at=text_to_datetime(row["requested_at"]),
            expires_at=text_to_datetime(row["expires_at"]),
            settled_at=(
                text_to_datetime(row["settled_at"])
                if row["settled_at"] is not None
                else None
            ),
        )
