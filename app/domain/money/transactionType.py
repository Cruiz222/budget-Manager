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
