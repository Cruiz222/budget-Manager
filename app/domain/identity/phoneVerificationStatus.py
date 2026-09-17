from enum import Enum


class PhoneVerificationStatus(Enum):
    """Where a requested number verification has got to.

    Three members and no fourth, and the shape is ``PasswordResetStatus``'s - which
    is ``EmailChangeStatus``'s. All three aggregates are the same kind of thing: a
    single-use request with a window it is usable inside, whose answer is decided by
    one atomic write.

    It is a separate enum rather than a reuse of either for the reason
    ``PasswordResetStatus`` gives about ``EmailChangeStatus``: the three look alike
    and would be read as interchangeable, and a status is a word a reader concludes
    something from. An address change is about an account's identity moving; a reset
    is about a secret being replaced; **this is about a number being shown to belong
    to a person who is not yet an account holder at all** - which is the one of the
    three where there is no ``user_id`` on the row, and therefore the one where
    "confirmed" means something structurally different. Sharing the vocabulary would
    mean a change to one had to be thought about in terms of the other.

    Values are lowercase names, matching every other enum here.
    """

    AWAITING = "awaiting"
    """Recorded, nothing changed, still usable until it expires."""

    CONFIRMED = "confirmed"
    """Spent, and the number it authorised has been claimed.

    Spent by the *attempt* rather than by the success, with the same one deliberate
    exception ``PasswordResetStatus`` records: a password that fails
    ``PlainPassword``'s policy is refused *before* the claim, so a code is not spent
    by a weak password the person will immediately retype.

    What is **not** an exception here is a number that has meanwhile been claimed by
    somebody else: that refusal spends the code too, and it does so by *committing*
    the claim before it raises ``DuplicatePhoneError``, so the retry arrives here
    rather than at ``AWAITING``. The rule is ``ConfirmEmailChange``'s, and
    ``ConfirmPhoneSignUp.execute`` carries the argument - the reason it is right here
    and not merely inherited is that a number which is taken is taken, so the retry
    this row would otherwise permit could only reproduce the same refusal, while a
    live row would go on holding the ``UNIQUE`` slot on ``phone`` against the person
    who now holds the handset.

    **This paragraph described the opposite until step 4**, and the correction is
    worth recording rather than tidying away: it was written against the shape the
    plan then had - ``ConfirmPhoneSignUp.execute(phone, code, password, now)``, where
    the duplicate arrived as an ``IntegrityError`` on the account write and therefore
    unwound the claim necessarily. Giving the confirm no ``phone`` parameter moved
    the duplicate lookup in front of the write, and the explicit refusal that
    replaced it chose to commit. Nothing about the row changed; what changed is that
    the outcome became a decision instead of a consequence.
    """

    EXPIRED = "expired"
    """Past its window, and refused from then on.

    **Derived, and never written to a row.** No instance of the aggregate is ever
    stored in this state - ``PhoneVerification.status_as_of`` returns it when an
    ``AWAITING`` request is asked about after its ``expires_at``, which is what a
    reader is shown and what the store's claim refuses on. Storing it would be a
    second record of a fact ``expires_at`` already holds, free to disagree with it,
    and the only thing that could write it is a reader - which would make a ``GET``
    a write.

    The stakes sit between the other two. A reset's window is the gravest, because a
    stale code replaces a password outright; a change request's leaves a credential
    in a mailbox. A stale code here claims a **unique identifier** - which means a
    person who asks for a number and does not answer holds the ``UNIQUE`` slot on
    it, so the number cannot be signed up with by its actual owner until the row
    expires. That is a nuisance rather than a breach, and it is the reason the window
    below is tighter than its two siblings'.
    """
