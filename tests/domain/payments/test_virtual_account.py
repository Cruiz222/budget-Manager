import uuid

import pytest
from app.domain.payments.exception import InvalidVirtualAccountError
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus


def test_pending_virtual_account_has_no_bank_details():
    account = VirtualAccount(
        wallet_id=uuid.uuid4(),
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    assert account.status is VirtualAccountStatus.PENDING
    assert account.account_number is None
    assert account.account_name is None
    assert account.bank_name is None


def test_active_virtual_account_holds_provider_issued_details():
    wallet_id = uuid.uuid4()

    account = VirtualAccount(
        wallet_id=wallet_id,
        status=VirtualAccountStatus.ACTIVE,
        provider="paystack",
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    assert account.wallet_id == wallet_id
    assert account.account_number == "1234567890"
    assert account.bank_name == "Wema Bank"
    

@pytest.mark.parametrize(
    "missing_field",
    [
        "provider_customer_code",
        "account_number",
        "account_name",
        "bank_name",
    ],
)
def test_active_virtual_account_requires_complete_bank_details(missing_field):
    values = {
        "provider_customer_code": "CUS_123",
        "account_number": "1234567890",
        "account_name": "JOHNNY SUCCESSFUL",
        "bank_name": "Wema Bank",
    }
    values[missing_field] = None

    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccount(
            wallet_id=uuid.uuid4(),
            status=VirtualAccountStatus.ACTIVE,
            provider="paystack",
            **values,
        )
    

@pytest.mark.parametrize(
    "bank_field",
    [
        "account_number",
        "account_name",
        "bank_name",
    ],
)
def test_pending_virtual_account_rejects_bank_details(bank_field):
    values = {
        "account_number": None,
        "account_name": None,
        "bank_name": None,
    }
    values[bank_field] = "not-active-yet"

    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccount(
            wallet_id=uuid.uuid4(),
            status=VirtualAccountStatus.PENDING,
            provider="paystack",
            provider_customer_code="CUS_123",
            **values,
        )
