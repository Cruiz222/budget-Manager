from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .calendarMath import MONTHS_PER_YEAR, add_months
from .durationUnit import DurationUnit
from .exception import InvalidDurationAmountError, InvalidDurationUnitError


@dataclass(frozen=True)
class Duration:
    """How long a plan is meant to last, as the user said it.

    "3 months" - not "2026-04-30". The difference matters because the user is
    choosing a *term*, and the term is what has to be understood relative to the
    plan's anchor. Resolving it to a date is a conversion this object performs,
    not a decision it makes: ``end_from`` is pure, so the same duration always
    yields the same end date and nothing here reads the clock.

    Resolving against the *anchor* rather than against today is the same choice
    that protects the schedule from month-end drift. A plan anchored on
    31 January and set to last one month ends 28 February, and would do so no
    matter which day the user happened to type the command.

    Note the asymmetry with ``Schedule``, because it is a product decision rather
    than an oversight: a plan *starts* at a moment and *ends* on a day. "Until
    2 July" means through 2 July, so a run at noon that day still happens;
    treating the end as a moment would quietly make it midnight, twelve hours
    earlier, and silently drop the last payment. ``end_from`` therefore returns a
    date even though it is handed a datetime.
    """

    amount: int
    unit: DurationUnit

    def end_from(self, anchor: datetime) -> date:
        """The last day the term reaches, counting from ``anchor``.

        Days and weeks are exact offsets. Months and years go through
        ``add_months``, which clamps to the target month's length - so "one
        month" from 31 January is 28 February, not 3 March.

        The end moment is computed first and reduced to its day once, at the
        end, rather than dropping the time at the start. It makes no difference
        to the answer - the clamp reads the day either way - but it keeps the
        time visible in the arithmetic, so the one place it is discarded is the
        one place that says a term ends on a day.
        """
        if self.unit is DurationUnit.DAYS:
            end = anchor + timedelta(days=self.amount)
        elif self.unit is DurationUnit.WEEKS:
            end = anchor + timedelta(weeks=self.amount)
        elif self.unit is DurationUnit.MONTHS:
            end = add_months(anchor, self.amount)
        else:
            end = add_months(anchor, self.amount * MONTHS_PER_YEAR)
        return end.date()

    def __post_init__(self):
        # bool is a subclass of int, so `Duration(True, ...)` would otherwise be
        # accepted as a one-day term. The same trap as datetime-under-date,
        # wearing different clothes: the check has to name the subclass it means.
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise InvalidDurationAmountError(
                f"duration amount must be an integer, got {type(self.amount).__name__}"
            )

        # Zero or negative would make the term end before it starts, which a
        # plan's own ends_on check would then reject with a confusing message
        # about dates. Rejecting it here names the actual problem.
        if self.amount <= 0:
            raise InvalidDurationAmountError(
                f"duration amount must be positive, got {self.amount}"
            )

        if not isinstance(self.unit, DurationUnit):
            raise InvalidDurationUnitError(
                f"unit must be a DurationUnit, got {type(self.unit).__name__}"
            )

    def __str__(self) -> str:
        # Singular when it reads naturally: "1 months" is the kind of detail
        # that makes an interface feel unfinished.
        if self.amount == 1:
            return f"1 {self.unit.value[:-1]}"
        return f"{self.amount} {self.unit.value}"
