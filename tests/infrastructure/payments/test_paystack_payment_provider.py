from decimal import Decimal

import httpx
import pytest
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.payments.exception import (
    InvalidPaymentIntentError,
    InvalidProviderAnswerError,
    PayerEmailRefusedError,
    PaymentProviderError,
    PaymentProviderUnavailableError,
)
from app.domain.payments.exception import InvalidTransferIntentError
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.infrastructure.payments import paystack_payment_provider
from app.infrastructure.payments.paystack_payment_provider import (
    BASE_URL,
    DEFAULT_TIMEOUT,
    SUPPORTED_CURRENCIES,
    PaystackPaymentProvider,
    _from_subunit,
    _subunit,
)
from tests.conftest import TEST_PAYSTACK_SECRET

NGN = Currency.NGN
USD = Currency.USD

#: A key nothing signs with, for the tests about the wrong one. Shaped like
#: ``TEST_PAYSTACK_SECRET`` so the two are recognisable as a pair.
OTHER_SECRET = "sk_test_" + "f" * 32

#: What Paystack actually answers a successful initialize with, trimmed to the
#: two fields this adapter reads and keeping their real spellings. Trimmed rather
#: than invented: a double that answered with ``authorizationUrl`` would let a
#: typo in the adapter pass, which is the one thing a recorded response is for.
A_TRANSACTION = {
    "status": True,
    "message": "Authorization URL created",
    "data": {
        "authorization_url": "https://checkout.paystack.com/abc123",
        "access_code": "abc123",
        "reference": "ps_ref_1",
    },
}


def a_charge(status: str, amount: int = 500000, currency: Currency = NGN) -> dict:
    """What ``/transaction/verify/:reference`` answers, in Paystack's own words.

    A builder rather than a constant because these tests are about *statuses* -
    one settled, several not - and the only thing that changes between them is
    the one word. The amount defaults to the half-million kobo that five thousand
    naira converts to, so a test can read ``amount=…`` and know it is the one
    field it is varying.

    ``amount`` is kobo, as it is on every Paystack payload, and it is here in
    kobo deliberately: a fixture holding naira would hide exactly the factor of a
    hundred that ``_from_subunit`` exists to undo.

    ``currency`` is a parameter for the same reason ``amount`` is: a response
    that always said NGN could not tell an adapter that reads the currency apart
    from one that assumes it, which is the difference between the lookup
    answering a question and answering its own.
    """
    return {
        "status": True,
        "message": "Verification successful",
        "data": {
            "status": status,
            "reference": "ps_ref_1",
            "amount": amount,
            "currency": currency.value,
        },
    }


class RecordingRequest:
    """Stands in for ``httpx.request``, and writes down what it was asked to send.

    One instance per test, and it answers with whatever it was set to answer
    with. ``raises`` is an exception to raise instead of answering, which is how
    a provider that cannot be reached is expressed - and it is the same shape
    ``RecordingSmtp`` uses for the same reason.

    **It records the method as well as the URL, and that is a change it had to
    make.** This double used to stand in for ``httpx.post``, whose method was
    implied by its name; the adapter now shares one ``_request`` between opening
    a collection and looking one up, so "which verb went out" became a fact about
    the call rather than a fact about the function being replaced. A double that
    still recorded ``(url, **kwargs)`` would let a lookup leave as a POST and
    still satisfy every assertion about its URL - and a POST to
    ``/transaction/verify/:reference`` would create a second charge, not ask about
    the first.

    The signature is ``(method, url, **kwargs)`` rather than the real one, so that
    a change to how the adapter calls this - a new keyword, a different one - shows
    up in the recorded call rather than in a ``TypeError``. What is being
    asserted about is the *content* of the call, and a double that could only
    accept today's arguments would fail on tomorrow's for a reason that has
    nothing to do with what the test is about.
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.response = httpx.Response(200, json=A_TRANSACTION)
        self.raises: Exception | None = None

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return self.response

    @property
    def call(self) -> dict:
        """The one call made, asserted to be exactly one.

        A test that asked for ``calls[0]`` would pass on an adapter that called
        twice, and "called twice" is a second collection opened under one
        reference. There is no retry here and this is where that is pinned.
        """
        assert len(self.calls) == 1, self.calls
        return self.calls[0]


@pytest.fixture
def requesting(monkeypatch):
    """Replace ``httpx.request`` for the duration of one test.

    ``httpx.request`` rather than ``httpx.post``, because that is the single seam
    ``_request`` calls through - one seam for both of the adapter's outbound
    methods, which is the property that makes this one fixture able to cover
    opening a collection and looking one up.
    """
    recorder = RecordingRequest()
    monkeypatch.setattr(paystack_payment_provider.httpx, "request", recorder)
    return recorder


@pytest.fixture
def provider():
    return PaystackPaymentProvider(secret_key=TEST_PAYSTACK_SECRET)


def an_amount(text) -> Money:
    return Money(Decimal(text), NGN)


class TestOpeningACollection:
    def test_it_posts_to_the_initialize_endpoint(self, provider, requesting):
        """**The verb as well as the URL, now that one double records both.**

        Opening a collection is a POST because it *creates* a charge; the lookup
        below is a GET to the same host with the same token because it asks about
        one. Asserting only the URL would leave the two calls distinguishable by
        nothing this file can see, and a lookup sent as a POST would be a second
        charge rather than a question.
        """
        provider.initialize_deposit(
            reference="ps_ref_1", amount=an_amount("5000"), email="payer@localhost"
        )

        assert requesting.call["method"] == "POST"
        assert requesting.call["url"] == f"{BASE_URL}/transaction/initialize"

    def test_it_authenticates_with_the_secret_key_as_a_bearer_token(
        self, provider, requesting
    ):
        """The one place the key is written down, and it goes in a header.

        Paystack takes the secret as a bearer token rather than as a query
        parameter or a body field, and the difference is not cosmetic: a query
        parameter ends up in access logs and in ``Referer`` headers, and a body
        field ends up in whatever the caller logs the request as.
        """
        provider.initialize_deposit(
            reference="ps_ref_1", amount=an_amount("5000"), email="payer@localhost"
        )

        assert (
            requesting.call["headers"]["Authorization"] == f"Bearer {TEST_PAYSTACK_SECRET}"
        )

    def test_it_sends_the_amount_in_kobo(self, provider, requesting):
        """**The one piece of arithmetic in the adapter, and the one that bites.**

        A factor of a hundred applied the wrong way is a 1 NGN deposit where
        10,000 was meant - and it would look like a working integration until
        somebody checked a bank statement. The awkward amount is chosen so the
        conversion cannot be right by luck.
        """
        provider.initialize_deposit(
            reference="ps_ref_1",
            amount=an_amount("10000.55"),
            email="payer@localhost",
        )

        assert requesting.call["json"]["amount"] == 1000055

    def test_it_sends_the_reference_it_was_given(self, provider, requesting):
        """So the provider's own idempotency key is this system's scoped key.

        That is what closes the window between two units of work: a second
        request under the same key reaches Paystack and is refused *there*, not
        merely here. The adapter's job is only to pass it through unchanged.
        """
        provider.initialize_deposit(
            reference="wallet:invoice-7",
            amount=an_amount("5000"),
            email="payer@localhost",
        )

        assert requesting.call["json"]["reference"] == "wallet:invoice-7"

    def test_it_sends_the_payers_email_and_nothing_else_about_them(
        self, provider, requesting
    ):
        """**The one fact about a person this system hands a third party.**

        Asserted as the whole payload rather than as one field, because what
        matters is the absence: no name, no user id, no wallet id, no history.
        A test that checked ``json["email"]`` would keep passing on the day
        somebody added a ``metadata`` field with the account's address in it.
        """
        provider.initialize_deposit(
            reference="ps_ref_1", amount=an_amount("5000"), email="payer@localhost"
        )

        assert requesting.call["json"] == {
            "email": "payer@localhost",
            "amount": 500000,
            "reference": "ps_ref_1",
            "currency": "NGN",
        }

    def test_the_currency_sent_is_the_amounts_own(self, provider, requesting):
        """**The assertion that would have caught the bug this file used to have.**

        The label and the number used to be decided in two different places: the
        number came from ``_subunit(amount)`` and the label came from a module
        constant. Nothing connected them, so a wallet holding USD posted a payload
        whose ``amount`` was the dollar figure and whose ``currency`` was naira -
        and because settlement compares what arrives against what the ledger row
        asked for, the payer's money was taken against a row that could never
        settle.

        A provider is constructed here as enabled for two currencies, and that
        construction is the test rather than scaffolding: against a provider that
        only ever collects one, "it reads the currency off the amount" and "it
        sends a constant" are the same behaviour and no assertion can separate
        them. The pair below separates them - the same object, two amounts, two
        labels.

        The control is the second call. It is what stops this passing on an
        adapter that simply forwards ``amount.currency`` without ever checking
        what the account is enabled for, which is a different bug with the same
        happy path.
        """
        provider = PaystackPaymentProvider(
            TEST_PAYSTACK_SECRET, currencies=frozenset({NGN, USD})
        )
        provider.initialize_deposit(
            reference="ps_ref_1",
            amount=Money(Decimal("100.00"), USD),
            email="payer@localhost",
        )
        provider.initialize_deposit(
            reference="ps_ref_2",
            amount=Money(Decimal("5000.00"), NGN),
            email="payer@localhost",
        )

        assert requesting.calls[0]["json"]["currency"] == "USD"
        assert requesting.calls[0]["json"]["amount"] == 10000
        assert requesting.calls[1]["json"]["currency"] == "NGN"
        assert requesting.calls[1]["json"]["amount"] == 500000

    def test_a_currency_the_account_is_not_enabled_for_is_refused(
        self, provider, requesting
    ):
        """The backstop, and it is deliberately not the door a client meets.

        ``InitiateDeposit`` asks ``supported_currencies`` and refuses before this
        method is ever reached, so nothing here is reachable from the API. What
        this covers is the caller that skips that door - a script, a future use
        case, a second adapter's worth of refactoring - and the failure it exists
        to prevent is the silent one: relabelling a collection rather than
        refusing it.
        """
        with pytest.raises(PaymentProviderError) as raised:
            provider.initialize_deposit(
                reference="ps_ref_1",
                amount=Money(Decimal("100.00"), USD),
                email="payer@localhost",
            )

        assert "USD" in str(raised.value)
        assert requesting.calls == [], "the request left the process anyway"

    def test_it_sets_a_timeout(self, provider, requesting):
        """Without one, a provider that accepts the connection and says nothing
        leaves the request blocked until the operating system gives up."""
        provider.initialize_deposit(
            reference="ps_ref_1", amount=an_amount("5000"), email="payer@localhost"
        )

        assert requesting.call["timeout"] == DEFAULT_TIMEOUT

    def test_it_returns_the_url_and_the_provider_s_own_reference(
        self, provider, requesting
    ):
        intent = provider.initialize_deposit(
            reference="ps_ref_1", amount=an_amount("5000"), email="payer@localhost"
        )

        assert intent.authorization_url == "https://checkout.paystack.com/abc123"
        assert intent.provider_reference == "ps_ref_1"

    def test_a_reference_the_provider_changed_is_the_one_recorded(
        self, provider, requesting
    ):
        """The two columns are separate even when the values agree, and this is why.

        A provider that mints its own reference and answers with it must have
        *that* one recorded: a webhook will arrive under the provider's name, and
        a ledger row filed under ours would never be found. The double answers
        with a different reference from the one sent, so an adapter that returned
        its input instead of the response would fail here.
        """
        requesting.response = httpx.Response(
            200,
            json={
                "status": True,
                "data": {
                    "authorization_url": "https://checkout.paystack.com/abc123",
                    "reference": "ps_minted_by_paystack",
                },
            },
        )

        intent = provider.initialize_deposit(
            reference="ours", amount=an_amount("5000"), email="payer@localhost"
        )

        assert intent.provider_reference == "ps_minted_by_paystack"


class TestWhenTheProviderSaysNo:
    """Every failure leaves through one door, and that is the port's contract.

    A caller has one thing to catch, and it cannot accidentally treat a provider
    outage as an ordinary result. The door is ``PaymentError`` - the tree the
    port's docstring names - and behind it are **two rooms**, which is what a
    live-money audit split apart: a provider that *refused* the call
    (``PaymentProviderError``) and a provider that could not be asked
    (``PaymentProviderUnavailableError``). These are the ways a call fails, and
    the class they arrive as is the assertion in each one.
    """

    def test_a_wrong_key_is_a_legible_configuration_error(self, provider, requesting):
        """**Paystack answers 401 with a message and no ``data`` at all.**

        That is the common case by a wide margin - it is what happens the first
        time anybody deploys this - and the whole reason this branch exists is to
        turn it into a sentence naming the secret key rather than a ``KeyError``
        three frames deeper with nothing about the provider in it.
        """
        requesting.response = httpx.Response(
            401, json={"status": False, "message": "Invalid key"}
        )

        with pytest.raises(InvalidPaymentIntentError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "secret key" in str(refused.value)

    def test_an_empty_two_hundred_is_not_reported_as_a_bad_key(
        self, provider, requesting
    ):
        """**The other fact that reaches the same line, and the one nothing tested.**

        A ``2xx`` carrying no transaction is the provider - or this adapter -
        failing, and no key change fixes it. Until a live run, this case and the
        ``401`` above left through a single sentence naming the secret key, so an
        operator whose provider was broken was sent to check a credential that was
        fine; and because the message was identical in both directions, there was
        no way to tell which one they were holding.

        The assertion is deliberately the **absence** of that word. It is the half
        that used to be wrong, and a test asserting only that a sentence exists
        would have passed against the version that sent people to the wrong place.
        """
        requesting.response = httpx.Response(200, json={"status": True})

        with pytest.raises(InvalidPaymentIntentError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "secret key" not in str(refused.value)
        assert "200" in str(refused.value)

    def test_a_response_missing_the_url_is_refused(self, provider, requesting):
        requesting.response = httpx.Response(
            200, json={"status": True, "data": {"reference": "ps_ref_1"}}
        )

        with pytest.raises(InvalidPaymentIntentError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "authorization_url" in str(refused.value)

    def test_a_failed_status_is_an_unavailable_provider(self, provider, requesting):
        """**A 500 from the far end, which is not a refusal and must not read as one.**

        The status is in the message because it is what a human acts on. The body
        here is an HTML error page and so carries no provider words - which is the
        other half of the amendment above: ``_provider_words`` contributes only
        when the provider sent the two fields it documents as its error contract.

        **This test used to assert ``PaymentProviderError``, and the change is the
        point of the class it asserts now.** A 500 says nothing about the request,
        so it fell through to a 400 and told a payer their deposit was
        unacceptable; a client reading that would send them back to the form
        rather than back to the button. The two classes are siblings rather than
        one inheriting the other, so this test cannot pass by accident under the
        old name.
        """
        requesting.response = httpx.Response(500, text="<html>oops</html>")

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "500" in str(refused.value)

    def test_a_failed_lookup_is_an_unavailable_provider(self, provider, requesting):
        """The same status through the other method, because ``_request`` is shared.

        Worth its own test rather than being inferred: the lookup is what the
        reconciler runs, and a 5xx there reported as a refusal would be a cron job
        that logged a provider outage as a bad row.
        """
        requesting.response = httpx.Response(503, text="<html>oops</html>")

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "503" in str(refused.value)

    def test_an_unreachable_provider_is_an_unavailable_provider(
        self, provider, requesting
    ):
        """The socket-level failure, kept distinct from a refusal by nobody.

        ``httpx``'s own exception does not escape: a caller that had to catch
        ``httpx.ConnectError`` would be a caller that knows what library this
        adapter uses, which is the thing the port exists to prevent.

        **A timeout arrives here too**, which is the case the split was drawn for:
        a slow provider and a dead one are the same exception to ``httpx``, and
        both mean "the far end did not answer" rather than "the request was
        wrong". Neither means nothing happened - a timeout on ``initialize`` may
        leave a collection open, which is why the reference is an idempotency key.
        """
        requesting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "ConnectError" in str(refused.value)

    def test_a_timeout_is_an_unavailable_provider(self, provider, requesting):
        """The specific transport failure a real deposit meets most often."""
        requesting.raises = httpx.ReadTimeout("the provider said nothing")

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "ReadTimeout" in str(refused.value)

    def test_a_body_that_is_not_json_is_an_unavailable_provider(
        self, provider, requesting
    ):
        """A 200 carrying an HTML error page, which is what a proxy in the way
        produces - and ``json.loads`` would raise a ``ValueError`` at the caller.

        Unavailable rather than a refusal because a body that cannot be read is
        the far end being broken: there is no ``message`` to quote and nothing in
        the request to correct.
        """
        requesting.response = httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(PaymentProviderUnavailableError):
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

    def test_it_does_not_retry(self, provider, requesting):
        """**A retry here would be one the caller did not ask for and cannot see.**

        The reference *is* an idempotency key, so retrying would be safe - which
        makes it more tempting and not less wrong. This is an endpoint whose whole
        job is to create something, and when a retry is the right answer it
        belongs one level up where somebody can decide whether the thing that
        failed is worth trying again.
        """
        requesting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(PaymentProviderUnavailableError):
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert len(requesting.calls) == 1


class TestWhatARefusalSays:
    """A refusal carries the provider's own words, and one of them is translated.

    **This class exists because of a live run**, and what it pins is the thing that
    cost two round trips to a bare ``curl``. The original deposit bug was answered
    with ``{"message": "Invalid character in transaction reference", "code":
    "invalid_character_in_reference"}``, and this adapter reported only "the payment
    provider refused the call with 400" - a sentence that sends the reader looking
    at this system rather than at the reference, which is precisely where three
    phases of nobody looked.

    The words are carried for a human. The **code** is carried because one of them
    is translated below, and a code is the half of Paystack's error contract that
    is stable - so nothing here, and nothing in the adapter, matches on prose.
    """

    def test_a_refused_reference_is_reported_with_the_providers_words(
        self, provider, requesting
    ):
        """The exact body the original bug produced, four phases later.

        Had this test existed, that bug would have been a five-minute read instead
        of an investigation: the provider names the problem, in the word
        ``reference``, and the assertion below is simply that we repeat it.
        """
        requesting.response = httpx.Response(
            400,
            json={
                "status": False,
                "message": "Invalid character in transaction reference",
                "code": "invalid_character_in_reference",
            },
        )

        with pytest.raises(PaymentProviderError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@example.com"
            )

        assert "400" in str(refused.value)
        assert "Invalid character in transaction reference" in str(refused.value)
        assert "invalid_character_in_reference" in str(refused.value)

    def test_an_address_the_provider_will_not_bill_is_our_own_refusal(
        self, provider, requesting
    ):
        """**Translated rather than reported**, which is what an adapter is for.

        The code is what says which input the provider objected to, and this one
        has a better answer than "the provider refused the call": the address this
        account would be billed under is not one they will take. The client is told
        that instead, in the same words the local courtesy check uses, so the two
        checks are indistinguishable from outside.

        Note the address below has a real domain - that is the point of the test.
        This is the path a *legitimate-looking* address takes when the provider
        refuses it for a reason this codebase did not anticipate, and it is why the
        courtesy check is not the guard.
        """
        requesting.response = httpx.Response(
            400,
            json={
                "status": False,
                "message": "Invalid Email Address Passed",
                "code": "invalid_email_address",
            },
        )

        with pytest.raises(PayerEmailRefusedError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1",
                amount=an_amount("5000"),
                email="payer@example.com",
            )

        assert "payer@example.com" in str(refused.value)
        assert "cannot be used as a payer address" in str(refused.value)

    @pytest.mark.parametrize(
        "body",
        [["not", "an", "object"], {"code": 42, "message": 7}],
        ids=["a-list", "an-object-of-the-wrong-types"],
    )
    def test_a_body_that_is_not_an_object_contributes_nothing(
        self, provider, requesting, body
    ):
        """A shape nobody expected contributes no words, and must not raise.

        This runs on the path that is already refusing a call, so a diagnostic that
        can itself fail is worse than no diagnostic - which is why both readers of
        the error contract are quiet about anything they do not recognise rather
        than trusting it. The second case is the narrower half: an object whose
        fields are present and of the wrong type, which a check for "is it a dict"
        alone would wave through.

        **This test found a defect rather than confirming an absence**, which is
        worth recording because the failing line was not the one under discussion.
        The 400 below is a status ``initialize_deposit`` asked to interpret, so it
        arrives as a *body* rather than as a refusal, and the translation branched
        on ``response.get("code")`` - an attribute a list does not have. A provider
        answering a refused call with a JSON array would therefore have produced an
        ``AttributeError`` out of a code path whose entire purpose is to report a
        problem legibly. ``_provider_code`` is the guard, and its twin's docstring
        claims the quietness for both.
        """
        requesting.response = httpx.Response(400, json=body)

        with pytest.raises(PaymentProviderError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@example.com"
            )

        assert str(refused.value) == "the payment provider refused the call with 400"

    def test_a_lookup_refusal_carries_the_words_too(self, provider, requesting):
        """The other method, because the sentence has one home for both.

        ``_refusal_sentence`` exists so that the transport and this adapter's two
        outbound methods cannot disagree about how a refusal reads - which is the
        drift this project has already been bitten by once, in a reference string
        that had two homes and disagreed with itself.

        **And the sentence is one home for both *kinds*, which this test now
        pins.** ``_refusal_sentence`` writes the words for a 4xx refusal and for a
        5xx alike; only the class it is raised inside differs. So the status below
        is a 500 and the assertions are unchanged from when it was a 400 - the
        provider's own words survive the split, and what changed is who is told
        what to do about them.
        """
        requesting.response = httpx.Response(
            500, json={"message": "Something went wrong", "code": "server_error"}
        )

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "Something went wrong" in str(refused.value)
        assert "server_error" in str(refused.value)


class TestLookingUpACollection:
    """The half that asks rather than tells, and the first one that can be told *no*.

    Three answers reach the caller as values and everything else leaves as an
    exception, and the split between those two groups is the whole content of this
    class. "Nothing has arrived under this reference" and "there is no such
    reference" are answers a reconciliation run exists to receive, so they must
    not raise; a wrong key, an unreadable body and a status this adapter has never
    seen are all failures, and a caller has exactly one thing to catch.
    """

    def test_it_gets_the_verify_endpoint(self, provider, requesting):
        """A GET, and that is not a detail: a POST here would open a charge."""
        requesting.response = httpx.Response(200, json=a_charge("success"))

        provider.outcome_for("ps_ref_1")

        assert requesting.call["method"] == "GET"
        assert requesting.call["url"] == f"{BASE_URL}/transaction/verify/ps_ref_1"

    def test_it_authenticates_with_the_secret_key_as_a_bearer_token(
        self, provider, requesting
    ):
        requesting.response = httpx.Response(200, json=a_charge("success"))

        provider.outcome_for("ps_ref_1")

        assert (
            requesting.call["headers"]["Authorization"]
            == f"Bearer {TEST_PAYSTACK_SECRET}"
        )

    def test_it_sends_no_body(self, provider, requesting):
        """A lookup asks about a reference already in the path, so there is
        nothing to send - and an empty JSON object would be a body the endpoint
        does not read."""
        requesting.response = httpx.Response(200, json=a_charge("success"))

        provider.outcome_for("ps_ref_1")

        assert requesting.call["json"] is None

    def test_it_sets_a_timeout(self, provider, requesting):
        requesting.response = httpx.Response(200, json=a_charge("success"))

        provider.outcome_for("ps_ref_1")

        assert requesting.call["timeout"] == DEFAULT_TIMEOUT

    def test_the_reference_is_escaped_into_the_path(self, provider, requesting):
        """**The one part of this URL a caller has any influence over.**

        A reference is built from a wallet id and a client-supplied key, so a
        ``/`` in one would add a path segment and a ``?`` would start a query
        string - turning a lookup about one transaction into a request about
        another. The colon is escaped too, and that is deliberate rather than
        collateral: this system's own references are built with one.
        """
        requesting.response = httpx.Response(200, json=a_charge("success"))

        provider.outcome_for("wallet:abc/../def?x=1")

        assert (
            requesting.call["url"]
            == f"{BASE_URL}/transaction/verify/wallet%3Aabc%2F..%2Fdef%3Fx%3D1"
        )

    def test_success_is_a_settled_answer_carrying_the_charge(
        self, provider, requesting
    ):
        requesting.response = httpx.Response(200, json=a_charge("success"))

        answer = provider.outcome_for("ps_ref_1")

        assert answer.status is ProviderAnswerStatus.SETTLED
        assert answer.outcome.event is ProviderEvent.CHARGE_SUCCEEDED

    def test_the_amount_comes_back_out_of_kobo(self, provider, requesting):
        """**The inverse of the arithmetic the initialize half is tested for.**

        Getting this wrong by a factor of a hundred does not merely misreport a
        figure - it makes every recovered deposit *disagree* with its own ledger
        row and be refused, which is a failure that looks like a data problem
        rather than an arithmetic one. The awkward amount is chosen so the
        conversion cannot be right by luck.
        """
        requesting.response = httpx.Response(200, json=a_charge("success", 1000055))

        answer = provider.outcome_for("ps_ref_1")

        assert answer.outcome.amount == an_amount("10000.55")

    def test_the_currency_comes_back_off_the_response(self, provider, requesting):
        """**Read, not assumed, and the assumption was the other half of a bug.**

        ``_from_subunit`` used to take its currency from a module constant, which
        meant the number came out of the provider's answer and the label came out
        of this repository. Against a charge this installation had opened in
        naira the two agreed; against any other they could not, and the
        disagreement would arrive as ``AMOUNT_DISAGREES`` on a row that was
        perfectly correct - a reconciler reading its own assumption back as the
        provider's answer, for ever.

        The response is varied rather than the provider, because the claim is
        that the *response* is what decides. A doubled amount would not
        distinguish the two.
        """
        requesting.response = httpx.Response(200, json=a_charge("success", currency=USD))

        answer = provider.outcome_for("ps_ref_1")

        assert answer.outcome.amount == Money(Decimal("5000"), USD)

    def test_a_currency_this_system_does_not_know_is_refused(
        self, provider, requesting
    ):
        """An unreadable answer, not an unknown server fault.

        ``Currency(…)`` raises a plain ``ValueError``, which is not in any
        vocabulary this codebase catches - so left alone it would report as a 500
        on the reconciler's path and say nothing about where it came from. The
        translation is the same one the webhook route makes for the same field,
        which is the point: the two readers of a provider's currency agree about
        what an unreadable one means.
        """
        body = a_charge("success")
        body["data"]["currency"] = "XYZ"
        requesting.response = httpx.Response(200, json=body)

        with pytest.raises(InvalidProviderAnswerError) as raised:
            provider.outcome_for("ps_ref_1")

        assert "XYZ" in str(raised.value)

    def test_a_settled_charge_with_no_currency_is_refused(self, provider, requesting):
        """The absent case and the unknown one are one answer, deliberately.

        Both mean the same thing to a caller - the provider's answer cannot be
        read - and settlement must not guess what it meant by falling back to a
        default. A default would be the old bug with better manners: a currency
        this side chose, compared against a row, reported as a disagreement.
        """
        body = a_charge("success")
        del body["data"]["currency"]
        requesting.response = httpx.Response(200, json=body)

        with pytest.raises(InvalidProviderAnswerError):
            provider.outcome_for("ps_ref_1")

    def test_the_reference_reported_is_the_one_asked_about(
        self, provider, requesting
    ):
        """**The lookup key is the fact this side holds.**

        Taking the response's copy instead would let a provider re-point a
        settlement at a ledger row other than the one the question was about -
        and ``SettlePayment`` files the outcome by this reference, so the row it
        credited would be whatever the far end named. The double answers with a
        *different* reference from the one sent, so an adapter that read it off
        the response would fail here.
        """
        body = a_charge("success")
        body["data"]["reference"] = "ps_minted_by_paystack"
        requesting.response = httpx.Response(200, json=body)

        answer = provider.outcome_for("ours")

        assert answer.outcome.reference == "ours"

    @pytest.mark.parametrize(
        "charge",
        ["abandoned", "failed", "ongoing", "pending", "processing", "queued"],
    )
    def test_a_charge_that_has_not_been_paid_is_not_settled(
        self, provider, requesting, charge
    ):
        """**``abandoned`` is the one that matters most and must not raise.**

        A payer who opened the checkout page and closed the tab produces this,
        on every run, for ever - so an adapter that treated it as an error would
        make the ordinary state of an abandoned checkout look like a broken
        integration. It is also the status a reconciler is most tempted to
        *resolve*, and the report-only policy says it must not: nothing moves.
        """
        requesting.response = httpx.Response(200, json=a_charge(charge))

        answer = provider.outcome_for("ps_ref_1")

        assert answer.status is ProviderAnswerStatus.NOT_SETTLED
        assert answer.outcome is None

    def test_a_reversed_charge_is_not_settled_either(self, provider, requesting):
        """**The uncomfortable one, named on its own rather than in the list.**

        Money *did* arrive here and was sent back, so this is not "nothing has
        happened" - it is "something happened that this system has no vocabulary
        for". It is filed as not-settled because that is the branch that moves
        nothing, which is the only safe answer available until a reversal has an
        event of its own. The README carries it as an open item.
        """
        requesting.response = httpx.Response(200, json=a_charge("reversed"))

        answer = provider.outcome_for("ps_ref_1")

        assert answer.status is ProviderAnswerStatus.NOT_SETTLED

    def test_a_404_is_an_answer_and_not_an_exception(self, provider, requesting):
        """**Decision 137's lesson, and the assertion that pins it.**

        ``_request`` refuses every status above 400 except the ones its caller
        named - and for a call that *asks about* something, a 404 is often the
        answer itself. This is what the port grew an ``answers`` parameter for:
        the same number is a refusal from the initialize endpoint and a fact from
        the verify endpoint, and only the caller knows which call it is making.

        It is also the loudest signal a reconciliation run can produce. Nothing
        is wrong with the payment this names - a healthy install writes the row
        only after a provider accepted the collection - so a provider denying the
        reference means this deployment's rows and this key's transactions belong
        to different accounts.
        """
        requesting.response = httpx.Response(
            404, json={"status": False, "message": "Transaction not found"}
        )

        answer = provider.outcome_for("ps_ref_1")

        assert answer.status is ProviderAnswerStatus.NO_SUCH_REFERENCE

    def test_a_wrong_key_is_a_legible_configuration_error(self, provider, requesting):
        """The same 401 branch ``initialize_deposit`` has, for the same reason.

        Paystack answers a bad secret key with a message and no ``data``, so the
        useful sentence - "this deployment's key is wrong" - can only be written
        by the caller, which is the only frame that knows what the call was for.
        ``_request`` therefore passes this status through rather than refusing it,
        and this branch is the answer it was passed through for.
        """
        requesting.response = httpx.Response(
            401, json={"status": False, "message": "Invalid key"}
        )

        with pytest.raises(InvalidProviderAnswerError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "secret key" in str(refused.value)

    def test_a_status_this_adapter_does_not_know_is_refused(
        self, provider, requesting
    ):
        """**A vocabulary change at the far end, refused at the edge.**

        The statuses are written out rather than reduced to "``success`` or not",
        because the alternative silently files a status Paystack adds later as
        "nothing has settled yet" - a row that stays PENDING for ever with nobody
        ever told why. Decision 6's rule, and this is where it is enforced.
        """
        requesting.response = httpx.Response(200, json=a_charge("quantum"))

        with pytest.raises(InvalidProviderAnswerError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "quantum" in str(refused.value)

    def test_a_body_that_is_not_json_is_an_unavailable_provider(
        self, provider, requesting
    ):
        """The lookup half of the same case, and it is the reconciler's path.

        A cron job that read a proxy's HTML error page as a *refusal* would log a
        broken far end as a bad row and stop - and the deposit it was asking about
        would sit PENDING for ever with the operator told the wrong thing about
        why.
        """
        requesting.response = httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(PaymentProviderUnavailableError):
            provider.outcome_for("ps_ref_1")

    def test_a_failed_status_is_an_unavailable_provider(self, provider, requesting):
        """A 500 is nobody's answer, so it leaves through the rail's door."""
        requesting.response = httpx.Response(500, text="<html>oops</html>")

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "500" in str(refused.value)

    def test_an_unreachable_provider_is_an_unavailable_provider(
        self, provider, requesting
    ):
        """And the reconciler's most common real failure: a cron run on a machine
        whose network is briefly down, which must not be reported as a bad row."""
        requesting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(PaymentProviderUnavailableError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "ConnectError" in str(refused.value)

    def test_it_looks_up_once(self, provider, requesting):
        """**And this is where "the next run is the retry" is pinned.**

        No retry in the adapter, for the reason the initialize half gives and one
        more: a lookup creates nothing, so retrying would be harmless - and it
        still does not belong here, because reconciliation's retry is the next
        run, which is a run somebody can watch. A double that saw two calls would
        be a job spending a provider call nobody asked for.
        """
        requesting.response = httpx.Response(200, json=a_charge("success"))

        provider.outcome_for("ps_ref_1")

        assert len(requesting.calls) == 1


class TestProvingAWebhook:
    """The half with no dependency and no socket, tested against the real thing.

    Nothing is monkeypatched anywhere in this class. ``verify_signature`` is pure
    HMAC over bytes, so these tests exercise the code that runs in production -
    and the reason that matters is that a suite which replaced it would be a
    suite that passes whether or not verification works.

    The signing rule is written once, in ``tests/conftest.py``, on the test's side
    of the wire. Nothing here asks that helper whether a signature is good; it
    only builds requests.
    """

    def test_a_signature_it_made_verifies(self, provider, sign_webhook):
        body = b'{"event": "charge.success"}'

        assert provider.verify_signature(body, sign_webhook(body))

    def test_a_signature_from_another_key_does_not(self, provider, sign_webhook):
        body = b'{"event": "charge.success"}'

        assert not provider.verify_signature(body, sign_webhook(body, OTHER_SECRET))

    def test_a_body_altered_after_signing_does_not_verify(
        self, provider, sign_webhook
    ):
        """**The assertion that proves the bytes are the bytes.**

        One byte changes - a ``5`` becomes a ``6`` in an amount - and the same
        signature stops verifying. This is the whole reason the webhook route
        reads the body as bytes rather than as a model: a verifier that hashed a
        re-serialized body would accept this, and what it would be accepting is a
        forged deposit of any size.
        """
        signed = b'{"data": {"amount": 500000}}'
        altered = b'{"data": {"amount": 600000}}'

        assert provider.verify_signature(signed, sign_webhook(signed))
        assert not provider.verify_signature(altered, sign_webhook(signed))

    @pytest.mark.parametrize("signature", [None, "", "not a signature"])
    def test_an_absent_or_unusable_signature_does_not(self, provider, signature):
        """``None`` and ``""`` are different inputs and one answer.

        ``Header(default=None)`` produces ``None`` for an absent header and ``""``
        for a blank one, so a verifier written as ``if signature is None`` would
        let the blank one through - to be compared against a digest it cannot
        equal, which is safe today and safe for a reason nobody wrote down.
        """
        assert not provider.verify_signature(b"{}", signature)

    def test_a_right_length_wrong_signature_does_not(self, provider, sign_webhook):
        """A guess of the correct shape, which is what an attacker sends.

        Pinned separately from the wrong-key case because the comparison is
        ``hmac.compare_digest`` rather than ``==``: string comparison stops at the
        first differing byte, so its running time leaks how much of a guess was
        right - and that turns forging one from 2^512 work into 512 tries of 256
        attempts. The property is timing, which a test cannot measure; what it can
        pin is that a same-length forgery is refused at all.
        """
        guess = "0" * 128

        assert not provider.verify_signature(b"{}", guess)

    def test_an_uppercase_hex_signature_does_not_verify(self, provider, sign_webhook):
        """Hex is compared as written, and Paystack writes lowercase.

        Not a bug being asserted - a fact being pinned. If this ever needs to
        accept uppercase, that is a decision about a provider's output rather than
        an incidental tolerance, and it should be made deliberately.
        """
        body = b"{}"
        signature = sign_webhook(body)

        assert not provider.verify_signature(body, signature.upper())

    def test_an_empty_body_can_still_be_signed(self, provider, sign_webhook):
        """The boundary of the function, stated rather than left to chance.

        An empty body is a valid byte string with a valid HMAC, so this returns
        ``True`` - and that is correct. Whether an empty body is a *webhook* is
        the route's question, and the route answers it with a 400 after the
        signature has passed.
        """
        assert provider.verify_signature(b"", sign_webhook(b""))


class TestWhatTheAccountCollects:
    """``supported_currencies``, which is what the deposit door refuses against.

    Its own class because it is the one method on this adapter that neither opens
    a collection nor reads one - it answers a question about the *deployment*, and
    the fact that it is answerable without a network call is exactly what lets
    ``InitiateDeposit`` refuse before a payer is sent anywhere.
    """

    def test_the_default_is_the_one_verified_currency(self):
        """A deployment that has configured nothing collects in naira.

        Pinned against the module constant rather than a literal, so that
        enabling a second currency in the one place that decides it does not
        require a test to be edited in a second place - which is the arrangement
        the constant's own docstring promises.
        """
        provider = PaystackPaymentProvider(secret_key=TEST_PAYSTACK_SECRET)

        assert provider.supported_currencies() == SUPPORTED_CURRENCIES
        assert provider.supported_currencies() == frozenset({NGN})

    def test_it_is_a_frozenset_so_a_caller_cannot_edit_the_answer(self):
        """The port asks for ``frozenset`` rather than ``set``, and this is why.

        A caller that could mutate what it was handed could widen an account's
        enabled currencies from outside the adapter - which is a strange thing
        for a use case to be able to do, and precisely the kind of accident that
        only shows up as a payload nobody can explain. The adapter returns the
        set it holds rather than a copy for the same reason: it cannot be
        mutated, so there is nothing to defend against.
        """
        provider = PaystackPaymentProvider(secret_key=TEST_PAYSTACK_SECRET)

        with pytest.raises(AttributeError):
            provider.supported_currencies().add(USD)

    def test_what_it_reports_and_what_it_sends_come_from_the_same_set(
        self, requesting
    ):
        """**The pairing, which is the property the door depends on.**

        The deposit use case refuses against ``supported_currencies`` and then
        hands the amount to ``initialize_deposit``. Those two have to be reading
        the same fact: a version where the constructor field was consulted by the
        send but not by the report would be a door that admits a currency the
        rail then refuses, and one where the report knew more than the send would
        be the original bug wearing a set instead of a string. A provider built
        for two currencies must therefore both report two and send for two, and
        this asserts the two halves against each other rather than each against
        the constructor argument.
        """
        provider = PaystackPaymentProvider(
            TEST_PAYSTACK_SECRET, currencies=frozenset({NGN, USD})
        )

        assert provider.supported_currencies() == frozenset({NGN, USD})

        provider.initialize_deposit(
            reference="ps_ref_1",
            amount=Money(Decimal("100.00"), USD),
            email="payer@localhost",
        )

        assert requesting.call["json"]["currency"] == "USD"


class TestTheSubunit:
    """The conversion, in both directions, on its own, so a failure names itself.

    Tested through the two calls above as well, because that is where it runs.
    Here it is because the arithmetic is the part of this file most likely to be
    changed by somebody who is not thinking about money, and a test that can only
    be run by making an HTTP call is a test that will be skipped.

    The two directions are tested in one class rather than two because they are
    only correct *together*: the property that matters is not that each multiplies
    by a hundred in the right direction but that a round trip is the identity, and
    an inverse that rounds differently shows up as a disagreement between a
    recovered deposit and its own row.
    """

    @pytest.mark.parametrize(
        "amount, subunits",
        [
            ("1", 100),
            ("5000", 500000),
            ("5000.55", 500055),
            ("0.01", 1),
            ("1000000", 100000000),
        ],
    )
    def test_it_multiplies_by_a_hundred_exactly(self, amount, subunits):
        """Integers, not floats - which is the whole reason ``Money`` refuses them.

        ``Money`` guarantees at most two decimal places and stores a ``Decimal``,
        so the multiplication is exact and the conversion to ``int`` cannot round.
        A float anywhere on this path is how 5000.55 becomes 500054.
        """
        assert _subunit(an_amount(amount)) == subunits

    def test_the_result_is_an_int(self):
        """Not a ``Decimal``, because the wire wants an integer and ``json`` would
        serialize a ``Decimal`` as a string - which Paystack refuses."""
        assert isinstance(_subunit(an_amount("5000.55")), int)

    @pytest.mark.parametrize(
        "subunits, amount",
        [
            (100, "1"),
            (500000, "5000"),
            (500055, "5000.55"),
            (1, "0.01"),
            (100000000, "1000000"),
        ],
    )
    def test_it_divides_by_a_hundred_exactly(self, subunits, amount):
        """The inverse, and the direction that did not exist until reconciliation.

        A verify response carries kobo like every other Paystack payload, and
        ``SettlePayment`` compares what the provider says against what the row
        asked for - so a factor of a hundred applied the wrong way here would not
        misreport a figure, it would make *every* recovered deposit disagree with
        its own row and be refused. That is a failure that looks like a data
        problem rather than an arithmetic one, which is why the awkward amounts
        are here rather than round ones.
        """
        assert _from_subunit(subunits, NGN) == an_amount(amount)

    def test_the_currency_is_the_one_it_was_told(self):
        """A parameter, and it used to be read from ``SUPPORTED_CURRENCY``.

        That version argued a deployment enabled for a second currency would read
        its answers back in the currency it asked in - and it was wrong in the
        one way that matters, because the *asking* was done with the same
        hard-coded label. Two constants agreeing with each other is not the same
        as either agreeing with the amount, and the failure was a recovered
        deposit reported as a mismatch for ever by a reconciler reading its own
        assumption back as the provider's answer.
        """
        assert _from_subunit(500000, NGN).currency is NGN
        assert _from_subunit(500000, USD).currency is USD

    @pytest.mark.parametrize("amount", ["1", "5000", "5000.55", "1000000"])
    def test_a_round_trip_is_the_identity(self, amount):
        """**The assertion the two directions exist to satisfy together.**

        Every other test in this class pins one half against a literal; this one
        pins them against each other. A pair of conversions could each look right
        against hand-written numbers and still not be inverses - and the place
        that would show up is a deposit the provider settled and this system
        refused, on a difference nobody could see by reading either function.
        """
        assert _from_subunit(_subunit(an_amount(amount)), NGN) == an_amount(amount)


class TestWhereThePayerIsSentBack:
    """The one outbound field that depends on where this installation lives.

    **It is the only thing ``initialize_deposit`` sends that this system chose
    rather than derived**, and that is what makes it worth its own class. Every
    other field in the payload follows from the amount, the reference or the
    payer; this one is an installation fact - its public address - threaded down
    from a setting in ``settings.py`` through a join in ``create_app`` to a
    constructor argument here. Three frames, and the failure if any of them drops
    it is silent: the deposit works perfectly and the payer is simply left on
    Paystack's own page at the end.

    **The interesting half is the absent case, not the present one.** A payload
    with no ``callback_url`` key is what every installation sent before the
    setting existed, and the docstring on ``initialize_deposit`` argues at length
    that ``"callback_url": None`` is a *third* thing - a field whose value is
    nothing - which a provider is free to refuse for reasons it will not explain.
    So the tests below assert the key's *absence* as carefully as they assert its
    value, and the last one asserts the difference between the two payloads is
    exactly one field.
    """

    CALLBACK_URL = "https://budget.example/app/"

    def open_a_collection(self, provider, reference: str):
        provider.initialize_deposit(
            reference=reference, amount=an_amount("5000"), email="payer@localhost"
        )

    def test_an_installation_with_no_address_sends_no_field(self, provider, requesting):
        """The default, which is what a fresh clone has."""
        self.open_a_collection(provider, "ps_ref_callback_1")

        assert "callback_url" not in requesting.call["json"]

    def test_and_that_payload_is_the_one_that_was_always_sent(
        self, provider, requesting
    ):
        """**Written as the whole dict rather than as a missing key**, because
        "nothing changed" is the actual claim. The literal below is the same one
        ``test_it_sends_the_payers_email_and_nothing_else_about_them`` asserts,
        which is the point: two tests, one payload, and adding a field here would
        have to be a change to both.
        """
        self.open_a_collection(provider, "ps_ref_callback_1")

        assert requesting.call["json"] == {
            "email": "payer@localhost",
            "amount": 500000,
            "reference": "ps_ref_callback_1",
            "currency": "NGN",
        }

    def test_a_given_address_is_sent_verbatim(self, requesting):
        """**Verbatim, because a path is not ours to improve.** The adapter is
        handed an address that already carries the landing path, composed by the
        one frame holding both the origin and the route table - see
        ``web.urls.callback_url`` - and an adapter that resolved, joined or
        normalised it would be a second composer free to disagree with the first.
        """
        provider = PaystackPaymentProvider(
            TEST_PAYSTACK_SECRET, callback_url=self.CALLBACK_URL
        )

        self.open_a_collection(provider, "ps_ref_callback_1")

        assert requesting.call["json"]["callback_url"] == self.CALLBACK_URL

    def test_none_is_the_same_as_absent(self, requesting):
        """**The collapsed case, and the one an implementation would get wrong by
        treating ``None`` as a value.** ``callback_url=None`` spelled explicitly
        is how the composition root's own join arrives when the setting is unset -
        ``urls.callback_url(None)`` returns ``None`` - so the two ways of saying
        nowhere have to produce the same payload, and it has to be the one with no
        key in it.
        """
        provider = PaystackPaymentProvider(
            TEST_PAYSTACK_SECRET, callback_url=None
        )

        self.open_a_collection(provider, "ps_ref_callback_1")

        assert "callback_url" not in requesting.call["json"]

    def test_the_address_is_the_only_thing_that_changed(self, requesting):
        """**The assertion that separates "added a field" from "changed a
        payload".**

        The two calls below differ in one constructor argument and must therefore
        differ in one JSON key. Asserted as a difference and not as two literals,
        so a field added to the adapter for an unrelated reason fails here rather
        than being copied into both dicts by whoever was updating the test.

        ``requesting.calls`` is read directly rather than through ``requesting.call``
        - the property that insists on exactly one call, for the reason its own
        docstring gives: this test is deliberately about two.

        **One reference for both calls, and that is what makes it a difference of
        one field.** The two collections below are the *same deposit* sent to two
        installations, so everything derived from the request - the reference, the
        amount, the payer - is identical by construction, and the only key left
        that can differ is the one this test is about. Two references would have
        made the dicts differ in two keys and the assertion would have been
        comparing spelling rather than the setting.
        """
        with_address = PaystackPaymentProvider(
            TEST_PAYSTACK_SECRET, callback_url=self.CALLBACK_URL
        )
        without = PaystackPaymentProvider(TEST_PAYSTACK_SECRET)

        self.open_a_collection(without, "ps_ref_callback_1")
        self.open_a_collection(with_address, "ps_ref_callback_1")

        no_address, address = requesting.calls
        assert address["json"] == {
            **no_address["json"],
            "callback_url": self.CALLBACK_URL,
        }

    def test_a_lookup_sends_no_address_at_all(self, requesting):
        """**Where the payer is sent is a fact about opening a collection and
        nothing else.** ``outcome_for`` shares ``_request`` with the method above,
        so the two are one edit apart from each other - and a reconciler asking
        about a reference is not a moment at which anybody is being sent
        anywhere.

        The body is asserted to be ``None`` rather than searched for a key, which
        is the stronger claim and the one this endpoint actually makes: a lookup
        carries no body at all.
        """
        requesting.response = httpx.Response(200, json=a_charge("success"))
        provider = PaystackPaymentProvider(
            TEST_PAYSTACK_SECRET, callback_url=self.CALLBACK_URL
        )

        provider.outcome_for("ps_ref_callback_1")

        assert requesting.call["method"] == "GET"
        assert requesting.call["json"] is None



class TestInitiatingATransfer:
    def test_it_creates_the_recipient_before_initiating_the_transfer(
        self, provider, monkeypatch
    ):
        calls = []

        responses = [
            {
                "status": True,
                "message": "Transfer recipient created successfully",
                "data": {"recipient_code": "RCP_recipient_123"},
            },
            {
                "status": True,
                "message": "Transfer has been queued",
                "data": {
                    "status": "pending",
                    "reference": "payout_ref_1",
                    "transfer_code": "TRF_transfer_123",
                },
            },
        ]

        def request(method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return httpx.Response(200, json=responses[len(calls) - 1])

        monkeypatch.setattr(
            paystack_payment_provider.httpx,
            "request",
            request,
        )

        destination = Destination(
            kind=DestinationKind.BANK_ACCOUNT,
            identifier="0123456789",
            name="Chinedu Okafor",
            details={"bank_code": "058"},
        )

        intent = provider.initiate_transfer(
            reference="payout_ref_1",
            amount=an_amount("5000"),
            destination=destination,
        )

        assert len(calls) == 2

        assert calls[0]["method"] == "POST"
        assert calls[0]["url"] == f"{BASE_URL}/transferrecipient"
        assert calls[0]["json"] == {
            "type": "nuban",
            "name": "Chinedu Okafor",
            "account_number": "0123456789",
            "bank_code": "058",
            "currency": "NGN",
        }

        assert calls[1]["method"] == "POST"
        assert calls[1]["url"] == f"{BASE_URL}/transfer"
        assert calls[1]["json"] == {
            "source": "balance",
            "amount": 500000,
            "recipient": "RCP_recipient_123",
            "reference": "payout_ref_1",
            "currency": "NGN",
        }

        assert intent.provider_reference == "TRF_transfer_123"


class TestMalformedTransferAnswers:
    def test_a_recipient_answer_without_a_recipient_code_is_rejected(
        self, provider, monkeypatch
    ):
        def request(method, url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "message": "Recipient created",
                    "data": {},
                },
            )

        monkeypatch.setattr(
            paystack_payment_provider.httpx,
            "request",
            request,
        )

        destination = Destination(
            kind=DestinationKind.BANK_ACCOUNT,
            identifier="0123456789",
            name="Chinedu Okafor",
            details={"bank_code": "058"},
        )

        with pytest.raises(InvalidTransferIntentError):
            provider.initiate_transfer(
                reference="payout_ref_1",
                amount=an_amount("5000"),
                destination=destination,
            )
