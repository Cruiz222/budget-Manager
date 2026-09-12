import sqlite3

from app.domain.planning.exception import SavingsPlanNotFoundError
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.repositories.savings_plan_repository import SavingsPlanRepository
from app.infrastructure.persistence.serialization import (
    date_to_text,
    datetime_to_text,
    enum_to_text,
    instructions_to_text,
    optional_uuid_to_text,
    schedule_to_text,
    text_to_date,
    text_to_datetime,
    text_to_enum,
    text_to_instructions,
    text_to_optional_uuid,
    text_to_schedule,
    text_to_uuid,
    uuid_to_text,
)

_COLUMNS = (
    "plan_id, wallet_id, user_id, name, source, schedule, instructions, "
    "status, completed_runs, ends_on, fund_id, created_at"
)


class SqliteSavingsPlanRepository(SavingsPlanRepository):
    """Plan store over a single SQLite connection.

    The connection owns the transaction this repository participates in; save()
    only issues SQL and does not commit, so the Unit of Work decides when the
    write becomes durable.

    The whole aggregate goes in one row. ``schedule`` and ``instructions`` are
    JSON because they are value objects inside the aggregate - no identity,
    never queried alone - so keeping them here is what makes a plan's save
    atomic by construction rather than by discipline.

    **The scoped reads filter on the plan's own ``user_id`` rather than joining
    out to ``wallets``.** That is the denormalised owner paying for itself: the
    same fact stored twice is what turns "is this plan mine?" from a join into a
    predicate. The join would be correct and would also be a second place that
    had to know the rule.
    """

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def save(self, plan: SavingsPlan) -> SavingsPlan:
        self._connection.execute(
            """
            INSERT INTO savings_plans
                (plan_id, wallet_id, user_id, name, source, schedule,
                 instructions, status, completed_runs, ends_on, fund_id,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                wallet_id      = excluded.wallet_id,
                user_id        = excluded.user_id,
                name           = excluded.name,
                source         = excluded.source,
                schedule       = excluded.schedule,
                instructions   = excluded.instructions,
                status         = excluded.status,
                completed_runs = excluded.completed_runs,
                ends_on        = excluded.ends_on,
                fund_id        = excluded.fund_id,
                created_at     = excluded.created_at
            """,
            (
                uuid_to_text(plan.plan_id),
                uuid_to_text(plan.wallet_id),
                uuid_to_text(plan.user_id),
                plan.name,
                enum_to_text(plan.source),
                schedule_to_text(plan.schedule),
                instructions_to_text(plan.instructions),
                enum_to_text(plan.status),
                plan.completed_runs,
                date_to_text(plan.ends_on),
                optional_uuid_to_text(plan.fund_id),
                datetime_to_text(plan.created_at),
            ),
        )
        return plan

    def get_owned(self, plan_id, user_id) -> SavingsPlan:
        """Return this owner's plan, or raise SavingsPlanNotFoundError.

        The owner is in the ``WHERE``, so a stranger's plan is never loaded -
        the same placement, and the same reasoning, as
        ``SqliteWalletRepository.get_owned``.

        The message deliberately does not say which of the two ways the lookup
        failed. ``SavingsPlanNotFoundError`` already carried the plan id for a
        caller who has one; naming the *owner* it was not found for would turn a
        probe into an oracle that answered "that plan exists, try another user".
        """
        row = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM savings_plans
            WHERE plan_id = ? AND user_id = ?
            """,
            (uuid_to_text(plan_id), uuid_to_text(user_id)),
        ).fetchone()
        if row is None:
            raise SavingsPlanNotFoundError(f"no plan with id {plan_id}")
        return self._row_to_plan(row)

    def get_by_wallet_id(self, wallet_id, user_id) -> list[SavingsPlan]:
        """Return this owner's plans drawn on this wallet, oldest first.

        Scoped even though every caller reaches it having already proved the
        wallet is theirs. The proof is what makes the answer *correct* - it is
        how "no such wallet" is told from "wallet with no plans" - and this
        filter is what makes it *safe*, so that a caller who forgets the proof
        still cannot read a stranger's plans.
        """
        rows = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM savings_plans
            WHERE wallet_id = ? AND user_id = ?
            ORDER BY created_at, plan_id
            """,
            (uuid_to_text(wallet_id), uuid_to_text(user_id)),
        ).fetchall()
        return [self._row_to_plan(row) for row in rows]

    def list_by_status(self, status: PlanStatus) -> list[SavingsPlan]:
        """Every plan in this status, oldest first, across every owner.

        Unscoped on purpose - see the port for why discovery is not a bypass.
        The owner travels on each plan that comes back, which is what lets the
        scheduler act as it for every read that follows.
        """
        rows = self._connection.execute(
            f"""
            SELECT {_COLUMNS}
            FROM savings_plans
            WHERE status = ?
            ORDER BY created_at, plan_id
            """,
            (enum_to_text(status),),
        ).fetchall()
        return [self._row_to_plan(row) for row in rows]

    def _row_to_plan(self, row) -> SavingsPlan:
        return SavingsPlan(
            plan_id=text_to_uuid(row["plan_id"]),
            wallet_id=text_to_uuid(row["wallet_id"]),
            user_id=text_to_uuid(row["user_id"]),
            name=row["name"],
            source=text_to_enum(PlanSource, row["source"]),
            schedule=text_to_schedule(row["schedule"]),
            _instructions=text_to_instructions(row["instructions"]),
            status=text_to_enum(PlanStatus, row["status"]),
            completed_runs=row["completed_runs"],
            ends_on=text_to_date(row["ends_on"]),
            fund_id=text_to_optional_uuid(row["fund_id"]),
            created_at=text_to_datetime(row["created_at"]),
        )
