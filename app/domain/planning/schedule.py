import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .cadence import Cadence
from .exception import (
    InvalidOccurrenceIndexError,
    InvalidScheduleAnchorError,
    InvalidScheduleCadenceError,
)

_MONTHS_PER_YEAR = 12


def _add_months(start: date, months: int) -> date:
    """``start`` moved forward by whole months, clamped to the target month's end.

    The clamp is what makes February survivable: a plan anchored on the 31st
    lands on the 28th in a short month. Note that the day is always read from
    ``start`` - the anchor - and never from the previous occurrence. That is the
    whole trick. Advancing month-by-month from the *last run* would take
    31 Jan -> 28 Feb -> 28 Mar and never find its way back to the 31st.
    """
    index = start.month - 1 + months
    year = start.year + index // _MONTHS_PER_YEAR
    month = index % _MONTHS_PER_YEAR + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day_of_month))


@dataclass(frozen=True)
class Schedule:
    """When a plan repeats: a cadence plus the date the pattern is anchored to.

    Frozen, and pure: nothing here reads the clock. A ``Schedule`` cannot tell
    you "the next run is Tuesday" - you have to hand it the moment you mean.
    Keeping the clock outside is what makes the drift arithmetic below testable
    without freezing time.
    """

    cadence: Cadence
    anchor: date

    def __post_init__(self):
        if not isinstance(self.cadence, Cadence):
            raise InvalidScheduleCadenceError(
                f"cadence must be a Cadence, got {type(self.cadence).__name__}"
            )
        # A datetime passes isinstance(x, date) - datetime subclasses date - so
        # without the second clause one would slip through and then poison the
        # date arithmetic with time-of-day components. Reject it explicitly.
        if not isinstance(self.anchor, date) or isinstance(self.anchor, datetime):
            raise InvalidScheduleAnchorError(
                f"anchor must be a date, got {type(self.anchor).__name__}"
            )

    def occurrence(self, index: int) -> date:
        """The date of the ``index``-th run, counting the anchor as run 0.

        Every occurrence is derived from the anchor rather than from its
        predecessor. That single choice is what makes month-end drift
        unrepresentable instead of merely unlikely: there is no code path that
        can accumulate a rounding error, because nothing is ever computed from
        the previous answer.
        """
        if index < 0:
            raise InvalidOccurrenceIndexError(
                f"occurrence index cannot be negative, got {index}"
            )
        if self.cadence is Cadence.DAILY:
            return self.anchor + timedelta(days=index)
        if self.cadence is Cadence.WEEKLY:
            return self.anchor + timedelta(weeks=index)
        if self.cadence is Cadence.MONTHLY:
            return _add_months(self.anchor, index)
        return _add_months(self.anchor, index * _MONTHS_PER_YEAR)

    def __str__(self) -> str:
        return f"{self.cadence.value} from {self.anchor.isoformat()}"
