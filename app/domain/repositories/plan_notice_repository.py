from abc import ABC, abstractmethod

from app.domain.planning.planNotice import PlanNotice


class PlanNoticeRepository(ABC):
    """Defines what a notice store must do (the domain does not care how)."""

    @abstractmethod
    def claim(self, notice: PlanNotice) -> bool:
        """Record a notice, and say **whether this caller was the one that did**.

        The return value is the entire interface, and it is what turns a save
        into a claim. ``True`` means the row was created by this call and the
        warning has therefore not been issued yet; ``False`` means an earlier
        claim already holds ``(plan_id, due_at)`` and there is nothing to report.

        Note the direction of the guarantee. This is not "check whether a notice
        exists, then write one" - that shape has a gap between the reading and
        the writing, and two ticks landing in that gap would both decide they
        were first and both warn. The decision has to be made *by the write*,
        which is why the key is a primary key and the insert is a no-op on
        conflict rather than an update.
        """
        pass

    @abstractmethod
    def list_by_plan_id(self, plan_id) -> list[PlanNotice]:
        """Return every notice raised for a plan, earliest occurrence first.

        A plan that has never been warned about returns an empty list. This is
        the audit of what the system told the user, and it is deliberately
        *not* consulted before a run: a notice is a courtesy, and a courtesy
        that can stop a payment is not a courtesy.
        """
        pass
