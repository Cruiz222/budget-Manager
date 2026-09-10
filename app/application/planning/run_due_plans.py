"""One tick of the scheduler: run every plan that is due.

The loop, not the work. All the interesting decisions - what a run costs,
whether it may proceed, what a refusal means - belong to ``ExecutePlanRun``.
This class exists to answer two questions the executor deliberately cannot:

  1. *Which* plans are due, without the domain having to be queryable in SQL.
  2. How much of the backlog one tick is allowed to clear.
"""

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
    """

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        execute_plan_run: ExecutePlanRun,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._execute_plan_run = execute_plan_run

    def execute(self, as_of: datetime) -> list[PlanRun]:
        """Run the next due occurrence of every plan that is owed one.

        Returns the runs that were recorded - successes and refusals - so a
        caller can report what the tick did. An empty list means everything was
        already up to date, which is the ordinary case.

        Plans that are PAUSED, COMPLETED or CANCELLED are not even considered:
        a paused plan is one a human stopped, or one that was blocked, and
        retrying it unattended would undo that decision.
        """
        results = []
        for plan_id in self._due_plan_ids(as_of):
            run = self._execute_plan_run.execute(plan_id, as_of)
            if run is not None:
                results.append(run)
        return results

    def _due_plan_ids(self, as_of: datetime) -> list:
        """The ids of ACTIVE plans that are due, in a stable order.

        A query, then a filter in Python - not a query that filters. A plan's
        next due moment is derived from its anchor and its run count, so there is
        no column to ask SQLite about. Materialising one would create a second
        source of truth for when a plan is due, free to disagree with the
        arithmetic that actually decides - so the design refuses that trade and
        pays for it here.

        The cost scales with the number of *active* plans, not with history. If
        that ever bites, the honest fix is a materialised column written by the
        same method that advances the counter, so the two cannot drift apart.
        """
        uow = self._unit_of_work_factory.start()
        try:
            candidates = uow.plans.list_by_status(PlanStatus.ACTIVE)
        finally:
            # A read-only unit. Nothing was written, so rollback is free - and
            # doing it in a finally clause means the connection is released even
            # if the filter below were to raise.
            uow.rollback()

        return [plan.plan_id for plan in candidates if plan.is_due_at(as_of)]
