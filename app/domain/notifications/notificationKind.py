from enum import Enum


class NotificationKind(Enum):
    """What a notification is *about*.

    A closed set, for the reason decision 6 gives: a fixed concept gets an enum,
    so a typo is an error rather than a new value nobody handles.

    **Which events are here, and which are deliberately not.** These are the
    events that cross the wallet's boundary:

      - money arriving from outside (``WALLET_DEPOSIT``)
      - money leaving to another account (``WALLET_PAYOUT``)
      - money moving between the wallet's own balances (``WALLET_WITHDRAWAL``
        is *not* that - see below)
      - and the two things a plan can do at its moment: ``PAYOUT_SUCCEEDED``
        and ``PAYOUT_BLOCKED``

    Absent on purpose: locking, releasing, freezing and unfreezing. Every one of
    those moves value between the wallet's *own* two balances, or changes only a
    status flag - none of them changes what the owner holds, and all of them are
    performed by the person reading the mail, at a terminal that has already
    printed the result. Emailing a receipt for a command someone typed one second
    ago is not information, it is noise, and noise is what makes a real
    notification easy to miss.

    Adding one back is a line in ``WalletService.ANNOUNCED`` - which is the point
    of holding this decision in a data structure rather than in prose. It is a
    reversible bet, not a gate.

    ``PAYOUT_BLOCKED`` is worth its own member rather than being a flag on
    ``PAYOUT_SUCCEEDED``, because it is the one message a user most needs and
    least expects: nothing moved, so nothing else in the system would tell them.
    """

    PAYOUT_SUCCEEDED = "payout_succeeded"
    """A scheduled run paid out. The money has already gone."""

    PAYOUT_BLOCKED = "payout_blocked"
    """A scheduled run could not proceed, and the plan is now paused."""

    WALLET_DEPOSIT = "wallet_deposit"
    """Money arrived in the wallet from outside."""

    WALLET_WITHDRAWAL = "wallet_withdrawal"
    """Money left the wallet to an external account, outside any plan."""

    WALLET_PAYOUT = "wallet_payout"
    """A manual payout from the wallet's locked balance to a bank account."""
