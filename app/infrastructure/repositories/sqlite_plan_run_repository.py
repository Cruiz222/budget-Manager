import sqlite3
from datetime import date

from app.domain.planning.planRun import PlanRun
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.domain.repositories.plan_run_repository import PlanRunRepository
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    optional_enum_to_text,
    text_to_datetime,
    text_to_enum,
    text_to_optional_enum,
    text_to_uuid,
    uuid_to_text,
)


class SqlitePlanRunRepository(PlanRunRepository):
    """Plan-run store over a single SQLite connection.

    Unlike the wallet and plan stores, the conflict clause here keys on a
    *composite* of two real values rather than on a generated id. That is the
    point: a run has no identity of its own - it is nothing more than (plan,
    occurrence) - so the natural key is also the primary key, and the upsert
    that falls out of it is what makes retrying a blocked run coherent.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, run: PlanRun) -> PlanRun:
        self._connection.execute(
            """
            INSERT INTO plan_runs (plan_id, due_at, status, reason, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(plan_id, due_at) DO UPDATE SET
                status      = excluded.status,
                reason      = excluded.reason,
                recorded_at = excluded.recorded_at
            """,
            (
                uuid_to_text(run.plan_id),
                run.due_at.isoformat(),
                enum_to_text(run.status),
                optional_enum_to_text(run.reason),
                datetime_to_text(run.recorded_at),
            ),
        )
        return run

    def list_by_plan_id(self, plan_id) -> list[PlanRun]:
        rows = self._connection.execute(
            """
            SELECT plan_id, due_at, status, reason, recorded_at
            FROM plan_runs
            WHERE plan_id = ?
            ORDER BY due_at
            """,
            (uuid_to_text(plan_id),),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def _row_to_run(self, row) -> PlanRun:
        return PlanRun(
            plan_id=text_to_uuid(row["plan_id"]),
            due_at=date.fromisoformat(row["due_at"]),
            status=text_to_enum(RunStatus, row["status"]),
            reason=text_to_optional_enum(RunBlockReason, row["reason"]),
            recorded_at=text_to_datetime(row["recorded_at"]),
        )
