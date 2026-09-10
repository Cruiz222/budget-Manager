from enum import Enum


class PlannedAction(Enum):
    """What a plan does with money when a run fires.

    Deliberately not ``TransactionType``. They answer different questions that
    happen to share two answers today:

    - ``TransactionType`` answers *what did the ledger record?* - past tense,
      and it also covers DEPOSIT and WITHDRAWAL, which no plan can cause.
    - ``PlannedAction`` answers *what should happen?* - future tense, and it is
      the plan's vocabulary, not the ledger's.

    Reusing one enum for both would look like economy and become a trap: the day
    a third action appears that has no ledger counterpart (or vice versa), the
    two vocabularies would have to be unpicked from each other. Mapping between
    them at the application layer costs one small function.
    """

    PAYOUT = "payout"
    """Value leaves the wallet for an external account."""

    RELEASE = "release"
    """Value moves from the locked balance to the available balance.

    The wallet still holds every unit - only the reservation is lifted.
    """
