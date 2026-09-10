import sqlite3

from app.domain.planning.planNotice import PlanNotice
from app.domain.repositories.plan_notice_repository import PlanNoticeRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    text_to_datetime,
    text_to_uuid,
    uuid_to_text,
)


class SqlitePlanNoticeRepository(PlanNoticeRepository):
    """Notice store over a single SQLite connection.

    Keyed on ``(plan_id, due_at)`` - the natural key, and the primary key - so
    that one plan has at most one notice per occurrence and the database, not a
    check in Python, is what enforces it.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def claim(self, notice: PlanNotice) -> bool:
        """Insert the notice, and report whether this call is the one that inserted it.

        ``DO NOTHING`` rather than the ``DO UPDATE`` the run store uses, and the
        difference is the point. A blocked run that is later paid *rewrites* its
        row, because a run has a truer second version and the ledger needs one
        answer. A notice does not: the first warning was accurate when it was
        given, and "you were already told" is not improved by being told again.
        So the conflicting insert is discarded, and discarding it is exactly the
        outcome we want.

        The row count is not an implementation detail here - it is the return
        value. SQLite reports zero rows changed when the conflict fires, which
        means the *write itself* answers "was this mine to report?". That is what
        makes the claim safe against two ticks overlapping, where reading first
        and writing second would leave a gap for both to fall through.
        """
        cursor = self._connection.execute(
            """
            INSERT INTO plan_notices (plan_id, due_at, raised_at)
            VALUES (?, ?, ?)
            ON CONFLICT(plan_id, due_at) DO NOTHING
            """,
            (
                uuid_to_text(notice.plan_id),
                notice.due_at.isoformat(),
                datetime_to_text(notice.raised_at),
            ),
        )
        return cursor.rowcount == 1

    def list_by_plan_id(self, plan_id) -> list[PlanNotice]:
        rows = self._connection.execute(
            """
            SELECT plan_id, due_at, raised_at
            FROM plan_notices
            WHERE plan_id = ?
            ORDER BY due_at
            """,
            (uuid_to_text(plan_id),),
        ).fetchall()
        return [self._row_to_notice(row) for row in rows]

    def _row_to_notice(self, row) -> PlanNotice:
        # text_to_datetime rather than a bare fromisoformat, so the reading rule
        # lives with every other one.
        return PlanNotice(
            plan_id=text_to_uuid(row["plan_id"]),
            due_at=text_to_datetime(row["due_at"]),
            raised_at=text_to_datetime(row["raised_at"]),
        )
