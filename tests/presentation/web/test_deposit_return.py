"""Where a payer is sent back to, and what happens when nowhere is configured.

**This is the smallest piece of the web layer and the only one that leaves the
building.** Everything else a page does is a rendering of something the API
already answered; ``PUBLIC_BASE_URL`` is a fact about the *installation* that is
handed to a third party, and the third party is a payment provider that will put
a payer's browser on it. Three frames have to agree about it -

  - ``settings.public_base_url_from_environment`` reads an origin and strips a
    trailing slash,
  - ``web.urls.callback_url`` joins that origin to this layer's landing path,
  - ``create_app`` passes the result to the provider, which puts it in the
    payload only when it is not ``None``.

- and the failure if one of them drops it is silent in a particular way: the
deposit works perfectly, the payer is charged, the webhook credits the wallet,
and the person is left looking at Paystack's own page wondering whether anything
happened. Nothing refuses and nothing logs.

**The absent case is tested as carefully as the present one**, because it is the
state a fresh clone is in and because the adapter's own docstring argues that a
``"callback_url": null`` field is a *third* thing - not "nowhere in particular"
but "here is a field whose value is nothing" - which a provider is free to refuse
for reasons it will not explain.

**The last class is the whole chain at once**, and it is here rather than in the
adapter's own test file because that is the only place the chain exists: the
adapter takes an argument, and this suite is what proves something upstream
produces it. It builds its own application instead of using the suite's ``app``
fixture, and the reason is the one thing this file cannot work around - that
fixture *injects* a provider, and ``create_app`` only composes a ``callback_url``
when it has to build one.
"""

import re
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.payments import paystack_payment_provider
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.settings import public_base_url_from_environment
from app.presentation.api.app import create_app
from app.presentation.web import urls
from tests.conftest import TEST_PAYSTACK_SECRET
from tests.infrastructure.payments.test_paystack_payment_provider import (
    A_TRANSACTION,
    RecordingRequest,
)
from tests.presentation.web.conftest import BASE_URL, Browser

#: An origin written the way a deployment would write one, and the way it would
#: be written *by mistake* - ``settings`` strips the second, and the pair is
#: tested below so that neither half can be dropped without a failure.
ORIGIN = "https://budget.example"
ORIGIN_WITH_A_TRAILING_SLASH = f"{ORIGIN}/"

#: The available balance, as ``wallet.html`` draws it. The same reader
#: ``test_withdrawal.py`` and ``test_methods.py`` use, repeated rather than
#: imported so that each file says what it is asserting about in its own words.
BALANCE = re.compile(r'class="balance-amount">([\d.]+)<')


def balance_in(page: str) -> str:
    """The available balance the page shows."""
    found = BALANCE.search(page)
    assert found is not None, page
    return found.group(1)


@pytest.fixture
def calling_out(monkeypatch):
    """The provider's one seam, replaced - the same double the adapter's own
    tests use, imported rather than rewritten.

    **Imported deliberately**, and the alternative is worth naming: a second
    recorder here would be a second copy of Paystack's success payload, and the
    day the real one changes the copy would be the one that kept passing. There
    is exactly one recorded ``/transaction/initialize`` answer in this suite, and
    both files read it.
    """
    recorder = RecordingRequest()
    monkeypatch.setattr(paystack_payment_provider.httpx, "request", recorder)
    return recorder


@contextmanager
def an_installation(monkeypatch, db_path, password_hasher, **environment):
    """A real application, configured by the environment it is built in.

    The environment matters here in a way it does not in the rest of this suite:
    ``create_app`` resolves ``PUBLIC_BASE_URL`` by reading ``os.environ`` at
    construction time, so the setting cannot be injected as a parameter and an
    application that is going to be *about* the setting has to be built inside
    the test that sets it.
    """
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    app = create_app(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=password_hasher,
    )
    with TestClient(app, base_url=BASE_URL, follow_redirects=False) as client:
        yield app, Browser(client, db_path)


def an_installation_that_can_take_money(
    monkeypatch, db_path, password_hasher, public_base_url=None
):
    """The above, with a payment key - and optionally an address.

    ``public_base_url`` is passed rather than read from a module constant so
    that the *unset* case is spelled ``None`` at the call site, which is the case
    half of this file is about.
    """
    environment = {"PAYSTACK_SECRET_KEY": TEST_PAYSTACK_SECRET}
    if public_base_url is not None:
        environment["PUBLIC_BASE_URL"] = public_base_url
    return an_installation(monkeypatch, db_path, password_hasher, **environment)


def pay_for_a_deposit(browser: Browser, wallet_id: str):
    """Post the deposit form, which is where the provider is asked to open one."""
    return browser.post(
        f"{urls.PREFIX}/wallets/{wallet_id}/deposits", data={"amount": "5000.00"}
    )


class TestTheJoin:
    """``urls.callback_url`` on its own, because it is the frame that knows the
    route table and the one place a path could be lost."""

    def test_nothing_in_nothing_out(self):
        """An installation that has not said where it lives takes deposits with
        no return address, which is a supported state and not an error."""
        assert urls.callback_url(None) is None

    def test_the_path_is_this_layers_own(self):
        """Read off the module rather than written as ``/app/``, so a renamed
        landing page moves this test with it instead of leaving it asserting
        about an address nothing serves."""
        assert urls.callback_url(ORIGIN) == f"{ORIGIN}{urls.LANDING_PATH}"

    def test_it_lands_where_a_signed_in_person_lands(self):
        """**The reason it is the landing page and not a receipt.** Nothing is
        settled here: whether the money arrived is decided by the webhook, so the
        page a payer comes back to is the one that will show the new balance a
        few seconds later. A dedicated "thank you" page would be a page that
        could only ever say "pending"."""
        assert urls.callback_url(ORIGIN) == f"{ORIGIN}{urls.RETURN_PATH}"

    def test_it_concatenates_rather_than_resolving(self):
        """**The ``urljoin`` hazard, written down as behaviour.**

        A base of ``https://example.com/deep`` would make ``urljoin`` return
        ``https://example.com/app/`` - silently *replacing* the segment rather
        than appending to it, and sending a payer to a page this system does not
        serve. It cannot happen to a deployment that followed the instruction to
        write an origin, and the function is written so that it cannot happen to
        one that did not.
        """
        assert urls.callback_url("https://example.com/deep") == (
            f"https://example.com/deep{urls.LANDING_PATH}"
        )

    def test_the_stripping_is_the_setting_readers_job_and_not_this_functions(
        self,
    ):
        """**The seam between the two frames, asserted rather than assumed.**

        This function does not tidy its argument, which is what makes the pair
        below a contract rather than two independent behaviours: the reader below
        ``settings`` hands over an origin with no trailing slash, and this
        function is what turns it into an address. A caller that skipped the
        reader would get a doubled separator, and that is the reason the two are
        tested together in this class's neighbour.
        """
        assert urls.callback_url(ORIGIN_WITH_A_TRAILING_SLASH) == (
            f"{ORIGIN}/{urls.LANDING_PATH}"
        )


class TestTheSetting:
    """``public_base_url_from_environment``, which is four lines and one of them
    is the whole point."""

    def test_it_reads_the_variable(self):
        assert public_base_url_from_environment({"PUBLIC_BASE_URL": ORIGIN}) == ORIGIN

    def test_a_trailing_slash_is_stripped(self):
        """**Because the join below it is a concatenation.** Left in, a base URL
        written with a slash - which is how most people write one - would give
        every payer a ``//app/`` in their address bar. It would very probably
        still resolve, which is what makes it worth removing here rather than
        hoping: a doubled separator is harmless right up until something that is
        not a browser resolves it."""
        assert (
            public_base_url_from_environment({"PUBLIC_BASE_URL": ORIGIN_WITH_A_TRAILING_SLASH})
            == ORIGIN
        )

    def test_several_trailing_slashes_are_stripped(self):
        assert public_base_url_from_environment({"PUBLIC_BASE_URL": f"{ORIGIN}///"}) == ORIGIN

    def test_an_installation_that_has_not_said(self):
        """The fresh clone, and the return type says so: ``str | None`` where
        ``web_from_environment`` above it cannot return ``None`` at all."""
        assert public_base_url_from_environment({}) is None

    def test_a_blank_value_counts_as_unset(self):
        """``_text``'s rule everywhere else in that module, and it matters more
        here than most: an address of whitespace would be a callback that sends a
        payer nowhere, which is worse than no callback at all."""
        assert public_base_url_from_environment({"PUBLIC_BASE_URL": "   "}) is None

    def test_the_pair_composes(self):
        """**The two frames, joined as ``create_app`` joins them**, so that the
        stripping above and the concatenation next door are one tested claim
        rather than two that happen to agree."""
        origin = public_base_url_from_environment(
            {"PUBLIC_BASE_URL": ORIGIN_WITH_A_TRAILING_SLASH}
        )

        assert urls.callback_url(origin) == f"{ORIGIN}{urls.LANDING_PATH}"


class TestTheApplicationReadsIt:
    def test_the_suites_own_installation_has_not_been_told(self, app):
        """**The state every other test in this directory runs in**, asserted
        once so that "the suite is testing the no-address installation" is a
        claim rather than an assumption about a fixture."""
        assert app.state.public_base_url is None

    def test_an_installation_that_has_been_told(self, monkeypatch, db_path, password_hasher):
        with an_installation(
            monkeypatch, db_path, password_hasher, PUBLIC_BASE_URL=ORIGIN_WITH_A_TRAILING_SLASH
        ) as (app, _):
            assert app.state.public_base_url == ORIGIN


class TestADepositThatSendsThePayerBack:
    @pytest.fixture
    def paying(self, monkeypatch, db_path, password_hasher, calling_out):
        """A signed-in person with a funded wallet, at an installation that knows
        its own address, and the provider's outbound call recorded."""
        with an_installation_that_can_take_money(
            monkeypatch, db_path, password_hasher, public_base_url=ORIGIN
        ) as (app, browser):
            yield app, browser, browser.funded_wallet()

    def test_the_provider_is_handed_the_address(self, paying, calling_out):
        """**The claim this file exists for**, and it is made against the
        payload rather than against the object that built it: the address is
        asserted where Paystack would read it."""
        _, browser, wallet_id = paying

        pay_for_a_deposit(browser, wallet_id)

        assert calling_out.call["json"]["callback_url"] == (
            f"{ORIGIN}{urls.LANDING_PATH}"
        )

    def test_the_browser_still_leaves_for_the_providers_own_page(
        self, paying, calling_out
    ):
        """**The callback is not a detour.** A deposit's next step is the
        provider's checkout, and the address above is where the payer arrives
        *after* it. A route that redirected to the callback instead would be
        sending somebody to the page they are meant to come back to, having
        charged them nothing."""
        _, browser, wallet_id = paying

        response = pay_for_a_deposit(browser, wallet_id)

        assert response.status_code == 303
        assert response.headers["location"] == A_TRANSACTION["data"]["authorization_url"]

    def test_nothing_is_credited_by_coming_back(self, paying):
        """**The honest reading of the return, asserted at the page.**

        Money settles from ``/webhooks/paystack`` and from nowhere else, so a
        payer who is sent back has paid and has not been credited - and the page
        they land on will show the old balance until the webhook arrives.

        **The balance and not the amount, and the difference is the whole test.**
        ``InitiateDeposit`` writes a ``PENDING`` row carrying the amount, so
        ``5000.00`` *is* on this page - in the ledger, where a movement that has
        been asked for belongs. A test written as ``"5000.00" not in page`` would
        have been asserting the absence of the record of the request, which is the
        one thing a deposit must leave behind. What must not have happened is the
        credit, and the available balance is where that is visible.
        """
        _, browser, wallet_id = paying
        before = balance_in(browser.wallet_page(wallet_id))

        pay_for_a_deposit(browser, wallet_id)

        page = browser.wallet_page(wallet_id)
        assert balance_in(page) == before

    def test_the_request_is_recorded_as_pending(self, paying):
        """The other half of that pair, so the claim is "recorded and not
        credited" rather than "nothing happened at all"."""
        _, browser, wallet_id = paying

        pay_for_a_deposit(browser, wallet_id)

        ledger = browser.wallet_page(wallet_id).split("Activity")[1]
        assert "deposit" in ledger
        assert "pending" in ledger


class TestADepositWithNowhereToReturnTo:
    @pytest.fixture
    def paying(self, monkeypatch, db_path, password_hasher, calling_out):
        with an_installation_that_can_take_money(
            monkeypatch, db_path, password_hasher
        ) as (app, browser):
            yield app, browser, browser.funded_wallet()

    def test_the_payload_carries_no_address_at_all(self, paying, calling_out):
        """**Absent rather than null**, which is the whole of the adapter's
        argument for the conditional: an installation that has not said where it
        lives must send byte-for-byte the payload it sent before the setting
        existed."""
        _, browser, wallet_id = paying

        pay_for_a_deposit(browser, wallet_id)

        assert "callback_url" not in calling_out.call["json"]

    def test_and_the_deposit_works(self, paying, calling_out):
        """**The setting is a nicety, not a dependency.** Everything a payment
        needs is here: the provider is asked, the payer is sent to a real
        checkout, and the webhook will settle it. What is lost is a courtesy."""
        _, browser, wallet_id = paying

        response = pay_for_a_deposit(browser, wallet_id)

        assert response.status_code == 303
        assert response.headers["location"] == A_TRANSACTION["data"]["authorization_url"]

    def test_a_deposit_is_not_refused_for_want_of_an_address(self, paying):
        """The same claim as an absence of an error, because the failure this
        guards against is a 503 - the shape ``payment_provider`` refuses a
        *missing key* with, which is a genuinely different fact from a missing
        return address and must not be graded like one."""
        _, browser, wallet_id = paying

        assert pay_for_a_deposit(browser, wallet_id).status_code != 503
