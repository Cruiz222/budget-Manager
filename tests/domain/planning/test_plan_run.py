from dataclasses import FrozenInstanceError
from datetime import date, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.planning.exception import (
    InvalidPlanRunDueAtError,
    InvalidPlanRunPlanIDError,
    InvalidPlanRunReasonError,
    InvalidPlanRunRecordedAtError,
    InvalidPlanRunStatusError,
    MissingRunBlockReasonError,
    UnexpectedRunBlockReasonError,
)
from app.domain.planning.planRun import PlanRun
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus


def build_run(**overrides):
    kwargs = dict(
        plan_id=uuid4(),
        due_at=date(2026, 4, 1),
        status=RunStatus.SUCCEEDED,
    )
    kwargs.update(overrides)
    return PlanRun(**kwargs)


def test_a_successful_run_records_no_reason():
    run = build_run()

    assert run.status is RunStatus.SUCCEEDED
    assert run.reason is None
    assert isinstance(run.recorded_at, datetime)


def test_a_blocked_run_records_why():
    run = build_run(
        status=RunStatus.BLOCKED,
        reason=RunBlockReason.INSUFFICIENT_BALANCE,
    )

    assert run.status is RunStatus.BLOCKED
    assert run.reason is RunBlockReason.INSUFFICIENT_BALANCE


def test_a_blocked_run_without_a_reason_is_rejected():
    """The whole point of the row is to answer 'why' - a blocked run that can't is worthless."""
    with pytest.raises(MissingRunBlockReasonError):
        build_run(status=RunStatus.BLOCKED)


def test_a_successful_run_carrying_a_reason_is_rejected():
    """The same invariant shape as payout/destination: the explanation must match the outcome."""
    with pytest.raises(UnexpectedRunBlockReasonError):
        build_run(status=RunStatus.SUCCEEDED, reason=RunBlockReason.WALLET_FROZEN)


@pytest.mark.parametrize("reason", list(RunBlockReason))
def test_every_block_reason_is_accepted_on_a_blocked_run(reason):
    assert build_run(status=RunStatus.BLOCKED, reason=reason).reason is reason


def test_an_invalid_plan_id_is_rejected():
    with pytest.raises(InvalidPlanRunPlanIDError):
        build_run(plan_id=str(uuid4()))


def test_a_datetime_due_at_is_rejected_despite_being_a_date():
    assert isinstance(datetime(2026, 4, 1, 9, 0), date)  # the trap, stated

    with pytest.raises(InvalidPlanRunDueAtError):
        build_run(due_at=datetime(2026, 4, 1, 9, 0))


def test_a_non_date_due_at_is_rejected():
    with pytest.raises(InvalidPlanRunDueAtError):
        build_run(due_at="2026-04-01")


def test_an_unknown_status_is_rejected():
    with pytest.raises(InvalidPlanRunStatusError):
        build_run(status="succeeded")


def test_a_non_datetime_recorded_at_is_rejected():
    with pytest.raises(InvalidPlanRunRecordedAtError):
        build_run(recorded_at="2026-04-01T09:00:00")


def test_an_unknown_reason_is_rejected():
    with pytest.raises(InvalidPlanRunReasonError):
        build_run(status=RunStatus.BLOCKED, reason="insufficient_balance")


def test_a_run_cannot_be_mutated():
    run = build_run()

    with pytest.raises(FrozenInstanceError):
        run.status = RunStatus.BLOCKED


def test_the_natural_key_is_plan_and_occurrence():
    plan_id = uuid4()
    run = build_run(plan_id=plan_id, due_at=date(2026, 4, 1))

    assert run.key == (plan_id, date(2026, 4, 1))


def test_runs_sharing_a_key_but_differing_in_outcome_are_not_equal():
    """They are not 'the same run' - they are a bug worth catching."""
    plan_id = uuid4()
    due_at = date(2026, 4, 1)

    blocked = build_run(plan_id=plan_id, due_at=due_at, status=RunStatus.BLOCKED, reason=RunBlockReason.WALLET_FROZEN)
    succeeded = build_run(plan_id=plan_id, due_at=due_at, status=RunStatus.SUCCEEDED)

    assert blocked.key == succeeded.key
    assert blocked != succeeded


def test_str_of_a_successful_run():
    run = build_run(plan_id=UUID(int=0), due_at=date(2026, 4, 1))

    assert str(run) == f"run of {UUID(int=0)} due 2026-04-01: succeeded"


def test_str_of_a_blocked_run_names_the_reason():
    run = build_run(
        plan_id=UUID(int=0),
        due_at=date(2026, 4, 1),
        status=RunStatus.BLOCKED,
        reason=RunBlockReason.INSUFFICIENT_BALANCE,
    )

    assert str(run) == (
        f"run of {UUID(int=0)} due 2026-04-01: blocked (insufficient_balance)"
    )
