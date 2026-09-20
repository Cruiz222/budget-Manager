"""A withdrawal, whole, as a person does it.

**This is the file the browser client was built for.** Everything else in this
layer is a rendering of something the API already did; these four requests are a
flow that had no way in before, because it needs a person to be shown a request
and to answer it - and the two halves are the whole design of money leaving this
system. The first records a question. The second is where the money moves. A
client that could only do the first would be a client that could not withdraw; one
that could do both in a single step would be a client that skipped the second
look, which ``confirmations.py`` argues is the point of the feature.

So the tests below are written as a sequence rather than as independent
assertions, and the sequence is the assertion: **the form moves nothing, the
confirmation page says so, the button moves it, and the wallet you land on shows
both the debit and the movement that has not settled.**

The two edge cases a person can reach in an ordinary life are here too - answering
the same request twice, and coming back to one after it has lapsed - and both are
about what the *page* says, since that is what this layer adds.
"""

import re

from app.presentation.web import urls
from tests.presentation.web.conftest import a_while_later, freeze_routes_at

#: The confirmation the page should be offering a button for.
AWAITING = "awaiting"
CONFIRMED = "confirmed"
EXPIRED = "expired"

#: The grade both refusals in this file get, written as the number rather than
#: imported from ``errors.CONFLICT``: that name is a *set of exception classes*,
#: and a set is not a status. The two are members of it - see its docstring for
#: why a spent or lapsed request is a conflict rather than a bad request.
CONFLICT = 409

#: The button's own words. Asserted verbatim because the claim "no button is
#: offered" has to be made against the thing itself rather than against the
#: absence of the word "confirm" - which the page says in a heading, in a link
#: and in a paragraph.
BUTTON = "Confirm and move the money"

#: The wallet's opening balance for every test here, and what is left after
#: :data:`WITHDRAWN` - written as strings because that is what the page renders
#: and what a person reads.
FUNDED = "10000.00"
WITHDRAWN = "500.00"
LEFT = "9500.00"

#: The balance as the page draws it, and *only* the balance.
#:
#: **A substring search over a whole page is a weak claim about a number**, and
#: here it is a wrong one: ``500.00`` appears in the ledger's amount cell after a
#: withdrawal and ``9500.00`` does not appear anywhere else, so the two would pass
#: and fail for the wrong reasons. Reading the element the number is drawn in is
#: the same reach a person makes with their eyes.
BALANCE = re.compile(r'class="balance-amount">([\d.]+)<')


def balance_in(page: str) -> str:
    """The available balance the page shows."""
    found = BALANCE.search(page)
    assert found is not None, page
    return found.group(1)


def wallet_path(wallet_id: str) -> str:
    return f"{urls.PREFIX}/wallets/{wallet_id}"


def confirmation_path(confirmation_id: str) -> str:
    return f"{urls.PREFIX}/confirmations/{confirmation_id}"


class TestTheWholeFlow:
    def test_the_form_records_a_request_and_moves_nothing(self, browser):
        """**The first half, and the half a person will not believe.**

        A form called "Review withdrawal" that debited the wallet would be a
        withdrawal with a receipt rather than a request for one. So the balance
        after the form is the balance before it, and that is asserted against the
        *page* rather than against the service: it is what the person is shown
        that decides whether the flow is honest.
        """
        wallet_id = browser.funded_wallet(FUNDED)

        browser.request_withdrawal(wallet_id, WITHDRAWN)

        assert balance_in(browser.wallet_page(wallet_id)) == FUNDED

    def test_it_sends_the_browser_to_the_request_it_recorded(self, browser):
        """A 303 whose ``Location`` is the page that answers it.

        Sending the person back to the wallet would be a page showing a balance
        that has not changed, which reads as a failure. The redirect is to the
        request, because answering it is the next thing to do.
        """
        wallet_id = browser.funded_wallet(FUNDED)

        response = browser.post(
            f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals",
            data={"amount": WITHDRAWN},
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith(
            f"{urls.PREFIX}/confirmations/"
        )

    def test_the_request_is_awaiting_and_carries_what_was_asked_for(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        page = browser.confirmation_page(confirmation_id)

        assert AWAITING in page
        assert WITHDRAWN in page
        assert wallet_id in page
        assert BUTTON in page

    def test_the_page_says_no_code_was_sent(self, browser):
        """**The sentence this feature cannot do without.**

        The confirmation is an accident guard and not a security control - there
        is no re-authentication, no second factor, and nothing is mailed. The page
        that asks for the withdrawal is where the person would form the opposite
        expectation, so it is where the denial belongs; leaving it out would have
        somebody watching an inbox for a code that is never coming.
        """
        wallet_id = browser.funded_wallet(FUNDED)

        browser.request_withdrawal(wallet_id, WITHDRAWN)

        assert "No code is sent to you." in browser.wallet_page(wallet_id)

    def test_the_button_moves_the_money_and_lands_on_the_wallet(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        response = browser.confirm(confirmation_id)

        assert response.headers["location"] == wallet_path(wallet_id)
        assert balance_in(browser.wallet_page(wallet_id)) == LEFT

    def test_the_wallet_it_lands_on_shows_a_pending_movement(self, browser):
        """**Debited and PENDING, which is the truthful pair.**

        The money has left the wallet and has not reached a bank - this system
        contacts no bank, so a withdrawal never becomes SUCCESSFUL here. A page
        that showed the debit and no row would look like a wallet that had lost
        money; one that showed the row as successful would be claiming a
        settlement nothing has performed.
        """
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        browser.confirm(confirmation_id)
        ledger = browser.wallet_page(wallet_id).split("Activity")[1]

        assert "withdrawal" in ledger
        assert "pending" in ledger
        assert WITHDRAWN in ledger

    def test_the_confirmation_page_says_it_has_been_answered(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        browser.confirm(confirmation_id)
        page = browser.confirmation_page(confirmation_id)

        assert CONFIRMED in page
        assert BUTTON not in page


class TestAnsweringTwice:
    def test_the_second_answer_is_refused(self, browser):
        """**The reload, the double click, and the back button.**

        Every one of those re-sends the confirm, and the second one must not be a
        second withdrawal. ``ConfirmationAlreadyUsedError`` is graded a conflict
        rather than a refusal, because the request was well-formed and the state
        is the problem - and the assertion here is about the *response*, since a
        browser is what produces the repeat.
        """
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)
        browser.confirm(confirmation_id)

        again = browser.post(f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm")

        assert again.status_code == CONFLICT

    def test_the_second_answer_leaves_the_balance_alone(self, browser):
        """A status code is a claim about the response; this is the claim about
        the money."""
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)
        browser.confirm(confirmation_id)

        browser.post(f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm")

        assert balance_in(browser.wallet_page(wallet_id)) == LEFT

    def test_it_records_one_movement_and_not_two(self, browser):
        """The ledger, which is where a second withdrawal would show up even if
        the balance somehow survived it."""
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)
        browser.confirm(confirmation_id)

        browser.post(f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm")
        ledger = browser.wallet_page(wallet_id).split("Activity")[1]

        assert ledger.count(WITHDRAWN) == 1

    def test_the_page_offers_nothing_to_click(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)
        browser.confirm(confirmation_id)

        assert BUTTON not in browser.confirmation_page(confirmation_id)


class TestARequestThatLapsed:
    """Fifteen minutes pass, and the person comes back to a page they left open.

    **The clock is moved at ``routes``, which is the boundary rather than a
    shortcut** - see ``freeze_routes_at``. The use cases are handed their moment
    by their caller, so a test that wanted this without touching a name would have
    to wait fifteen minutes.
    """

    def test_the_page_says_it_expired(self, browser, monkeypatch):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        freeze_routes_at(monkeypatch, a_while_later(minutes=16))
        page = browser.confirmation_page(confirmation_id)

        assert EXPIRED in page

    def test_the_page_offers_no_button(self, browser, monkeypatch):
        """**The one that matters, because the button would be a lie.**

        A request past its window still stores ``awaiting`` - nothing sweeps, and
        expiry is derived - so a page that read the stored status, or that
        compared ``expires_at`` against the browser's own clock, would draw a
        button that refuses. The server decides, and the server says expired.
        """
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        freeze_routes_at(monkeypatch, a_while_later(minutes=16))

        assert BUTTON not in browser.confirmation_page(confirmation_id)

    def test_answering_it_anyway_is_refused(self, browser, monkeypatch):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        freeze_routes_at(monkeypatch, a_while_later(minutes=16))
        response = browser.post(
            f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm"
        )

        assert response.status_code == CONFLICT

    def test_nothing_moved(self, browser, monkeypatch):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, WITHDRAWN)

        freeze_routes_at(monkeypatch, a_while_later(minutes=16))
        browser.post(f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm")

        assert FUNDED in browser.wallet_page(wallet_id)
