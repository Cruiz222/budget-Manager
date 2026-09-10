from enum import Enum


class PlanStatus(Enum):
    """Where a plan sits between creation and its end.

    Transitions (all one-way except pause/resume):

        ACTIVE <-> PAUSED
        ACTIVE  -> COMPLETED   (the next occurrence falls past the end date)
        ACTIVE  -> CANCELLED   (the user stopped it)
        PAUSED  -> CANCELLED

    ``PAUSED`` is not a synonym for ``CANCELLED``: it is what a plan becomes
    when a run could not proceed, and it waits there for a human to look. The
    run counter does **not** advance while paused, so resuming picks the missed
    payment back up rather than skipping it.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
