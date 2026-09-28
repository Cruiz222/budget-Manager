from datetime import datetime
from uuid import UUID, uuid4

from app.domain.identity.profile import Profile
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountDetails import (
    VirtualAccountDetails,
)
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus
from app.presentation.api.dependencies import (
    current_actor,
    virtual_account_provider,
    virtual_account_service,
)
from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE, BOB


class RecordingProvisioner:
    def __init__(self):
        self.wallet_id = None

    def execute(self, wallet_id: UUID) -> VirtualAccount:
        self.wallet_id = wallet_id
        return VirtualAccount(
            wallet_id=wallet_id,
            status=VirtualAccountStatus.ACTIVE,
            provider="paystack",
            provider_customer_code="CUS_123",
            account_number="1234567890",
            account_name="JOHNNY SUCCESSFUL",
            bank_name="Wema Bank",
        )


class RecordingVirtualAccountProvider:
    def __init__(self):
        self.customer_requests = []
        self.account_requests = []

    def create_customer(
        self,
        *,
        email: str,
        phone: str,
        first_name: str,
        last_name: str,
    ) -> str:
        self.customer_requests.append(
            {
                "email": email,
                "phone": phone,
                "first_name": first_name,
                "last_name": last_name,
            }
        )
        return "CUS_123"

    def create_virtual_account(
        self,
        *,
        customer_code: str,
    ) -> VirtualAccountDetails:
        self.account_requests.append(customer_code)
        return VirtualAccountDetails(
            account_number="1234567890",
            account_name="JOHNNY SUCCESSFUL",
            bank_name="Wema Bank",
        )



def test_the_real_service_provisions_and_persists_the_account(
    client,
    app,
    build_user,
    build_wallet,
):
    moment = datetime(2026, 1, 1, 12, 0)
    user = build_user(
        email="johnny@example.com",
        phone="08012345678",
    )
    wallet = build_wallet(
        available="0",
        user_id=user.user_id,
    )
    profile = Profile(
        user_id=user.user_id,
        display_name="Johnny",
        legal_first_name="Johnny",
        legal_last_name="Successful",
        date_of_birth=None,
        phone=user.phone,
        country="NG",
        address_line=None,
        created_at=moment,
        updated_at=moment,
    )
    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    uow = app.state.unit_of_work_factory.start()
    try:
        uow.users.save(user)
        uow.profiles.save(profile)
        uow.wallets.save(wallet)
        uow.virtual_accounts.save(pending)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise

    provider = RecordingVirtualAccountProvider()
    client.app.dependency_overrides[current_actor] = lambda: user
    client.app.dependency_overrides[
        virtual_account_provider
    ] = lambda: provider

    try:
        response = client.post(
            f"/wallets/{wallet.wallet_id}/virtual-account"
        )
        repeated = client.post(
            f"/wallets/{wallet.wallet_id}/virtual-account"
        )
    finally:
        client.app.dependency_overrides.pop(current_actor, None)
        client.app.dependency_overrides.pop(
            virtual_account_provider,
            None,
        )

    assert response.status_code == 200
    assert provider.customer_requests == [
        {
            "email": "johnny@example.com",
            "phone": "2348012345678",
            "first_name": "Johnny",
            "last_name": "Successful",
        }
    ]
    assert provider.account_requests == ["CUS_123"]
    assert response.json()["status"] == "active"
    assert response.json()["account_number"] == "1234567890"
    assert repeated.status_code == 200
    assert repeated.json() == response.json()

    stored = app.state.unit_of_work_factory.start()
    try:
        account = stored.virtual_accounts.get_by_wallet_id(
            wallet.wallet_id
        )
        assert account is not None
        assert account.status is VirtualAccountStatus.ACTIVE
        assert account.account_number == "1234567890"
    finally:
        stored.rollback()


def test_provisioning_returns_the_issued_bank_account(
    client,
    as_user,
    open_wallet,
):
    headers = as_user()
    wallet_id = open_wallet(headers)
    provisioner = RecordingProvisioner()

    client.app.dependency_overrides[
        virtual_account_service
    ] = lambda: provisioner

    try:
        response = client.post(
            f"/wallets/{wallet_id}/virtual-account",
            headers=headers,
        )
    finally:
        client.app.dependency_overrides.pop(
            virtual_account_service,
            None,
        )

    assert response.status_code == 200
    assert provisioner.wallet_id == UUID(wallet_id)
    assert response.json() == {
        "wallet_id": wallet_id,
        "status": "active",
        "provider": "paystack",
        "account_number": "1234567890",
        "account_name": "JOHNNY SUCCESSFUL",
        "bank_name": "Wema Bank",
    }



def test_an_unconfigured_install_is_a_503(
    unconfigured_client,
):
    registered = unconfigured_client.post(
        "/users",
        json={
            "email": "carol@example.com",
            "password": TEST_USER_PASSWORD,
        },
    )
    assert registered.status_code == 201, registered.text

    signed_in = unconfigured_client.post(
        "/sessions",
        json={
            "email": "carol@example.com",
            "password": TEST_USER_PASSWORD,
        },
    )
    assert signed_in.status_code == 201, signed_in.text

    headers = {
        "Authorization": f"Bearer {signed_in.json()['token']}"
    }

    wallets = unconfigured_client.get(
        "/wallets",
        headers=headers,
    )
    assert wallets.status_code == 200, wallets.text
    assert len(wallets.json()) == 1
    wallet_id = wallets.json()[0]["wallet_id"]

    response = unconfigured_client.post(
        f"/wallets/{wallet_id}/virtual-account",
        headers=headers,
    )

    assert response.status_code == 503
    assert (
        response.json()["error"]
        == "PaymentsUnconfiguredError"
    )
    assert "virtual account provider" in response.json()["detail"]


def test_provisioning_requires_authentication(client):
    response = client.post(
        f"/wallets/{uuid4()}/virtual-account"
    )

    assert response.status_code == 401


def test_a_person_cannot_provision_somebody_elses_account(
    client,
    as_user,
):
    alice_headers = as_user(ALICE)
    bob_headers = as_user(BOB)

    wallets = client.get(
        "/wallets",
        headers=alice_headers,
    )
    assert wallets.status_code == 200, wallets.text
    assert len(wallets.json()) == 1
    alice_wallet_id = wallets.json()[0]["wallet_id"]

    provider = RecordingVirtualAccountProvider()
    client.app.dependency_overrides[
        virtual_account_provider
    ] = lambda: provider

    try:
        response = client.post(
            f"/wallets/{alice_wallet_id}/virtual-account",
            headers=bob_headers,
        )
    finally:
        client.app.dependency_overrides.pop(
            virtual_account_provider,
            None,
        )

    assert response.status_code == 404
    assert provider.customer_requests == []
    assert provider.account_requests == []