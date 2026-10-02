from datetime import datetime
from app.presentation.web import routes
from app.domain.identity.phoneVerification import PhoneVerification
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.web import urls, routes


TYPED_PHONE = "08012345678"
FOLDED_PHONE = "2348012345678"

class RecordingPhoneRequester:
    def __init__(self):
        self.requests = []

    def execute(self, phone: str, now: datetime):
        self.requests.append(
            {
                "phone": phone,
                "now": now,
            }
        )

def test_a_signed_in_person_can_request_a_phone_code(
    browser,
    monkeypatch,
):
    browser.sign_up()
    requester = RecordingPhoneRequester()

    monkeypatch.setattr(
        routes,
        "request_phone_verification_service",
        lambda request: requester,
        raising=False,
    )

    response = browser.post(
        f"{urls.PREFIX}/phone-verifications",
        data={"phone": TYPED_PHONE},
    )

    assert response.status_code == 303
    assert response.headers["location"] == urls.LANDING_PATH
    assert len(requester.requests) == 1
    assert requester.requests[0]["phone"] == TYPED_PHONE
    assert isinstance(requester.requests[0]["now"], datetime)


def test_a_signed_in_person_can_confirm_their_phone(
    browser,
    monkeypatch,
):
    browser.sign_up()
    user_id = browser.user_id

    verification, code = PhoneVerification.issue(
        phone=TYPED_PHONE,
        now=datetime.now(),
    )

    seed = SqliteUnitOfWorkFactory(browser.db_path).start()
    try:
        seed.phone_verifications.save(verification)
        seed.commit()
    finally:
        seed.rollback()

    provisioner = RecordingReadyAccountProvisioner()

    monkeypatch.setattr(
        routes,
        "provision_ready_virtual_accounts_service",
        lambda request, actor: provisioner,
        raising=False,
    )

    response = browser.post(
        f"{urls.PREFIX}/phone-verifications/confirm",
        data={"code": code},
    )

    assert response.status_code == 303
    assert response.headers["location"] == urls.LANDING_PATH

    stored = SqliteUnitOfWorkFactory(browser.db_path).start()
    try:
        user = stored.users.get_by_id(user_id)
        assert user.phone == FOLDED_PHONE
    finally:
        stored.rollback()

    assert provisioner.calls == 1


def test_a_pending_account_asks_for_phone_verification(
    browser,
):
    browser.sign_up()
    wallet_id = browser.wallet_ids()[0]

    page = browser.wallet_page(wallet_id)

    assert "Verify your phone" in page
    assert (
        f'action="{urls.PREFIX}/phone-verifications"'
        in page
    )
    assert 'name="phone"' in page
    assert (
        f'action="{urls.PREFIX}/phone-verifications/confirm"'
        in page
    )
    assert 'name="code"' in page



class RecordingReadyAccountProvisioner:
    def __init__(self):
        self.calls = 0

    def execute(self):
        self.calls += 1
        return []