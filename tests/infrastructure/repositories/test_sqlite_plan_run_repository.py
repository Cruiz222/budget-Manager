import sqlite3
from datetime import datetime

import pytest

from app.domain.planning.planRun import PlanRun
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SCHEMA,
    open_sqlite_connection,
)
from app.infrastructure.repositories.sqlite_plan_run_repository import (
    SqlitePlanRunRepository,
)
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)


def build_repository(wallet, plan):
    """A run store on a fresh in-memory database, with its parents seeded.

    plan_runs.plan_id carries a foreign key, so the wallet and the plan both
    have to exist first - a run cannot outlive the plan it belongs to.
    """
    connection = open_sqlite_connection(":memory:")
    SqliteWalletRepository(connection).save(wallet)
    SqliteSavingsPlanRepository(connection).save(plan)
    return SqlitePlanRunRepository(connection)


def test_round_trips_a_successful_run(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    run = PlanRun(plan_id=plan.plan_id, due_at=datetime(2026, 4, 1), status=RunStatus.SUCCEEDED)

    repository.save(run)

    stored = repository.list_by_plan_id(plan.plan_id)[0]
    assert stored.plan_id == plan.plan_id
    assert stored.due_at == datetime(2026, 4, 1)
    assert stored.status is RunStatus.SUCCEEDED
    assert stored.reason is None
    assert stored.recorded_at == run.recorded_at


def test_round_trips_a_blocked_run_with_its_reason(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.save(
        PlanRun(
            plan_id=plan.plan_id,
            due_at=datetime(2026, 4, 1),
            status=RunStatus.BLOCKED,
            reason=RunBlockReason.INSUFFICIENT_BALANCE,
        )
    )

    stored = repository.list_by_plan_id(plan.plan_id)[0]
    assert stored.status is RunStatus.BLOCKED
    assert stored.reason is RunBlockReason.INSUFFICIENT_BALANCE


@pytest.mark.parametrize("reason", list(RunBlockReason))
def test_every_block_reason_round_trips(reason, build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.save(
        PlanRun(
            plan_id=plan.plan_id,
            due_at=datetime(2026, 4, 1),
            status=RunStatus.BLOCKED,
            reason=reason,
        )
    )

    assert repository.list_by_plan_id(plan.plan_id)[0].reason is reason


def test_runs_come_back_in_occurrence_order(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    for day in (datetime(2026, 4, 1), datetime(2026, 1, 1), datetime(2026, 3, 1), datetime(2026, 2, 1)):
        repository.save(
            PlanRun(plan_id=plan.plan_id, due_at=day, status=RunStatus.SUCCEEDED)
        )

    stored = repository.list_by_plan_id(plan.plan_id)

    assert [run.due_at for run in stored] == [
        datetime(2026, 1, 1),
        datetime(2026, 2, 1),
        datetime(2026, 3, 1),
        datetime(2026, 4, 1),
    ]


def test_a_plan_that_has_never_run_has_no_history(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    assert repository.list_by_plan_id(plan.plan_id) == []


def test_only_that_plans_runs_come_back(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    other_plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    SqliteSavingsPlanRepository(repository._connection).save(other_plan)

    repository.save(PlanRun(plan_id=plan.plan_id, due_at=datetime(2026, 4, 1), status=RunStatus.SUCCEEDED))
    repository.save(PlanRun(plan_id=other_plan.plan_id, due_at=datetime(2026, 4, 1), status=RunStatus.SUCCEEDED))

    assert [run.plan_id for run in repository.list_by_plan_id(plan.plan_id)] == [plan.plan_id]


class TestTheNaturalKey:
    """One plan has at most one run per occurrence - the reason the store is idempotent."""

    def test_re_saving_the_same_occurrence_updates_rather_than_appends(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        due_at = datetime(2026, 4, 1)

        repository.save(PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED))
        repository.save(PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED))

        assert len(repository.list_by_plan_id(plan.plan_id)) == 1

    def test_a_blocked_run_retried_successfully_leaves_one_coherent_row(
        self, build_wallet, build_plan
    ):
        """The story this table exists to tell: blocked in April, paid in May, one row."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        due_at = datetime(2026, 4, 1)

        repository.save(
            PlanRun(
                plan_id=plan.plan_id,
                due_at=due_at,
                status=RunStatus.BLOCKED,
                reason=RunBlockReason.INSUFFICIENT_BALANCE,
            )
        )
        repository.save(
            PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED)
        )

        stored = repository.list_by_plan_id(plan.plan_id)
        assert len(stored) == 1
        assert stored[0].status is RunStatus.SUCCEEDED
        assert stored[0].reason is None

    def test_different_occurrences_of_one_plan_are_separate_rows(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.save(PlanRun(plan_id=plan.plan_id, due_at=datetime(2026, 1, 1), status=RunStatus.SUCCEEDED))
        repository.save(PlanRun(plan_id=plan.plan_id, due_at=datetime(2026, 2, 1), status=RunStatus.SUCCEEDED))

        assert len(repository.list_by_plan_id(plan.plan_id)) == 2


def test_round_trips_a_run_due_at_a_time_of_day(build_wallet, build_plan):
    """The whole point of the change: a noon occurrence survives save and load.

    Every other test in this file uses midnight, where a stored date and a
    stored moment are indistinguishable - the string differs, but the value
    loaded back is the same day either way. Only a non-midnight occurrence can
    tell the two representations apart.
    """
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.save(
        PlanRun(
            plan_id=plan.plan_id,
            due_at=datetime(2026, 3, 2, 12, 0),
            status=RunStatus.SUCCEEDED,
        )
    )

    stored = repository.list_by_plan_id(plan.plan_id)[0]
    assert stored.due_at == datetime(2026, 3, 2, 12, 0)


def test_a_noon_run_and_a_midnight_run_are_different_rows(build_wallet, build_plan):
    """Two occurrences, two runs - the natural key still discriminates.

    Worth stating because the two moments fall on the *same day*: read as dates
    they would collide, and the second save would silently overwrite the first.
    """
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.save(
        PlanRun(plan_id=plan.plan_id, due_at=datetime(2026, 3, 2), status=RunStatus.SUCCEEDED)
    )
    repository.save(
        PlanRun(plan_id=plan.plan_id, due_at=datetime(2026, 3, 2, 12, 0), status=RunStatus.SUCCEEDED)
    )

    assert [run.due_at for run in repository.list_by_plan_id(plan.plan_id)] == [
        datetime(2026, 3, 2),
        datetime(2026, 3, 2, 12, 0),
    ]


class TestTheDueAtMigration:
    """Widening a keyed column's text, and why it is not a refactor.

    ``plan_runs`` is keyed on ``(plan_id, due_at)``, and the text stored in a
    keyed column *is* part of the key. A database written while ``due_at`` was a
    date holds "2026-01-01"; this version writes "2026-01-01T00:00:00". Those
    are different strings, so they do not collide - which means the row a
    retried blocked run was meant to *update* would quietly become a second row
    for the same occurrence instead.

    These tests build the old shape by hand. That is the only way to reproduce
    it: no code path in this version can write a bare date, so a test that went
    through the repository would be testing the migration against data it had
    already written correctly.
    """

    def _write_legacy_database(self, path, wallet, plan, due_at="2026-01-01"):
        """Write the schema, then a run row the way the previous version did."""
        connection = sqlite3.connect(path)
        try:
            connection.row_factory = sqlite3.Row
            connection.executescript(SCHEMA)
            SqliteWalletRepository(connection).save(wallet)
            SqliteSavingsPlanRepository(connection).save(plan)
            connection.execute(
                "INSERT INTO plan_runs (plan_id, due_at, status, reason, recorded_at)"
                " VALUES (?, ?, 'BLOCKED', 'INSUFFICIENT_BALANCE', ?)",
                (str(plan.plan_id), due_at, "2026-01-01T00:00:00"),
            )
            connection.commit()
        finally:
            connection.close()

    def test_a_legacy_row_is_widened_to_a_moment(self, tmp_path, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        path = str(tmp_path / "legacy.db")
        self._write_legacy_database(path, wallet, plan)

        connection = open_sqlite_connection(path)  # the migration runs on open
        try:
            stored = SqlitePlanRunRepository(connection).list_by_plan_id(plan.plan_id)
        finally:
            connection.close()

        # "2026-01-01" was always a midnight occurrence - this is not a guess.
        assert [run.due_at for run in stored] == [datetime(2026, 1, 1)]

    def test_a_legacy_blocked_run_retried_updates_rather_than_duplicates(
        self, tmp_path, build_wallet, build_plan
    ):
        """The failure the migration exists to prevent, stated as a test.

        Blocked at January's occurrence, topped up, retried. Before the
        migration the retry would insert a *second* January row - two rows for
        one occurrence, which is exactly what the natural key promises cannot
        happen.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        path = str(tmp_path / "legacy.db")
        self._write_legacy_database(path, wallet, plan)

        connection = open_sqlite_connection(path)
        try:
            repository = SqlitePlanRunRepository(connection)
            repository.save(
                PlanRun(
                    plan_id=plan.plan_id,
                    due_at=datetime(2026, 1, 1),  # the same occurrence
                    status=RunStatus.SUCCEEDED,
                )
            )
            stored = repository.list_by_plan_id(plan.plan_id)
        finally:
            connection.close()

        assert len(stored) == 1
        assert stored[0].status is RunStatus.SUCCEEDED
        assert stored[0].reason is None

    def test_the_migration_is_idempotent(self, tmp_path, build_wallet, build_plan):
        """Opening the same database twice must not append a second T00:00:00."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        path = str(tmp_path / "legacy.db")
        self._write_legacy_database(path, wallet, plan)

        for _ in range(3):  # every connection runs the migrations
            connection = open_sqlite_connection(path)
            try:
                raw = connection.execute(
                    "SELECT due_at FROM plan_runs WHERE plan_id = ?",
                    (str(plan.plan_id),),
                ).fetchone()["due_at"]
            finally:
                connection.close()

            assert raw == "2026-01-01T00:00:00"

    def test_a_row_that_is_already_a_moment_is_left_alone(
        self, tmp_path, build_wallet, build_plan
    ):
        """The WHERE clause is what makes this safe to run on a live database.

        A database already written by this version must come through the
        migration untouched - widening it twice would give
        "2026-01-01T00:00:00T00:00:00", which nothing could parse.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        path = str(tmp_path / "legacy.db")
        self._write_legacy_database(path, wallet, plan, due_at="2026-01-01T12:00:00")

        connection = open_sqlite_connection(path)
        try:
            stored = SqlitePlanRunRepository(connection).list_by_plan_id(plan.plan_id)
        finally:
            connection.close()

        assert [run.due_at for run in stored] == [datetime(2026, 1, 1, 12, 0)]
