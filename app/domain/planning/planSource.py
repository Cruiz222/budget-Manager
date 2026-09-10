from enum import Enum


class PlanSource(Enum):
    """Which of the wallet's two balances a plan spends from.

    This is the "from where" axis, and it is what makes the same plan shape
    serve both halves of the product:

    - ``LOCKED`` - spend money the user deliberately set aside. The reservation
      is a discipline device: the funds were not available to spend on impulse.
    - ``AVAILABLE`` - spend money that was never locked. Per the product rule,
      scheduling is not conditional on locking.

    ``RELEASE`` instructions only make sense against ``LOCKED``: releasing to the
    available balance when you were already spending from it is a no-op dressed
    up as an instruction. ``SavingsPlan`` rejects that combination.
    """

    LOCKED = "locked"
    AVAILABLE = "available"
