import sqlite3

from app.domain.identity.password_credential import PasswordCredential
from app.domain.repositories.password_credential_repository import (
    PasswordCredentialRepository,
)
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    text_to_datetime,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = "user_id, password_hash, updated_at"


class SqlitePasswordCredentialRepository(PasswordCredentialRepository):
    """Credential store over a single SQLite connection.

    The connection owns the transaction this repository participates in; ``save``
    only issues SQL and does not commit, so the Unit of Work decides when the write
    becomes durable. That matters more here than anywhere else in the package: a
    sign-up writes the user *and* this row, and the unit is what makes it
    impossible to end up with one and not the other.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, credential: PasswordCredential) -> PasswordCredential:
        """Insert or replace this user's credential, keyed on ``user_id``.

        Upserting rather than inserting, because ``user_id`` is the primary key: a
        change of password is the same row with a new hash, and the ``updated_at``
        with it. That is also what makes a second sign-up for one address
        impossible to express as a second credential - the row would be
        overwritten rather than duplicated, and the ``UNIQUE`` on ``users.email``
        is what actually refuses the second sign-up, one table over.
        """
        self._connection.execute(
            """
            INSERT INTO password_credentials (user_id, password_hash, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                password_hash = excluded.password_hash,
                updated_at    = excluded.updated_at
            """,
            (
                uuid_to_text(credential.user_id),
                credential.password_hash,
                datetime_to_text(credential.updated_at),
            ),
        )
        return credential

    def find_by_user_id(self, user_id) -> PasswordCredential | None:
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM password_credentials WHERE user_id = ?",
            (uuid_to_text(user_id),),
        ).fetchone()
        return self._row_to_credential(row) if row is not None else None

    def _row_to_credential(self, row) -> PasswordCredential:
        """Rebuild a credential from its row.

        The constructor runs on the way back in, as it does in every repository
        here, so a row emptied by hand - a blank hash, a bare date where a moment
        belongs - fails loudly at load rather than travelling further into the
        application wearing a valid shape.
        """
        return PasswordCredential(
            user_id=text_to_uuid(row["user_id"]),
            password_hash=row["password_hash"],
            updated_at=text_to_datetime(row["updated_at"]),
        )
