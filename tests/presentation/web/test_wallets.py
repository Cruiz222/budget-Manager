"""Opening an NGN wallet in a browser, and currencies that are not on offer.

The form displays only currencies in the product's current offer that the user
does not already hold in an active wallet. For this MVP that offer is NGN only:
a new account sees NGN, an account with an active NGN wallet sees no option, and
closing that wallet makes NGN available again.

The backend refusal remains necessary for stale pages, repeated submissions and
hand-written requests, so the tests cover both the friendly dropdown and the
authoritative application rule.
"""

import re
from datetime import datetime
from uuid import UUID

from app.composition_root import build_wallet_service
from app.domain.money.confirmationKind import ConfirmationKind
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.web import urls

#: The currencies the form offers, in the order it offers them.
#:
#: Read off the page rather than compared against ``Currency``, so a route that
#: rendered a stale list would fail here even if the enum agreed with it.
OPTIONS = re.compile(r'<option value="([A-Z]{3})">')


def offered(page: str) -> list[str]:
    """The currencies the open-a-wallet form offers."""
    return OPTIONS.findall(page)


def close_through_the_service(browser, wallet_id: str) -> None:
    """Close a wallet the way the application does, because no page can.

    **A precondition builder and not a subject**, for the reason ``Browser.fund``
    gives one action up: this layer has no close form - it is not in the browser
    client's scope yet, see the plan - so a test about what closing *frees* has to
    reach the state some other way. Doing it through the use case keeps the claim
    honest: the wallet really is CLOSED in the database, which is the only way the
    currency can come back.

    The two calls are the confirmation flow every irreversible operation uses -
    record the request, then answer it - and the wallet is empty, which is the
    condition a close has to meet.
    """
    service = build_wallet_service(
        unit_of_work_factory=SqliteUnitOfWorkFactory(browser.db_path),
        actor=browser.user_id,
    )
    request = service.request_confirmation(
        UUID(wallet_id), ConfirmationKind.CLOSE, datetime.now()
    )
    service.confirm(request.confirmation.confirmation_id, datetime.now())


class TestTheCurrenciesOnOffer:
    def test_a_new_account_already_holds_the_mvp_currency(
        self,
        browser,
    ):
        """Signup creates NGN, so there is no remaining currency to offer."""
        browser.sign_up()

        assert offered(browser.page(urls.LANDING_PATH)) == []
        assert len(browser.wallet_ids()) == 1


    def test_a_currency_already_held_is_no_longer_offered(self, browser):
        """The rule as a person meets it: not a refusal, an absence. The wallet
        they hold is listed above the form, and the form does not offer to open a
        second one like it.
        """
        browser.sign_up()
        browser.open_wallet(currency="NGN")

        page = browser.page(urls.LANDING_PATH)

        assert offered(page) == []
        assert len(browser.wallet_ids()) == 1

    def test_holding_the_only_offered_currency_leaves_nothing_to_open(
        self,
        browser,
    ):
        """An active NGN wallet exhausts the MVP's wallet offer.

        The page explains that there is nothing else to open rather than
        rendering an empty select element.
        """
        browser.sign_up()
        browser.open_wallet(currency="NGN")

        page = browser.page(urls.LANDING_PATH)

        assert offered(page) == []
        assert "every currency this system serves" in page
        
        
    def test_a_closed_wallet_gives_its_currency_back(self, browser):
        """Closing the NGN wallet makes NGN available to open again."""
        browser.sign_up()
        wallet_id = browser.open_wallet(currency="NGN")

        assert offered(browser.page(urls.LANDING_PATH)) == []

        close_through_the_service(browser, wallet_id)

        page = browser.page(urls.LANDING_PATH)

        assert offered(page) == ["NGN"]
        assert browser.wallet_ids() == [wallet_id]


class TestOpeningASecondWallet:
    def test_a_known_currency_outside_the_mvp_offer_is_refused(
        self,
        browser,
    ):
        """A direct USD form submission is refused and changes nothing.

        USD is a currency the domain understands, but it is not a wallet
        currency offered by this MVP. The direct POST represents a stale or
        hand-written form submission.
        """
        browser.sign_up()
        naira = browser.open_wallet(currency="NGN")

        response = browser.post(
            f"{urls.PREFIX}/wallets",
            data={"currency": "USD"},
        )

        assert response.status_code == 400
        assert "CurrencyNotOfferedError" in response.text
        assert "NGN" in response.text
        assert "USD" in response.text
        assert browser.wallet_ids() == [naira]

    def test_a_form_that_arrives_with_a_currency_already_taken_is_refused(
        self, browser
    ):
        """**The refusal, from the one place a browser can still reach it.** The
        form is never rendered with a taken currency, so the request is made
        directly: this is exactly what a page left open in a tab sends, and what a
        double-click sends.

        The answer is the error page - the same 409 the JSON API gives, rendered
        with the same ``error`` and ``detail`` pair - and nothing was written.
        """
        browser.sign_up()
        held = browser.open_wallet(currency="NGN")

        response = browser.post(
            f"{urls.PREFIX}/wallets", data={"currency": "NGN"}
        )

        assert response.status_code == 409
        assert "DuplicateWalletCurrencyError" in response.text
        assert held in response.text
        assert browser.wallet_ids() == [held]

    def test_a_currency_the_system_no_longer_serves_is_refused_by_name(self, browser):
        """``GHS`` is not hidden from a person who types it - there is no way to
        type it into a ``select`` - but the door still refuses it, and the message
        names what this system does serve. That message is the enum's own, so it
        narrowed to two without being edited.
        """
        browser.sign_up()

        response = browser.post(
            f"{urls.PREFIX}/wallets", data={"currency": "GHS"}
        )

        assert response.status_code == 400
        assert "UnsupportedCurrencyError" in response.text
        assert "NGN, USD" in response.text
