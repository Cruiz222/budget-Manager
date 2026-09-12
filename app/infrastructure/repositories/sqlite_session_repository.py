import sqlite3

from app.domain.identity.session import Session
from app.domain.repositories.session_repository import SessionRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    text_to_datetime,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = "session_id, user_id, token_hash, issued_at, expires_at"


class SqliteSessionRepository(SessionRepository):
    """Session store over a single SQLite connection.

    The connection owns the transaction this repository participates in; ``save``
    only issues SQL and does not commit.

    Note what this class does **not** do: it never sees a token. Everything it is
    given and everything it returns carries the hash, and the plaintext exists only
    inside the use case that issued it. That is not a rule this repository follows -
    it is a rule the types make it impossible to break, since there is no method
    here that accepts or returns anything but a ``Session``, and a ``Session``
    cannot hold a token.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, session: Session) -> Session:
        """Insert this session, keyed on ``session_id``.

        The update branch exists for the shape rather than for a use: nothing in
        this codebase re-saves a session, because there is nothing about one that
        changes - expiry is fixed at issue and revocation is deletion. It is here
        because an upsert that silently did nothing on conflict would be a save
        whose name lied, and because a future "extend this session" would otherwise
        have to be discovered as a no-op first.

        ``token_hash`` is ``UNIQUE`` in the schema, and that is the same backstop
        ``users.email`` provides: two sessions sharing a token is not something
        that can realistically happen, and if it did - a broken RNG, a copy-pasted
        row - the store refuses it rather than leaving one token able to resolve to
        two identities.
        """
        self._connection.execute(
            """
            INSERT INTO sessions
                (session_id, user_id, token_hash, issued_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id    = excluded.user_id,
                token_hash = excluded.token_hash,
                issued_at  = excluded.issued_at,
                expires_at = excluded.expires_at
            """,
            (
                uuid_to_text(session.session_id),
                uuid_to_text(session.user_id),
                session.token_hash,
                datetime_to_text(session.issued_at),
                datetime_to_text(session.expires_at),
            ),
        )
        return session

    def find_by_token_hash(self, token_hash: str) -> Session | None:
        """Return the session with this hash, expired or not - see the port."""
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        return self._row_to_session(row) if row is not None else None

    def delete_by_token_hash(self, token_hash: str) -> None:
        """Remove the session with this hash.

        No check on ``rowcount``, which is the idempotence the port promises: a
        ``DELETE`` that matched nothing is a statement about a row that was already
        absent, and that is the state the caller asked for. Raising there would turn
        signing out twice into an error.
        """
        self._connection.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
        )

    def _row_to_session(self, row) -> Session:
        """Rebuild a session from its row.

        The constructor runs on the way back in, so a row whose ``expires_at``
        precedes its ``issued_at`` - a corrupt window - is refused at load. That
        check is why this conversion is worth having rather than reading columns at
        the call site: a born-dead session would otherwise present as a login that
        reports success and then does not work.
        """
        return Session(
            session_id=text_to_uuid(row["session_id"]),
            user_id=text_to_uuid(row["user_id"]),
            token_hash=row["token_hash"],
            issued_at=text_to_datetime(row["issued_at"]),
            expires_at=text_to_datetime(row["expires_at"]),
        )
