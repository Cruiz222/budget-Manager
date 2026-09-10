from dataclasses import dataclass

from app.domain.money.destination import Destination
from app.domain.money.money import Money

from .exception import (
    InvalidInstructionActionError,
    InvalidInstructionAmountError,
    InvalidInstructionDestinationError,
    InvalidInstructionLabelError,
    MissingInstructionDestinationError,
    UnexpectedInstructionDestinationError,
)
from .plannedAction import PlannedAction


@dataclass(frozen=True)
class Instruction:
    """One line of a plan: how much moves, where to, and what it is for.

    The ``action``/``destination`` pairing repeats the rule ``Transaction``
    already enforces - a payout must carry a destination, and nothing else may -
    because it is the *same rule* about the same concept, one step earlier in
    time. It is written out again rather than shared through a base class:
    coupling two aggregates to deduplicate a four-line validation would trade a
    little repetition for a much harder-to-move boundary.
    """

    action: PlannedAction
    amount: Money
    label: str
    destination: Destination | None = None

    def __post_init__(self):
        if not isinstance(self.action, PlannedAction):
            raise InvalidInstructionActionError(
                f"action must be a PlannedAction, got {type(self.action).__name__}"
            )

        if not isinstance(self.amount, Money):
            raise InvalidInstructionAmountError(
                f"amount must be a Money, got {type(self.amount).__name__}"
            )

        if self.amount.amount <= 0:
            raise InvalidInstructionAmountError("amount must be greater than zero")

        if not isinstance(self.label, str):
            raise InvalidInstructionLabelError(
                f"label must be a string, got {type(self.label).__name__}"
            )

        if not self.label.strip():
            raise InvalidInstructionLabelError("label must not be blank")

        if self.destination is not None and not isinstance(
            self.destination, Destination
        ):
            raise InvalidInstructionDestinationError(
                f"destination must be a Destination or None, "
                f"got {type(self.destination).__name__}"
            )

        if self.action is PlannedAction.PAYOUT and self.destination is None:
            raise MissingInstructionDestinationError(
                "a payout instruction must name a destination"
            )

        if self.action is not PlannedAction.PAYOUT and self.destination is not None:
            raise UnexpectedInstructionDestinationError(
                f"a {self.action.value} instruction must not carry a destination"
            )

    def __str__(self) -> str:
        if self.action is PlannedAction.PAYOUT:
            return f"{self.label}: pay {self.amount} to {self.destination}"
        return f"{self.label}: release {self.amount} to available balance"
