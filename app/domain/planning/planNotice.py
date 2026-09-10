import uuid
from dataclasses import dataclass
from datetime import datetime

from .exception import (
    InvalidPlanNoticeDueAtError,
    InvalidPlanNoticePlanIDError,
    InvalidPlanNoticeRaisedAtError,
)


@dataclass(frozen=True)
class PlanNotice:
    """The record that a warning about an upcoming run was issued.

    A plan about to fire is a *courtesy* to report, and the only thing that makes
    the report sane is that it happens once. A tick may run every minute from
    11:30 to 12:00, so without a record the user would be told thirty times that
    the same payout is thirty minutes away. This row is what stops that.

    What this row is **not** is a gate. It is never read to decide whether a run
    may proceed - that question is answered by the wallet and by the run use
    case, which do not know this table exists. Conferring a courtesy record with
    the power to hold up a payment would undo the whole point of "automated".
    The distinction is easy to lose and worth holding onto, because the two both
    look like "did we already do the thing for this occurrence?".

    Its *natural key* is ``(plan_id, due_at)`` - the same key ``PlanRun`` uses,
    and for the same reason: a notice is nothing but (this plan, this
    occurrence), so it has no identity of its own to generate. One plan warns at
    most once per occurrence, and the store enforces it with a primary key rather
    than with a check, so two overlapping ticks cannot both win.

    The store's write is a *claim* rather than a save, which is the one place
    this differs from ``PlanRun``. A run gets a second, better version when a
    blocked occurrence is later paid; a notice has no better version. The first
    one stands, and a later attempt is simply not news.
    """

    plan_id: uuid.UUID
    due_at: datetime
    raised_at: datetime

    @property
    def key(self) -> tuple[uuid.UUID, datetime]:
        """The natural key: one notice per plan per occurrence."""
        return (self.plan_id, self.due_at)

    def __post_init__(self):
        if not isinstance(self.plan_id, uuid.UUID):
            raise InvalidPlanNoticePlanIDError("invalid plan id")

        # The moment of the occurrence being announced, so it matches what the
        # schedule derives and what a PlanRun of the same occurrence records.
        # The subclass trap applies here exactly as it does in PlanRun: a
        # ``datetime`` passes ``isinstance(x, date)``, so the check has to be
        # for the narrower type rather than against the wider one.
        if not isinstance(self.due_at, datetime):
            raise InvalidPlanNoticeDueAtError(
                f"due_at must be a datetime, got {type(self.due_at).__name__}"
            )

        if not isinstance(self.raised_at, datetime):
            raise InvalidPlanNoticeRaisedAtError(
                f"raised_at must be a datetime, got {type(self.raised_at).__name__}"
            )

    def __str__(self) -> str:
        # To the minute, matching Schedule, PlanRun and the CLI's moment
        # display. Bare interpolation would give "2026-04-01 00:00:00" - a space
        # instead of a T, and seconds nobody set - which would be a fourth
        # format for the same idea.
        moment = self.due_at.isoformat(timespec="minutes")
        return f"notice of {self.plan_id} for {moment}"
