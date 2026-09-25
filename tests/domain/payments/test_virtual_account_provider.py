import pytest

from app.domain.payments.exception import InvalidVirtualAccountError
from app.domain.payments.virtualAccountDetails import VirtualAccountDetails
from app.domain.payments.virtualAccountProvider import VirtualAccountProvider


def test_virtual_account_details_hold_provider_issued_bank_details():
    details = VirtualAccountDetails(
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    assert details.account_number == "1234567890"
    assert details.account_name == "JOHNNY SUCCESSFUL"
    assert details.bank_name == "Wema Bank"


@pytest.mark.parametrize(
    "field_name",
    [
        "account_number",
        "account_name",
        "bank_name",
    ],
)
def test_virtual_account_details_require_every_field(field_name):
    values = {
        "account_number": "1234567890",
        "account_name": "JOHNNY SUCCESSFUL",
        "bank_name": "Wema Bank",
    }
    values[field_name] = None

    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccountDetails(**values)


@pytest.mark.parametrize("invalid_value", ["", "   ", 123])
def test_virtual_account_details_require_non_empty_strings(invalid_value):
    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccountDetails(
            account_number=invalid_value,
            account_name="JOHNNY SUCCESSFUL",
            bank_name="Wema Bank",
        )


def test_virtual_account_provider_defines_the_two_provisioning_steps():
    assert VirtualAccountProvider.__abstractmethods__ == frozenset(
        {
            "create_customer",
            "create_virtual_account",
        }
    )        