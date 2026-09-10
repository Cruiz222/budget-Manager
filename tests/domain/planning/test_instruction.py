from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.planning.exception import (
    InvalidInstructionActionError,
    InvalidInstructionAmountError,
    InvalidInstructionDestinationError,
    InvalidInstructionLabelError,
    MissingInstructionDestinationError,
    UnexpectedInstructionDestinationError,
)
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction

NGN = Currency.NGN

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_instruction(**overrides):
    kwargs = dict(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal("2000"), NGN),
        label="salary",
        destination=DESTINATION,
    )
    kwargs.update(overrides)
    return Instruction(**kwargs)


def test_a_payout_instruction_records_its_destination():
    instruction = build_instruction()

    assert instruction.destination == DESTINATION
    assert instruction.destination.detail("bank_code") == "058"


def test_a_payout_instruction_without_a_destination_is_rejected():
    """Money with nowhere to go is not a payable instruction."""
    with pytest.raises(MissingInstructionDestinationError):
        build_instruction(destination=None)


def test_a_release_instruction_needs_no_destination():
    instruction = build_instruction(
        action=PlannedAction.RELEASE, destination=None
    )

    assert instruction.destination is None


def test_a_release_instruction_may_not_carry_a_destination():
    """The mirror rule: value released to the available balance goes nowhere."""
    with pytest.raises(UnexpectedInstructionDestinationError):
        build_instruction(action=PlannedAction.RELEASE, destination=DESTINATION)


def test_a_destination_must_be_a_destination_object():
    with pytest.raises(InvalidInstructionDestinationError):
        build_instruction(destination="0123456789")


@pytest.mark.parametrize("amount", ["0", "-1", "0.00"])
def test_a_non_positive_amount_is_rejected(amount):
    with pytest.raises(InvalidInstructionAmountError):
        build_instruction(amount=Money(Decimal(amount), NGN))


def test_an_amount_must_be_a_money():
    with pytest.raises(InvalidInstructionAmountError):
        build_instruction(amount=Decimal("2000"))


def test_a_blank_label_is_rejected():
    with pytest.raises(InvalidInstructionLabelError):
        build_instruction(label="   ")


def test_a_non_string_label_is_rejected():
    with pytest.raises(InvalidInstructionLabelError):
        build_instruction(label=42)


def test_an_unknown_action_is_rejected():
    with pytest.raises(InvalidInstructionActionError):
        build_instruction(action="payout")


def test_an_instruction_cannot_be_mutated():
    instruction = build_instruction()

    with pytest.raises(FrozenInstanceError):
        instruction.label = "rent"


def test_instructions_with_the_same_fields_are_equal():
    assert build_instruction() == build_instruction()


def test_instructions_differing_only_by_label_are_not_equal():
    assert build_instruction(label="salary") != build_instruction(label="rent")


def test_str_describes_a_payout():
    instruction = build_instruction()

    assert str(instruction) == (
        "salary: pay 2000.00 NGN to Chinedu Okafor (bank_account:0123456789)"
    )


def test_str_describes_a_release():
    instruction = build_instruction(
        action=PlannedAction.RELEASE, destination=None, label="emergency"
    )

    assert str(instruction) == "emergency: release 2000.00 NGN to available balance"
