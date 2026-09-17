import sqlite3

from app.domain.identity.exception import UserNotFoundError
from app.domain.identity.phoneNumber import fold_phone
from app.domain.identity.user import User, fold_email
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    text_to_datetime,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = "user_id, email, phone, google_subject, created_at"


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

        The ``UNIQUE`` constraints on ``email``, ``phone`` and ``google_subject``
        are the backstop for the gap between "is this identifier taken?" and the
        write that takes it - the same role ``internal_reference`` plays for
        transactions and ``(wallet_id, name)`` plays for pots. The aggregate folds
        both identifiers before they get here, which is what makes those
        constraints mean what they look like they mean; without the folds, one
        person could hold two accounts and the database would be perfectly happy
        about it.

        **A ``NULL`` identifier is written as ``NULL`` and not as an empty
        string**, and that is the column design rather than an accident of what
        the aggregate happens to hold. SQLite's ``UNIQUE`` permits any number of
        ``NULL``s, so every account without a phone coexists happily; an empty
        string would *collide* with every other one, and the second phone-less
        signup would fail against the first. It is the same argument
        ``google_subject``'s column carries, arriving for a second column.
        """
        self._connection.execute(
            """
            INSERT INTO users (user_id, email, phone, google_subject, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email          = excluded.email,
                phone          = excluded.phone,
                google_subject = excluded.google_subject,
                created_at     = excluded.created_at
            """,
            (
                uuid_to_text(user.user_id),
                user.email,
                user.phone,
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

        **``None`` is refused rather than passed through**, and the guard is not
        defensive padding: since an address became optional, ``None`` is a value a
        caller could plausibly reach this method with - "look up the account's
        email" reads naturally when the account may not have one. The fold would
        raise ``AttributeError`` on it, which reports as a 500. An account with no
        address is found by *no* address, so answering ``None`` is both the
        correct answer and the one that does not crash.
        """
        if email is None:
            return None

        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM users WHERE email = ?",
            (fold_email(email),),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def find_by_phone(self, phone: str) -> User | None:
        """Return the user holding this number, or None.

        ``find_by_email`` one identifier over, including its guard: ``None`` is
        answered with ``None`` rather than handed to the fold.

        The comparison is on the **folded** number, which is what makes the three
        ways a person writes their own number - ``08012345678``,
        ``+2348012345678``, ``2348012345678`` - find one account rather than
        three. See ``fold_phone``; this is the second of its two callers and the
        reason it is a module function rather than a step inside ``User``.
        """
        if phone is None:
            return None

        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM users WHERE phone = ?",
            (fold_phone(phone),),
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
            phone=row["phone"],
            google_subject=row["google_subject"],
            created_at=text_to_datetime(row["created_at"]),
        )
