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
    whether anything else is needed. Adding a member later is a data migration,
    not a refactor - the persisted contract is the member's *name*.
    """

    INSUFFICIENT_BALANCE = "insufficient_balance"
    """The run costs more than the source balance holds. Nothing was attempted."""

    WALLET_FROZEN = "wallet_frozen"
    """The wallet is frozen, so value may not leave it."""

    WALLET_CLOSED = "wallet_closed"
    """The wallet is closed and can no longer be drawn on."""
