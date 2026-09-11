from abc import ABC, abstractmethod

from app.domain.repositories.notification_repository import NotificationRepository
from app.domain.repositories.outbound_message_repository import (
    OutboundMessageRepository,
)
from app.domain.repositories.plan_notice_repository import PlanNoticeRepository
from app.domain.repositories.plan_run_repository import PlanRunRepository
from app.domain.repositories.savings_plan_repository import SavingsPlanRepository
from app.domain.repositories.transaction_repository import TransactionRepository
from app.domain.repositories.wallet_repository import WalletRepository


class UnitOfWork(ABC):
    """Boundary of one atomic business operation.

    Every aggregate repository is tied to a single transactional store: writes
    made through ``.wallets``, ``.transactions``, ``.plans``, ``.plan_runs``,
    ``.notices`` and ``.outbound_messages`` during the unit are not durable until
    ``commit()``. ``rollback()`` discards every write since the unit started. A
    concrete implementation opens one database transaction per unit.

    The repositories exist on one unit - rather than one unit per aggregate -
    because a plan run has to move money *and* record what it did in the same
    breath. Saving the run and debiting the wallet are one business fact; if
    they could commit separately, a crash between them would leave a ledger
    entry with no explanation, or an explanation with no ledger entry.

    ``outbound_messages`` is on the unit for that same reason, and it is no
    longer the only other repository that is. A warning becomes two rows - the
    claim in ``notices`` and the queued message here - and they must land
    together. If they could commit separately, a crash between them would leave
    the notice claimed and no message queued, and since a claimed notice is never
    raised again, that warning would be lost *permanently*. The pairing is what
    makes "we decided to warn" imply "the warning will be delivered".

    ``notifications`` is here for that same class of reason applied to a second
    pair. A run writes its ledger rows, its ``plan_runs`` row and its receipt in
    one unit, because the run row is the *only* record that money left - a payout
    that committed without its receipt queued would be money the user is never
    told about, with nothing left to notice the omission later. The pairing is
    what makes "the money moved" imply "the user was told".

    Two repositories carrying that weight is worth marking, because the pressure
    is always to treat a message as a side effect. It is not: a message that can
    be lost while the fact it describes survives is a message that will be.

    ``notices``, by contrast, sits here for convenience rather than for that
    reason, and the difference is worth noticing: on its own a warning shares no
    fact with a run, so nothing forces it into the same transaction. It is on
    the unit because a use case that needs several repositories should not have
    to open several units to get them. The same is true of every repository
    above that is not ``wallets``/``transactions``/``plan_runs`` - convenience is
    the ordinary case, and correctness is the exception worth marking.
    """

    wallets: WalletRepository
    transactions: TransactionRepository
    plans: SavingsPlanRepository
    plan_runs: PlanRunRepository
    notices: PlanNoticeRepository
    outbound_messages: OutboundMessageRepository
    notifications: NotificationRepository

    @abstractmethod
    def commit(self) -> None:
        """Make all writes of this unit durable, atomically."""
        pass

    @abstractmethod
    def rollback(self) -> None:
        """Discard every write made since the unit started."""
        pass


class UnitOfWorkFactory(ABC):
    """Creates a fresh Unit of Work on demand.

    Each business operation should run in its own unit (its own connection and
    transaction), so callers depend on a factory rather than a shared instance.
    """

    @abstractmethod
    def start(self) -> UnitOfWork:
        pass
