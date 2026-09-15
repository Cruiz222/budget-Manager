from enum import Enum


class PasswordResetStatus(Enum):
    """Where a requested password reset has got to.

    Three members and no fourth, and the shape is ``EmailChangeStatus``'s - the
    two aggregates are the same kind of thing: a single-use request with a window
    it is usable inside, whose answer is decided by one atomic write.

    It is a separate enum rather than a reuse of that one for the reason
    ``EmailChangeStatus`` gives about ``ConfirmationStatus``: the two look alike
    and would be read as interchangeable, and a status is a word a reader
    concludes something from. ``EmailChangeStatus`` is about an address leaving an
    account; this is about a secret being replaced. Sharing the vocabulary would
    mean a change to one had to be thought about in terms of the other.

    Values are lowercase names, matching every other enum here.
    """

    AWAITING = "awaiting"
    """Recorded, nothing changed, still usable until it expires."""

    CONFIRMED = "confirmed"
    """Spent, and the password it authorised has been replaced.

    Spent by the *attempt* rather than by the success, with one deliberate
    exception that is argued on ``PasswordReset``: a new password that fails
    ``PlainPassword``'s policy is refused *before* the claim, so the token is not
    spent by it.

    What is **not** an exception is a code that names an account which has since
    been deleted. That refusal unwinds the claim with it, so the row goes back to
    ``AWAITING`` rather than arriving here - and it is harmless, because there is
    no longer an account for any retry to reach. The alternative, committing the
    spend before refusing, would write a row that stakes a claim to a fact the
    transaction cannot deliver, for a remedy that is identical either way.
    """

    EXPIRED = "expired"
    """Past its window, and refused from then on.

    **Derived, and never written to a row.** No instance of the aggregate is ever
    stored in this state - ``PasswordReset.status_as_of`` returns it when an
    ``AWAITING`` request is asked about after its ``expires_at``, which is what a
    reader is shown and what the store's claim refuses on. Storing it would be a
    second record of a fact ``expires_at`` already holds, free to disagree with it,
    and the only thing that could write it is a reader - which would make a ``GET``
    a write.

    The stakes here are the highest of the three aggregates that carry this state.
    A row saying ``EXPIRED`` whose ``expires_at`` is in the future is a credential
    refused for no reason, while one saying ``AWAITING`` past its window is a code
    that still replaces an account's password a day after it was mailed.
    """
