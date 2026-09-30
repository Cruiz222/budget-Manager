from datetime import date, datetime

from app.presentation.web import routes, urls


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