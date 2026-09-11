from enum import Enum


class RunBlockReason(Enum):
    """Why a blocked run was blocked.

    An enum rather than a free-text note, because the question this exists to
    answer is a *countable* one: "how many times has this plan been blocked for
    insufficient funds?" A sentence cannot be grouped by. Structured reasons are
    also what the AI insight layer would read - it can aggregate and trend an
    enum, and can only grep a string.

    The set below is **incomplete by design**. These are the refusals the wallet
    can already make, so they are certain; the run use case is what will prove
    whether anything else is needed.

    Adding a member is free, and this line said the opposite until it was
    corrected. The persisted contract is the member's *name*, so:

    - **renaming** a member is a data migration - existing rows still hold the
      old name and will fail to read back;
    - **adding** one is free - no row can hold a name that did not exist;
    - **widening** what a member *means* is free too, so long as every row
      already written stays true under the new meaning. If it does not, the old
      rows silently start lying, and no migration tool can catch that.

    The third case is the dangerous one precisely because it is the quiet one.
    """

    INSUFFICIENT_BALANCE = "insufficient_balance"
    """The run costs more than the source balance holds. Nothing was attempted."""

    WALLET_FROZEN = "wallet_frozen"
    """The wallet is frozen, so value may not leave it."""

    WALLET_CLOSED = "wallet_closed"
    """The wallet is closed and can no longer be drawn on."""

    FUND_NOT_MATURED = "fund_not_matured"
    """The pot the plan draws on may not pay this run yet.

    Two facts land here, and they share one member because they share one
    remedy: the pot has not come due yet, or an extension has moved its date
    past the commitment that used to authorise paying it early. Either way the
    answer to "what do I do?" is *wait for the date*, so the run reports the
    pot as unspendable and says the true thing.

    That is an *addition* to the set, not a widening of an existing member, and
    the distinction in the docstring above is why it has to be: no row already
    written under another member now means something different.

    The split that does earn its own member is against
    ``INSUFFICIENT_BALANCE``. A run that is short of money wants a top-up; a run
    held back by a date wants patience, and until this member existed the run
    could not tell the user which of the two it needed.
    """
