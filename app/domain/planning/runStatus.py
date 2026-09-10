from enum import Enum


class RunStatus(Enum):
    """How one run of a plan ended.

    Note what is *missing* and why. There is no ``PENDING``: a run row is
    written only once its outcome is known, so there is no in-between state for
    the enum to name. And there is no ``FAILED``: a run that dies for an
    infrastructure reason rolls its transaction back, taking the run row with
    it, so a FAILED row could never be read back. ``BLOCKED`` is different - it
    is a *decision* the domain reached (the run cannot proceed), recorded
    deliberately, and it does not roll back.

    Deliberately not ``TransactionStatus``, for the same reason
    ``PlannedAction`` is not ``TransactionType``: a run and a ledger entry are
    different things that would only appear to share a vocabulary.
    """

    SUCCEEDED = "succeeded"
    """Every instruction in the run moved its money."""

    BLOCKED = "blocked"
    """The run did not proceed, and nothing moved. Always carries a reason."""
