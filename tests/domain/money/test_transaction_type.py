"""Which ledger movements the daily outflow cap is built from.

``OUTBOUND_TYPES`` is one frozenset in ``app/domain/money/transactionType.py``
and this file is three assertions about it. That ratio is the point rather than
an accident: the constant is trivial and its *completeness* is not, because the
way a movement type escapes a financial control is by falling silently through to
"not an outflow". Nothing raises, no row is refused, and the cap is simply not
applied to a class of movement nobody decided to exempt.

So the test that matters is a partition. The members that are outbound are
``OUTBOUND_TYPES``'s business - the constant says which they are - and the
members that are not are written out *here*, in full, so that the two sets
together have to be the whole enum. Adding a member above fails this file until
somebody decides which side it is on, and that decision is exactly the one that
must not be made by default.
"""

from app.domain.money.transactionType import (
    OUTBOUND_TYPES,
    TransactionType,
)

#: Every member whose money does **not** leave the wallet, and why each is here.
#:
#: A deliberate second copy of the argument in ``OUTBOUND_TYPES``'s docstring,
#: and the duplication is what makes the partition a test rather than a tautology:
#: if this set imported the constant, or were derived from it by subtraction, it
#: would agree with the code by construction and the completeness assertion below
#: would hold no matter what either said.
NOT_OUTBOUND = {
    # Value arriving, which the balance ceiling faces instead - see
    # ``check_credit``. An inbound movement cannot be capped by a rule about what
    # leaves.
    TransactionType.DEPOSIT,
    # The wallet's own two balances, reshuffled. A pot is money the account still
    # holds, so moving money into one changes what the owner has by nothing at
    # all - and counting it would make saving look like spending.
    TransactionType.LOCK_FUNDS,
    TransactionType.UNLOCK_FUNDS,
}


def test_every_transaction_type_is_classified():
    """The union of the two sides is the whole enum, and they do not overlap.

    Both halves are needed and they fail for different mistakes:

    - the **union** catches a member that reached neither side, which is a new
      movement type outside the cap;
    - the **intersection** catches a member placed on both, which would make the
      cap and the balance ceiling disagree about the same row.
    """
    assert OUTBOUND_TYPES | NOT_OUTBOUND == set(TransactionType)
    assert OUTBOUND_TYPES & NOT_OUTBOUND == set()


def test_a_withdrawal_and_a_payout_are_outbound():
    """The two members the cap exists for, named rather than counted.

    The partition above would still hold if ``OUTBOUND_TYPES`` were emptied and
    ``NOT_OUTBOUND`` were filled to match - every member classified, and no money
    out of any wallet. This is the assertion that stops that, and it is separate
    from the partition because the two say different things: one that nothing is
    unplaced, this one that the right things are placed.
    """
    assert TransactionType.WITHDRAWAL in OUTBOUND_TYPES
    assert TransactionType.PAYOUT in OUTBOUND_TYPES
