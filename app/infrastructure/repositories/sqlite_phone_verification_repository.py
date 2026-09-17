import sqlite3
from datetime import datetime

from app.domain.identity.exception import (
    InvalidPhoneVerificationTokenError,
    PhoneVerificationAlreadyUsedError,
    PhoneVerificationExpiredError,
)
from app.domain.identity.phoneVerification import PhoneVerification
from app.domain.identity.phoneVerificationStatus import PhoneVerificationStatus
from app.domain.repositories.phone_verification_repository import (
    PhoneVerificationRepository,
)
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_uuid,
    uuid_to_text,
)


class SqlitePhoneVerificationRepository(PhoneVerificationRepository):
    """Phone-verification store over a single SQLite connection.

    Like every other repository here, this never commits: it issues SQL inside the
    connection's transaction and lets the Unit of Work commit or roll back. That is
    load-bearing for this table, because the claim below is only one of the two writes
    a confirm performs - the account and its credential are the other one, in two other
    repositories - and all three have to land together. A claimed code with no account
    behind it is a number that cannot be signed up with at all until the row expires,
    which is the slot described on ``PhoneVerificationStatus.EXPIRED``.

    Note what this class does *not* do: it never sees a password. There is no column
    for one and no parameter through which one could arrive, so this file cannot be the
    thing that breaks "a password is written down exactly once, as an argon2 hash".
    That is a rule the types make impossible to break rather than a rule this class
    remembers - the same shape of guarantee ``SqliteSessionRepository`` gives about
    tokens, and the same one ``SqlitePasswordResetRepository`` gives next door.

    **It also never touches ``users``**, and the absence is worth stating because a
    reader arriving from the confirm flow will wonder where the account is created. It
    is not here. Creating the account is ``SqliteUserRepository`` and its credential,
    and the two are tied to this claim by the unit rather than by a statement.
    """

    #: The columns, in one place, so the statements below cannot drift.
    _COLUMNS = (
        "phone_verification_id, phone, token_hash, status, "
        "requested_at, expires_at, settled_at"
    )

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, verification: PhoneVerification) -> PhoneVerification:
        """Write this request, superseding any request this number already has.

        ``DO UPDATE`` on ``phone``, so the number's row is *replaced* rather than added
        to - and every column moves, including the primary key, because a second request
        is a different request and not a correction to the first. The old code is dead
        the instant this commits, which is what makes asking again the remedy for a text
        that never arrived.

        **What ``ON CONFLICT(phone)`` is doing here is worth one sentence, because the
        column it names is the one an account does not have yet.** A verification is
        written *before* anybody owns the number, so this statement is also what makes
        the ``UNIQUE`` slot the only thing standing between a number and two accounts -
        the second ``save`` replaces the pending request rather than joining it, and the
        account write that follows a claim is the write that finally takes the slot on
        ``users.phone``. Two tables hold the same guarantee for the two different
        halves of the flow's life.

        A consequence worth naming, and it is the same one ``PasswordReset.save`` names:
        this cannot promote a number's *spent* request back to ``AWAITING``, but not
        because it refuses to. It writes whatever the aggregate holds, and the only way
        an aggregate arrives here holding ``CONFIRMED`` is a caller that has read one
        and is writing it back unchanged. Nothing does that - a new request is a new
        ``PhoneVerification``, and ``PhoneVerification.issue`` always builds one
        ``AWAITING``.

        The seven values match the seven columns of ``_COLUMNS`` exactly, which is why
        the INSERT names the columns rather than relying on table order: this tuple is
        the same length as ``sqlite_password_reset_repository``'s and differs from it in
        exactly one position, so a statement copied from that repository and adjusted by
        eye is the one mistake this signature invites.
        """
        self._connection.execute(
            f"""
            INSERT INTO phone_verifications ({self._COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                phone_verification_id = excluded.phone_verification_id,
                token_hash            = excluded.token_hash,
                status                = excluded.status,
                requested_at          = excluded.requested_at,
                expires_at            = excluded.expires_at,
                settled_at            = excluded.settled_at
            """,
            self._values(verification),
        )
        return verification

    def claim_by_token_hash(
        self, token_hash: str, as_of: datetime
    ) -> PhoneVerification:
        """Spend the request in one statement; see the port for the reasoning.

        ``expires_at > ?`` and not ``>=``, which is the boundary worth pinning against
        ``PhoneVerification.is_expired``: that method reads ``as_of >= expires_at`` as
        expired, so the window is closed *at* the instant it closes. The two must agree
        or the store and the aggregate would disagree about the last instant of a
        request's life - and the direction of the disagreement would be a number claimed
        one instant after the person was told the code had stopped working. The same
        pair is pinned in the two sibling stores.

        Both ``status`` and ``settled_at`` are set here rather than by the aggregate, and
        that placement is the same argument the port makes: the check and the write have
        to be one statement, so the transition cannot be a method on the object being
        transitioned. The two move together in one UPDATE, which is what makes
        ``__post_init__``'s pairing rule a check on *this statement* rather than on a
        caller's discipline.

        The comparison is a string comparison on ISO-8601 text, which is exact rather
        than lucky: every component is zero-padded to a fixed width, so lexicographic
        order is chronological order.

        **What is not in the ``WHERE`` clause matters as much as what is**, and it is the
        decision the port argues at length. There is no ``phone`` here: the number this
        code authorises is the one the claimed row names, and a caller's own idea of
        which number it is has no bearing on what gets spent. So the single fact a caller
        has to hold is the token - which is also what keeps the three refusals below
        falling through exactly.
        """
        cursor = self._connection.execute(
            """
            UPDATE phone_verifications
               SET status = ?, settled_at = ?
             WHERE token_hash = ?
               AND status = ?
               AND expires_at > ?
            """,
            (
                enum_to_text(PhoneVerificationStatus.CONFIRMED),
                datetime_to_text(as_of),
                token_hash,
                enum_to_text(PhoneVerificationStatus.AWAITING),
                datetime_to_text(as_of),
            ),
        )
        if cursor.rowcount == 1:
            # Re-read rather than build the settled object in Python, so the value
            # returned is the row that is now on disk - the same reason every other
            # claim here re-reads. It matters here for the reason it matters next door
            # and one more: the use case takes the number to create an account for from
            # ``verification.phone``, so a locally-assembled object could name a
            # different number from the one the stored row names - and the thing being
            # created would be an account.
            return self._find_by_token_hash(token_hash)

        raise self._refusal(token_hash, as_of)

    # --- internals -----------------------------------------------------------

    def _find_by_token_hash(self, token_hash: str) -> PhoneVerification | None:
        row = self._connection.execute(
            f"SELECT {self._COLUMNS} FROM phone_verifications WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return None if row is None else self._row_to_verification(row)

    def _refusal(self, token_hash: str, as_of: datetime) -> Exception:
        """Why the claim above matched no row - as an exception, not raised yet.

        Returned rather than raised so the caller reads as one statement: the
        ``raise self._refusal(...)`` at the end of ``claim_by_token_hash`` says what is
        happening without a second indentation level hiding the decision. The same shape
        ``SqliteEmailChangeRepository._refusal`` and
        ``SqlitePasswordResetRepository._refusal`` have.

        The three conditions the UPDATE tested are checked here in the same order, so the
        fallthrough is exact rather than a catch-all. A row that is present, is awaiting,
        and did not match can only have failed the window test - and it is *only* exact
        because the claim does not scope by number, which the port argues for.

        **Bare for the unknown case, and that is deliberate rather than lazy.** A message
        naming the token hash would make this answer differ from the same answer about a
        hash that names nothing - and the whole authorisation for claiming a number is
        holding the token, so an oracle here would tell a guesser which of their guesses
        was a real token. ``errors._detail`` turns the empty message into the class name,
        so a client still gets something to read. The number is not named either, for a
        second reason that is this table's alone: a phone number is the smallest
        enumerable identifier in this product, so a sentence repeating one back puts it
        somewhere it need not be.

        **The three classes are not interchangeable here**, which is why there are three
        rather than one refusal for the whole claim. "That code means nothing" and "you
        already used that code" and "that code has expired" send a person to three
        different actions - try again, log in, ask for another - and a caller who can tell
        them apart learns nothing they could not have learned by holding the token in the
        first place.
        """
        verification = self._find_by_token_hash(token_hash)
        if verification is None:
            return InvalidPhoneVerificationTokenError
        if verification.status is PhoneVerificationStatus.CONFIRMED:
            return PhoneVerificationAlreadyUsedError(
                "this phone verification has already been answered"
            )
        return PhoneVerificationExpiredError(
            f"this phone verification expired at "
            f"{verification.expires_at.isoformat()} and can no longer be answered"
        )

    @staticmethod
    def _values(verification: PhoneVerification) -> tuple:
        """The column values, in the order the INSERT above expects.

        Written out rather than derived from the dataclass, matching every other
        repository here: the column order is a contract with the SQL, and a
        reflection-driven version would make a field rename silently reorder the
        statement. Note the second position - ``phone`` where
        ``sqlite_password_reset_repository`` has ``user_id`` - which is the one field
        this row carries that that one has no equivalent of, and the reason the two
        tuples are the same length.
        """
        return (
            uuid_to_text(verification.phone_verification_id),
            verification.phone,
            verification.token_hash,
            enum_to_text(verification.status),
            datetime_to_text(verification.requested_at),
            datetime_to_text(verification.expires_at),
            datetime_to_text(verification.settled_at)
            if verification.settled_at
            else None,
        )

    @staticmethod
    def _row_to_verification(row) -> PhoneVerification:
        """Rebuild a request from its row.

        The constructor runs on the way back in, as it does in every repository here, so
        a row corrupted by hand - a settled row with no moment, an awaiting one carrying
        one, a window that runs backwards - fails loudly at load rather than travelling
        further wearing a valid shape. It is also where ``checked_phone`` runs a second
        time, so a number that reached the table by a route other than the aggregate
        still cannot come back out of it unfolded.
        """
        return PhoneVerification(
            phone_verification_id=text_to_uuid(row["phone_verification_id"]),
            phone=row["phone"],
            token_hash=row["token_hash"],
            status=text_to_enum(PhoneVerificationStatus, row["status"]),
            requested_at=text_to_datetime(row["requested_at"]),
            expires_at=text_to_datetime(row["expires_at"]),
            settled_at=(
                text_to_datetime(row["settled_at"])
                if row["settled_at"] is not None
                else None
            ),
        )
