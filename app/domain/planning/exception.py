"""Errors raised by the planning side of the domain.

Every one derives from ``PlanError`` so a caller can catch the whole planning
category in a single clause - and, one level up, from ``MoneyError`` so callers
that already treat "the domain said no" as one outcome (the CLI does) keep
working without learning a second root.

The rule this encodes: **a new exception belongs under the existing root, or
every catch site in the codebase has to be revisited.**
"""

from app.domain.money.exception import MoneyError


class PlanError(MoneyError):
    """Base for every rejection the planning domain makes."""


# --- Schedule ---------------------------------------------------------------


class InvalidScheduleCadenceError(PlanError):
    pass


class InvalidScheduleAnchorError(PlanError):
    pass


class InvalidOccurrenceIndexError(PlanError):
    pass


# --- Duration ---------------------------------------------------------------


class InvalidDurationAmountError(PlanError):
    pass


class InvalidDurationUnitError(PlanError):
    pass


# --- Instruction ------------------------------------------------------------


class InvalidInstructionActionError(PlanError):
    pass


class InvalidInstructionAmountError(PlanError):
    pass


class InvalidInstructionLabelError(PlanError):
    pass


class InvalidInstructionDestinationError(PlanError):
    pass


class MissingInstructionDestinationError(PlanError):
    pass


class UnexpectedInstructionDestinationError(PlanError):
    pass


# --- SavingsPlan ------------------------------------------------------------


class InvalidPlanIDError(PlanError):
    pass


class InvalidPlanNameError(PlanError):
    pass


class InvalidPlanWalletIDError(PlanError):
    pass


class InvalidPlanSourceError(PlanError):
    pass


class InvalidPlanScheduleError(PlanError):
    pass


class InvalidPlanInstructionsError(PlanError):
    pass


class EmptyPlanInstructionsError(PlanError):
    pass


class InvalidPlanStatusError(PlanError):
    pass


class InvalidCompletedRunsError(PlanError):
    pass


class InvalidPlanEndDateError(PlanError):
    pass


class MixedInstructionCurrenciesError(PlanError):
    pass


class ReleaseRequiresLockedSourceError(PlanError):
    pass


class ReleasePlanRequiresEndDateError(PlanError):
    pass


class IrreversibleReleasePlanError(PlanError):
    pass


class PlanNotActiveError(PlanError):
    pass


class PlanNotPausedError(PlanError):
    pass


class PlanAlreadyFinishedError(PlanError):
    pass


class SavingsPlanNotFoundError(PlanError):
    pass


# --- PlanRun ----------------------------------------------------------------


class InvalidPlanRunPlanIDError(PlanError):
    pass


class InvalidPlanRunDueAtError(PlanError):
    pass


class InvalidPlanRunStatusError(PlanError):
    pass


class InvalidPlanRunRecordedAtError(PlanError):
    pass


class InvalidPlanRunReasonError(PlanError):
    pass


class MissingRunBlockReasonError(PlanError):
    pass


class UnexpectedRunBlockReasonError(PlanError):
    pass


# --- PlanNotice -------------------------------------------------------------


class InvalidPlanNoticePlanIDError(PlanError):
    pass


class InvalidPlanNoticeDueAtError(PlanError):
    pass


class InvalidPlanNoticeRaisedAtError(PlanError):
    pass
