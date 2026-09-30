from datetime import datetime

from app.application.payments.provision_ready_virtual_accounts import (
    ProvisionReadyVirtualAccounts,
)
from app.domain.identity.profile import Profile
from app.domain.payments.virtualAccount import VirtualAccount
from app.domain.payments.virtualAccountDetails import (
    VirtualAccountDetails,
)
from app.domain.payments.virtualAccountStatus import (
    VirtualAccountStatus,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)


NOW = datetime(2026, 1, 1, 12, 0)


class RecordingProvider:
    def __init__(self):
        self.customers = []
        self.accounts = []

    def create_customer(
        self,
        *,
        email,
        phone,
        first_name,
        last_name,
    ):
        self.customers.append(
            {
                "email": email,
                "phone": phone,
                "first_name": first_name,
                "last_name": last_name,
            }
        )
        return "CUS_123"

    def create_virtual_account(self, *, customer_code):
        self.accounts.append(customer_code)
        return VirtualAccountDetails(
            account_number="1234567890",
            account_name="JOHNNY SUCCESSFUL",
            bank_name="Wema Bank",
        )


def test_it_provisions_every_pending_wallet_for_a_ready_actor(
    tmp_path,
    build_user,
    build_wallet,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "ready-virtual-accounts.db")
    )
    user = build_user(
        email="johnny@example.com",
        phone="08012345678",
    )
    wallet = build_wallet(user_id=user.user_id)
    profile = Profile(
        user_id=user.user_id,
        display_name="Johnny",
        legal_first_name="Johnny",
        legal_last_name="Successful",
        date_of_birth=None,
        phone=None,
        country="NG",
        address_line=None,
        created_at=NOW,
        updated_at=NOW,
    )
    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    uow = factory.start()
    try:
        uow.users.save(user)
        uow.wallets.save(wallet)
        uow.profiles.save(profile)
        uow.virtual_accounts.save(pending)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise

    provider = RecordingProvider()
    service = ProvisionReadyVirtualAccounts(
        factory,
        provider,
        actor=user.user_id,
    )

    accounts = service.execute()

    assert len(accounts) == 1
    assert accounts[0].status is VirtualAccountStatus.ACTIVE
    assert provider.customers == [
        {
            "email": "johnny@example.com",
            "phone": "2348012345678",
            "first_name": "Johnny",
            "last_name": "Successful",
        }
    ]
    assert provider.accounts == ["CUS_123"]



def test_it_waits_without_calling_the_provider_when_the_actor_is_not_ready(
    tmp_path,
    build_user,
    build_wallet,
):
    factory = SqliteUnitOfWorkFactory(
        str(tmp_path / "not-ready-virtual-accounts.db")
    )
    user = build_user(
        email="johnny@example.com",
        phone=None,
    )
    wallet = build_wallet(user_id=user.user_id)
    profile = Profile(
        user_id=user.user_id,
        display_name="Johnny",
        legal_first_name="Johnny",
        legal_last_name="Successful",
        date_of_birth=None,
        phone=None,
        country="NG",
        address_line=None,
        created_at=NOW,
        updated_at=NOW,
    )
    pending = VirtualAccount(
        wallet_id=wallet.wallet_id,
        status=VirtualAccountStatus.PENDING,
        provider="paystack",
    )

    uow = factory.start()
    try:
        uow.users.save(user)
        uow.wallets.save(wallet)
        uow.profiles.save(profile)
        uow.virtual_accounts.save(pending)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise

    provider = RecordingProvider()
    service = ProvisionReadyVirtualAccounts(
        factory,
        provider,
        actor=user.user_id,
    )

    accounts = service.execute()

    assert accounts == []
    assert provider.customers == []
    assert provider.accounts == []