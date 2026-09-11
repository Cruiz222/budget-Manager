"""Execute one due run of one plan.

The use case that finally spends the money. Everything before this phase built
a description of what should happen; this is where it happens, atomically.
"""

import uuid
from datetime import datetime

from app.application.notifications import compose
from app.application.payout.payout_from_available import PayoutFromAvailable
from app.application.payout.payout_from_locked import PayoutFromLocked
from app.application.release.release_funds import ReleaseFunds
from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notification import Notification
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planRun import PlanRun
from app.domain.planning.planSource import PlanSource
from app.domain.planning.runBlockReason import RunBlockReason
from app.domain.planning.runStatus import RunStatus
from app.domain.planning.savingsPlan import SavingsPlan


class ExecutePlanRun:
    """Run the occurrence a plan is currently due for - or explain why it could not.

    One call does one run, and the whole run is one database transaction. Money
    moves, a PlanRun row records what happened, and the plan's counter advances
    - or none of those things are durable. There is no state in which the
    ledger says people were paid and the plan disagrees.

    **The receipt is in that transaction too.** A run also queues the message
    saying the money moved, and it does so before committing, for the same reason
    the plan's own counter is written there: the run row is the *only* record
    that money left. A payout that committed without its receipt queued would be
    money the user is never told about, and nothing would be left to notice the
    omission later - no pending row, no error, no trace. The pairing is what
    makes "the money moved" imply "the user was told", which is a stronger claim
    than "a message was composed" and the only one worth making.

    The consequence is worth stating, because it is the opposite of the usual
    instinct about side effects. If composing the message raises, the *payout*
    rolls back - a mail-formatting bug stops a payment. That is deliberate, and
    it is survivable rather than dangerous: the plan's counter has not advanced,
    so the next tick derives the same occurrence, runs it again, and pays then.
    Failing loudly costs one tick and buys *no payout without a receipt*. The
    alternative - commit the money and queue the message afterwards - trades that
    one tick for a permanent, silent hole.

    Nothing is *sent* here. Composing and queueing are writes; sending is a
    network call, and no network call belongs inside a transaction that moves
    money. ``DeliverNotifications`` does that part afterwards, and can fail as
    loudly as it likes without touching a payment.

    Why one occurrence per call, rather than looping to catch up:

    A plan checked after a long outage is due for every occurrence it missed. If
    this method looped, waking the app after a year would fire twelve payrolls
    in one second, from a balance that was never going to cover them. Running
    one occurrence per call spreads the backlog across ticks and bounds the
    damage of an outage to one run. Callers that want the backlog cleared
    faster call this repeatedly; the ordering is safe either way because each
    call advances the counter by exactly one.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        recipient: str | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # ``None`` means this installation has no notification address, which is
        # the ordinary state of a fresh install rather than an error. The run
        # happens exactly as it would otherwise and simply says nothing about it.
        # Note this is a *delivery* address, not a reason to run: it is read only
        # when composing words, long after every decision about the money.
        self._recipient = recipient

    def execute(self, plan_id: uuid.UUID, as_of: datetime) -> PlanRun | None:
        """Run the plan's next due occurrence, as of ``as_of``.

        Returns the PlanRun that was recorded - SUCCEEDED or BLOCKED. Returns
        None when the plan was not due, which is the common case and not an
        error: a plan that has already run today is simply not owed anything.

        ``as_of`` is passed in rather than read from the clock so the whole use
        case is testable without freezing time, and so one scheduler tick has
        exactly one idea of what "now" is. A tick that read the clock per plan
        could cross the moment a plan comes due - or cross a day boundary - and
        judge two plans by two different versions of now.
        """
        uow = self._unit_of_work_factory.start()
        try:
            plan = uow.plans.get_by_id(plan_id)

            # Not due: nothing to do, and nothing to record. A tick over many
            # plans will take this branch most of the time.
            if not plan.is_due_at(as_of):
                uow.rollback()
                return None

            wallet = uow.wallets.get_by_id(plan.wallet_id)
            due_at = plan.next_due_at

            reason = self._blocking_reason(plan, wallet)
            if reason is not None:
                return self._record_blocked(uow, plan, due_at, reason)

            self._move_the_money(uow, plan, wallet, due_at)
            return self._record_success(uow, plan, wallet, due_at)
        except BaseException:
            uow.rollback()
            raise

    # --- the decision -------------------------------------------------------

    def _blocking_reason(
        self, plan: SavingsPlan, wallet: Wallet
    ) -> RunBlockReason | None:
        """Whether anything stops this run, checked *before* a single instruction runs.

        This is the pre-flight, and it is not an optimisation - it is what makes
        a run atomic in a way the database transaction cannot provide on its
        own. The failure it exists to prevent is the half-paid payroll: five
        payouts, a balance that covers three, and three people paid while the
        plan's own record claims the run happened. Imagine instead that each
        instruction were attempted and the balance checked as it went: the first
        three succeed, the fourth raises InsufficientFundsError, and the
        question "was this run done?" has no answer that is true.

        So the run is judged as a whole, against a total, before anything moves.
        ``plan.total_to_move`` answers "what does this run cost" without knowing
        anything about a balance - that is the seam Phase 2 built for exactly
        this moment.

        Status is checked here too, and that is subtler than it looks. A FROZEN
        wallet permits ``release_funds`` (money does not leave, it only moves
        between balances) but refuses every payout (money leaving is the one
        thing freezing stops). A plan holding both a release and a payout would
        therefore release successfully and then fail on the payout - the exact
        half-execution this method exists to prevent, arriving through the one
        door a balance check cannot see. So a frozen wallet blocks any run that
        sends value out, and permits one that only reshuffles.
        """
        if wallet.status is WalletStatus.CLOSED:
            return RunBlockReason.WALLET_CLOSED

        if wallet.status is WalletStatus.FROZEN and self._sends_value_out(plan):
            return RunBlockReason.WALLET_FROZEN

        if self._funding_balance(plan, wallet) < plan.total_to_move:
            return RunBlockReason.INSUFFICIENT_BALANCE

        return None

    @staticmethod
    def _sends_value_out(plan: SavingsPlan) -> bool:
        """Whether any line of this run sends money outside the wallet.

        A release moves value between the wallet's own balances, so it is not
        value leaving. A payout is. That distinction is the whole reason a
        frozen wallet can still be released from.
        """
        return any(
            instruction.action is PlannedAction.PAYOUT
            for instruction in plan.instructions
        )

    @staticmethod
    def _funding_balance(plan: SavingsPlan, wallet: Wallet) -> Money:
        """Which of the wallet's two balances this plan spends.

        The mapping lives here, in the application layer, for the same reason
        the plan-currency-matches-wallet rule does: it is the only place both
        aggregates are loaded at once. Putting it on the plan would mean the
        plan reaching into a wallet; putting it on the wallet would mean the
        money domain knowing what a ``PlanSource`` is. Neither aggregate can
        hold a rule that spans both, so the use case holds it.

        Note this is deliberately the *whole* balance, not a per-instruction
        allowance. Available balance is not a fallback for a plan that spends
        locked funds - a locked-source plan is funded by locked money only, and
        vice versa.
        """
        if plan.source is PlanSource.LOCKED:
            return wallet.locked_balance
        return wallet.available_balance

    # --- the outcomes -------------------------------------------------------

    def _move_the_money(
        self,
        uow: UnitOfWork,
        plan: SavingsPlan,
        wallet: Wallet,
        due_at: datetime,
    ) -> None:
        """Apply every instruction, through the operation that owns its rules.

        Each instruction runs as a normal ``WalletOperation``, so the ledger
        rows, the FAILED-on-rejection behaviour and the idempotency check are
        all the ones a manual operation would get. Nothing here bypasses the
        wallet to edit a balance directly - if a run could do that, the plan
        would be a second, unchecked way to spend money.
        """
        for index, instruction in enumerate(plan.instructions):
            operation_cls = self._operation_for(plan.source, instruction)
            operation_cls(wallet, uow.transactions).execute(
                amount=instruction.amount,
                internal_reference=self._reference(plan.plan_id, due_at, index),
                destination=instruction.destination,
            )

    @staticmethod
    def _operation_for(source: PlanSource, instruction: Instruction):
        """Pick the operation an instruction implies. The planning-to-money seam.

        A RELEASE is a locked -> available move whatever the plan's source, and
        the aggregate has already refused to build an AVAILABLE-source plan
        containing one - so the source only has to disambiguate the payout case.
        """
        if instruction.action is PlannedAction.RELEASE:
            return ReleaseFunds
        if source is PlanSource.LOCKED:
            return PayoutFromLocked
        return PayoutFromAvailable

    @staticmethod
    def _reference(plan_id: uuid.UUID, due_at: datetime, index: int) -> str:
        """The idempotency key for one instruction of one occurrence.

        Derived, never generated - which is what makes a re-run safe. If a run
        is executed twice, the second attempt finds the ledger row the first one
        wrote and returns it instead of paying again. The occurrence *moment* is
        part of the key precisely so that the *same* plan paying next month is a
        different payment and not a duplicate.

        Note that this key is a persisted value, so widening ``due_at`` from a
        date to a datetime changed the shape of every key written from here on
        ("plan:{id}:2026-01-01:0" became "plan:{id}:2026-01-01T00:00:00:0"). That
        looks alarming and is not: a key is only written by a run that
        *succeeded*, and success advances the plan's counter in the same
        transaction. So no key can exist for an occurrence the plan is still due
        for, and the old and new shapes never meet. Contrast the ``plan_runs``
        primary key, which a *blocked* run does write - which is why the store
        needed a migration and this does not.
        """
        return f"plan:{plan_id}:{due_at.isoformat()}:{index}"

    def _record_blocked(
        self,
        uow: UnitOfWork,
        plan: SavingsPlan,
        due_at: datetime,
        reason: RunBlockReason,
    ) -> PlanRun:
        """Record the refusal, and stop the plan until a human looks at it.

        Nothing is half-applied here: no money moved, so committing this unit
        writes only the explanation. The plan is paused rather than left due so
        that a plan which cannot be paid sits visibly stalled instead of
        silently failing on every tick - and because the counter deliberately
        does not advance, resuming it picks the *same* missed occurrence back
        up. Pausing is how the run says "blocked on 1 April and still owes
        1 April", not "1 April was skipped".
        """
        run = PlanRun(
            plan_id=plan.plan_id,
            due_at=due_at,
            status=RunStatus.BLOCKED,
            reason=reason,
        )
        uow.plan_runs.save(run)
        plan.pause()
        uow.plans.save(plan)
        # The receipt for a run that did *not* happen, queued in the same unit as
        # the record that it did not. This is the one message the user most needs
        # and least expects: nothing moved, so the wallet is unchanged, there is
        # no ledger row, and without this the only trace is a ``plan_runs`` row
        # nobody reads unless the plan is already known to be stuck.
        #
        # Note it is queued on the blocked path for the same reason as on the
        # successful one - a plan that cannot be funded is not a non-event, it is
        # a payment the user is expecting that is not going to arrive.
        self._announce(uow, compose.payout_blocked(plan, run, self._recipient))
        uow.commit()
        return run

    def _record_success(
        self,
        uow: UnitOfWork,
        plan: SavingsPlan,
        wallet: Wallet,
        due_at: datetime,
    ) -> PlanRun:
        """Record the run, advance the counter, persist the wallet's new balances.

        Order matters only for readability - it is one transaction, so all three
        writes land together or none does. The three things that must agree:
        the run row says 1 April succeeded, the plan's counter says it is now
        looking at 1 May, and the wallet holds what is left.

        The fourth write - the receipt - is not one of those three agreeing
        things; it is a consequence of them. It is composed *last* so that it
        describes a run that has already been fully recorded, and committed with
        them so that a message cannot exist for a run that rolled back, or fail to
        exist for one that did not.
        """
        run = PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED)
        uow.plan_runs.save(run)
        plan.record_run()
        uow.plans.save(plan)
        uow.wallets.save(wallet)
        self._announce(uow, compose.payout_succeeded(plan, run, self._recipient))
        uow.commit()
        return run

    @staticmethod
    def _announce(uow: UnitOfWork, notification: Notification | None) -> None:
        """Queue the receipt, or nothing when there is nowhere to send it.

        A one-line method, and it exists so that the ``None`` handling is written
        once instead of at both call sites. ``compose`` returns ``None`` when no
        recipient is configured - see there - and the two outcomes it stands for
        are different in kind: no message, versus a message that will be sent.
        Neither is an error.

        ``enqueue`` rather than ``save``, and the difference is the claim. The
        insert itself decides whether this event has already been announced, so a
        receipt cannot be queued twice however many times the run is executed -
        which matters because at-least-once delivery and repeated runs both make
        a second call reachable. See ``eventKey`` for why the same event always
        derives the same key.
        """
        if notification is not None:
            uow.notifications.enqueue(notification)
