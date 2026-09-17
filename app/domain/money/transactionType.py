from enum import Enum


class TransactionType(Enum):
    """What a ledger entry actually did.

    A transaction type says what happened to the money - never *when* it
    happened. "Scheduled" is a trigger, not a type: a scheduled payout and a
    manual payout leave the same ledger entry, and the schedule that caused it
    is recorded on the plan, not on the transaction.

    Two axes are worth keeping straight:

      deposit        external -> available   (money enters the wallet)
      withdrawal     available -> external   (money leaves, no counterparty recorded)
      payout         available -> external   (money leaves, to a named destination)
                     locked    -> external   (money leaves, from the reserved pool)
      lock_funds     available -> locked     (internal move, nothing leaves)
      unlock_funds   locked    -> available  (internal move, nothing leaves)

    PAYOUT was once "locked -> external" only. It widened when scheduled payouts
    from the available balance were added - and widening it was safe in a way
    worth understanding, because it is the general rule for enum members used as
    a persistence contract:

    - **Renaming** a member is a data migration. Rows hold the old name, and
      ``text_to_enum`` raises a bare KeyError on the way back in.
    - **Adding** a member is free. No existing row mentions it.
    - **Widening what an existing member means** is free *when the old rows stay
      true under the new meaning* - and the check is that, not a feeling.

    Every PAYOUT row written before this change was a locked-source payout, and
    it is still a payout now. Nothing on disk became wrong. What PAYOUT means is
    "value left the wallet for a named account"; which balance funded it was
    never the type's business, and is recoverable from the plan that caused it
    and from how the wallet's balances moved.
    """

    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    LOCK_FUNDS = "lock_funds"
    # Renamed from SCHEDULED_RELEASE. The operation it labels is a manual,
    # immediate locked -> available move; nothing about it is scheduled.
    UNLOCK_FUNDS = "unlock_funds"
    PAYOUT = "payout"


#: The members whose money leaves the wallet for good.
#:
#: **This is the classification the daily outflow cap is built from**, and it is
#: a named constant rather than a set assembled at the limit check because it is
#: a fact about the *type* and not about the limits: a fifth member added above
#: has to be placed on one side or the other, and
#: ``test_every_transaction_type_is_classified`` in ``tests/domain/money`` fails
#: until somebody does. A new movement type that fell silently through to "not an
#: outflow" would be outside the cap - which is the direction a financial control
#: must fail in the loud one.
#:
#: ``DEPOSIT`` is absent because it is value *arriving*, which the balance ceiling
#: faces instead (see ``check_credit``); ``LOCK_FUNDS`` and ``UNLOCK_FUNDS`` are
#: absent because they move money between the wallet's own two balances, which
#: changes what the owner holds not at all. The axis is the table in the class
#: docstring above.
#:
#: **The two repository adapters spell these same two members inside their
#: ``outflow_total_between`` queries rather than reading this constant**, and the
#: reason is that the clause binds a fixed number of parameters -
#: ``AND type IN (?, ?)`` - so a constant that grew a member would need the SQL to
#: grow with it, and a derived placeholder count would trade a readable query for
#: a clever one. What keeps the two spellings together is
#: ``tests/infrastructure/repositories/``, which exercises every member of this
#: enum against both stores; a divergence there fails a test rather than
#: silently widening the cap.
OUTBOUND_TYPES: frozenset[TransactionType] = frozenset(
    {TransactionType.WITHDRAWAL, TransactionType.PAYOUT}
)
