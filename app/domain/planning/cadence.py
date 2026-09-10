from enum import Enum


class Cadence(Enum):
    """How often a plan repeats.

    A cadence is a *step*, not a frequency in hertz: it says how to get from one
    occurrence to the next, which is why the value is meaningless without a
    starting point. ``Cadence.MONTHLY`` alone cannot tell you whether the next
    run is the 3rd or the 31st - that comes from the anchor, and lives in
    ``Schedule``.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
