from datetime import date, datetime

import pytest

from app.domain.planning.duration import Duration
from app.domain.planning.durationUnit import DurationUnit
from app.domain.planning.exception import (
    InvalidDurationAmountError,
    InvalidDurationUnitError,
)


class TestResolution:
    def test_days_are_an_exact_offset(self):
        assert Duration(12, DurationUnit.DAYS).end_from(datetime(2026, 1, 1)) == date(
            2026, 1, 13
        )

    def test_weeks_are_an_exact_offset(self):
        assert Duration(2, DurationUnit.WEEKS).end_from(datetime(2026, 1, 1)) == date(
            2026, 1, 15
        )

    def test_months_move_by_calendar_month(self):
        assert Duration(3, DurationUnit.MONTHS).end_from(datetime(2026, 1, 1)) == date(
            2026, 4, 1
        )

    def test_years_move_by_calendar_year(self):
        assert Duration(2, DurationUnit.YEARS).end_from(datetime(2026, 1, 1)) == date(
            2028, 1, 1
        )


class TestMonthsAreNotDayCounts:
    """The reason DurationUnit has four members instead of one.

    If "1 month" and "30 days" were the same instruction, this class would be
    unnecessary - and the difference would only ever surface at a year boundary,
    which is exactly where nobody is looking.
    """

    def test_one_month_is_not_thirty_days(self):
        anchor = datetime(2026, 1, 15)

        assert Duration(1, DurationUnit.MONTHS).end_from(anchor) != Duration(
            30, DurationUnit.DAYS
        ).end_from(anchor)

    def test_a_year_is_exactly_twelve_months(self):
        """Unlike months-versus-days, these two *are* the same instruction.

        YEARS is implemented as ``add_months(anchor, n * 12)``, so the two units
        agree by construction. Asserting it here pins the equivalence down: if
        someone later "fixes" years to be 365 days, this test says no.
        """
        anchor = datetime(2026, 1, 31)

        assert Duration(1, DurationUnit.YEARS).end_from(anchor) == Duration(
            12, DurationUnit.MONTHS
        ).end_from(anchor)

    def test_two_weeks_is_fourteen_days(self):
        """Weeks, by contrast, are an exact offset - they have no calendar of their own."""
        anchor = datetime(2026, 1, 1)

        assert Duration(2, DurationUnit.WEEKS).end_from(anchor) == Duration(
            14, DurationUnit.DAYS
        ).end_from(anchor)


class TestMonthEndClamping:
    """A duration inherits the calendar arithmetic's protection, not a copy of it."""

    def test_one_month_from_the_31st_of_january_lands_on_the_28th(self):
        assert Duration(1, DurationUnit.MONTHS).end_from(datetime(2026, 1, 31)) == date(
            2026, 2, 28
        )

    def test_one_month_from_the_31st_of_march_lands_on_the_30th(self):
        assert Duration(1, DurationUnit.MONTHS).end_from(datetime(2026, 3, 31)) == date(
            2026, 4, 30
        )

    def test_a_year_from_a_leap_day_lands_on_the_28th(self):
        assert Duration(1, DurationUnit.YEARS).end_from(datetime(2024, 2, 29)) == date(
            2025, 2, 28
        )

    def test_the_anchor_is_never_mutated_by_resolution(self):
        """end_from is a query, not a step - the same anchor must resolve the same way twice."""
        anchor = datetime(2026, 1, 31)
        duration = Duration(1, DurationUnit.MONTHS)

        first = duration.end_from(anchor)
        second = duration.end_from(anchor)

        assert first == second == date(2026, 2, 28)
        assert anchor == datetime(2026, 1, 31)


class TestTheEndIsADay:
    """The asymmetry with ``Schedule``, stated as claims.

    A plan *starts* at a moment and *ends* on a day: "until 2 July" means through
    2 July, so a run at noon that day still happens. That is why ``end_from``
    takes a datetime and returns a date, and it is the only place in the
    arithmetic where a time is deliberately thrown away.

    These tests exist because "we drop the time" is a rule that can be applied
    wrongly in two directions - by keeping it, or by letting the drop shift the
    day - and both would be invisible in the caller, which only ever compares
    days.
    """

    def test_the_time_of_day_is_dropped(self):
        assert Duration(1, DurationUnit.MONTHS).end_from(
            datetime(2026, 1, 31, 12, 0)
        ) == date(2026, 2, 28)

    def test_a_late_evening_start_does_not_roll_into_the_next_day(self):
        """23:59 is still the 31st. Nothing here rounds *up* into tomorrow."""
        assert Duration(1, DurationUnit.MONTHS).end_from(
            datetime(2026, 3, 31, 23, 59)
        ) == date(2026, 4, 30)

    def test_days_and_weeks_drop_the_time_too(self):
        assert Duration(14, DurationUnit.DAYS).end_from(
            datetime(2026, 1, 1, 18, 0)
        ) == date(2026, 1, 15)
        assert Duration(2, DurationUnit.WEEKS).end_from(
            datetime(2026, 1, 1, 18, 0)
        ) == date(2026, 1, 15)

    def test_the_start_time_does_not_change_the_end_day(self):
        """Two plans starting the same day at different hours end the same day."""
        midnight = Duration(3, DurationUnit.MONTHS).end_from(datetime(2026, 1, 31))
        noon = Duration(3, DurationUnit.MONTHS).end_from(datetime(2026, 1, 31, 12, 0))

        assert midnight == noon == date(2026, 4, 30)

    def test_the_result_is_a_date_and_not_a_datetime(self):
        """Stated explicitly, because equality would not catch the difference.

        ``datetime`` subclasses ``date``, so a datetime at midnight compares equal
        to the date it falls on - which means every test above would pass even if
        the conversion were forgotten. Only ``isinstance`` can tell them apart.
        """
        end = Duration(1, DurationUnit.MONTHS).end_from(datetime(2026, 1, 31, 12, 0))

        assert not isinstance(end, datetime)
        assert isinstance(end, date)


class TestValidation:
    def test_a_zero_amount_is_rejected(self):
        with pytest.raises(InvalidDurationAmountError):
            Duration(0, DurationUnit.DAYS)

    def test_a_negative_amount_is_rejected(self):
        with pytest.raises(InvalidDurationAmountError):
            Duration(-3, DurationUnit.MONTHS)

    def test_a_bool_amount_is_rejected_despite_being_an_int(self):
        """bool subclasses int, so True would otherwise be a one-day term."""
        assert isinstance(True, int)  # the trap, stated

        with pytest.raises(InvalidDurationAmountError):
            Duration(True, DurationUnit.DAYS)

    def test_a_non_integer_amount_is_rejected(self):
        with pytest.raises(InvalidDurationAmountError):
            Duration("3", DurationUnit.DAYS)

    def test_an_unknown_unit_is_rejected(self):
        with pytest.raises(InvalidDurationUnitError):
            Duration(3, "months")


def test_a_duration_cannot_be_mutated():
    from dataclasses import FrozenInstanceError

    duration = Duration(3, DurationUnit.MONTHS)

    with pytest.raises(FrozenInstanceError):
        duration.amount = 4


def test_str_reads_naturally_in_the_plural():
    assert str(Duration(3, DurationUnit.MONTHS)) == "3 months"


def test_str_reads_naturally_in_the_singular():
    """'1 months' is the kind of detail that makes an interface feel unfinished."""
    assert str(Duration(1, DurationUnit.MONTHS)) == "1 month"
    assert str(Duration(1, DurationUnit.DAYS)) == "1 day"
