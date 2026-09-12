import sqlite3

from app.domain.identity.exception import UserNotFoundError
from app.domain.identity.user import User, fold_email
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    text_to_datetime,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = "user_id, email, google_subject, created_at"


class SqliteUserRepository(UserRepository):
    """User store over a single SQLite connection.

    The connection owns the transaction this repository participates in; save()
    only issues SQL and does not commit, so the Unit of Work decides when the
    write becomes durable.

    **This is the only repository in the package whose read methods take an
    argument that identifies a person, rather than one that a person is checked
    against.** There is no owner column to filter by and no ``get_owned`` here,
    because a user *is* the owner - the identity every other repository is scoped
    by is the thing this one stores. See ``UserRepository`` for why that is the
    shape of the problem rather than a hole in it.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, user: User) -> User:
        """Insert or update this user, keyed on ``user_id``.

        The ``UNIQUE`` constraints on ``email`` and ``google_subject`` are the
        backstop for the gap between "is this address taken?" and the write that
        takes it - the same role ``internal_reference`` plays for transactions
        and ``(wallet_id, name)`` plays for pots. The aggregate folds the address
        before it gets here, which is what makes that constraint mean what it
        looks like it means; without the fold, one person could hold two accounts
        and the database would be perfectly happy about it.
        """
        self._connection.execute(
            """
            INSERT INTO users (user_id, email, google_subject, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email          = excluded.email,
                google_subject = excluded.google_subject,
                created_at     = excluded.created_at
            """,
            (
                uuid_to_text(user.user_id),
                user.email,
                user.google_subject,
                datetime_to_text(user.created_at),
            ),
        )
        return user

    def get_by_id(self, user_id) -> User:
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM users WHERE user_id = ?",
            (uuid_to_text(user_id),),
        ).fetchone()
        if row is None:
            raise UserNotFoundError
        return self._row_to_user(row)

    def find_by_email(self, email: str) -> User | None:
        """Return the user with this address, or None.

        The fold is applied here rather than by the caller, because this is the
        last point before the comparison and the one place that cannot be
        skipped. It is the *same function* the aggregate calls, not a matching
        rule written twice - see ``fold_email``.
        """
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM users WHERE email = ?",
            (fold_email(email),),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def find_by_google_subject(self, subject: str) -> User | None:
        """Return the user holding this Google subject id, or None.

        Compared exactly, with no folding - mirroring ``User``, which stores a
        subject verbatim because it is an identifier Google issued rather than a
        handle a human types. Trimming it here would mean a subject that arrived
        with whitespace could be stored one way and looked up another.

        A NULL subject never matches: SQL's ``=`` is not true of NULL, so an
        account with no Google identity is simply not found by this query, which
        is exactly right. It is the reason the column is nullable rather than
        defaulted to ``''`` - an empty string *would* match, and every such
        account would match the same one.
        """
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM users WHERE google_subject = ?",
            (subject,),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def _row_to_user(self, row) -> User:
        """Rebuild a user from its row.

        The constructor runs on the way back in, as it does in every repository
        here, so a row that has been corrupted by hand - an address that is not
        an address, a bare date where a moment belongs - fails loudly at load
        rather than travelling further into the application wearing a valid
        shape.
        """
        return User(
            user_id=text_to_uuid(row["user_id"]),
            email=row["email"],
            google_subject=row["google_subject"],
            created_at=text_to_datetime(row["created_at"]),
        )
