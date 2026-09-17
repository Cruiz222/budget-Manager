from enum import Enum


class LimitKind(Enum):
    """Which ceiling refused a movement.

    An enum rather than a sentence in the exception, for ``RunBlockReason``'s
    reason exactly: the question an operator asks afterwards is a *countable*
    one - "how many withdrawals were refused for the daily cap this week?" - and
    a sentence cannot be grouped by.

    It is also what a client switches on, because the remedy differs by kind and
    the difference is the only useful thing to tell somebody:

    - ``PER_TRANSACTION`` means *send less* - the same movement, smaller, works
      immediately.
    - ``DAILY_OUTFLOW`` means *wait* - sending less does not help if the day is
      spent, and the allowance returns on a boundary the client cannot move.
    - ``MAX_BALANCE`` means *spend or withdraw first* - the wallet is full, and
      the refusal is about where the money would come to rest rather than about
      the movement itself.

    Three distinct remedies is the whole argument for three members. A client
    shown one generic "limit exceeded" would tell a person to try again
    tomorrow when they needed to send 1,000 less.

    A module of its own rather than a member of ``tier``, so that
    ``exception.py`` can name it without importing the limits table - the
    arrangement ``confirmationKind``, ``fundKind`` and ``runBlockReason``
    already use, and for the same reason: an enum the exception layer needs is
    an enum the exception layer should not have to reach through a module of
    business rules to find.
    """

    PER_TRANSACTION = "per_transaction"
    """One movement is larger than this tier allows."""

    DAILY_OUTFLOW = "daily_outflow"
    """This movement would take the day's total outflow past the tier's allowance."""

    MAX_BALANCE = "max_balance"
    """This movement would leave the wallet holding more than the tier permits."""
