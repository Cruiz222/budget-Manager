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


def test_pending_virtual_account_can_be_activated():
    pending = VirtualAccount(
        wallet_id=uuid.uuid4(),
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    active = pending.activate(
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    assert pending.status is VirtualAccountStatus.PENDING
    assert pending.account_number is None

    assert active.status is VirtualAccountStatus.ACTIVE
    assert active.provider_customer_code == "CUS_123"
    assert active.account_number == "1234567890"
    assert active.account_name == "JOHNNY SUCCESSFUL"
    assert active.bank_name == "Wema Bank"
    

def test_active_virtual_account_cannot_be_activated_again():
    active = VirtualAccount(
        wallet_id=uuid.uuid4(),
        status=VirtualAccountStatus.ACTIVE,
        provider="paystack",
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    with pytest.raises(InvalidVirtualAccountError):
        active.activate(
            provider_customer_code="CUS_456",
            account_number="0987654321",
            account_name="ANOTHER NAME",
            bank_name="Another Bank",
        )    


def test_virtual_account_requires_a_uuid_wallet_id():
    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccount(
            wallet_id="not-a-uuid",
            status=VirtualAccountStatus.PENDING,
            provider="paystack",
        )


def test_virtual_account_requires_a_valid_status():
    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccount(
            wallet_id=uuid.uuid4(),
            status="pending",
            provider="paystack",
        )


@pytest.mark.parametrize("provider", [None, "", "   "])
def test_virtual_account_requires_a_provider(provider):
    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccount(
            wallet_id=uuid.uuid4(),
            status=VirtualAccountStatus.PENDING,
            provider=provider,
        )        


def test_pending_virtual_account_can_record_its_provider_customer_code():
    pending = VirtualAccount(
        wallet_id=uuid.uuid4(),
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    recorded = pending.record_provider_customer_code("CUS_123")

    # VirtualAccount is frozen, so the original value object is unchanged.
    assert pending.provider_customer_code is None

    assert recorded.wallet_id == pending.wallet_id
    assert recorded.status is VirtualAccountStatus.PENDING
    assert recorded.provider == "paystack"
    assert recorded.provider_customer_code == "CUS_123"
    assert recorded.account_number is None
    assert recorded.account_name is None
    assert recorded.bank_name is None


@pytest.mark.parametrize("customer_code", ["", "   ", 123])
def test_provider_customer_code_must_be_a_non_empty_string(customer_code):
    with pytest.raises(InvalidVirtualAccountError):
        VirtualAccount(
            wallet_id=uuid.uuid4(),
            status=VirtualAccountStatus.PENDING,
            provider="paystack",
            provider_customer_code=customer_code,
        )


def test_an_active_virtual_account_cannot_record_another_customer_code():
    active = VirtualAccount(
        wallet_id=uuid.uuid4(),
        status=VirtualAccountStatus.ACTIVE,
        provider="paystack",
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    with pytest.raises(InvalidVirtualAccountError):
        active.record_provider_customer_code("CUS_456")


def test_a_recorded_customer_code_cannot_be_replaced():
    pending = VirtualAccount(
        wallet_id=uuid.uuid4(),
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
        provider_customer_code="CUS_123",
    )

    with pytest.raises(InvalidVirtualAccountError):
        pending.record_provider_customer_code("CUS_456")