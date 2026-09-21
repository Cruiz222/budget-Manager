"""Opening a wallet in a browser, and the currency that is not on offer.

Decision 267 is two rules with one shape, and this file is where they meet the
page. **A person may hold one wallet per currency**, and the form offers only the
currencies they do not already hold - which makes the *dropdown*, rather than a
refusal page, the ordinary way this rule is met. The refusal is still reachable and
still correct: a page left open in a tab, or a form posted twice, carries a currency
that is taken by the time it arrives.

**Why the form is narrowed at all, since the refusal would cover it.** The dropdown
is not decoration - the first option is what a person gets by clicking the button
without reading it - so leaving a currency on the list that cannot be had would make
the form's default choice an error page. Hiding the option is courtesy. Refusing it
is the rule, and both are asserted below.

The empty case is real rather than theoretical: with two currencies, a person who
holds both has no wallet left to open, and the page has to say so instead of
rendering a form with nothing in it.
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
    def test_a_new_account_is_offered_every_currency_the_system_serves(self, browser):
        """Two, and they come from the domain's enum rather than from a list kept
        in the template - so a currency added to ``Currency`` appears here without
        an edit, and the three that were removed are not here at all.
        """
        browser.sign_up()

        assert offered(browser.page(urls.LANDING_PATH)) == ["NGN", "USD"]

    def test_the_page_offers_the_naira_first(self, browser):
        """**The order is part of the interface**, because the first option is what
        a person gets by clicking the button without opening the dropdown. NGN is
        the currency this installation can actually collect in - the rail is
        naira-only - so it is the one that should be reached by accident.
        """
        browser.sign_up()

        assert offered(browser.page(urls.LANDING_PATH))[0] == "NGN"

    def test_a_currency_already_held_is_no_longer_offered(self, browser):
        """The rule as a person meets it: not a refusal, an absence. The wallet
        they hold is listed above the form, and the form does not offer to open a
        second one like it.
        """
        browser.sign_up()
        browser.open_wallet(currency="NGN")

        page = browser.page(urls.LANDING_PATH)

        assert offered(page) == ["USD"]
        assert len(browser.wallet_ids()) == 1

    def test_holding_both_currencies_leaves_nothing_to_open(self, browser):
        """**A real state and not an edge case**: with two currencies in the
        system, holding both is holding everything. The page says so rather than
        rendering a form with an empty dropdown - a form with nothing in it reads
        as a bug and gives a person nothing to do.
        """
        browser.sign_up()
        browser.open_wallet(currency="NGN")
        browser.open_wallet(currency="USD")

        page = browser.page(urls.LANDING_PATH)

        assert offered(page) == []
        assert "every currency this system serves" in page

    def test_a_closed_wallet_gives_its_currency_back(self, browser):
        """**The other half of the rule, from the page's side.** Closing frees the
        currency, so the option comes back - and the closed wallet is still listed,
        because closing a wallet does not erase it.
        """
        browser.sign_up()
        wallet_id = browser.open_wallet(currency="NGN")
        assert offered(browser.page(urls.LANDING_PATH)) == ["USD"]

        close_through_the_service(browser, wallet_id)

        page = browser.page(urls.LANDING_PATH)
        assert offered(page) == ["NGN", "USD"]
        assert browser.wallet_ids() == [wallet_id]


class TestOpeningASecondWallet:
    def test_a_second_currency_adds_a_wallet_and_leaves_the_first_alone(
        self, browser
    ):
        """The feature the rule is built around: two currencies, two wallets, both
        listed, each with its own balance and its own pots.
        """
        browser.sign_up()
        naira = browser.open_wallet(currency="NGN")
        dollars = browser.open_wallet(currency="USD")

        page = browser.page(urls.LANDING_PATH)

        assert browser.wallet_ids() == [naira, dollars]
        # A USD wallet is a wallet like any other at this level: it opens, it is
        # listed and it renders. What cannot be done with it is *collect* into it,
        # which is the deposit door's refusal and a different test.
        assert dollars in page

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
