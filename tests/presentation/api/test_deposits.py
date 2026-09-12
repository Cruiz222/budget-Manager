"""``POST /wallets/{id}/deposits``: asking for money, and being told nothing arrived.

**This file exists to pin one sentence: the route credits nothing.** Everything
else about a deposit - that it eventually lands, that it lands exactly once, that
a failed payout gives its hold back - is the webhook's, and lives in
``test_webhooks.py``. What is only assertable from here is the *shape of the
answer*: that opening a collection returns a URL and a reference, that the row it
writes is PENDING, and that the balance a client reads immediately afterwards is
the balance it read before.

That last assertion is decision 97 made visible, and it is the reason this route
spent three phases on the boundary test's held list. A deposit endpoint that
credited on intent would be creating money out of a promise; the money exists
only when the party that actually moved it says so.

The other half of the file is the refusals, and the interesting one is the
duplicate key. The friendly-looking alternative - hand back the first
``authorization_url`` - is wrong, and the test says why rather than leaving it to
the docstring: a checkout URL is single-use, so replaying one that has been paid
gives the client a page that will not take money.

Two things are deliberately *not* here. There is no test that a frozen wallet can
receive a deposit: it is asserted at the domain level, and reaching it over the
wire would mean the freeze-confirmation dance to set up a state whose only
relevance is to a check three layers down. And there is no test that a deposit
lands in a pot - because it cannot, and ``test_boundary.py`` is where that
absence is recorded.
"""

import pytest

from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE, BOB


@pytest.fixture
def a_wallet(open_wallet, as_user):
    """A wallet of the caller's own, and the headers that reach it.

    Returns ``(headers, wallet_id)`` rather than the URL, because the wallet id is
    what most of these tests need next - to read a balance, to list transactions,
    to close it - and the one thing every test needs is the path, which is one
    f-string away. Building the URL here and parsing the id back out of it would
    be a fixture making work for its callers.
    """

    def _open(headers=None, currency="NGN"):
        own = as_user() if headers is None else headers
        return own, open_wallet(own, currency)

    return _open


def deposits_url(wallet_id) -> str:
    return f"/wallets/{wallet_id}/deposits"


class TestTheIntentCreditsNothing:
    def test_the_balance_is_exactly_what_it_was(self, client, a_wallet, balance_of):
        """**The whole claim of the route, in two reads of one number.**

        The second read is what makes this a test rather than a description: a
        201 whose body says ``pending`` is still a 201 that could have credited
        the wallet, and the only way to know it did not is to look.
        """
        headers, wallet_id = a_wallet()
        assert balance_of(wallet_id, headers) == "0.00"

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 201, response.text
        assert balance_of(wallet_id, headers) == "0.00"

    def test_the_answer_names_the_row_the_provider_will_settle(
        self, client, a_wallet
    ):
        """Three fields, and each is one the client cannot compute for itself.

        ``provider_reference`` is the handle a webhook will arrive under;
        ``authorization_url`` is the provider's page, which this system may not
        parse, store or reconstruct; and ``status`` is carried so the client is
        told *pending* in so many words rather than inferring it from a balance
        that did not move.

        There is no ``expires_at``, and its absence is honest rather than an
        omission - nothing about a collection expires on this side, and a field
        invented here would be a promise no code keeps.
        """
        headers, wallet_id = a_wallet()

        body = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        ).json()

        assert body["status"] == "pending"
        assert body["amount"] == {"amount": "5000.00", "currency": "NGN"}
        assert body["provider_reference"]
        assert body["authorization_url"].startswith("https://")

    def test_the_row_is_recorded_as_pending_and_not_as_money(self, client, a_wallet):
        """The ledger holds the request, which is the other half of "nothing moved".

        A balance is one place a credit could hide; the transactions list is the
        other, and a row written SUCCESSFUL would be a claim that money arrived
        with no party behind it. ``completed_at`` being absent is the same fact
        from the aggregate's side.
        """
        headers, wallet_id = a_wallet()
        client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        rows = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()

        assert [row["status"] for row in rows] == ["pending"]
        assert rows[0]["type"] == "deposit"
        assert rows[0]["completed_at"] is None

    def test_the_reference_is_the_one_that_was_asked_for(self, client, a_wallet):
        """A client that supplies a key can find the deposit by it afterwards.

        Which is the whole point of accepting one: the key is how the caller
        reconciles a payment it is waiting for against its own records, and it is
        also what the provider is given as its own idempotency key - so a retry
        that reached the provider would be refused there as well as here.
        """
        headers, wallet_id = a_wallet()

        body = client.post(
            deposits_url(wallet_id),
            json={"amount": "5000.00", "ref": "invoice-7"},
            headers=headers,
        ).json()

        assert body["provider_reference"].endswith("invoice-7")

    def test_a_deposit_with_no_key_still_gets_one(self, client, a_wallet):
        """The key is optional, and its absence is not a hole.

        A caller that does not care about reconciling should not have to invent a
        unique string, so the system mints one - which makes "no key" mean "I do
        not need to look this up later" rather than "this request has no
        identity". Note the wallet is in the reference, which is what keeps two
        clients' ``invoice-7`` from colliding.
        """
        headers, wallet_id = a_wallet()

        body = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        ).json()

        assert body["provider_reference"].startswith(wallet_id)


class TestTheRefusals:
    def test_another_persons_wallet_is_a_404(self, client, as_user, open_wallet):
        """The same answer a wallet that never existed gets.

        A caller cannot open a collection against somebody else's wallet, and
        cannot learn that one exists by trying - which is Phase 1a's guarantee
        arriving at a route added three phases later, unchanged and without being
        thought about again, because ``get_owned`` is the only door.
        """
        alice = as_user(ALICE)
        bob = as_user(BOB)
        wallet_id = open_wallet(alice)

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=bob
        )

        assert response.status_code == 404

    def test_an_unauthenticated_caller_is_a_401(self, client, a_wallet):
        headers, wallet_id = a_wallet()

        response = client.post(deposits_url(wallet_id), json={"amount": "5000.00"})

        assert response.status_code == 401

    def test_a_closed_wallet_is_a_409_and_no_collection_is_opened(
        self, client, a_wallet, balance_of
    ):
        """Refused *before* the provider is called, which is the reason for the check.

        Closing is a two-step confirmation now, so the wallet is closed the way a
        client would close it - ask, then answer - rather than by writing a status
        into a column. The payer is therefore never sent anywhere, which is the
        property that matters: a collection opened against a wallet nothing can
        credit would take a real person's money.
        """
        headers, wallet_id = a_wallet()
        closing = client.post(f"/wallets/{wallet_id}/close", headers=headers)
        assert closing.status_code == 201, closing.text
        confirmed = client.post(
            f"/confirmations/{closing.json()['confirmation_id']}/confirm",
            headers=headers,
        )
        assert confirmed.status_code == 201, confirmed.text

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 409
        assert response.json()["error"] == "WalletClosedError"
        assert balance_of(wallet_id, headers) == "0.00"

    def test_the_same_key_twice_is_a_409(self, client, a_wallet):
        """**The refusal that is deliberately not a friendly replay.**

        Returning the first ``authorization_url`` looks kinder and is worse:
        nothing stores it, and a checkout URL is single-use, so the client would
        be handed a page the provider refuses to take money on - and would believe
        a deposit was live. The honest answer is that a collection is already open
        under this key.
        """
        headers, wallet_id = a_wallet()
        payload = {"amount": "5000.00", "ref": "invoice-7"}
        assert (
            client.post(deposits_url(wallet_id), json=payload, headers=headers)
        ).status_code == 201

        response = client.post(deposits_url(wallet_id), json=payload, headers=headers)

        assert response.status_code == 409
        assert response.json()["error"] == "DepositAlreadyInitiatedError"

    @pytest.mark.parametrize(
        "amount", ["-5000.00", "0", "five thousand", ""], ids=repr
    )
    def test_an_amount_that_is_not_money_is_a_400(self, client, a_wallet, amount):
        """Four ways to not be an amount, and one door that refuses all of them.

        ``translate.money_in`` is where a bare string becomes ``Money``, and it is
        the same door ``withdraw`` and ``payout`` use - so the money-in and
        money-out routes agree about what a malformed amount is, which is the
        property the route's docstring claims. The negative and the zero are
        refused one layer further down, by ``InitiateDeposit``, and reach the
        client identically; asserting both here is what keeps that layering free
        to move without the wire changing.
        """
        headers, wallet_id = a_wallet()

        response = client.post(
            deposits_url(wallet_id), json={"amount": amount}, headers=headers
        )

        assert response.status_code == 400

    def test_an_unconfigured_install_is_a_503(self, unconfigured_client):
        """No payment key, so no collection can be opened - and it never gets further.

        The refusal comes from the dependency rather than from the use case, so
        it happens before the wallet is read, before the amount is parsed and
        before any row is written. There is no partial state to clean up because
        nothing has happened, which is the property to want from a request whose
        whole purpose is to reach outside this system.

        The caller is real and authenticated, and the wallet is real, so the only
        thing that could produce this status is the missing key. Registering by
        hand rather than through ``as_user`` is forced: that fixture is bound to
        the *configured* client, and a token from one app is not a token in
        another.
        """
        registered = unconfigured_client.post(
            "/users", json={"email": "carol@localhost", "password": TEST_USER_PASSWORD}
        )
        assert registered.status_code == 201, registered.text
        signed_in = unconfigured_client.post(
            "/sessions",
            json={"email": "carol@localhost", "password": TEST_USER_PASSWORD},
        )
        assert signed_in.status_code == 201, signed_in.text
        headers = {"Authorization": f"Bearer {signed_in.json()['token']}"}
        wallet = unconfigured_client.post(
            "/wallets", json={"currency": "NGN"}, headers=headers
        )
        assert wallet.status_code == 201, wallet.text

        response = unconfigured_client.post(
            deposits_url(wallet.json()["wallet_id"]),
            json={"amount": "5000.00"},
            headers=headers,
        )

        assert response.status_code == 503
        assert response.json()["error"] == "PaymentsUnconfiguredError"
