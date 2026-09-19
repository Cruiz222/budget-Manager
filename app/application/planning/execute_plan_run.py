"""Execute one due run of one plan.

The use case that finally spends the money. Everything before this phase built
a description of what should happen; this is where it happens, atomically.
"""

import uuid
from datetime import datetime

from app.application.notifications import compose
from app.application.payout.payout_from_available import PayoutFromAvailable
from app.application.payout.payout_from_locked import PayoutFromLocked
from app.application.release.release_from_locked import ReleaseFromLocked
from app.application.unit_of_work import UnitOfWork, UnitOfWorkFactory
from app.domain.identity.exception import TierLimitExceededError
from app.domain.identity.tier import check_outflow, limit_day_bounds, tier_for
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
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
        *,
        actor: uuid.UUID,
        recipient: str | None = None,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # Who this run is executed *as*.
        #
        # This is the class that makes "the scheduler is not a privileged actor"
        # true rather than merely asserted. It was a single long-lived object
        # serving every user; it now takes an owner like any other service, and
        # ``RunDuePlans`` builds one per plan with that plan's ``user_id``. So
        # the scheduler holds no authority a user does not - it holds a loop over
        # single-user executions. See ``RunDuePlans`` for the building, and the
        # wallet read in ``execute`` for why it could not be written any other
        # way.
        self._actor = actor
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
            plan = uow.plans.get_owned(plan_id, self._actor)

            # Not due: nothing to do, and nothing to record. A tick over many
            # plans will take this branch most of the time.
            if not plan.is_due_at(as_of):
                uow.rollback()
                return None

            # Read as the plan's owner, which is who this executor was built for.
            # The scheduler therefore reaches a wallet it does not own without
            # any authority of its own: the owner is named on the plan, and this
            # is an ordinary scoped read made with that name. A plan whose owner
            # is not the actor this was built for fails here rather than
            # proceeding - there is no path that reads a wallet on behalf of
            # nobody.
            wallet = uow.wallets.get_owned(plan.wallet_id, self._actor)
            due_at = plan.next_due_at

            reason = self._blocking_reason(uow, plan, wallet, as_of)
            if reason is not None:
                return self._record_blocked(uow, plan, wallet, due_at, reason)

            transactions = self._move_the_money(uow, plan, wallet, due_at, as_of)
            return self._record_success(uow, plan, wallet, due_at, transactions)
        except BaseException:
            uow.rollback()
            raise

    # --- the decision -------------------------------------------------------

    def _blocking_reason(
        self, uow: UnitOfWork, plan: SavingsPlan, wallet: Wallet, as_of: datetime
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
        wallet permits a release (money does not leave, it only moves between
        balances) but refuses every payout (money leaving is the one thing
        freezing stops). A plan holding both a release and a payout would
        therefore release successfully and then fail on the payout - the exact
        half-execution this method exists to prevent, arriving through the one
        door a balance check cannot see. So a frozen wallet blocks any run that
        sends value out, and permits one that only reshuffles.

        **The currency is checked before either of the money questions**, and
        that is a third instance of the same rule rather than a new one. A plan
        whose money is in a currency its wallet does not hold cannot be reasoned
        about at all: the two methods below would each raise trying to compare
        amounts that ``Money`` refuses to compare. So the question that *can* be
        answered is asked first, and the ones that cannot are never reached. See
        ``_currency_block``.

        **The tier ceilings are checked before the balance**, and the order is
        the advice - the argument ``_money_block`` already makes for putting
        maturity ahead of money. A run that breaches a ceiling cannot be helped
        by the size of the balance, so telling somebody to top up would send them
        to move money that changes nothing: they would fund the plan and be
        blocked again for the same reason. The ceiling is the thing they can act
        on, so it is the thing named first.
        """
        if wallet.status is WalletStatus.CLOSED:
            return RunBlockReason.WALLET_CLOSED

        if wallet.status is WalletStatus.FROZEN and self._sends_value_out(plan):
            return RunBlockReason.WALLET_FROZEN

        blocked_for_a_currency = self._currency_block(plan, wallet)
        if blocked_for_a_currency is not None:
            return blocked_for_a_currency

        blocked_for_a_limit = self._limit_block(uow, plan, wallet, as_of)
        if blocked_for_a_limit is not None:
            return blocked_for_a_limit

        return self._money_block(plan, wallet, as_of)

    @staticmethod
    def _currency_block(
        plan: SavingsPlan, wallet: Wallet
    ) -> RunBlockReason | None:
        """Whether this plan's money is in a currency its wallet cannot hold.

        **This asks the question rather than letting arithmetic raise it, and
        that is the whole change.** The mismatch was always *detected* - both
        methods below compare a plan's money against the wallet's, and ``Money``
        refuses arithmetic across two currencies - but it was detected as an
        exception, which propagated out of ``ExecutePlanRun``, out of the loop
        in ``RunDuePlans``, and killed the tick. Every plan after the bad one in
        that pass did not run, so a single bad edit was a denial of service on
        every other user's savings.

        **So it is a block, and it is checked first.** First because it is the
        cheapest question here - two attribute reads and no query - and because
        the check has to precede the two methods that would otherwise raise on
        their way to answering something else. ``_limit_block`` cannot answer
        anything about a mismatched plan: its first arithmetic is
        ``running + instruction.amount`` across two currencies, which raises.
        The same is true of ``_money_block``'s balance comparison. Neither is
        wrong; both simply cannot be the thing that decides a plan they cannot
        do arithmetic on.

        **A static method because it needs no unit of work**, and that is worth
        noticing rather than being a style choice: the rule is about two loaded
        aggregates and nothing else, which is why ``PlanService`` can hold the
        same rule one door over with no query either. The two are deliberately
        not shared code - the application service raises and this blocks, which
        is the correct difference between an edit that has not happened yet and
        a run that is being judged.
        """
        if plan.total_to_move.currency is not wallet.currency:
            return RunBlockReason.CURRENCY_MISMATCH
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

    def _limit_block(
        self, uow: UnitOfWork, plan: SavingsPlan, wallet: Wallet, as_of: datetime
    ) -> RunBlockReason | None:
        """Whether the owner's tier ceilings refuse this run, checked line by line.

        **The second door money leaves by, and the reason the first one was not
        enough.** ``WalletService._run`` enforces the ceilings for every command
        a *person* types - a withdrawal, a payout - and a plan run never goes
        through it: ``_move_the_money`` builds its operations directly, which is
        deliberate (a scheduled run has no confirmation and no person in it). So
        without this method a plan would be the one way to move money with no
        ceiling at all, and the caps would be decorative for exactly the users
        who move the most.

        **A refusal here is a *block*, not a raise**, and that is not a
        stylistic preference. Five payout instructions judged one at a time would
        raise on the fourth and take the whole scheduler tick down with it - every
        other plan that tick, including the ones with nothing wrong with them.
        Blocking is the honest state: the run did not happen, it is recorded as
        blocked for a reason, and the tick carries on. See ``RunBlockReason``.

        **The ceilings are applied per instruction and cumulatively**, which is
        what makes one call to ``check_outflow`` per payout do the work of both:
        passing the running day *forward* means the third payout of a run is
        judged against a day that already includes the first two, so a plan whose
        lines each fit but which together cross the cap is refused. The
        per-transaction ceiling comes along for free, since that is the first
        comparison the same call makes - and a plan is not a way to send one
        payout bigger than a person could send by hand.

        **Only the lines that send value out are counted**, and the check itself
        is skipped entirely for a plan that has none. A RELEASE moves value
        between the wallet's own two balances, which changes what the owner holds
        not at all - so a release-only plan is judged by nothing here, exactly as
        it faces no ceiling when a person types it.

        **A plan whose money is in another currency never reaches this method.**
        ``_currency_block`` runs before it and returns a block, so the arithmetic
        below is always between two amounts of one currency. That ordering is
        load-bearing rather than tidy: ``Money`` refuses arithmetic across two
        currencies, so before ``_currency_block`` existed, this method's first
        ``running + instruction.amount`` raised on a mismatched plan - and an
        exception here is not a block, it is the end of the whole tick. The
        ``except`` below still names one class rather than ``MoneyError``, and
        that is now doubly right: it is a net for the tier refusal, and it is
        deliberately not a net for the currency one, which is why the currency
        check was given its own answer above instead of being caught here.
        """
        if not self._sends_value_out(plan):
            return None

        tier = tier_for(uow.profiles.find_for_user(wallet.user_id))
        day_start, day_end = limit_day_bounds(as_of)
        running = uow.transactions.outflow_total_between(
            wallet.wallet_id, day_start, day_end, wallet.currency
        )

        try:
            for instruction in plan.instructions:
                if instruction.action is not PlannedAction.PAYOUT:
                    continue
                check_outflow(tier, instruction.amount, running)
                running = running + instruction.amount
        except TierLimitExceededError:
            return RunBlockReason.TIER_LIMIT_EXCEEDED

        return None

    def _money_block(
        self, plan: SavingsPlan, wallet: Wallet, as_of: datetime
    ) -> RunBlockReason | None:
        """Whether the plan's source can cover this run, and if not, why not.

        Which of the wallet's balances a plan spends is decided here, in the
        application layer, for the same reason the plan-currency-matches-wallet
        rule is: it is the only place both aggregates are loaded at once. Putting
        it on the plan would mean the plan reaching into a wallet; putting it on
        the wallet would mean the money domain knowing what a ``PlanSource`` is.
        Neither aggregate can hold a rule that spans both, so the use case holds
        it.

        Note this is deliberately the *whole* balance, not a per-instruction
        allowance. Available balance is not a fallback for a plan that spends
        locked funds - a locked-source plan is funded by locked money only, and
        vice versa.

        **A locked plan names its pot, and the pot is what is asked.** The
        previous phase asked ``matured_locked_balance`` - the sum over every
        matured pot - because a plan could not say which pot it meant, and this
        method's docstring then promised that "a dedicated reason arrives with
        the phase that lets a plan name its pot". This is that phase, and the
        promise is kept by removing the arithmetic rather than by adding a
        second check beside it: there is now one pot, so there is no sum to get
        wrong, and the answer comes from ``Fund.authorises_early_payout`` - the
        very method the payout itself calls. Pre-flight and execution ask one
        question in one place, which is the only way the pre-flight is worth
        anything.

        **The maturity test comes before the balance test**, and the order is
        the advice. A pot that may not be spent at all cannot be helped by the
        size of its balance, so "this pot is not available yet" is the more
        useful thing to say even when it is also short. Reversed, a user would
        be told to top up a pot that topping up would not free.

        **``is_irreversible`` is load-bearing in that test, not decoration.** The
        business-pot exemption is for a *scheduled payout to an external
        account*; a RELEASE moves money between the wallet's own balances and is
        never exempt, for either kind of pot (see ``Fund.release``). A plan
        holding a release is exactly ``plan.is_irreversible``, so granting the
        exemption here for such a plan would approve a run that the release then
        refuses - the half-executed run this pre-flight exists to prevent,
        reappearing inside the pre-flight.

        The last branch is the legacy draw, and it stays because a plan on disk
        may have no pot. Its reasoning is unchanged: money that cannot be spent
        yet is, from a run's point of view, money that is not there - reported as
        ``INSUFFICIENT_BALANCE`` rather than ``FUND_NOT_MATURED``, because with
        no pot named there is no pot to point at.
        """
        if plan.source is not PlanSource.LOCKED:
            if wallet.available_balance < plan.total_to_move:
                return RunBlockReason.INSUFFICIENT_BALANCE
            return None

        if plan.fund_id is None:
            if wallet.matured_locked_balance(as_of) < plan.total_to_move:
                return RunBlockReason.INSUFFICIENT_BALANCE
            return None

        fund = wallet.fund_by_id(plan.fund_id)
        if not fund.is_matured(as_of) and not (
            not plan.is_irreversible
            and fund.authorises_early_payout(plan.created_at)
        ):
            return RunBlockReason.FUND_NOT_MATURED
        if fund.balance < plan.total_to_move:
            return RunBlockReason.INSUFFICIENT_BALANCE
        return None

    # --- the outcomes -------------------------------------------------------

    def _move_the_money(
        self,
        uow: UnitOfWork,
        plan: SavingsPlan,
        wallet: Wallet,
        due_at: datetime,
        as_of: datetime,
    ) -> list[Transaction]:
        """Apply every instruction, through the operation that owns its rules.

        Each instruction runs as a normal ``WalletOperation``, so the ledger
        rows, the FAILED-on-rejection behaviour and the idempotency check are
        all the ones a manual operation would get. Nothing here bypasses the
        wallet to edit a balance directly - if a run could do that, the plan
        would be a second, unchecked way to spend money.

        **It returns the rows it produced**, which is new. They used to be
        discarded because nothing downstream needed them: the run's own outcome
        was the only answer anybody wanted. That stopped being true when a
        movement that crosses the system's edge began settling as PENDING rather
        than SUCCESSFUL - a run can now be *recorded* as succeeded while its
        money is still only held, and the caller has to be able to tell. Reading
        it off the rows rather than off the plan is deliberate: the row is what
        happened, and a second derivation from the instructions could disagree
        with it. ``compose.wallet_movement`` takes the same position one layer
        down.

        Note which moment the operations are handed, because the two moments in
        this method are easy to confuse. The *reference* is keyed on ``due_at`` -
        the occurrence being run - because that is what identifies the payment.
        The operations are given ``as_of`` - when the run is actually happening -
        because that is what decides whether a pot has matured. Using ``due_at``
        for maturity would judge a pot against the date the payment was *meant*
        to happen, so a run executed late would be refused for a pot that has
        since opened, and - worse - the pre-flight, which can only know
        ``as_of``, would have approved it. Pre-flight and execution must be
        answering the same question or the pre-flight is worth nothing.

        ``as_of`` is also *stamped* onto the rows, and there is a third reason
        for that beyond maturity. ``_limit_block`` above sums this wallet's day
        from the ledger, so a payout recorded under the wall clock while it was
        judged under ``as_of`` would sit in a day the next plan of the same tick
        never asks about - and the failure would be silent, because a day total
        that misses a row reads as a smaller total rather than as an error. One
        value, one day: the same reason ``WalletService._run`` assigns
        ``operation.now`` beside ``operation.guard``. Note it has to be assigned
        *here* rather than inherited from a service, because this method builds
        its operations itself - that is what makes it the second door, and what
        makes every rule a movement faces something this class must remember to
        apply. See ``WalletOperation.now``.
        """
        transactions = []
        for index, instruction in enumerate(plan.instructions):
            operation_cls, extra = self._operation_for(plan, instruction, as_of)
            operation = operation_cls(wallet, uow.transactions, **extra)
            operation.now = as_of
            transactions.append(
                operation.execute(
                    amount=instruction.amount,
                    internal_reference=self._reference(plan.plan_id, due_at, index),
                    destination=instruction.destination,
                )
            )
        return transactions

    @staticmethod
    def _operation_for(plan: SavingsPlan, instruction: Instruction, as_of: datetime):
        """Pick the operation an instruction implies, and how to build it.

        The planning-to-money seam, and it returns **a class and its keyword
        arguments** rather than a bare class, because the operations no longer
        share one constructor shape. A RELEASE and a locked-source payout both
        need the moment they run at - a pot pays out and releases only once it
        has come due - while an available-source payout has no pots in it at all
        and needs nothing. Pairing class with arguments here keeps that decision
        in the seam that exists to hold it, instead of a conditional scattered
        where the operation happens to be built.

        It takes the whole ``plan`` rather than only its ``source``, and the
        reason is the new middle argument: a locked payout needs to know *which*
        pot it draws on and *when the commitment to pay was made*, and both of
        those are facts about the plan. Passing them as loose parameters would
        have made the signature a list of things the plan owns.

        ``committed_at`` is ``plan.created_at``, and that is the whole
        anti-temptation rule in one expression. The pot decides whether a
        commitment old enough to authorise an early payment exists, and the only
        commitment a plan has is the moment it was created. Passing ``None``
        here - as an ad-hoc payout does - would mean "no commitment", not "any
        commitment", which is why this cannot be defaulted.

        A RELEASE is a locked -> available move whatever the plan's source, and
        the aggregate has already refused to build an AVAILABLE-source plan
        containing one - so the source only has to disambiguate the payout case.
        It is *not* given ``plan.created_at`` even when the plan names a pot: the
        exemption is for scheduled payments to external accounts, and a release
        is not one. Passing it would have no effect today, since ``Fund.release``
        takes no such argument - which is exactly why it would be a trap.
        """
        if instruction.action is PlannedAction.RELEASE:
            return ReleaseFromLocked, {"as_of": as_of, "fund_id": plan.fund_id}
        if plan.source is PlanSource.LOCKED:
            return PayoutFromLocked, {
                "as_of": as_of,
                "fund_id": plan.fund_id,
                "committed_at": plan.created_at,
            }
        return PayoutFromAvailable, {}

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
        wallet: Wallet,
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
        self._announce(
            uow,
            compose.payout_blocked(
                plan, run, self._recipient, self._pot_name(plan, wallet)
            ),
        )
        uow.commit()
        return run

    def _record_success(
        self,
        uow: UnitOfWork,
        plan: SavingsPlan,
        wallet: Wallet,
        due_at: datetime,
        transactions: list[Transaction],
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

        **The receipt is skipped when a row it describes is still only held.**
        "Succeeded" here means the run did what a run does - applied its
        instructions, advanced its counter. It does not mean the money arrived
        anywhere, and a payout's money cannot have arrived, because the far end
        is a bank account this code has never contacted. The transaction is left
        PENDING for exactly that reason, and ``payout_succeeded``'s words are
        past tense and specific: *"Plan X: 5000.00 NGN moved"*, *"the plan ran"*.

        So a run whose payout is pending records its success and sends nothing,
        which is the same silence ``WalletService._announce`` keeps one layer
        down and for the same reason: a receipt about money leaving is written
        when the money leaves. It will be, in the phase that settles these - and
        that phase fires the message, because that is the moment there is
        something true to say.

        A run of pure releases still announces, because a release genuinely
        completes here: both of its balances are inside this system. That is what
        ``all`` is doing rather than a blanket skip on the plan's source.

        Note the vacuous-truth hazard this would have if an empty plan were
        possible - ``all`` of nothing is True, so the receipt would fire for a
        run that moved nothing. ``SavingsPlan`` refuses to exist with no
        instructions, which is what makes the test safe to write this way.
        """
        run = PlanRun(plan_id=plan.plan_id, due_at=due_at, status=RunStatus.SUCCEEDED)
        uow.plan_runs.save(run)
        plan.record_run()
        uow.plans.save(plan)
        uow.wallets.save(wallet)
        if all(
            transaction.status is TransactionStatus.SUCCESSFUL
            for transaction in transactions
        ):
            self._announce(
                uow,
                compose.payout_succeeded(
                    plan, run, self._recipient, self._pot_name(plan, wallet)
                ),
            )
        uow.commit()
        return run

    @staticmethod
    def _pot_name(plan: SavingsPlan, wallet: Wallet) -> str | None:
        """The name of the pot a run drew on, or ``None`` if it named none.

        The translation from what the plan holds to what a human reads, and it
        lives here rather than on the aggregate deliberately: a plan holds a
        ``fund_id`` and nothing more, because an aggregate references another by
        identity and not by a copy of its name - a name copied onto a plan would
        be a second record of a fact the pot already owns, and the two would
        drift the first time a pot was renamed.

        Only a receipt needs the name, and a receipt is written by the use case
        that has both aggregates in hand, so this is exactly where the
        translation belongs. See ``SavingsPlan.fund_id``.
        """
        if plan.fund_id is None:
            return None
        return wallet.fund_by_id(plan.fund_id).name

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
