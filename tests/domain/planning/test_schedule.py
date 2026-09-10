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
    """Run 0 is the anchor, for every cadence. Everything else counts from here.

    Anchored at noon rather than midnight so this also says the time is part of
    what is returned, not a field that happens to survive.
    """
    anchor = datetime(2026, 3, 15, 12, 0)

    assert Schedule(cadence=cadence, anchor=anchor).occurrence(0) == anchor


def test_daily_occurrences_step_by_days():
    schedule = Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 9, 10))

    assert schedule.occurrence(1) == datetime(2026, 9, 11)
    assert schedule.occurrence(7) == datetime(2026, 9, 17)


def test_daily_occurrences_cross_a_year_boundary():
    schedule = Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 12, 30))

    assert schedule.occurrence(3) == datetime(2027, 1, 2)


def test_weekly_occurrences_step_by_weeks():
    schedule = Schedule(cadence=Cadence.WEEKLY, anchor=datetime(2026, 9, 10))

    assert schedule.occurrence(1) == datetime(2026, 9, 17)
    assert schedule.occurrence(5) == datetime(2026, 10, 15)


def test_monthly_occurrences_step_by_calendar_months():
    schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 15))

    assert schedule.occurrence(1) == datetime(2026, 2, 15)
    assert schedule.occurrence(12) == datetime(2027, 1, 15)


def test_monthly_occurrences_cross_a_year_boundary():
    schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 11, 20))

    assert schedule.occurrence(2) == datetime(2027, 1, 20)


def test_yearly_occurrences_step_by_years():
    schedule = Schedule(cadence=Cadence.YEARLY, anchor=datetime(2026, 6, 1))

    assert schedule.occurrence(1) == datetime(2027, 6, 1)
    assert schedule.occurrence(3) == datetime(2029, 6, 1)


class TestMonthEndDrift:
    """The bug this class exists to prevent.

    A plan anchored on the 31st must return to the 31st after passing through a
    short month. The naive implementation - add a month to the *previous* run -
    produces 31 Jan -> 28 Feb -> 28 Mar and silently migrates the plan to the
    28th forever. Deriving every occurrence from the anchor is what stops it.
    """

    def test_a_monthly_plan_anchored_on_the_31st_clamps_and_recovers(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 31))

        assert schedule.occurrence(0) == datetime(2026, 1, 31)
        assert schedule.occurrence(1) == datetime(2026, 2, 28)  # clamped, not 3 March
        assert schedule.occurrence(2) == datetime(2026, 3, 31)  # back to the anchor's day
        assert schedule.occurrence(3) == datetime(2026, 4, 30)
        assert schedule.occurrence(4) == datetime(2026, 5, 31)

    def test_drift_does_not_accumulate_over_a_year(self):
        """By month 12 the naive version would be on the 28th of every month."""
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 31))

        # January of the following year is the 13th occurrence, and still the 31st.
        assert schedule.occurrence(12) == datetime(2027, 1, 31)

    def test_a_monthly_plan_anchored_on_the_30th_recovers_in_february(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 30))

        assert schedule.occurrence(1) == datetime(2026, 2, 28)
        assert schedule.occurrence(2) == datetime(2026, 3, 30)

    def test_a_leap_day_anchor_returns_on_the_next_leap_year(self):
        """29 February exists only every four years; the plan must survive the gap."""
        schedule = Schedule(cadence=Cadence.YEARLY, anchor=datetime(2024, 2, 29))

        assert schedule.occurrence(1) == datetime(2025, 2, 28)
        assert schedule.occurrence(2) == datetime(2026, 2, 28)
        assert schedule.occurrence(3) == datetime(2027, 2, 28)
        assert schedule.occurrence(4) == datetime(2028, 2, 29)  # 2028 is a leap year


class TestTheTimeOfDay:
    """A plan fires at a *moment*, so the time has to survive every step.

    This class could not have existed while the anchor was a date. There was no
    time to lose, so nothing could lose it. Now that there is, the risk is
    specific: ``add_months`` used to rebuild the value from its year, month and
    day fields, which would drop the time silently - and because every occurrence
    is derived from the anchor, a plan that lost its noon would pay at midnight
    for the rest of its life, including the returns to the 31st where month-end
    drift is being handled correctly in every other respect.

    The suite's default anchor is midnight (see ``build_plan``), which cannot
    detect that bug. These tests are what does.
    """

    def test_the_anchor_keeps_its_time(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 3, 2, 12, 0))

        assert schedule.occurrence(0) == datetime(2026, 3, 2, 12, 0)

    def test_monthly_occurrences_keep_the_time(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 3, 2, 12, 0))

        assert schedule.occurrence(1) == datetime(2026, 4, 2, 12, 0)
        assert schedule.occurrence(12) == datetime(2027, 3, 2, 12, 0)

    def test_a_month_end_clamp_keeps_the_time(self):
        """The test that fails if ``add_months`` goes back to rebuilding a value."""
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 31, 12, 0))

        assert schedule.occurrence(1) == datetime(2026, 2, 28, 12, 0)
        assert schedule.occurrence(2) == datetime(2026, 3, 31, 12, 0)

    def test_a_leap_day_clamp_keeps_the_time(self):
        schedule = Schedule(cadence=Cadence.YEARLY, anchor=datetime(2024, 2, 29, 9, 30))

        assert schedule.occurrence(1) == datetime(2025, 2, 28, 9, 30)
        assert schedule.occurrence(4) == datetime(2028, 2, 29, 9, 30)

    def test_daily_and_weekly_occurrences_keep_the_time(self):
        daily = Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 9, 10, 23, 45))
        weekly = Schedule(cadence=Cadence.WEEKLY, anchor=datetime(2026, 9, 10, 23, 45))

        assert daily.occurrence(2) == datetime(2026, 9, 12, 23, 45)
        assert weekly.occurrence(1) == datetime(2026, 9, 17, 23, 45)

    def test_nothing_here_rounds_the_anchor(self):
        """The anchor is *carried*, not reconstructed, so precision is not lost.

        Seconds are not something this CLI produces - ``--from`` is typed to the
        minute - but a value object that quietly truncated them would be lying
        about what it was given, and the truncation would show up as an off-by-a-
        second idempotency key rather than as an error.
        """
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 31, 12, 0, 30))

        assert schedule.occurrence(1) == datetime(2026, 2, 28, 12, 0, 30)

    def test_a_midnight_anchor_is_a_midnight_anchor(self):
        """Not a special case, just the other end of the range.

        Worth stating because midnight is what every pre-datetime plan loads as,
        and what ``--from 2026-01-01`` still means. It must behave like any other
        time rather than like "no time given".
        """
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 31))

        assert schedule.occurrence(1) == datetime(2026, 2, 28, 0, 0)


class TestScheduleValidation:
    def test_a_negative_occurrence_index_is_rejected(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1))

        with pytest.raises(InvalidOccurrenceIndexError):
            schedule.occurrence(-1)

    def test_an_unknown_cadence_is_rejected(self):
        with pytest.raises(InvalidScheduleCadenceError):
            Schedule(cadence="monthly", anchor=datetime(2026, 1, 1))

    def test_a_bare_date_anchor_is_rejected(self):
        """The trap, in the new direction.

        A ``datetime`` passes ``isinstance(x, date)``, so the date-based version
        of this test had to *exclude* datetimes by name. The anchor now wants the
        datetime - which makes the reverse mistake just as easy: passing a plain
        date, which is a date but has no time to be due at. An assertion about
        the subclass relation is kept because it is still the reason this needs a
        test rather than being obvious.
        """
        assert isinstance(datetime(2026, 1, 1), date)  # the trap, still true

        with pytest.raises(InvalidScheduleAnchorError):
            Schedule(cadence=Cadence.DAILY, anchor=date(2026, 1, 1))

    def test_a_datetime_anchor_is_accepted(self):
        schedule = Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 1, 1, 9, 30))

        assert schedule.anchor == datetime(2026, 1, 1, 9, 30)

    def test_a_non_datetime_anchor_is_rejected(self):
        with pytest.raises(InvalidScheduleAnchorError):
            Schedule(cadence=Cadence.DAILY, anchor="2026-01-01")

    def test_a_schedule_cannot_be_mutated(self):
        schedule = Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 1, 1))

        with pytest.raises(FrozenInstanceError):
            schedule.anchor = datetime(2026, 2, 1)

    def test_two_schedules_with_the_same_cadence_and_anchor_are_equal(self):
        first = Schedule(cadence=Cadence.WEEKLY, anchor=datetime(2026, 1, 1))
        second = Schedule(cadence=Cadence.WEEKLY, anchor=datetime(2026, 1, 1))

        assert first == second
        assert hash(first) == hash(second)

    def test_schedules_differing_only_by_cadence_are_not_equal(self):
        daily = Schedule(cadence=Cadence.DAILY, anchor=datetime(2026, 1, 1))
        weekly = Schedule(cadence=Cadence.WEEKLY, anchor=datetime(2026, 1, 1))

        assert daily != weekly

    def test_schedules_differing_only_by_time_are_not_equal(self):
        """Same cadence, same day, different hour - two different plans.

        Another claim the date-based version could not make.
        """
        noon = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1, 12, 0))
        midnight = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 1))

        assert noon != midnight

    def test_str_names_the_cadence_and_the_anchor(self):
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 1, 31))

        assert str(schedule) == "monthly from 2026-01-31T00:00"

    def test_str_shows_a_noon_anchor(self):
        """To the minute, so the visible time is the one that was set."""
        schedule = Schedule(cadence=Cadence.MONTHLY, anchor=datetime(2026, 3, 2, 12, 0))

        assert str(schedule) == "monthly from 2026-03-02T12:00"
