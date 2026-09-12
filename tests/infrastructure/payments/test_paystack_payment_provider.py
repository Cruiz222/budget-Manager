"""The Paystack adapter: two calls out, and one thing checked coming in.

Two halves, and they are tested differently because they *are* different. The
outbound half is JSON over HTTP and is tested against a recording double, the way
``SmtpNotificationChannel`` is - what matters is not that HTTP works but that the
right method, the right URL, the right bearer token and the right *number* go
out. Verifying is pure HMAC over bytes with no I/O anywhere in it, so it is tested
against the real thing with nothing replaced at all.

**The outbound half is two methods and one double, and the double knows it.**
Opening a collection and looking one up share ``_request``, so they share the
recorder too - which is why it records the verb. The difference between the two
calls is mostly *that* difference: the same host, the same bearer token, the same
JSON handling, and a POST that creates a charge where a GET asks about one. A
double built for the first would have let the second leave as a POST and never
noticed.

**The recording double is here rather than in ``tests/conftest.py``**, and that
is the opposite of where ``FakePaymentProvider`` lives. The distinction is who
needs it: the fake provider is used by the API suite, which must never open a
socket, and this double is used by one file that is specifically about what goes
on the wire. Putting it in the shared place would invite a second module to
depend on the shape of an HTTP call it does not make.

The one thing neither half of this file does is reach the network. There is no
skip marker, no ``pytest.mark.network``, and no test that only runs when a key is
set - which is the property ``requirements.txt``'s comment claims for the
inbound half and the injection seam claims for the outbound one.
"""

from decimal import Decimal

import httpx
import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.payments.exception import (
    InvalidPaymentIntentError,
    InvalidProviderAnswerError,
    PaymentProviderError,
)
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.infrastructure.payments import paystack_payment_provider
from app.infrastructure.payments.paystack_payment_provider import (
    BASE_URL,
    DEFAULT_TIMEOUT,
    SUPPORTED_CURRENCY,
    PaystackPaymentProvider,
    _from_subunit,
    _subunit,
)
from tests.conftest import TEST_PAYSTACK_SECRET

NGN = Currency.NGN

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


def a_charge(status: str, amount: int = 500000) -> dict:
    """What ``/transaction/verify/:reference`` answers, in Paystack's own words.

    A builder rather than a constant because these tests are about *statuses* -
    one settled, several not - and the only thing that changes between them is
    the one word. The amount defaults to the half-million kobo that five thousand
    naira converts to, so a test can read ``amount=…`` and know it is the one
    field it is varying.

    ``amount`` is kobo, as it is on every Paystack payload, and it is here in
    kobo deliberately: a fixture holding naira would hide exactly the factor of a
    hundred that ``_from_subunit`` exists to undo.
    """
    return {
        "status": True,
        "message": "Verification successful",
        "data": {
            "status": status,
            "reference": "ps_ref_1",
            "amount": amount,
            "currency": SUPPORTED_CURRENCY,
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
            "currency": SUPPORTED_CURRENCY,
        }

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
    outage as an ordinary result. These four are the ways a call fails, and the
    class they arrive as is the assertion in each one.
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

    def test_a_response_missing_the_url_is_refused(self, provider, requesting):
        requesting.response = httpx.Response(
            200, json={"status": True, "data": {"reference": "ps_ref_1"}}
        )

        with pytest.raises(InvalidPaymentIntentError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "authorization_url" in str(refused.value)

    def test_a_refused_status_is_a_provider_error(self, provider, requesting):
        """A 400 or a 500 from the far end, and the two are one answer here.

        The status is in the message because it is what a human acts on; the
        response *body* deliberately is not, because it is a third party's words
        and may quote the request back - including the secret key, if it decides
        to echo headers.
        """
        requesting.response = httpx.Response(500, text="<html>oops</html>")

        with pytest.raises(PaymentProviderError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "500" in str(refused.value)

    def test_an_unreachable_provider_is_a_provider_error(self, provider, requesting):
        """The socket-level failure, kept distinct from a refusal by nobody.

        ``httpx``'s own exception does not escape: a caller that had to catch
        ``httpx.ConnectError`` would be a caller that knows what library this
        adapter uses, which is the thing the port exists to prevent.
        """
        requesting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(PaymentProviderError) as refused:
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert "ConnectError" in str(refused.value)

    def test_a_body_that_is_not_json_is_a_provider_error(self, provider, requesting):
        """A 200 carrying an HTML error page, which is what a proxy in the way
        produces - and ``json.loads`` would raise a ``ValueError`` at the caller."""
        requesting.response = httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(PaymentProviderError):
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

        with pytest.raises(PaymentProviderError):
            provider.initialize_deposit(
                reference="ps_ref_1", amount=an_amount("5000"), email="p@localhost"
            )

        assert len(requesting.calls) == 1


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

    def test_a_body_that_is_not_json_is_a_provider_error(self, provider, requesting):
        requesting.response = httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(PaymentProviderError):
            provider.outcome_for("ps_ref_1")

    def test_a_refused_status_is_a_provider_error(self, provider, requesting):
        """A 500 is nobody's answer, so it leaves through the one door."""
        requesting.response = httpx.Response(500, text="<html>oops</html>")

        with pytest.raises(PaymentProviderError) as refused:
            provider.outcome_for("ps_ref_1")

        assert "500" in str(refused.value)

    def test_an_unreachable_provider_is_a_provider_error(self, provider, requesting):
        requesting.raises = httpx.ConnectError("connection refused")

        with pytest.raises(PaymentProviderError) as refused:
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
        assert _from_subunit(subunits) == an_amount(amount)

    def test_the_currency_is_the_one_it_asked_in(self):
        """Looked up from ``SUPPORTED_CURRENCY`` rather than written as a second
        literal, so a deployment enabled for a second currency changes one
        constant and reads its answers back in the currency it asked in."""
        assert _from_subunit(500000).currency is Currency(SUPPORTED_CURRENCY)

    @pytest.mark.parametrize("amount", ["1", "5000", "5000.55", "1000000"])
    def test_a_round_trip_is_the_identity(self, amount):
        """**The assertion the two directions exist to satisfy together.**

        Every other test in this class pins one half against a literal; this one
        pins them against each other. A pair of conversions could each look right
        against hand-written numbers and still not be inverses - and the place
        that would show up is a deposit the provider settled and this system
        refused, on a difference nobody could see by reading either function.
        """
        assert _from_subunit(_subunit(an_amount(amount))) == an_amount(amount)
