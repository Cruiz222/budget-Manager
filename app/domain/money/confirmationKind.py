from enum import Enum


class ConfirmationKind(Enum):
    """What a confirmation, once answered, will do.

    The four members are **exactly the four ``WalletService`` methods a
    confirmation can dispatch to** - ``withdraw``, ``payout_from_available``,
    ``payout_from_locked``, ``close_wallet`` - and that one-to-one is the reason
    this is a single enum rather than a pair of fields like (transaction type,
    source balance).

    A pair would have made some combinations spellable that mean nothing:
    "withdrawal from locked" is not an operation this system has, and a
    ``kind``/``source`` encoding would let a caller ask for it and leave the
    dispatch table to invent an answer. One enum makes the meaningless states
    unrepresentable, and the dispatch table becomes a total function over four
    names with no default branch.

    Which operations get a confirmation at all is a separate decision, and a
    narrow one: only the three that move money *out of the wallet for good*.
    ``lock``, ``release`` and ``extend`` are reversible and stay inside the
    wallet, so a mis-tap is undone by typing the opposite command - see the
    README's decision log.

    Values are lowercase names, matching ``PlannedAction`` and ``TransactionType``.
    """

    WITHDRAWAL = "withdrawal"
    """Available balance -> outside the system, with no counterparty recorded."""

    PAYOUT_FROM_AVAILABLE = "payout_from_available"
    """Available balance -> a named external account."""

    PAYOUT_FROM_LOCKED = "payout_from_locked"
    """A pot's balance -> a named external account.

    Separate from ``PAYOUT_FROM_AVAILABLE`` rather than one PAYOUT with a source
    field, for the reason above: the two read different balances, refuse for
    different reasons (a locked payout can be refused for immaturity where an
    available one cannot), and draw on the wallet's pots in an order the other
    never consults.
    """

    CLOSE = "close"
    """The wallet itself, and the one kind that moves no money.

    It is here because it is irreversible in the way the others are - there is no
    transition that reopens a wallet - and because a confirmation is the only
    route to it. It carries no amount, no destination and no fund: there is
    nothing to describe beyond the wallet it names.
    """
