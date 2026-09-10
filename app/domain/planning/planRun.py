import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .exception import (
    InvalidPlanRunDueAtError,
    InvalidPlanRunPlanIDError,
    InvalidPlanRunReasonError,
    InvalidPlanRunRecordedAtError,
    InvalidPlanRunStatusError,
    MissingRunBlockReasonError,
    UnexpectedRunBlockReasonError,
)
from .runBlockReason import RunBlockReason
from .runStatus import RunStatus


@dataclass(frozen=True)
class PlanRun:
    """The record of one occurrence of a plan having been attempted.

    This is what makes "why did my plan stop?" answerable. A blocked run writes
    no transactions - that is the entire point of checking the balance before
    starting - so without a row here it would leave no trace anywhere in the
    system, and a paused plan would be a mystery forever after.

    Immutable once written. It is a historical record, and history does not get
    edited.

    Its *natural key* is ``(plan_id, due_at)``: a plan has at most one run per
    occurrence, ever. That key is what makes the store idempotent - a run that
    was BLOCKED and later retried successfully updates the same row rather than
    appending a second, contradictory one. The key lives in the database as the
    primary key; Python equality stays whole-object, because two runs that share
    a key and disagree about the outcome are not "the same run" - they are a bug.

    Because ``due_at`` is a moment, the *text* the key is stored as changed from
    "2026-01-01" to "2026-01-01T00:00:00" when this became a datetime - and two
    different strings do not collide, so a retried run would have appended the
    second row this docstring promises cannot exist. That is why the store
    carries a migration. A primary key is a persisted contract, and changing the
    representation of a keyed column is a data change, not a refactor.
    """

    plan_id: uuid.UUID
    due_at: datetime
    status: RunStatus
    recorded_at: datetime = field(default_factory=datetime.now)
    reason: RunBlockReason | None = None

    @property
    def key(self) -> tuple[uuid.UUID, datetime]:
        """The natural key: one run per plan per occurrence."""
        return (self.plan_id, self.due_at)

    def __post_init__(self):
        if not isinstance(self.plan_id, uuid.UUID):
            raise InvalidPlanRunPlanIDError("invalid plan id")

        # The moment of the occurrence, so it matches what the schedule derives.
        # The check here used to have to exclude datetimes - they pass
        # isinstance(x, date) - and now it requires one; a plain date is simply
        # not a datetime, so no second clause is needed to say so.
        if not isinstance(self.due_at, datetime):
            raise InvalidPlanRunDueAtError(
                f"due_at must be a datetime, got {type(self.due_at).__name__}"
            )

        if not isinstance(self.status, RunStatus):
            raise InvalidPlanRunStatusError(
                f"status must be a RunStatus, got {type(self.status).__name__}"
            )

        if not isinstance(self.recorded_at, datetime):
            raise InvalidPlanRunRecordedAtError(
                f"recorded_at must be a datetime, got {type(self.recorded_at).__name__}"
            )

        if self.reason is not None and not isinstance(self.reason, RunBlockReason):
            raise InvalidPlanRunReasonError(
                f"reason must be a RunBlockReason or None, "
                f"got {type(self.reason).__name__}"
            )

        # The same shape of rule as "a payout must name a destination" and "a
        # RELEASE may not": the explanation and the outcome must agree. A blocked
        # run with no reason cannot answer the question it exists to answer; a
        # successful run with a reason is describing a problem that did not happen.
        if self.status is RunStatus.BLOCKED and self.reason is None:
            raise MissingRunBlockReasonError("a blocked run must record why it was blocked")

        if self.status is not RunStatus.BLOCKED and self.reason is not None:
            raise UnexpectedRunBlockReasonError(
                f"a {self.status.value} run must not carry a block reason"
            )

    def __str__(self) -> str:
        # To the minute, so this reads like the schedule line and the CLI's
        # moment display. Bare interpolation would give "2026-04-01 00:00:00" -
        # a space instead of a T, and seconds nobody set - which is a third
        # format for the same idea.
        moment = self.due_at.isoformat(timespec="minutes")
        if self.reason is not None:
            return f"run of {self.plan_id} due {moment}: {self.status.value} ({self.reason.value})"
        return f"run of {self.plan_id} due {moment}: {self.status.value}"
