"""One account cannot reach another's money, at the browser's door.

**The Phase 1a property, asserted again one floor up.** That phase spent its
effort making it impossible to act as somebody else, and the API's own tests pin
it: a wallet belonging to another account is not refused, it is *not there* - the
same 404 a wallet that never existed gets, with the same body.

A browser client is a new door onto the same rooms, and what makes it worth
re-testing rather than trusting is that the door works differently. The actor
arrives in a cookie instead of a header, the id arrives in a *path* the person can
edit and share instead of in a URL a program composed, and the response is a page
that could plausibly carry a different word for "not yours". Every test below is
the same claim as its API counterpart with one of those differences in front of
it - and the sharpest one is that the refusal must be *byte-identical* to the
refusal for an id that never existed, because the alternative is an oracle that
answers "does this wallet exist?" to anybody who asks.
"""

from uuid import uuid4

from app.presentation.web import urls
from tests.presentation.web.conftest import ALICE, BOB


class TestSomebodyElsesWallet:
    def test_it_is_not_there(self, browser, other_browser):
        wallet_id = browser.funded_wallet()

        other_browser.sign_up(BOB)

        assert other_browser.get(
            f"{urls.PREFIX}/wallets/{wallet_id}"
        ).status_code == 404

    def test_it_says_exactly_what_an_id_that_never_existed_says(
        self, browser, other_browser
    ):
        """**The assertion the whole file is for.**

        ``WalletNotFoundError`` carries one sentence for *no such wallet* and *not
        yours* on purpose - see ``errors.NOT_FOUND`` - and the risk this test
        guards is a page that made the difference visible, whether by a friendlier
        message, a different status, or an extra link in the body. Comparing whole
        bodies rather than status codes is what closes all three at once.

        Both ids are stripped before the comparison, so the claim is about
        everything *except* the address that was asked for: a page that echoed the
        path it failed on would still pass this, which is right - the id is what
        the asker already knew, and it is not the secret.
        """
        wallet_id = browser.funded_wallet()
        nobodys_id = uuid4()
        other_browser.sign_up(BOB)

        somebody_elses = other_browser.get(f"{urls.PREFIX}/wallets/{wallet_id}")
        nobodys = other_browser.get(f"{urls.PREFIX}/wallets/{nobodys_id}")

        assert somebody_elses.status_code == nobodys.status_code == 404
        assert somebody_elses.text.replace(
            wallet_id, ""
        ) == nobodys.text.replace(str(nobodys_id), "")

    def test_it_is_not_on_the_list(self, browser, other_browser):
        """A page that never names it cannot leak it, and the list is where a
        person goes looking."""
        wallet_id = browser.funded_wallet()

        other_browser.sign_up(BOB)
        page = other_browser.page(urls.LANDING_PATH)

        assert wallet_id not in page

    def test_asking_it_to_hold_money_for_them_is_refused(
        self, browser, other_browser
    ):
        """A POST is a second door to the same room, and the one that would move
        money if the scoping were only on the reads."""
        wallet_id = browser.funded_wallet()
        other_browser.sign_up(BOB)

        response = other_browser.post(
            f"{urls.PREFIX}/wallets/{wallet_id}/withdrawals",
            data={"amount": "1.00"},
        )

        assert response.status_code == 404

    def test_answering_their_request_is_refused(self, browser, other_browser):
        """**The one that matters most.** Reading somebody else's request is a
        leak; confirming it is a withdrawal from their wallet, and the difference
        is the reason this case is stated rather than left to the read below."""
        wallet_id = browser.funded_wallet()
        confirmation_id = browser.request_withdrawal(wallet_id)
        other_browser.sign_up(BOB)

        response = other_browser.post(
            f"{urls.PREFIX}/confirmations/{confirmation_id}/confirm"
        )

        assert response.status_code == 404


class TestSomebodyElsesConfirmation:
    def test_it_is_not_there(self, browser, other_browser):
        wallet_id = browser.funded_wallet()
        confirmation_id = browser.request_withdrawal(wallet_id)

        other_browser.sign_up(BOB)

        assert other_browser.get(
            f"{urls.PREFIX}/confirmations/{confirmation_id}"
        ).status_code == 404


class TestTheWalletListIsScoped:
    def test_it_shows_your_own(self, browser):
        wallet_id = browser.open_wallet()

        page = browser.page(urls.LANDING_PATH)

        assert wallet_id in page

    def test_two_accounts_do_not_share_one(self, browser, other_browser):
        """The wallets genuinely differ, so the tests above are about scoping
        rather than about a fixture that happened to make one wallet."""
        hers = browser.open_wallet()

        his = other_browser.open_wallet(email=BOB)
        page = other_browser.page(urls.LANDING_PATH)

        assert his != hers
        assert his in page
        assert hers not in page

    def test_a_funded_wallet_is_still_only_its_owners(self, browser, other_browser):
        """Money does not change who can see a wallet, which is worth stating
        because every funding fixture in this suite reaches the store directly."""
        wallet_id = browser.funded_wallet(amount="5000.00")

        other_browser.sign_up(BOB)

        assert other_browser.get(
            f"{urls.PREFIX}/wallets/{wallet_id}"
        ).status_code == 404
        assert ALICE not in other_browser.page(urls.LANDING_PATH)
