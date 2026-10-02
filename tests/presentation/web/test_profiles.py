from datetime import date, datetime

from app.presentation.web import routes, urls
from app.domain.payments.virtualAccountDetails import (
    VirtualAccountDetails,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)


class RecordingProfileService:
    def __init__(self):
        self.saved = None

    def save(self, **fields):
        self.saved = fields


def test_a_signed_in_person_can_save_their_profile(
    browser,
    monkeypatch,
):
    browser.sign_up()
    service = RecordingProfileService()
    provisioner = RecordingReadyAccountProvisioner()

    monkeypatch.setattr(
        routes,
        "profile_service",
        lambda request, actor: service,
        raising=False,
)
    monkeypatch.setattr(
        routes,
        "provision_ready_virtual_accounts_service",
        lambda request, actor: provisioner,
        raising=False,
)

    response = browser.post(
        f"{urls.PREFIX}/profile",
        data={
            "display_name": "Johnny",
            "legal_first_name": "Johnny",
            "legal_last_name": "Successful",
            "date_of_birth": "1998-06-15",
            "phone": "",
            "country": "NG",
            "address_line": "Lagos",
        },
    )

    assert response.status_code == 303

    saved = service.saved
    assert saved is not None

    moment = saved.pop("now")
    assert isinstance(moment, datetime)
    assert provisioner.calls == 1
    assert saved == {
        "display_name": "Johnny",
        "legal_first_name": "Johnny",
        "legal_last_name": "Successful",
        "date_of_birth": date(1998, 6, 15),
        "phone": None,
        "country": "NG",
        "address_line": "Lagos",
    }


def test_a_pending_bank_account_asks_for_the_legal_profile(
    browser,
):
    browser.sign_up()
    wallet_id = browser.wallet_ids()[0]

    page = browser.wallet_page(wallet_id)

    assert "Complete your profile" in page
    assert 'action="/app/profile"' in page
    assert 'name="display_name"' in page
    assert 'name="legal_first_name"' in page
    assert 'name="legal_last_name"' in page
    assert 'name="date_of_birth"' in page
    assert 'name="country"' in page
    assert 'name="address_line"' in page



def test_the_profile_form_remembers_the_saved_values(
    browser,
):
    browser.sign_up()
    wallet_id = browser.wallet_ids()[0]

    response = browser.post(
        f"{urls.PREFIX}/profile",
        data={
            "display_name": "Johnny",
            "legal_first_name": "Johnny",
            "legal_last_name": "Successful",
            "date_of_birth": "1998-06-15",
            "phone": "",
            "country": "ng",
            "address_line": "12 Lagos Road",
        },
    )

    assert response.status_code == 303

    page = browser.wallet_page(wallet_id)

    assert 'value="Johnny"' in page
    assert 'value="Successful"' in page
    assert 'value="1998-06-15"' in page
    assert 'value="NG"' in page
    assert 'value="12 Lagos Road"' in page


class RecordingReadyAccountProvisioner:
    def __init__(self):
        self.calls = 0

    def execute(self):
        self.calls += 1
        return []


class IssuingVirtualAccountProvider:
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


def test_completing_the_last_requirement_issues_the_bank_account(
    browser,
):
    browser.sign_up()
    user_id = browser.user_id
    wallet_id = browser.wallet_ids()[0]

    factory = SqliteUnitOfWorkFactory(browser.db_path)
    uow = factory.start()
    try:
        user = uow.users.get_by_id(user_id)
        user.change_phone("08012345678")
        uow.users.save(user)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise

    provider = IssuingVirtualAccountProvider()
    browser.client.app.state.virtual_account_provider = provider

    response = browser.post(
        f"{urls.PREFIX}/profile",
        data={
            "display_name": "Johnny",
            "legal_first_name": "Johnny",
            "legal_last_name": "Successful",
            "date_of_birth": "",
            "phone": "",
            "country": "NG",
            "address_line": "",
        },
    )

    assert response.status_code == 303
    assert provider.customers == [
        {
            "email": "alice@example.com",
            "phone": "2348012345678",
            "first_name": "Johnny",
            "last_name": "Successful",
        }
    ]
    assert provider.accounts == ["CUS_123"]

    page = browser.wallet_page(wallet_id)

    assert "1234567890" in page
    assert "JOHNNY SUCCESSFUL" in page
    assert "Wema Bank" in page