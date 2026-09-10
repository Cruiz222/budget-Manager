import sqlite3
from datetime import datetime

import pytest

from app.domain.notifications.outboundMessage import OutboundMessage
from app.domain.notifications.outboundMessageStatus import OutboundMessageStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SCHEMA,
    open_sqlite_connection,
)
from app.infrastructure.repositories.sqlite_outbound_message_repository import (
    SqliteOutboundMessageRepository,
)
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NOON = datetime(2026, 3, 2, 12, 0)
HALF_PAST_ELEVEN = datetime(2026, 3, 2, 11, 30)
QUARTER_PAST_ELEVEN = datetime(2026, 3, 2, 11, 15)


def build_repository(wallet, plan):
    """An outbox on a fresh in-memory database, with its parents seeded.

    outbound_messages.plan_id carries a foreign key, so the wallet and the plan
    both have to exist first - a message cannot outlive the plan it is about.
    """
    connection = open_sqlite_connection(":memory:")
    SqliteWalletRepository(connection).save(wallet)
    SqliteSavingsPlanRepository(connection).save(plan)
    return SqliteOutboundMessageRepository(connection)


def build_message(plan_id, **overrides):
    kwargs = dict(
        plan_id=plan_id,
        due_at=NOON,
        recipient="chinedu@example.com",
        subject="Payout of 2000.00 NGN in 30 minutes",
        body="The plan 'salary' pays 2000.00 NGN at 2026-03-02T12:00.",
        created_at=HALF_PAST_ELEVEN,
    )
    kwargs.update(overrides)
    return OutboundMessage(**kwargs)


def test_round_trips_a_message(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    repository.enqueue(build_message(plan.plan_id))

    stored = repository.pending()[0]
    assert stored.plan_id == plan.plan_id
    assert stored.due_at == NOON
    assert stored.recipient == "chinedu@example.com"
    assert stored.subject == "Payout of 2000.00 NGN in 30 minutes"
    assert stored.body == "The plan 'salary' pays 2000.00 NGN at 2026-03-02T12:00."
    assert stored.created_at == HALF_PAST_ELEVEN
    assert stored.status is OutboundMessageStatus.PENDING
    assert stored.attempts == 0
    assert stored.last_error is None
    assert stored.settled_at is None


def test_an_empty_outbox_owes_nothing(build_wallet, build_plan):
    wallet = build_wallet()
    plan = build_plan(wallet_id=wallet.wallet_id)
    repository = build_repository(wallet, plan)

    assert repository.pending() == []


class TestEnqueueing:
    """The write answers the question, so that nothing has to be read first."""

    def test_the_first_enqueue_wins(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        assert repository.enqueue(build_message(plan.plan_id)) is True

    def test_a_second_enqueue_of_the_same_occurrence_loses(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.enqueue(build_message(plan.plan_id))
        second = repository.enqueue(build_message(plan.plan_id))

        assert second is False

    def test_a_losing_enqueue_writes_no_second_row(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.enqueue(build_message(plan.plan_id))
        repository.enqueue(build_message(plan.plan_id))
        repository.enqueue(build_message(plan.plan_id))

        assert len(repository.pending()) == 1

    def test_a_losing_enqueue_does_not_rewrite_the_words(
        self, build_wallet, build_plan
    ):
        """``DO NOTHING``, not ``DO UPDATE`` - the difference from the notice store.

        A message's *composition* is frozen at enqueue: it was written once, at
        the moment the notice was claimed, and a second enqueue is a bug that
        should change nothing - certainly not the text of a warning already
        queued. Compare ``save`` below, which does overwrite, because progress
        is a different kind of fact from wording.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        repository.enqueue(build_message(plan.plan_id, subject="first wording"))
        repository.enqueue(build_message(plan.plan_id, subject="second wording"))

        assert repository.pending()[0].subject == "first wording"

    def test_two_occurrences_of_one_plan_are_two_rows(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        first = repository.enqueue(
            build_message(plan.plan_id, due_at=datetime(2026, 3, 2, 12, 0))
        )
        second = repository.enqueue(
            build_message(plan.plan_id, due_at=datetime(2026, 4, 2, 12, 0))
        )

        assert first is True and second is True
        assert len(repository.pending()) == 2

    def test_a_message_for_an_unknown_plan_is_refused(
        self, build_wallet, build_plan
    ):
        """The foreign key, not a convention: a message about nothing cannot exist."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        with pytest.raises(sqlite3.IntegrityError):
            repository.enqueue(build_message(build_plan().plan_id))


class TestSaving:
    """``save`` overwrites, unlike ``enqueue`` - and that contrast is the design.

    A message genuinely has a truer second version once it has been sent, or
    once it has failed again. If ``save`` did not overwrite, every tick would
    find the message still PENDING and send it again, forever.
    """

    def test_saving_a_sent_message_settles_the_row(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        repository.enqueue(build_message(plan.plan_id))

        message = repository.pending()[0]
        message.mark_sent(NOON)
        repository.save(message)

        assert repository.pending() == []

    def test_the_settled_row_can_still_be_read_for_its_moment(
        self, build_wallet, build_plan
    ):
        """Settled is not deleted. The row is the record of what was sent."""
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        repository.enqueue(build_message(plan.plan_id))
        message = repository.pending()[0]
        message.mark_sent(NOON)
        repository.save(message)

        row = repository._connection.execute(
            "SELECT status, settled_at FROM outbound_messages WHERE plan_id = ?",
            (str(plan.plan_id),),
        ).fetchone()

        assert row["status"] == "SENT"
        assert row["settled_at"] == NOON.isoformat()

    def test_a_failed_attempt_is_written_and_the_message_stays_owed(
        self, build_wallet, build_plan
    ):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        repository.enqueue(build_message(plan.plan_id))

        message = repository.pending()[0]
        message.record_failure("OSError: connection refused")
        repository.save(message)

        stored = repository.pending()[0]
        assert stored.attempts == 1
        assert stored.last_error == "OSError: connection refused"

    def test_saving_does_not_duplicate_the_row(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        repository.enqueue(build_message(plan.plan_id))

        message = repository.pending()[0]
        for _ in range(3):
            message.record_failure("still refused")
            repository.save(message)

        assert len(repository.pending()) == 1


class TestWhatIsOwed:
    def test_pending_excludes_settled_messages(self, build_wallet, build_plan):
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)
        sent = build_message(plan.plan_id, due_at=datetime(2026, 3, 2, 12, 0))
        expired = build_message(plan.plan_id, due_at=datetime(2026, 4, 2, 12, 0))
        owed = build_message(plan.plan_id, due_at=datetime(2026, 5, 2, 12, 0))
        repository.enqueue(sent)
        repository.enqueue(expired)
        repository.enqueue(owed)
        sent.mark_sent(NOON)
        expired.mark_expired(NOON)
        repository.save(sent)
        repository.save(expired)

        assert [message.key for message in repository.pending()] == [owed.key]

    def test_pending_is_ordered_by_occurrence_not_by_when_it_was_composed(
        self, build_wallet, build_plan
    ):
        """The most urgent warning goes first.

        ``created_at`` is deliberately *not* the sort key: a message composed
        later can be about an earlier occurrence, and sending in composition
        order would deliver the least urgent warning first.
        """
        wallet = build_wallet()
        plan = build_plan(wallet_id=wallet.wallet_id)
        repository = build_repository(wallet, plan)

        for occurrence, composed in (
            (datetime(2026, 5, 2, 12, 0), QUARTER_PAST_ELEVEN),
            (datetime(2026, 3, 2, 12, 0), NOON),
            (datetime(2026, 4, 2, 12, 0), HALF_PAST_ELEVEN),
        ):
            repository.enqueue(
                build_message(plan.plan_id, due_at=occurrence, created_at=composed)
            )

        assert [message.due_at for message in repository.pending()] == [
            datetime(2026, 3, 2, 12, 0),
            datetime(2026, 4, 2, 12, 0),
            datetime(2026, 5, 2, 12, 0),
        ]


class TestTheNewTableNeedsNoMigration:
    """Decision 18 as an executable claim: a new table is free.

    ``CREATE TABLE IF NOT EXISTS`` reaches a database that *already exists* and
    is missing the table - which is exactly the case ``savings_plans.name`` and
    ``plan_runs.due_at`` could not rely on, because those changed a table that
    was already there. So ``outbound_messages`` has no migration function, and
    this class is what says that absence is correct rather than forgotten.
    """

    def _write_the_old_database(self, path, wallet, plan):
        """Write a database as the previous version would have left it.

        The schema *without* outbound_messages, which is what a real budget.db
        looked like one commit ago. Built by executing the current SCHEMA and
        then dropping the table: no code path in this version can produce a
        database missing it, so hand-writing the older shape is the only way to
        ask the question.
        """
        connection = sqlite3.connect(path)
        try:
            connection.row_factory = sqlite3.Row
            connection.executescript(SCHEMA)
            SqliteWalletRepository(connection).save(wallet)
            SqliteSavingsPlanRepository(connection).save(plan)
            connection.execute("DROP TABLE outbound_messages")
            connection.commit()
        finally:
            connection.close()

    def test_the_table_really_is_absent_before_opening(
        self, tmp_path, build_wallet, build_plan
    ):
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

        assert "outbound_messages" not in names

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
            repository = SqliteOutboundMessageRepository(connection)
            assert repository.enqueue(build_message(plan.plan_id)) is True
            assert len(repository.pending()) == 1
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
