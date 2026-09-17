import sqlite3

from app.domain.identity.profile import Profile
from app.domain.repositories.profile_repository import ProfileRepository
from app.infrastructure.persistence.serialization import (
    date_to_text,
    datetime_to_text,
    text_to_date,
    text_to_datetime,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = (
    "user_id, display_name, legal_first_name, legal_last_name, date_of_birth, "
    "phone, country, address_line, created_at, updated_at"
)


class SqliteProfileRepository(ProfileRepository):
    """Profile store over a single SQLite connection.

    The connection owns the transaction this repository participates in; save()
    only issues SQL and does not commit, so the Unit of Work decides when the
    write becomes durable - the same arrangement every repository here has.

    **``date_of_birth`` is the only ``date`` column in the schema**, and it is
    why ``date_to_text`` exists beside ``datetime_to_text`` rather than one
    function doing both: storing a birth date as an ISO *datetime* would write a
    "T00:00:00" that nothing meant, and the value on the way back would be a
    ``datetime`` the aggregate then refuses. The round-trip has to preserve the
    type as well as the value, because ``Profile.__post_init__`` tests for it.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, profile: Profile) -> Profile:
        """Insert or update this profile, keyed on ``user_id``.

        No ``UNIQUE`` constraint is doing load-bearing work here the way
        ``users.email`` does, and that is worth stating rather than leaving to be
        noticed: the primary key already makes "two profiles for one person"
        unrepresentable, so the conflict target is the same column the row is
        identified by and there is no second spelling of the same person to
        collide with.
        """
        self._connection.execute(
            """
            INSERT INTO profiles (
                user_id, display_name, legal_first_name, legal_last_name,
                date_of_birth, phone, country, address_line, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name     = excluded.display_name,
                legal_first_name = excluded.legal_first_name,
                legal_last_name  = excluded.legal_last_name,
                date_of_birth    = excluded.date_of_birth,
                phone            = excluded.phone,
                country          = excluded.country,
                address_line     = excluded.address_line,
                updated_at       = excluded.updated_at
            """,
            (
                uuid_to_text(profile.user_id),
                profile.display_name,
                profile.legal_first_name,
                profile.legal_last_name,
                date_to_text(profile.date_of_birth),
                profile.phone,
                profile.country,
                profile.address_line,
                datetime_to_text(profile.created_at),
                datetime_to_text(profile.updated_at),
            ),
        )
        return profile

    def find_for_user(self, user_id) -> Profile | None:
        """Return this person's profile, or ``None``.

        **The single-row read is the whole of the scoping**, and it is worth
        being explicit about why that is enough rather than reachable: the
        ``WHERE`` clause is on the primary key, so this query cannot return a row
        that is not the one asked for and cannot return more than one. There is
        no ``ORDER BY`` to get wrong and no second row to filter in Python.

        ``created_at`` is deliberately absent from the ``DO UPDATE`` list above
        and present here. A profile's creation moment is a fact about the row
        that no edit changes, and the aggregate's ``revise`` carries it over
        rather than taking a new one - so the two agree about which field is
        mutable, which is the agreement that would otherwise drift.
        """
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM profiles WHERE user_id = ?",
            (uuid_to_text(user_id),),
        ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def _row_to_profile(self, row) -> Profile:
        """Rebuild a profile from its row.

        The constructor runs on the way back in, as it does in every repository
        here, so a row corrupted by hand - a country of "Lagos", a bare
        ``datetime`` where the birth date belongs - fails loudly at load rather
        than travelling further wearing a valid shape. That is worth more here
        than elsewhere: the tier is derived from these fields, so a row that
        loaded wrong would not merely display wrong, it would *grant a limit*.
        """
        return Profile(
            user_id=text_to_uuid(row["user_id"]),
            display_name=row["display_name"],
            legal_first_name=row["legal_first_name"],
            legal_last_name=row["legal_last_name"],
            date_of_birth=text_to_date(row["date_of_birth"]),
            phone=row["phone"],
            country=row["country"],
            address_line=row["address_line"],
            created_at=text_to_datetime(row["created_at"]),
            updated_at=text_to_datetime(row["updated_at"]),
        )
