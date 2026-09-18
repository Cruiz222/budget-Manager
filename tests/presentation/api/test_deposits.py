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

**The newest refusal is about the payer's address**, and it is the second one a
live provider taught this codebase - in the same run that found the first. The
address a deposit is billed under is the account's own, and this system's email
rule was, at the time, that it contains an ``@`` - so an account registered at
``nobody@localhost`` was perfectly legal and unbillable at the far end. It is
refused before the call now, and the refusal is a courtesy: the provider is still
the authority, and the adapter raises the same error when Paystack says
``invalid_email_address``.

**That address can no longer be registered, and the test below was re-seeded
rather than re-pointed.** ``POST /users`` refuses it at entry as of the same
change that added the email change endpoint, so the account this refusal is about
is now a row that *predates* the rule - written straight into the store, which is
the only way it can exist. The deposit check did not become dead code when the
entry rule landed; it became the second line, and what it catches is exactly the
accounts the change endpoint exists to rescue.

**The newest refusal was about the payer's address, and there is now one past it:
an account with no address at all.** ``PayerEmailRefusedError`` is about an address
a provider will not bill; ``PayerEmailMissingError`` is about there being none, and
it became reachable over the wire when a phone number became a way to sign in -
before that, a phone-only account existed but could not obtain a session, so the
refusal could only be driven one layer down. The two sentences are deliberately not
interchangeable: one caller has a field to correct and the other has a field to
fill in, and the test below asserts the remedy is named rather than only the class.

The refusal before that one is about the characters in the key. A supplied key is
quoted into the reference Paystack is given as its idempotency key, and Paystack
takes letters, digits and ``- . , =`` and nothing else - so a key holding anything
else produced a reference the far end rejected, with an answer that read as though
this system were broken. Nothing here noticed, because the double the suite talks
to accepted whatever it was handed. It refuses now, and the test below is the one
that failed the first time it did.

Two things are deliberately *not* here. There is no test that a frozen wallet can
receive a deposit: it is asserted at the domain level, and reaching it over the
wire would mean the freeze-confirmation dance to set up a state whose only
relevance is to a check three layers down. And there is no test that a deposit
lands in a pot - because it cannot, and ``test_boundary.py`` is where that
absence is recorded.

**The newest pair here is about the provider failing, and it is the one this file
was missing for its whole life.** ``PaymentsUnconfiguredError`` was the only
unavailability a deposit could meet, and it is the *easy* one - it happens before
anything is read. The harder one is a provider that is configured, reachable in
principle, and not answering: a timeout, or Paystack's own 500. Both left as
``PaymentProviderError``, which grades 400, so a payer was told their request was
unacceptable while a collection may have been open on the far end. The pair of
tests at the end of ``TestTheRefusals`` asserts the split from the outside - a
503 for the rail, still a 400 for a refusal - because that is the only way to
assert that a *client* would do the right thing with each.
"""

import pytest

from app.domain.payments.exception import (
    PaymentProviderError,
    PaymentProviderUnavailableError,
)
from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE, BOB

#: A number as it is typed, which is the spelling a person signs in with. The
#: store holds the folded ``234``-prefixed form; ``User`` folds on the way in and
#: ``find_by_phone`` folds on the way out, so the two are one account.
TYPED_PHONE = "08012345678"


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

    def test_a_wallet_the_rail_cannot_collect_is_a_409_and_nothing_is_sent(
        self, client, a_wallet, balance_of, payment_provider
    ):
        """**The refusal that stands between a payer and money nobody can credit.**

        A wallet can be opened in any of five currencies and the rail is enabled
        for particular ones, so a collection opened for the others would go out
        carrying a naira label against a ledger row that says dollars. The far end
        takes the payer's money in naira, the webhook says naira, the row says
        dollars, and ``SettlePayment`` - which compares what arrived against what
        was asked for - refuses for ever. Nothing is credited and the money has
        already left the payer.

        So the assertions are three, and the middle one is the point: a 409 rather
        than a 400 because the request is well formed and there is nothing in it
        to fix, and an empty ``requests`` list because the payer was never sent to
        a payment page to find out.

        **The currency is named, and so is what can be collected.** "This wallet
        holds USD" tells a caller what they already know; naming the currencies
        this installation *does* collect is what turns the sentence into one
        somebody can act on.
        """
        headers, wallet_id = a_wallet(currency="USD")

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "CurrencyNotCollectableError"
        assert "USD" in response.json()["detail"]
        assert "NGN" in response.json()["detail"]
        assert "authorization_url" not in response.json()
        assert payment_provider.requests == []
        assert balance_of(wallet_id, headers) == "0.00"

    def test_a_wallet_the_rail_can_collect_still_gets_its_collection(
        self, client, a_wallet, payment_provider
    ):
        """The control, and it is the one that would catch an over-eager guard.

        The change this guards *adds* a refusal to a route that mostly works, and
        the way to get it wrong is not a missed refusal - it is a deposit route
        that stops opening collections at all. The same request against a naira
        wallet, through the same fixtures, has to come back a 201 with a URL.
        """
        headers, wallet_id = a_wallet()

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 201, response.text
        assert response.json()["authorization_url"].startswith("https://")
        assert len(payment_provider.requests) == 1

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

    def test_a_key_a_provider_would_refuse_is_a_400_naming_the_characters(
        self, client, a_wallet, balance_of
    ):
        """**The refusal that would have caught the bug this route shipped with.**

        A supplied key is quoted into the reference Paystack is handed as its own
        idempotency key, and Paystack accepts letters, digits and ``- . , =`` and
        nothing else. A colon in the key therefore produced a reference the far
        end rejected - and it rejected it with a body that reads as though *this*
        system is broken, which is how a deposit route that had never once worked
        against the real provider stayed unexamined.

        The status is the assertion that matters, and the ``detail`` is the second:
        a client holding a key it chose itself is owed the characters that would
        have been accepted. Note the refusal is a 400 rather than the 500 an
        unlisted exception would give - it is graded by falling through
        ``errors._grade``, which is what makes a refusal this new behave like every
        refusal that came before it.
        """
        headers, wallet_id = a_wallet()

        response = client.post(
            deposits_url(wallet_id),
            json={"amount": "5000.00", "ref": "invoice:7"},
            headers=headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "InvalidIdempotencyKeyError"
        assert ":" in response.json()["detail"]
        assert balance_of(wallet_id, headers) == "0.00"

    def test_a_payer_address_a_provider_would_refuse_is_a_400(
        self, client, legacy_account, as_existing_user, open_wallet, balance_of
    ):
        """**The second thing a live run taught this route, and the cheaper one.**

        Paystack will not bill an address with no domain in it. Its answer is
        ``invalid_email_address``, with a ``nextStep`` about passing the ``email``
        parameter, which reads as though this codebase forgot to send one rather
        than as though the address is unusable.

        So the address is refused here, at the only moment it matters and the only
        moment it *can* matter - a person is trying to put money in and cannot.
        Note it is the same 400 the key refusal gets, by the same route: an
        unlisted ``MoneyError`` falls through ``errors._grade``, so a refusal this
        new needs no entry there to behave like every refusal before it.

        **The account is seeded by ``legacy_account`` rather than registered, and
        that change is the finding closing behind this test.** It used to register
        ``nobody@localhost`` through ``as_user``, and registering it was the
        point: the test *was* the proof of the gap between what registration
        accepted and what a provider would bill. That gap is shut - ``POST
        /users`` refuses the address now - so an account like this cannot be
        created through the API at all. It can still be *held*, because rows
        written before the rule are on disk and are deliberately still readable,
        and this is one of those rows.

        So the test did not stop being about the live finding; it moved from
        "registration lets this through" to "this is what the deposit check is
        still for". What it proves now is narrower and still worth proving: the
        payer check is the second line, and what it catches is accounts already
        stranded. ``test_request_email_change``'s rescue case is the other half.
        """
        email = legacy_account("nobody@localhost")
        headers = as_existing_user(email)
        wallet_id = open_wallet(headers)

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "PayerEmailRefusedError"
        assert "nobody@localhost" in response.json()["detail"]
        assert balance_of(wallet_id, headers) == "0.00"

    def test_a_phone_only_account_cannot_deposit_yet_and_is_told_how(
        self, client, as_phone_user, open_wallet, balance_of
    ):
        """**The state phone signup creates, refused legibly, over the wire.**

        ``PayerEmailRefusedError`` above is about an address a provider will not
        take; this is the case with no address at all, and it is reachable now in a
        way it was not: a phone-only account can obtain a session, open a wallet,
        hold a balance and be signed in - it simply cannot be *billed*, because a
        payment provider is handed the payer's address and there is nothing honest
        to send. ``PayerEmailMissingError``, not the refusal beside it: one caller
        has something to correct and the other has a field to fill in, and the two
        sentences are deliberately not interchangeable.

        **This is the route-level half of a test the application layer has had
        since step 1**, and it needed this step to exist at all. Before it, a
        phone-only account could not sign in, so there was no way to make this
        request with a real token - the refusal was proven one layer down
        (``tests/application/payments/test_initiate_deposit.py``) and could not be
        driven through a route.

        The remedy is asserted in the detail rather than checked for presence: the
        whole point of naming a missing field is that the caller can act on it, and
        an error class alone would leave a client to guess which of an account's
        several fields was absent.

        ``balance_of`` is the assertion that nothing happened. A refusal on the way
        to a provider is worth nothing if a row was written first - and this is the
        one flow in the system whose failure spends real money, so the "and no
        money moved" half is not a formality.
        """
        headers = as_phone_user(TYPED_PHONE)
        wallet_id = open_wallet(headers)

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "PayerEmailMissingError"
        assert "email" in response.json()["detail"]
        assert balance_of(wallet_id, headers) == "0.00"

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

        ``carol@example.com`` is a real-domain address, and it moved there when
        the entry rule landed. What used to sit here was a paragraph defending
        ``carol@localhost`` - the argument being that the 503 comes from the
        dependency, so the request never reaches the payer check and any address
        would do. That was true, and it is why this test needed no change to keep
        passing *for the reason it is about*; but the registration in front of it
        is a real ``POST /users``, and the entry rule refuses the address there.
        The premise the old paragraph rested on had evaporated, so it is replaced
        rather than left to describe a fixture that no longer exists.

        What the test asserts is unchanged: the ordering, not the address.
        """
        registered = unconfigured_client.post(
            "/users", json={"email": "carol@example.com", "password": TEST_USER_PASSWORD}
        )
        assert registered.status_code == 201, registered.text
        signed_in = unconfigured_client.post(
            "/sessions",
            json={"email": "carol@example.com", "password": TEST_USER_PASSWORD},
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

    def test_a_provider_that_cannot_be_reached_is_a_503_and_not_a_400(
        self, client, a_wallet, payment_provider
    ):
        """**The live-money audit's fix, asserted where a client actually meets it.**

        Every branch of ``PaystackPaymentProvider._request`` that is not a refusal
        used to leave as ``PaymentProviderError``, which is not in any of
        ``errors``' lists and therefore fell through to a **400** - so a timeout,
        or Paystack's own 500, arrived at a payer as "there is something
        unacceptable in your request". There was nothing unacceptable in it, and
        the sentence sent them to edit a form.

        The assertion that matters is the second one, and it is the reason this
        test is in this file rather than in the adapter's: **a client that saw a
        400 would not retry**, and a timeout on ``initialize`` may well have left
        a collection open. A 503 is this codebase's way of saying "come back
        later", which is the only useful thing to tell them.

        The queue is drained by the one call, so nothing here depends on ordering;
        and the wallet is the fixture's, so the refusal is reached through a
        request that would otherwise have succeeded.
        """
        headers, wallet_id = a_wallet()
        payment_provider.fail_next(
            PaymentProviderUnavailableError("could not reach the payment provider")
        )

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 503
        assert response.json()["error"] == "PaymentProviderUnavailableError"

    def test_a_provider_that_refuses_is_still_a_400(
        self, client, a_wallet, payment_provider
    ):
        """**The control, without which the test above proves nothing.**

        A 503 for everything would pass the test above and be just as wrong in the
        other direction: a caller whose request the provider *refused* has
        something to change, and telling them "come back later" would have them
        retrying a request that will be refused identically for ever. The two
        classes are siblings rather than one inheriting the other precisely so
        that neither can stand in for the other, and this pair is what says so.
        """
        headers, wallet_id = a_wallet()
        payment_provider.fail_next(
            PaymentProviderError(
                "the payment provider refused the call with 400: "
                "Invalid character in transaction reference "
                "(invalid_character_in_reference)"
            )
        )

        response = client.post(
            deposits_url(wallet_id), json={"amount": "5000.00"}, headers=headers
        )

        assert response.status_code == 400
        assert response.json()["error"] == "PaymentProviderError"
