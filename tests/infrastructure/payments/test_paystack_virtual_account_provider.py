import httpx
import pytest

from app.domain.payments.exception import (
    PaymentProviderUnavailableError,
)

from app.infrastructure.payments import (
    paystack_virtual_account_provider,
)
from app.infrastructure.payments.paystack_virtual_account_provider import (
    BASE_URL,
    DEFAULT_TIMEOUT,
    PaystackVirtualAccountProvider,
)
from tests.conftest import TEST_PAYSTACK_SECRET


class RecordingRequest:
    def __init__(self):
        self.calls = []
        self.raises = None
        self.response = httpx.Response(
            200,
            json={
                "status": True,
                "message": "Customer created",
                "data": {
                    "customer_code": "CUS_123",
                },
            },
        )

    def __call__(self, method, url, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                **kwargs,
            }
        )

        if self.raises is not None:
            raise self.raises

        return self.response

    @property
    def call(self):
        assert len(self.calls) == 1
        return self.calls[0]


def test_create_customer_posts_the_identity_and_returns_the_code(
    monkeypatch,
):
    requesting = RecordingRequest()
    monkeypatch.setattr(
        paystack_virtual_account_provider.httpx,
        "request",
        requesting,
    )
    provider = PaystackVirtualAccountProvider(
        secret_key=TEST_PAYSTACK_SECRET
    )

    customer_code = provider.create_customer(
        email="johnny@example.com",
        phone="2348012345678",
        first_name="Johnny",
        last_name="Successful",
    )

    assert customer_code == "CUS_123"
    assert requesting.call == {
        "method": "POST",
        "url": f"{BASE_URL}/customer",
        "json": {
            "email": "johnny@example.com",
            "phone": "2348012345678",
            "first_name": "Johnny",
            "last_name": "Successful",
        },
        "headers": {
            "Authorization": f"Bearer {TEST_PAYSTACK_SECRET}",
            "Content-Type": "application/json",
        },
        "timeout": DEFAULT_TIMEOUT,
    }


def test_create_virtual_account_returns_the_issued_bank_details(
    monkeypatch,
):
    requesting = RecordingRequest()
    requesting.response = httpx.Response(
        200,
        json={
            "status": True,
            "message": "NUBAN successfully created",
            "data": {
                "bank": {
                    "name": "Paystack-Titan",
                    "slug": "titan-paystack",
                },
                "account_name": "BUDGET / JOHNNY SUCCESSFUL",
                "account_number": "1234567890",
                "assigned": True,
                "active": True,
                "currency": "NGN",
            },
        },
    )
    monkeypatch.setattr(
        paystack_virtual_account_provider.httpx,
        "request",
        requesting,
    )
    provider = PaystackVirtualAccountProvider(
        secret_key=TEST_PAYSTACK_SECRET
    )

    details = provider.create_virtual_account(
        customer_code="CUS_123"
    )

    assert details.account_number == "1234567890"
    assert details.account_name == "BUDGET / JOHNNY SUCCESSFUL"
    assert details.bank_name == "Paystack-Titan"

    assert requesting.call == {
        "method": "POST",
        "url": f"{BASE_URL}/dedicated_account",
        "json": {
            "customer": "CUS_123",
        },
        "headers": {
            "Authorization": f"Bearer {TEST_PAYSTACK_SECRET}",
            "Content-Type": "application/json",
        },
        "timeout": DEFAULT_TIMEOUT,
    }    


def test_a_network_failure_is_reported_as_provider_unavailable(
    monkeypatch,
):
    requesting = RecordingRequest()
    requesting.raises = httpx.ReadTimeout(
        "the provider stopped answering"
    )
    monkeypatch.setattr(
        paystack_virtual_account_provider.httpx,
        "request",
        requesting,
    )
    provider = PaystackVirtualAccountProvider(
        secret_key=TEST_PAYSTACK_SECRET
    )

    with pytest.raises(PaymentProviderUnavailableError):
        provider.create_customer(
            email="johnny@example.com",
            phone="2348012345678",
            first_name="Johnny",
            last_name="Successful",
        )    