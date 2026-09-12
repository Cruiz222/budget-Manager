from enum import Enum


class ConfirmationStatus(Enum):
    """Where a recorded request has got to.

    **Deliberately not ``TransactionStatus``, and the near-miss is the point.**
    ``PENDING`` and ``AWAITING`` both read as "not finished", and they are
    opposites:

      PENDING   a movement that has been *done* and cannot yet be called
                finished - the wallet is already debited, and what is missing is
                settlement.
      AWAITING  a request that has been *made* and nothing has been done about
                it at all - not one unit has moved.

    Phase 2b introduced the first one (money crossing the system edge settles
    later, so the ledger row is held PENDING), and this feature adds the second
    one level earlier. Reusing ``TransactionStatus`` here would have let a reader
    who saw "PENDING" on a confirmation conclude that money had already left -
    which is precisely the mistake the confirmation exists to prevent.

    So the two vocabularies do not overlap: ``PENDING`` is never a confirmation
    status, and ``AWAITING`` is never a transaction status.

    Three members and no fourth, because there is no failure state to record: a
    refused confirm is an ordinary domain refusal whose *outcome* is a FAILED
    ledger row, and the confirmation that authorised the attempt is spent by it
    (``CONFIRMED``). A separate ``REFUSED`` would be a second word for a fact the
    row beside it already states.

    Values are lowercase names, matching every other enum here.
    """

    AWAITING = "awaiting"
    """Recorded, nothing done, still usable until it expires."""

    CONFIRMED = "confirmed"
    """Spent. The attempt it authorised has run - successfully or not.

    Spent by the *attempt*, not by the success: a confirm refused for
    insufficient funds still writes a FAILED ledger row, and that row is what
    spends the request. See ``WalletService.confirm``.
    """

    EXPIRED = "expired"
    """Past its window, and refused from then on.

    **Derived, and never written to a row.** No instance of the aggregate is
    ever stored in this state - ``Confirmation.status_as_of`` returns it when an
    ``AWAITING`` request is asked about after its ``expires_at``, which is what a
    reader is shown and what ``confirm`` refuses on. Storing it would be a second
    record of a fact ``expires_at`` already holds, free to disagree with it, and
    the only thing that could write it is a reader - which would make a ``GET`` a
    write. Expiry here is *checked*, not swept, exactly as ``Session`` expiry is.
    """
