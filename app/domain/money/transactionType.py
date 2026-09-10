from enum import Enum


class TransactionType(Enum):
    """What a ledger entry actually did.

    A transaction type says what happened to the money - never *when* it
    happened. "Scheduled" is a trigger, not a type: a scheduled payout and a
    manual payout leave the same ledger entry, and the schedule that caused it
    is recorded on the plan, not on the transaction.

    Two axes are worth keeping straight:

      deposit        external -> available   (money enters the wallet)
      withdrawal     available -> external   (money leaves)
      payout         locked    -> external   (money leaves, from the reserved pool)
      lock_funds     available -> locked     (internal move, nothing leaves)
      unlock_funds   locked    -> available  (internal move, nothing leaves)
    """

    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    LOCK_FUNDS = "lock_funds"
    # Renamed from SCHEDULED_RELEASE. The operation it labels is a manual,
    # immediate locked -> available move; nothing about it is scheduled.
    UNLOCK_FUNDS = "unlock_funds"
    PAYOUT = "payout"
