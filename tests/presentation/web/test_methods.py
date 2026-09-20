"""Nothing that moves money can be reached with a GET.

**This is the second of the three layers ``forms.py`` describes, and the one that
is a property of the routes rather than of a header.** ``SameSite=Lax`` is the
first: it stops a cross-site POST from carrying the cookie. The origin check is
the third: it refuses the POST that arrived anyway. Both of those are about
requests that *are* POSTs - and neither would help at all if a withdrawal could
also be spelled as a GET, because a ``Lax`` cookie **is** sent on a top-level GET
navigation. A link is a GET. So a money operation reachable by GET is a money
operation reachable by putting a link in a page, a mail, or a comment thread, and
that is the whole reason this file exists.

**Every claim below is about a method that is not allowed, not about a handler
that refuses.** The difference matters: a route registered for both methods that
answered a GET with a 403 would pass a status-code assertion written carelessly
and would still be reachable. So the tests assert the router's own answer - 405 -
and then assert the thing that would have changed has not.
"""

import re

from app.presentation.web import urls

#: The available balance, as ``wallet.html`` draws it. The same reader
#: ``test_withdrawal.py`` uses, repeated rather than imported so that each file
#: says what it is asserting about in its own words.
BALANCE = re.compile(r'class="balance-amount">([\d.]+)<')

FUNDED = "10000.00"

#: Every path in this layer that changes something and takes no id.
#:
#: **The two sign-in and sign-up forms are not in this list, and their absence
#: is the interesting part of it.** Both are reached by GET - a person has to be
#: able to *open* a form - so they are POST-only in the sense that matters (the
#: POST is the only half that mutates) while still being real pages. Their POST
#: halves are asserted below, at the addresses their forms actually post to.
#:
#: Read off ``urls`` rather than written out, so that a renamed path moves the
#: test with it instead of leaving it asserting about an address nothing serves.
MUTATING = (
    f"{urls.PREFIX}/wallets",
    f"{urls.PREFIX}/sign-in/google",
    f"{urls.PREFIX}/sign-up/google",
    f"{urls.PREFIX}/sign-out",
)


def balance_in(page: str) -> str:
    found = BALANCE.search(page)
    assert found is not None, page
    return found.group(1)


def ledger_of(page: str) -> str:
    """The activity section, so a claim about the ledger is not a claim about
    the withdrawal form's own markup."""
    return page.split("Activity")[1]


class TestThePathsWithNoIds:
    def test_one_of_them_is_refused(self, browser):
        browser.sign_up()

        response = browser.get(f"{urls.PREFIX}/wallets")

        assert response.status_code == 405

    def test_every_one_of_them_is_refused(self, browser):
        """**The whole list, so that a route added later is covered by asking
        rather than by remembering.**"""
        browser.sign_up()

        allowed = {
            path: browser.get(path).status_code for path in MUTATING
        }

        assert allowed == {path: 405 for path in MUTATING}

    def test_the_refusal_is_a_page_and_not_a_body(self, browser):
        """The router raised this one, not a route - so it is the fourth handler
        that has to catch it, and the point of the browser-aware wiring is that
        it does. Without it a mistyped or misused address under ``/app`` would
        answer JSON to a browser."""
        browser.sign_up()

        response = browser.get(f"{urls.PREFIX}/wallets")

        assert response.headers["content-type"].startswith("text/html")

    def test_the_pages_that_are_meant_to_be_fetched_still_are(self, browser):
        """**The guard on the test above.** A router that refused every GET under
        the prefix would pass all four tests above and be useless, so the two
        pages with no session requirement are fetched here as a control."""
        assert browser.get(urls.SIGN_IN_PATH).status_code == 200
        assert browser.get(urls.SIGN_UP_PATH).status_code == 200


class TestThePathsWithIds:
    def test_a_withdrawal_cannot_be_pulled_by_a_link(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)

        response = browser.get(f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals")

        assert response.status_code == 405

    def test_a_deposit_cannot_be_pulled_by_a_link(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)

        response = browser.get(f"{urls.PREFIX}/wallets/{wallet_id}/deposits")

        assert response.status_code == 405

    def test_a_confirmation_cannot_be_answered_by_a_link(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, "500.00")

        response = browser.get(
            f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm"
        )

        assert response.status_code == 405


class TestTheDatabaseIsUnchangedAfterwards:
    """**The assertion that survives a mistake in the one above.**

    A status code says what the server answered. What matters is whether anything
    moved, and the two are separable: a route that read the form off the query
    string, moved the money and *then* answered 405 would pass every test above.
    So each of these fetches the path and then reads the page back.
    """

    def test_a_withdrawal_asked_for_by_get_moves_nothing(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        before = balance_in(browser.wallet_page(wallet_id))

        browser.get(f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals")

        assert balance_in(browser.wallet_page(wallet_id)) == before

    def test_and_records_no_row(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        before = ledger_of(browser.wallet_page(wallet_id))

        browser.get(f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals")

        assert ledger_of(browser.wallet_page(wallet_id)) == before

    def test_a_confirmation_asked_for_by_get_moves_nothing(self, browser):
        wallet_id = browser.funded_wallet(FUNDED)
        confirmation_id = browser.request_withdrawal(wallet_id, "500.00")
        before = balance_in(browser.wallet_page(wallet_id))

        browser.get(f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm")

        assert balance_in(browser.wallet_page(wallet_id)) == before

    def test_a_wallet_is_not_opened_by_a_get(self, browser):
        """A wallet opening is the one mutation that leaves no ledger row and
        no change to any balance, so the only way to see it is the list."""
        browser.open_wallet()
        before = browser.wallet_ids()

        browser.get(f"{urls.PREFIX}/wallets")

        assert browser.wallet_ids() == before
