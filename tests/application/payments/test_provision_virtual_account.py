from datetime import datetime
import pytest
from app.domain.payments.exception import VirtualAccountNotReadyError

from app.application.payments.provision_virtual_account import (
    ProvisionVirtualAccount,
)
from app.domain.identity.profile import Profile
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountDetails import VirtualAccountDetails
from app.domain.payments.virtualAccountProvider import VirtualAccountProvider
from app.domain.payments.virtualAccountStatus import VirtualAccountStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)


MOMENT = datetime(2026, 1, 1, 12, 0)
EMAIL = "johnny@example.com"
PHONE = "08012345678"


class RecordingVirtualAccountProvider(VirtualAccountProvider):
    """A provider double that can inspect the committed retry checkpoint."""

    def __init__(self, factory, wallet_id):
        self.factory = factory
        self.wallet_id = wallet_id
        self.customer_requests = []
        self.account_requests = []
        self.customer_code_was_persisted = False

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

        # A fresh unit sees the code only if the application committed it
        # before making this second provider call.
        uow = self.factory.start()
        try:
            stored = uow.virtual_accounts.get_by_wallet_id(
                self.wallet_id
            )
            self.customer_code_was_persisted = (
                stored is not None
                and stored.status is VirtualAccountStatus.PENDING
                and stored.provider_customer_code == "CUS_123"
            )
        finally:
            uow.rollback()

        return VirtualAccountDetails(
            account_number="1234567890",
            account_name="JOHNNY SUCCESSFUL",
            bank_name="Wema Bank",
        )


class AccountCreationFails(RecordingVirtualAccountProvider):
    def create_virtual_account(
        self,
        *,
        customer_code: str,
    ) -> VirtualAccountDetails:
        self.account_requests.append(customer_code)
        raise RuntimeError(
            "provider could not create the bank account"
        )


def test_provisioning_creates_customer_then_activates_the_account(
    tmp_path,
    build_user,
    build_wallet,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "virtual-account.db")
    )
    user = build_user(email=EMAIL, phone=PHONE)
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
        created_at=MOMENT,
        updated_at=MOMENT,
    )
    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    uow = factory.start()
    uow.users.save(user)
    uow.profiles.save(profile)
    uow.wallets.save(wallet)
    uow.virtual_accounts.save(pending)
    uow.commit()

    provider = RecordingVirtualAccountProvider(
        factory,
        wallet.wallet_id,
    )
    service = ProvisionVirtualAccount(
        factory,
        provider,
        actor=user.user_id,
    )

    result = service.execute(wallet.wallet_id)

    assert provider.customer_requests == [
        {
            "email": user.email,
            "phone": user.phone,
            "first_name": "Johnny",
            "last_name": "Successful",
        }
    ]
    assert provider.account_requests == ["CUS_123"]
    assert provider.customer_code_was_persisted is True

    assert result.status is VirtualAccountStatus.ACTIVE
    assert result.provider_customer_code == "CUS_123"
    assert result.account_number == "1234567890"
    assert result.account_name == "JOHNNY SUCCESSFUL"
    assert result.bank_name == "Wema Bank"

    stored = factory.start()
    try:
        assert (
            stored.virtual_accounts.get_by_wallet_id(wallet.wallet_id)
            == result
        )
    finally:
        stored.rollback()



def test_retry_reuses_the_saved_provider_customer(
    tmp_path,
    build_user,
    build_wallet,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "virtual-account-retry.db")
    )
    user = build_user(email=EMAIL, phone=PHONE)
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
        created_at=MOMENT,
        updated_at=MOMENT,
    )
    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    uow = factory.start()
    uow.users.save(user)
    uow.profiles.save(profile)
    uow.wallets.save(wallet)
    uow.virtual_accounts.save(pending)
    uow.commit()

    failing_provider = AccountCreationFails(
        factory,
        wallet.wallet_id,
    )
    first_attempt = ProvisionVirtualAccount(
        factory,
        failing_provider,
        actor=user.user_id,
    )

    with pytest.raises(RuntimeError):
        first_attempt.execute(wallet.wallet_id)

    checkpoint = factory.start()
    try:
        stored = checkpoint.virtual_accounts.get_by_wallet_id(
            wallet.wallet_id
        )
        assert stored is not None
        assert stored.status is VirtualAccountStatus.PENDING
        assert stored.provider_customer_code == "CUS_123"
    finally:
        checkpoint.rollback()

    retry_provider = RecordingVirtualAccountProvider(
        factory,
        wallet.wallet_id,
    )
    retry = ProvisionVirtualAccount(
        factory,
        retry_provider,
        actor=user.user_id,
    )

    active = retry.execute(wallet.wallet_id)

    assert failing_provider.customer_requests != []
    assert failing_provider.account_requests == ["CUS_123"]

    # The retry sees CUS_123 in SQLite and skips customer creation.
    assert retry_provider.customer_requests == []
    assert retry_provider.account_requests == ["CUS_123"]

    assert active.status is VirtualAccountStatus.ACTIVE
    assert active.provider_customer_code == "CUS_123"
    assert active.account_number == "1234567890"        


def test_an_active_account_is_returned_without_calling_the_provider(
    tmp_path,
    build_user,
    build_wallet,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "already-active.db")
    )
    user = build_user(email=EMAIL, phone=PHONE)
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
        created_at=MOMENT,
        updated_at=MOMENT,
    )
    active = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.ACTIVE,
        provider="paystack",
        provider_customer_code="CUS_123",
        account_number="1234567890",
        account_name="JOHNNY SUCCESSFUL",
        bank_name="Wema Bank",
    )

    uow = factory.start()
    uow.users.save(user)
    uow.profiles.save(profile)
    uow.wallets.save(wallet)
    uow.virtual_accounts.save(active)
    uow.commit()

    provider = RecordingVirtualAccountProvider(
        factory,
        wallet.wallet_id,
    )
    service = ProvisionVirtualAccount(
        factory,
        provider,
        actor=user.user_id,
    )

    result = service.execute(wallet.wallet_id)

    assert result == active
    assert provider.customer_requests == []
    assert provider.account_requests == []    


@pytest.mark.parametrize(
    "missing",
    [
        "email",
        "phone",
        "profile",
        "legal_first_name",
        "legal_last_name",
    ],
)
def test_incomplete_customer_information_makes_no_provider_call(
    tmp_path,
    build_user,
    build_wallet,
    missing,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / f"missing-{missing}.db")
    )

    user = build_user(
        email=None if missing == "email" else EMAIL,
        phone=None if missing == "phone" else PHONE,
    )
    wallet = build_wallet(
        available="0",
        user_id=user.user_id,
    )
    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    profile = Profile(
        user_id=user.user_id,
        display_name="Johnny",
        legal_first_name=(
            None if missing == "legal_first_name" else "Johnny"
        ),
        legal_last_name=(
            None if missing == "legal_last_name" else "Successful"
        ),
        date_of_birth=None,
        phone=user.phone,
        country="NG",
        address_line=None,
        created_at=MOMENT,
        updated_at=MOMENT,
    )

    uow = factory.start()
    uow.users.save(user)
    uow.wallets.save(wallet)
    uow.virtual_accounts.save(pending)

    if missing != "profile":
        uow.profiles.save(profile)

    uow.commit()

    provider = RecordingVirtualAccountProvider(
        factory,
        wallet.wallet_id,
    )
    service = ProvisionVirtualAccount(
        factory,
        provider,
        actor=user.user_id,
    )

    with pytest.raises(VirtualAccountNotReadyError):
        service.execute(wallet.wallet_id)

    assert provider.customer_requests == []
    assert provider.account_requests == []    