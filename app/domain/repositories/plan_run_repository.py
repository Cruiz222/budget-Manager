from abc import ABC, abstractmethod

from app.domain.planning.planRun import PlanRun


class PlanRunRepository(ABC):
    """Defines what a plan-run store must do (the domain does not care how)."""

    @abstractmethod
    def save(self, run: PlanRun) -> PlanRun:
        """Record a run, keyed by ``(plan_id, due_at)``.

        A save under a key that already exists **updates in place**, it does not
        append. That is what makes a retry coherent: a run that was BLOCKED on
        1 April and later succeeded after a top-up rewrites its own row, rather
        than leaving two rows that disagree about what happened on 1 April.

        The idempotency comes from the natural key, not from a generated id -
        which is why PlanRun has no primary key of its own.
        """
        pass

    @abstractmethod
    def list_by_plan_id(self, plan_id) -> list[PlanRun]:
        """Return every run of a plan, earliest occurrence first.

        A plan that has never run returns an empty list. This is the history the
        user-facing "why did my plan stop?" answer and the AI insight layer both
        read from.
        """
        pass
