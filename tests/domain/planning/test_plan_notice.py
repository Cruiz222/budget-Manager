from dataclasses import FrozenInstanceError
from datetime import date, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.planning.exception import (
    InvalidPlanNoticeDueAtError,
    InvalidPlanNoticePlanIDError,
    InvalidPlanNoticeRaisedAtError,
)
from app.domain.planning.planNotice import PlanNotice


def build_notice(**overrides):
    kwargs = dict(
        plan_id=uuid4(),
        due_at=datetime(2026, 4, 1, 12, 0),
        raised_at=datetime(2026, 4, 1, 11, 30),
    )
    kwargs.update(overrides)
    return PlanNotice(**kwargs)


def test_a_notice_holds_the_plan_the_occurrence_and_the_moment_it_was_raised():
    notice = build_notice(
        due_at=datetime(2026, 4, 1, 12, 0),
        raised_at=datetime(2026, 4, 1, 11, 30),
    )

    assert notice.due_at == datetime(2026, 4, 1, 12, 0)
    assert notice.raised_at == datetime(2026, 4, 1, 11, 30)


def test_an_invalid_plan_id_is_rejected():
    with pytest.raises(InvalidPlanNoticePlanIDError):
        build_notice(plan_id=str(uuid4()))


def test_a_bare_date_due_at_is_rejected():
    """The subclass trap, and this is the third place it bites.

    ``datetime`` passes ``isinstance(x, date)``, so a check written against the
    wider type would accept either. Here the narrower type is required by name,
    and a plain date fails for that reason rather than by a special case.
    """
    assert isinstance(datetime(2026, 4, 1, 9, 0), date)  # the trap, still true

    with pytest.raises(InvalidPlanNoticeDueAtError):
        build_notice(due_at=date(2026, 4, 1))


def test_a_non_datetime_due_at_is_rejected():
    with pytest.raises(InvalidPlanNoticeDueAtError):
        build_notice(due_at="2026-04-01T12:00:00")


def test_a_non_datetime_raised_at_is_rejected():
    with pytest.raises(InvalidPlanNoticeRaisedAtError):
        build_notice(raised_at="2026-04-01T11:30:00")


def test_a_notice_cannot_be_mutated():
    notice = build_notice()

    with pytest.raises(FrozenInstanceError):
        notice.due_at = datetime(2026, 5, 1, 12, 0)


def test_the_natural_key_is_plan_and_occurrence():
    """Not the moment it was raised.

    Two ticks inside the same window are the same *notice* - that is the entire
    reason the warning does not repeat - so the raised-at moment cannot be part
    of what identifies it.
    """
    plan_id = uuid4()
    notice = build_notice(plan_id=plan_id, due_at=datetime(2026, 4, 1, 12, 0))

    assert notice.key == (plan_id, datetime(2026, 4, 1, 12, 0))


def test_two_notices_of_one_occurrence_differing_only_in_raised_at_share_a_key():
    plan_id = uuid4()
    due_at = datetime(2026, 4, 1, 12, 0)

    early = build_notice(plan_id=plan_id, due_at=due_at, raised_at=datetime(2026, 4, 1, 11, 30))
    late = build_notice(plan_id=plan_id, due_at=due_at, raised_at=datetime(2026, 4, 1, 11, 45))

    assert early.key == late.key
    assert early != late


def test_notices_for_different_occurrences_do_not_share_a_key():
    plan_id = uuid4()

    first = build_notice(plan_id=plan_id, due_at=datetime(2026, 4, 1, 12, 0))
    second = build_notice(plan_id=plan_id, due_at=datetime(2026, 5, 1, 12, 0))

    assert first.key != second.key


def test_str_reads_as_the_occurrence_being_announced():
    notice = build_notice(plan_id=UUID(int=0), due_at=datetime(2026, 4, 1, 12, 0))

    assert str(notice) == f"notice of {UUID(int=0)} for 2026-04-01T12:00"
