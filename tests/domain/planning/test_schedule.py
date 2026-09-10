from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from app.domain.planning.cadence import Cadence
from app.domain.planning.exception import (
    InvalidOccurrenceIndexError,
    InvalidScheduleAnchorError,
    InvalidScheduleCadenceError,
)
from app.domain.planning.schedule import Schedule


@pytest.mark.parametrize("cadence", list(Cadence))
def test_the_first_occurrence_is_always_the_anchor(cadence):
    """Run 0 is the anchor, for every cadence. Everything else counts from here."""
    anchor = date(2026, 3, 15)

    assert Schedule(cadence=cadence, anchor=anchor).occurrence(0) == anchor


def test_daily_occurrences_step_by_days():
    schedule = Schedule(cadence=Cadence.DAILY, anchor=date(2026, 9, 10))

    assert schedule.occurrence(1) == date(2026, 9, 11)
    assert schedule.occurrence(7) == date(2026, 9, 17)


def test_daily_occurrences_cross_a_year_boundary():
    schedule = Schedule(cadence=Cadence.DAILY, anchor=date(2026, 12, 30))

    assert schedule.occurrence(3) == date(2027, 1, 2)


def test_weekly_occurrences_step_by_weeks():
    schedule = Schedule(cadence=Cadence.WEEKLY, anchor=date(2026, 9, 10))

    assert schedule.occurrence(1) == date(2026, 9, 17)
    assert schedule.occurrence(5) == date(2026, 10, 15)


def test_monthly_occurrences_step_by_calendar_months():
    schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 15))

    assert schedule.occurrence(1) == date(2026, 2, 15)
    assert schedule.occurrence(12) == date(2027, 1, 15)


def test_monthly_occurrences_cross_a_year_boundary():
    schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 11, 20))

    assert schedule.occurrence(2) == date(2027, 1, 20)


def test_yearly_occurrences_step_by_years():
    schedule = Schedule(cadence=Cadence.YEARLY, anchor=date(2026, 6, 1))

    assert schedule.occurrence(1) == date(2027, 6, 1)
    assert schedule.occurrence(3) == date(2029, 6, 1)


class TestMonthEndDrift:
    """The bug this class exists to prevent.

    A plan anchored on the 31st must return to the 31st after passing through a
    short month. The naive implementation - add a month to the *previous* run -
    produces 31 Jan -> 28 Feb -> 28 Mar and silently migrates the plan to the
    28th forever. Deriving every occurrence from the anchor is what stops it.
    """

    def test_a_monthly_plan_anchored_on_the_31st_clamps_and_recovers(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 31))

        assert schedule.occurrence(0) == date(2026, 1, 31)
        assert schedule.occurrence(1) == date(2026, 2, 28)  # clamped, not the 3rd of March
        assert schedule.occurrence(2) == date(2026, 3, 31)  # back to the anchor's day
        assert schedule.occurrence(3) == date(2026, 4, 30)
        assert schedule.occurrence(4) == date(2026, 5, 31)

    def test_drift_does_not_accumulate_over_a_year(self):
        """By month 12 the naive version would be on the 28th of every month."""
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 31))

        # January of the following year is the 13th occurrence, and still the 31st.
        assert schedule.occurrence(12) == date(2027, 1, 31)

    def test_a_monthly_plan_anchored_on_the_30th_recovers_in_february(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 30))

        assert schedule.occurrence(1) == date(2026, 2, 28)
        assert schedule.occurrence(2) == date(2026, 3, 30)

    def test_a_leap_day_anchor_returns_on_the_next_leap_year(self):
        """29 February exists only every four years; the plan must survive the gap."""
        schedule = Schedule(cadence=Cadence.YEARLY, anchor=date(2024, 2, 29))

        assert schedule.occurrence(1) == date(2025, 2, 28)
        assert schedule.occurrence(2) == date(2026, 2, 28)
        assert schedule.occurrence(3) == date(2027, 2, 28)
        assert schedule.occurrence(4) == date(2028, 2, 29)  # 2028 is a leap year


class TestScheduleValidation:
    def test_a_negative_occurrence_index_is_rejected(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 1))

        with pytest.raises(InvalidOccurrenceIndexError):
            schedule.occurrence(-1)

    def test_an_unknown_cadence_is_rejected(self):
        with pytest.raises(InvalidScheduleCadenceError):
            Schedule(cadence="monthly", anchor=date(2026, 1, 1))

    def test_a_datetime_anchor_is_rejected_despite_being_a_date(self):
        """datetime subclasses date, so isinstance alone would let it through."""
        assert isinstance(datetime(2026, 1, 1), date)  # the trap, stated

        with pytest.raises(InvalidScheduleAnchorError):
            Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 1, 1, 9, 30))

    def test_a_non_date_anchor_is_rejected(self):
        with pytest.raises(InvalidScheduleAnchorError):
            Schedule(cadence=Cadence.DAILY, anchor="2026-01-01")

    def test_a_schedule_cannot_be_mutated(self):
        schedule = Schedule(cadence=Cadence.DAILY, anchor=date(2026, 1, 1))

        with pytest.raises(FrozenInstanceError):
            schedule.anchor = date(2026, 2, 1)

    def test_two_schedules_with_the_same_cadence_and_anchor_are_equal(self):
        first = Schedule(cadence=Cadence.WEEKLY, anchor=date(2026, 1, 1))
        second = Schedule(cadence=Cadence.WEEKLY, anchor=date(2026, 1, 1))

        assert first == second
        assert hash(first) == hash(second)

    def test_schedules_differing_only_by_cadence_are_not_equal(self):
        daily = Schedule(cadence=Cadence.DAILY, anchor=date(2026, 1, 1))
        weekly = Schedule(cadence=Cadence.WEEKLY, anchor=date(2026, 1, 1))

        assert daily != weekly

    def test_str_names_the_cadence_and_the_anchor(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=date(2026, 1, 31))

        assert str(schedule) == "monthly from 2026-01-31"
