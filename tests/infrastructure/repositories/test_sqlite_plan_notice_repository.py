import sqlite3
from datetime import datetime

import pytest

from app.domain.planning.planNotice import PlanNotice
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SCHEMA,
    open_sqlite_connection,
)
from app.infrastructure.repositories.sqlite_plan_notice_repository import (
    SqlitePlanNoticeRepository,
)
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NOON = datetime(2026, 3, 2, 12, 0)
HALF_PAST_ELEVEN = datetime(2026, 3, 2, 11, 30)


def build_repository(wallet, plan):
    """A notice store on a fresh in-memory database, with its parents seeded.

    plan_notices.plan_id carries a foreign key, so the wallet and the plan both
    have to exist first - a notice cannot outlive the plan it is about.
    """
    connection = open_sqlite_connection(":memory:")
    SqliteWalletRepository(connection).save(wallet)
    SqliteSavingsPlanRepository(connection).save(plan)
    return SqlitePlanNoticeRepository(connection)


def build_notice(plan_id, **overrides):
    kwargs = dict(
        plan_id=plan_id,
        due_at=NOON,
        raised_at=HALF_PAST_ELEVEN,
    )
    kwargs.update(overrides)
    return PlanNotice(**kwargs)


def test_round_trips_a_notice(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.claim(build_notice(plan.plan_id))

    stored = repository.list_by_plan_id(plan.plan_id)[0]
    assert stored.plan_id == plan.plan_id
    assert stored.due_at == NOON
    assert stored.raised_at == HALF_PAST_ELEVEN


def test_a_notice_for_a_plan_that_has_never_been_warned_about_is_no_history(
    build_wallet, build_plan
):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    assert repository.list_by_plan_id(plan.plan_id) == []


def test_only_that_plans_notices_come_back(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    other_plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    SqliteSavingsPlanRepository(repository._connection).save(other_plan)

    repository.claim(build_notice(plan.plan_id))
    repository.claim(build_notice(other_plan.plan_id))

    assert [notice.plan_id for notice in repository.list_by_plan_id(plan.plan_id)] == [
        plan.plan_id
    ]


def test_notices_come_back_in_occurrence_order(build_wallet, build_plan):
    """Ordered by when the run is, not by when the warning was given.

    ``raised_at`` is deliberately *not* the sort key. Two notices raised minutes
    apart can belong to occurrences months apart - a tick near midnight on
    31 January can see both January's run and, on a daily cadence, February's -
    and reading them back in the order they were said would interleave two
    different stories.
    """
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)
    raised = HALF_PAST_ELEVEN
    for occurrence in (
        datetime(2026, 4, 1, 12, 0),
        datetime(2026, 1, 1, 12, 0),
        datetime(2026, 3, 1, 12, 0),
        datetime(2026, 2, 1, 12, 0),
    ):
        repository.claim(build_notice(plan.plan_id, due_at=occurrence, raised_at=raised))

    stored = repository.list_by_plan_id(plan.plan_id)

    assert [notice.due_at for notice in stored] == [
        datetime(2026, 1, 1, 12, 0),
        datetime(2026, 2, 1, 12, 0),
        datetime(2026, 3, 1, 12, 0),
        datetime(2026, 4, 1, 12, 0),
    ]


class TestClaiming:
    """The write answers the question, so that nothing has to be read first."""

    def test_the_first_claim_wins(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        assert repository.claim(build_notice(plan.plan_id)) is True

    def test_a_second_claim_of_the_same_occurrence_loses(
        self, build_wallet, build_plan
    ):
        """The return value *is* the interface - False is the warning being old news."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.claim(build_notice(plan.plan_id))
        second = repository.claim(build_notice(plan.plan_id, raised_at=datetime(2026, 3, 2, 11, 45)))

        assert second is False

    def test_a_losing_claim_writes_no_second_row(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.claim(build_notice(plan.plan_id))
        repository.claim(build_notice(plan.plan_id, raised_at=datetime(2026, 3, 2, 11, 45)))
        repository.claim(build_notice(plan.plan_id, raised_at=datetime(2026, 3, 2, 11, 50)))

        assert len(repository.list_by_plan_id(plan.plan_id)) == 1

    def test_a_losing_claim_does_not_rewrite_the_winner(
        self, build_wallet, build_plan
    ):
        """``DO NOTHING``, not ``DO UPDATE`` - the difference from the run store.

        A blocked run that is later paid really does have a truer second version,
        so its store overwrites. A notice does not: the first warning was
        accurate when it was given, and the row is a record of *that*. If the
        conflict updated, the ledger of warnings would silently drift to whichever
        tick happened to run last.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.claim(build_notice(plan.plan_id, raised_at=HALF_PAST_ELEVEN))
        repository.claim(build_notice(plan.plan_id, raised_at=datetime(2026, 3, 2, 11, 50)))

        assert repository.list_by_plan_id(plan.plan_id)[0].raised_at == HALF_PAST_ELEVEN

    def test_two_occurrences_of_one_plan_are_two_rows(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        first = repository.claim(build_notice(plan.plan_id, due_at=datetime(2026, 3, 2, 12, 0)))
        second = repository.claim(build_notice(plan.plan_id, due_at=datetime(2026, 4, 2, 12, 0)))

        assert first is True and second is True
        assert len(repository.list_by_plan_id(plan.plan_id)) == 2

    def test_a_notice_for_an_unknown_plan_is_refused(self, build_wallet, build_plan):
        """The foreign key, not a convention: a warning about nothing cannot exist."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        with pytest.raises(sqlite3.IntegrityError):
            repository.claim(build_notice(build_plan().plan_id))


class TestTheNewTableNeedsNoMigration:
    """Decision 18 as an executable claim: a new table is free.

    ``CREATE TABLE IF NOT EXISTS`` reaches a database that *already exists* and
    is missing the table - which is exactly the case ``savings_plans.name`` and
    ``plan_runs.due_at`` could not rely on, because those changed a table that
    was already there. So ``plan_notices`` has no migration function, and this
    class is what says that absence is correct rather than forgotten.
    """

    def _write_the_old_database(self, path, wallet, plan):
        """Write a database as the previous version would have left it.

        The schema *without* plan_notices, which is what a real budget.db looked
        like one commit ago. Built by executing the current SCHEMA and then
        dropping the table: no code path in this version can produce a database
        missing it, so hand-writing the older shape is the only way to ask the
        question.
        """
        connection = sqlite3.connect(path)
        try:
            connection.row_factory = sqlite3.Row
            connection.executescript(SCHEMA)
            SqliteWalletRepository(connection).save(wallet)
            SqliteSavingsPlanRepository(connection).save(plan)
            connection.execute("DROP TABLE plan_notices")
            connection.commit()
        finally:
            connection.close()

    def test_the_table_really_is_absent_before_opening(self, tmp_path, build_wallet, build_plan):
        """The premise of the test below, checked rather than assumed."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        path = str(tmp_path / "old.db")
        self._write_the_old_database(path, wallet, plan)

        connection = sqlite3.connect(path)
        try:
            connection.row_factory = sqlite3.Row
            names = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

        assert "plan_notices" not in names

    def test_opening_an_existing_database_creates_the_missing_table(
        self, tmp_path, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        path = str(tmp_path / "old.db")
        self._write_the_old_database(path, wallet, plan)

        connection = open_sqlite_connection(path)
        try:
            # Usable immediately, with no ALTER and no migration function.
            repository = SqlitePlanNoticeRepository(connection)
            assert repository.claim(build_notice(plan.plan_id)) is True
            assert len(repository.list_by_plan_id(plan.plan_id)) == 1
        finally:
            connection.close()

    def test_opening_does_not_disturb_what_was_already_there(
        self, tmp_path, build_wallet, build_plan
    ):
        """The other half of "free": the rows that were there are still there.

        A migration that lost data would be worse than no migration at all, so
        the claim that this needs none is worth checking against a populated
        database rather than an empty one.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id, name="Rent")
        path = str(tmp_path / "old.db")
        self._write_the_old_database(path, wallet, plan)

        for _ in range(2):  # every connection runs this
            connection = open_sqlite_connection(path)
            try:
                reloaded = SqliteSavingsPlanRepository(connection).get_by_id(
                    plan.plan_id
                )
            finally:
                connection.close()

            assert reloaded.name == "Rent"
            assert reloaded.completed_runs == 0
