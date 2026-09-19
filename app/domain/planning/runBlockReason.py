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

    TIER_LIMIT_EXCEEDED = "tier_limit_exceeded"
    """The owner's account may not move this much, whatever the wallet holds.

    **One member for three ceilings**, which is the opposite of the split above
    and is worth the sentence: the per-transaction ceiling, the daily outflow
    cap and the balance cap are three rules with one remedy between them - *this
    account is not allowed to, and completing a profile is how that changes* -
    where the two money members above have genuinely different remedies. The
    counter this enum exists for asks "how many runs did a limit turn away?",
    and it is answered.

    What is *not* recorded here is which ceiling refused the run. The exception
    the wallet path raises carries it (see ``TierLimitExceededError``), and a
    caller that needs the same granularity from the plan path will want another
    member - which is free to add, per the note above, and which no row already
    written would become wrong by.
    """

    CURRENCY_MISMATCH = "currency_mismatch"
    """The plan's money is in a currency the wallet it draws on does not hold.

    **An addition to the set, and the note at the top of this file is why it
    has to be.** No row already written under another member changes meaning
    because this exists: a mismatched plan did not previously block as
    ``INSUFFICIENT_BALANCE`` or as anything else, because it did not previously
    block at all. It raised.

    **Which is the whole argument for the member.** A plan in USD drawn on an
    NGN wallet used to reach ``_money_block``, where ``Money`` refused the
    comparison between two currencies - so the tick died and every plan after it
    in the pass did not run. One plan's bad edit stopped everybody's runs. That
    is the failure ``_limit_block`` already refuses to commit one door over, and
    the reasoning transfers exactly: five instructions judged one at a time
    would raise on the fourth and take the tick down, so a refusal that is a
    *fact about one plan* is recorded as a block and the loop carries on.

    **A block rather than a pause is what it means for the owner.** The run did
    not happen and the plan stops until a human looks at it - which is right in
    a way ``INSUFFICIENT_BALANCE`` is not, because topping up cannot fix this.
    The remedy is to edit the instructions back into the wallet's currency, and
    the blocked run is the record that says so.

    **The door is closed on the other side too.** ``PlanService`` now re-applies
    the currency rule on ``edit_instructions``, so a plan cannot be moved into
    this state any more. This member exists for the plans that were already put
    there, and as the backstop that makes the tick's resilience a property of
    the scheduler rather than a promise about every writer.
    """
