from enum import Enum


class EmailChangeStatus(Enum):
    """Where a requested move of an account's address has got to.

    Three members and no fourth, and the shape is ``ConfirmationStatus``'s - the
    two aggregates are the same kind of thing: a single-use request with a window
    it is usable inside, whose answer is decided by one atomic write.

    It is a separate enum rather than a reuse of that one for the reason
    ``ConfirmationStatus`` gives about ``TransactionStatus``: the two look alike
    and would be read as interchangeable, and a status is a word a reader
    concludes something from. ``ConfirmationStatus`` is about money leaving a
    wallet; this is about an address leaving an account. Sharing the vocabulary
    would mean a change to one had to be thought about in terms of the other.

    Values are lowercase names, matching every other enum here.
    """

    AWAITING = "awaiting"
    """Recorded, nothing changed, still usable until it expires."""

    CONFIRMED = "confirmed"
    """Spent. The change it authorised was attempted - successfully or not.

    Spent by the *attempt*, not by the success, which is the rule
    ``ConfirmationStatus.CONFIRMED`` states and the reason it is restated here
    rather than inherited: a confirm that finds the new address has been taken by
    somebody else in the meantime refuses with ``DuplicateEmailError``, and the
    request that carried the token is spent all the same. The alternative - leaving
    it ``AWAITING`` so the same token can be retried - would make a refused change
    a token that stays live, and the remedy for a taken address is to ask again
    with a different one anyway.
    """

    EXPIRED = "expired"
    """Past its window, and refused from then on.

    **Derived, and never written to a row.** No instance of the aggregate is ever
    stored in this state - ``EmailChange.status_as_of`` returns it when an
    ``AWAITING`` request is asked about after its ``expires_at``, which is what a
    reader is shown and what the store's claim refuses on. Storing it would be a
    second record of a fact ``expires_at`` already holds, free to disagree with it,
    and the only thing that could write it is a reader - which would make a ``GET``
    a write.

    The stakes here are higher than they look. A row saying ``EXPIRED`` whose
    ``expires_at`` is in the future is a credential refused for no reason, while
    one saying ``AWAITING`` past its window is a token that still works a day
    after it was mailed - and this one moves an account's address rather than
    spending a wallet's balance.
    """
