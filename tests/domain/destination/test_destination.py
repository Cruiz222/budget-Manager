from dataclasses import FrozenInstanceError

import pytest

from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    InvalidDestinationDetailsError,
    InvalidDestinationIdentifierError,
    InvalidDestinationKindError,
    InvalidDestinationNameError,
    MissingDestinationDetailError,
)

BANK = DestinationKind.BANK_ACCOUNT


def bank(identifier="0123456789", name="Chinedu Okafor", details=None):
    """The common case, so tests only spell out the field they are varying."""
    return Destination(
        kind=BANK,
        identifier=identifier,
        name=name,
        details={"bank_code": "058"} if details is None else details,
    )


# --- Construction ---

def test_bank_account_destination_carries_its_bank_code():
    destination = bank()

    assert destination.kind is BANK
    assert destination.identifier == "0123456789"
    assert destination.name == "Chinedu Okafor"
    assert destination.detail("bank_code") == "058"


def test_bank_account_without_a_bank_code_is_rejected():
    with pytest.raises(MissingDestinationDetailError):
        Destination(kind=BANK, identifier="0123456789", name="Chinedu Okafor")


def test_details_beyond_the_required_ones_are_allowed():
    """Required keys are a floor, not a ceiling.

    Forbidding unknown keys would make adding a detail later a breaking change
    for every stored row, so extra ones are kept as given.
    """
    destination = bank(details={"bank_code": "058", "branch": "IKJ"})

    assert destination.detail("branch") == "IKJ"


def test_absent_detail_reads_as_none():
    destination = bank()

    assert destination.detail("branch") is None


# --- Identity fields are validated ---

def test_non_kind_kind_is_rejected():
    with pytest.raises(InvalidDestinationKindError):
        Destination(kind="bank_account", identifier="0123456789", name="A")


def test_empty_identifier_is_rejected():
    with pytest.raises(InvalidDestinationIdentifierError):
        bank(identifier="")


def test_whitespace_identifier_is_rejected():
    with pytest.raises(InvalidDestinationIdentifierError):
        bank(identifier="   ")


def test_non_string_identifier_is_rejected():
    with pytest.raises(InvalidDestinationIdentifierError):
        bank(identifier=123456789)


def test_empty_name_is_rejected():
    with pytest.raises(InvalidDestinationNameError):
        bank(name="")


def test_non_string_name_is_rejected():
    with pytest.raises(InvalidDestinationNameError):
        bank(name=None)


# --- Details are validated ---

def test_non_mapping_details_are_rejected():
    with pytest.raises(InvalidDestinationDetailsError):
        bank(details="bank_code=058")


def test_non_string_detail_values_are_rejected():
    with pytest.raises(InvalidDestinationDetailsError):
        bank(details={"bank_code": 58})


# --- Immutability ---

def test_destination_fields_cannot_be_reassigned():
    destination = bank()

    with pytest.raises(FrozenInstanceError):
        destination.identifier = "9999999999"


def test_details_cannot_be_mutated_through_the_destination():
    destination = bank()

    with pytest.raises(TypeError):
        destination.details["bank_code"] = "999"


def test_mutating_the_callers_dict_afterwards_does_not_leak_in():
    """The value object copies its details, so it does not share the caller's dict."""
    supplied = {"bank_code": "058"}
    destination = bank(details=supplied)

    supplied["bank_code"] = "999"

    assert destination.detail("bank_code") == "058"


# --- Equality and hashing ---

def test_equal_value_objects_compare_equal():
    assert bank() == bank()


def test_differing_identifier_breaks_equality():
    assert bank(identifier="0123456789") != bank(identifier="9876543210")


def test_differing_name_breaks_equality():
    assert bank(name="Chinedu Okafor") != bank(name="Amaka Okafor")


def test_differing_details_break_equality():
    first = bank(details={"bank_code": "058"})
    second = bank(details={"bank_code": "011"})

    assert first != second


def test_destinations_are_hashable():
    destination = bank()

    assert {destination, destination} == {destination}


def test_hash_ignores_the_order_details_were_supplied_in():
    first = bank(details={"bank_code": "058", "branch": "IKJ"})
    second = bank(details={"branch": "IKJ", "bank_code": "058"})

    assert first == second
    assert hash(first) == hash(second)


# --- Representation ---

def test_str_names_the_destination_and_its_rail():
    assert str(bank()) == "Chinedu Okafor (bank_account:0123456789)"


def test_repr_is_unambiguous():
    assert repr(bank()) == (
        "Destination(kind=BANK_ACCOUNT, identifier='0123456789', "
        "name='Chinedu Okafor', details={'bank_code': '058'})"
    )
