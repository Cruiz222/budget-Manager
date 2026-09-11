"""The derived keys, which are the whole of "exactly once".

A key here is not a convenience - it is the primary key of a row, and therefore
the thing that decides whether a second enqueue is a second email or a no-op.
These tests pin its *text*, not just its behaviour, because the text is persisted:
changing the format tomorrow does not rewrite the keys written today, and two
strings for one event would let that event be announced twice.
"""

from datetime import datetime
from uuid import uuid4

from app.domain.notifications.eventKey import for_plan_event, for_wallet_event
from app.domain.notifications.notificationKind import NotificationKind

NOON = datetime(2026, 3, 2, 12, 0)
NEXT_MONTH = datetime(2026, 4, 2, 12, 0)


class TestAPlanEventKey:
    def test_its_shape_is_pinned(self):
        """The literal string, because this value is written to the database.

        A test that only asserted "two equal inputs give two equal keys" would
        pass just as happily after a format change, and the failure that hides is
        a real one: an event whose old key is already stored, and whose new key
        does not collide with it, gets announced twice.
        """
        plan_id = uuid4()

        assert (
            for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, plan_id, NOON)
            == f"payout_succeeded:plan:{plan_id}:2026-03-02T12:00:00"
        )

    def test_the_same_event_always_derives_the_same_key(self):
        plan_id = uuid4()

        first = for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, plan_id, NOON)
        second = for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, plan_id, NOON)

        assert first == second

    def test_the_same_outcome_for_a_later_occurrence_is_a_different_key(self):
        """Otherwise a plan would be announced once, at its first run, forever.

        The occurrence's moment is in the key for the same reason it is in
        ``ExecutePlanRun._reference``: paying next month is a different payment,
        not a duplicate of this month's.
        """
        plan_id = uuid4()

        assert for_plan_event(
            NotificationKind.PAYOUT_SUCCEEDED, plan_id, NOON
        ) != for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, plan_id, NEXT_MONTH)

    def test_blocked_and_succeeded_for_one_occurrence_are_two_keys(self):
        """The load-bearing case for the kind being the first part of the key.

        A run that is blocked at noon and retried successfully later updates the
        *same* ``PlanRun`` row - see ``PlanRun`` - so the two outcomes share a
        ``due_at`` and would derive one key if the kind were left out. Two things
        really did happen to the user, so there must be two messages; dropping the
        kind here is not a loud failure, it is a message that silently never sends.
        """
        plan_id = uuid4()

        assert for_plan_event(
            NotificationKind.PAYOUT_BLOCKED, plan_id, NOON
        ) != for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, plan_id, NOON)

    def test_the_same_outcome_reached_twice_collapses_to_one_key(self):
        """A plan blocked, resumed unfunded, and blocked again is one event.

        The user has already been told. Announcing it on every resume would teach
        them to ignore the one message that means their money did not arrive.
        """
        plan_id = uuid4()

        assert for_plan_event(
            NotificationKind.PAYOUT_BLOCKED, plan_id, NOON
        ) == for_plan_event(NotificationKind.PAYOUT_BLOCKED, plan_id, NOON)

    def test_two_plans_do_not_share_a_key(self):
        assert for_plan_event(
            NotificationKind.PAYOUT_SUCCEEDED, uuid4(), NOON
        ) != for_plan_event(NotificationKind.PAYOUT_SUCCEEDED, uuid4(), NOON)


class TestAWalletEventKey:
    def test_its_shape_is_pinned(self):
        wallet_id = uuid4()

        assert (
            for_wallet_event(NotificationKind.WALLET_DEPOSIT, wallet_id, "ref-1")
            == f"wallet_deposit:wallet:{wallet_id}:ref-1"
        )

    def test_the_same_reference_always_derives_the_same_key(self):
        """Which is what makes the idempotent path silent.

        Repeating an operation with the same ``--ref`` returns the *existing*
        ledger row rather than moving money twice, and the returned row carries
        the same reference as the first attempt - so the second enqueue derives
        the key already in the table and inserts nothing. One deposit, one
        receipt, with no check anywhere.
        """
        wallet_id = uuid4()

        assert for_wallet_event(
            NotificationKind.WALLET_DEPOSIT, wallet_id, "ref-1"
        ) == for_wallet_event(NotificationKind.WALLET_DEPOSIT, wallet_id, "ref-1")

    def test_two_references_are_two_keys(self):
        wallet_id = uuid4()

        assert for_wallet_event(
            NotificationKind.WALLET_DEPOSIT, wallet_id, "ref-1"
        ) != for_wallet_event(NotificationKind.WALLET_DEPOSIT, wallet_id, "ref-2")

    def test_the_kind_is_part_of_the_key_here_too(self):
        """A payout and a withdrawal can carry the same reference.

        Nothing makes ``--ref`` unique across kinds - it is the *ledger* that is
        unique on it, and only within one wallet - so without the kind a wallet
        whose payout and withdrawal shared a reference would announce one of them.
        """
        wallet_id = uuid4()

        assert for_wallet_event(
            NotificationKind.WALLET_PAYOUT, wallet_id, "ref-1"
        ) != for_wallet_event(
            NotificationKind.WALLET_WITHDRAWAL, wallet_id, "ref-1"
        )


class TestTheTwoScopesNeverMeet:
    def test_a_plan_key_is_never_a_wallet_key(self):
        """Both subjects are ``UUID``s, so only the scope word separates them.

        The scope exists for exactly this: without it, a plan event and a wallet
        event could derive the same string, and the second one would be dropped as
        a duplicate of an event that has nothing to do with it.
        """
        subject_id = uuid4()
        stamp = NOON.isoformat()

        plan_key = for_plan_event(
            NotificationKind.PAYOUT_SUCCEEDED, subject_id, NOON
        )
        wallet_key = for_wallet_event(
            NotificationKind.PAYOUT_SUCCEEDED, subject_id, stamp
        )

        assert plan_key != wallet_key
        assert ":plan:" in plan_key
        assert ":wallet:" in wallet_key
