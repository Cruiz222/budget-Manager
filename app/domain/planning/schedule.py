from dataclasses import dataclass
from datetime import datetime, timedelta

from .cadence import Cadence
from .calendarMath import MONTHS_PER_YEAR, add_months
from .exception import (
    InvalidOccurrenceIndexError,
    InvalidScheduleAnchorError,
    InvalidScheduleCadenceError,
)


@dataclass(frozen=True)
class Schedule:
    """When a plan repeats: a cadence plus the moment the pattern is anchored to.

    A *moment*, not a day. A plan that pays salaries at noon is a different
    promise from one that pays them at midnight, and while the anchor was a
    ``date`` the difference could not be expressed at all - which is what made
    "notify me 30 minutes before it fires" unrepresentable.

    Frozen, and pure: nothing here reads the clock. A ``Schedule`` cannot tell
    you "the next run is Tuesday" - you have to hand it the moment you mean.
    Keeping the clock outside is what makes the drift arithmetic below testable
    without freezing time.
    """

    cadence: Cadence
    anchor: datetime

    def __post_init__(self):
        if not isinstance(self.cadence, Cadence):
            raise InvalidScheduleCadenceError(
                f"cadence must be a Cadence, got {type(self.cadence).__name__}"
            )
        # The old check here had the opposite shape, and the reason is worth
        # keeping. It had to *exclude* datetimes, because a datetime passes
        # isinstance(x, date) - the subclass trap - and one would otherwise have
        # slipped in and been truncated. Now the datetime is what we want, so no
        # second clause is needed: a plain date is not a datetime, and it is
        # rejected for that reason rather than by a special case.
        if not isinstance(self.anchor, datetime):
            raise InvalidScheduleAnchorError(
                f"anchor must be a datetime, got {type(self.anchor).__name__}"
            )

    def occurrence(self, index: int) -> datetime:
        """The moment of the ``index``-th run, counting the anchor as run 0.

        Every occurrence is derived from the anchor rather than from its
        predecessor. That single choice is what makes month-end drift
        unrepresentable instead of merely unlikely: there is no code path that
        can accumulate a rounding error, because nothing is ever computed from
        the previous answer. It is now also what carries the time of day - every
        occurrence is the anchor, moved - so a noon plan pays at noon forever.
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
            return add_months(self.anchor, index)
        return add_months(self.anchor, index * MONTHS_PER_YEAR)

    def __str__(self) -> str:
        # To the minute: seconds are never something a user set, so printing
        # "12:00:00" would be reporting precision this value does not have.
        return f"{self.cadence.value} from {self.anchor.isoformat(timespec='minutes')}"
