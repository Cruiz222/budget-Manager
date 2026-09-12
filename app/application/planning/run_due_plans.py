"""One tick of the scheduler: run every plan that is due.

The loop, not the work. All the interesting decisions - what a run costs,
whether it may proceed, what a refusal means - belong to ``ExecutePlanRun``.
This class exists to answer two questions the executor deliberately cannot:

  1. *Which* plans are due, without the domain having to be queryable in SQL.
  2. How much of the backlog one tick is allowed to clear.
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from app.application.planning.execute_plan_run import ExecutePlanRun
from app.application.unit_of_work import UnitOfWorkFactory
from app.domain.planning.planRun import PlanRun
from app.domain.planning.planStatus import PlanStatus


class RunDuePlans:
    """Find the plans that are owed a run, and execute one occurrence of each.

    Each run gets its own Unit of Work - its own database transaction - and that
    is the single most important decision in this file. The tempting
    alternative is one transaction for the whole tick, which sounds tidier and
    is a trap: if the fifth plan fails, a shared transaction would roll back the
    four that already succeeded. Those four moved real money to real accounts
    and the ledger would forget it. **The transaction boundary follows the
    aggregate, not the batch.** A tick is a loop, not a unit of work.

    What one tick does *not* do is catch a plan all the way up. A plan that
    missed four months is due for four occurrences, and a tick runs the oldest
    one and stops. Clearing the rest takes more ticks. That is deliberate: it
    bounds what a single tick can pay out, so an app that was off for a year
    does not wake up and fire twelve payrolls from one balance. See
    ``ExecutePlanRun`` for the other half of that reasoning.

    **This class holds no executor, and that is the whole of its design.** It
    held one once: a single ``ExecutePlanRun`` built at the composition root and
    reused for every plan in every tick. That object had to read each plan's
    wallet, and since it belonged to nobody it could only have done so by being
    granted an authority no user has - a privileged read, which is exactly the
    bypass this phase exists to remove. So it takes a *builder* instead, and
    mints an executor per plan, owned by that plan's user. The scheduler is not
    a privileged actor now; it is a loop over single-user executions, and there
    is no seat in it from which a wallet can be read on behalf of nobody. See
    ``_due_plans`` for the read that makes the owner available, and
    ``ExecutePlanRun`` for what the executor does with it.
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        build_execute_plan_run: Callable[[uuid.UUID], ExecutePlanRun],
    ):
        self._unit_of_work_factory = unit_of_work_factory
        # A callable taking a user id and answering an executor that acts as
        # that user - not an executor, and not a ``type[ExecutePlanRun]``. A
        # class would have made this loop responsible for knowing that a
        # recipient and a shared factory go into the construction, which is
        # wiring, and wiring belongs to the composition root that already knows
        # it. So the loop names *whose* executor it wants and nothing else.
        self._build_execute_plan_run = build_execute_plan_run

    def execute(self, as_of: datetime) -> list[PlanRun]:
        """Run the next due occurrence of every plan that is owed one.

        Returns the runs that were recorded - successes and refusals - so a
        caller can report what the tick did. An empty list means everything was
        already up to date, which is the ordinary case.

        Plans that are PAUSED, COMPLETED or CANCELLED are not even considered:
        a paused plan is one a human stopped, or one that was blocked, and
        retrying it unattended would undo that decision.

        The executor is built inside the loop rather than before it, because
        ``plan.user_id`` is what it is built *from* - so the ownership of every
        wallet this tick touches is decided here, once per plan, from the plan
        itself. Nothing in this method can name a wallet it has not also named
        the owner of.
        """
        results = []
        for plan in self._due_plans(as_of):
            executor = self._build_execute_plan_run(plan.user_id)
            run = executor.execute(plan.plan_id, as_of)
            if run is not None:
                results.append(run)
        return results

    def _due_plans(self, as_of: datetime) -> list:
        """The ACTIVE plans that are due, in a stable order.

        A query, then a filter in Python - not a query that filters. A plan's
        next due moment is derived from its anchor and its run count, so there is
        no column to ask SQLite about. Materialising one would create a second
        source of truth for when a plan is due, free to disagree with the
        arithmetic that actually decides - so the design refuses that trade and
        pays for it here.

        The cost scales with the number of *active* plans, not with history. If
        that ever bites, the honest fix is a materialised column written by the
        same method that advances the counter, so the two cannot drift apart.

        **It returns whole plans rather than their ids, and that changed for a
        reason worth keeping.** Ids were enough while an executor served
        everybody; they are not enough now, because the executor for a plan has
        to be built as that plan's owner and the owner is not in the id. The
        alternative - looking each plan up again inside the loop to recover its
        ``user_id`` - would be a second read of a row already in hand, and would
        only be possible at all through an unscoped read, which is the thing
        this phase removed.

        ``list_by_status`` is the one read in the codebase that crosses owners,
        and it is *discovery*, not access: it answers "which plans are owed a
        run" across the installation, returns plans that each carry their owner,
        and every wallet read that follows is scoped to that owner. That is why
        it can be unscoped without being a bypass - and why ``PlanService``
        deliberately does not expose it to a user-facing caller.
        """
        uow = self._unit_of_work_factory.start()
        try:
            candidates = uow.plans.list_by_status(PlanStatus.ACTIVE)
        finally:
            # A read-only unit. Nothing was written, so rollback is free - and
            # doing it in a finally clause means the connection is released even
            # if the filter below were to raise. The plans stay usable: they are
            # plain objects by then, holding no connection of their own, which is
            # what lets the executor above open a *different* unit to read one.
            uow.rollback()

        return [plan for plan in candidates if plan.is_due_at(as_of)]
