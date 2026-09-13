"""Paystack: the one adapter that talks to a payment provider.

The interesting thing about this module is how little of it is money. Opening a
collection is one HTTP call with a JSON body and a bearer token; asking what
became of one is the same call with a different method and a reference in the
path; proving a webhook is an HMAC over the bytes that arrived. None of the three
is complicated, and all three are things the rest of the codebase must never
learn - which is the whole reason this file exists behind a port rather than
being written at the call site.

**``httpx`` is the only third-party dependency the calls need, and the signature
needs none.** ``hmac`` and ``hashlib`` are standard library, so verification
could have been written without adding a package - and it is worth noticing that
the *inbound* direction, the one that decides whether money is credited, is the
one with no dependency at all.

**Three methods, one transport.** The two that call out share ``_request``, so
the failure handling, the bearer token and the timeout are written once - and
each of them names the statuses it will interpret, which is the one thing about a
call that only its caller knows. See ``_request``.
"""

import hashlib
import hmac
from decimal import Decimal
from urllib.parse import quote

import httpx

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.payments.exception import (
    InvalidPaymentIntentError,
    InvalidProviderAnswerError,
    PayerEmailRefusedError,
    PaymentProviderError,
)
from app.domain.payments.paymentIntent import PaymentIntent
from app.domain.payments.paymentProvider import PaymentProvider
from app.domain.payments.providerAnswer import ProviderAnswer
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome

#: Where Paystack lives. A module constant rather than a setting, because there
#: is no second value it could sensibly take in production - the sandbox and the
#: live service share this host and are told apart by which secret key is
#: presented. A configurable base URL would be a setting whose only non-default
#: values are a mistake or a mock, and a mock is what the port's injection seam
#: is for.
BASE_URL = "https://api.paystack.co"

#: How long any single call may take, in seconds.
#:
#: **Not optional, and not a tuning knob** - the same argument
#: ``SmtpNotificationChannel.DEFAULT_TIMEOUT`` makes, and it is worth repeating
#: because the consequence here is a user watching a spinner rather than a tick
#: hanging. Without it a provider that accepts the connection and then says
#: nothing leaves the request blocked until the operating system's TCP timeout.
DEFAULT_TIMEOUT = 15

#: The statuses that mean "this installation's key is wrong" rather than "the
#: provider refused this call".
#:
#: Paystack answers a bad secret key with ``401`` and a body carrying no ``data``
#: at all, and that is a *configuration* error rather than a provider failure -
#: the two call for different answers. A refusal is a fact to report as it
#: stands; a bad key has an answer that only the caller can give, because the
#: caller is the one that knows what it was trying to create. So these statuses
#: are passed through to it rather than swallowed here, and
#: ``initialize_deposit``'s missing-``data`` branch is that answer.
AUTHENTICATION_FAILURES = frozenset({401, 403})

#: The statuses that answer "what happened to this reference?" with "there is no
#: such transaction".
#:
#: A 404 from the initialize endpoint would be a broken deployment; from the
#: verify endpoint it is Paystack saying it has never heard of the reference -
#: which is an *answer*, and the loudest one a reconciliation run can produce.
#: The same number is a refusal or a fact depending on the question, which is why
#: the caller names it rather than the transport deciding.
NO_SUCH_REFERENCE_FAILURES = frozenset({404})

#: Everything a lookup will interpret rather than refuse. The authentication
#: failures are in here for ``initialize_deposit``'s reason: the sentence a wrong
#: key deserves can only be written by the caller, which is the only frame that
#: knows what the call was for.
LOOKUP_FAILURES = NO_SUCH_REFERENCE_FAILURES | AUTHENTICATION_FAILURES

#: The statuses ``initialize_deposit`` reads for itself.
#:
#: A 400 is a refusal everywhere else in this file, and the distinction is exactly
#: the one ``answers`` exists to draw: for a call that *creates* something, a
#: refusal is the whole of what the transport can know, so it reports the status
#: and stops. Paystack's 400 on ``/transaction/initialize`` is not uniform, though
#: - it carries a ``code`` saying which input it objected to - and one of those
#: codes has a better answer than "the provider refused the call": the address
#: this account would be billed under is not one they will take. Telling that
#: apart from a malformed reference takes the caller, which is the only frame that
#: knows an address was sent at all.
BAD_REQUEST_STATUSES = frozenset({400})

#: Paystack's code for "that is not an address I will bill".
#:
#: Learned from a live call rather than from documentation, and quoted from it:
#: ``live@localhost`` produced ``{"message": "Invalid Email Address Passed",
#: "code": "invalid_email_address"}`` while the same call with a real domain
#: returned a checkout URL.
#:
#: A **code** is matched rather than a message, and the difference matters. A code
#: is the part of Paystack's error contract that is machine-readable and stable; a
#: message is prose they are free to reword, so matching on it would pin this
#: adapter to the wire's vocabulary in the one place its own rule says the
#: translation runs the other way.
INVALID_PAYER_EMAIL_CODE = "invalid_email_address"

#: Paystack's name for the one transaction status that means money has arrived.
#:
#: One value rather than a set, because there is one: everything else in
#: ``NOT_SETTLED_CHARGE_STATUSES`` below is a charge that has not been paid.
SETTLED_CHARGE_STATUS = "success"

#: The statuses a charge can be in when no money has arrived under it.
#:
#: Written out, rather than treated as "anything that is not ``success``", so
#: that a status Paystack adds later raises ``InvalidProviderAnswerError`` and is
#: reported, instead of being silently filed as "not settled yet" and leaving a
#: row PENDING for ever with no trace of why.
#:
#: ``failed`` is here rather than treated as a settlement in its own right, and
#: that is the report-only policy stated in the adapter: a charge that failed
#: means nothing arrived, which is the same operational fact as a payer who never
#: finished - and the reconciler fails no rows, because a row failed too early
#: could never be credited by a late success. ``reversed`` is here too, and it is
#: the uncomfortable one: money *did* arrive and was sent back, so this reports
#: "not settled" for a deposit that was reversed. Modelling a chargeback needs a
#: fifth ``ProviderEvent`` and a decision about what reversing a deposit means;
#: until then the row stays PENDING and the README carries it as an open item,
#: which is the honest answer rather than a wrong one.
NOT_SETTLED_CHARGE_STATUSES = frozenset(
    {
        "abandoned",
        "failed",
        "ongoing",
        "pending",
        "processing",
        "queued",
        "reversed",
    }
)

#: The one currency this integration is exercised against, and deliberately the
#: only one it will send.
#:
#: Not a limitation of the code - ``Money`` carries its currency and the request
#: below forwards whatever it is given. It is a statement about what has been
#: verified: a provider account is enabled for particular currencies, and sending
#: one the account is not enabled for fails at *their* end with a message that
#: reads like ours is broken. Kept as a constant so that enabling a second one is
#: a deliberate line rather than a value that happened to arrive.
SUPPORTED_CURRENCY = "NGN"


class PaystackPaymentProvider(PaymentProvider):
    """The Paystack implementation of the payment port.

    Raises ``PaymentProviderError`` on every failure rather than returning a
    status, because that is the contract the port states: the caller records the
    exception and refuses the request, so the exception *is* the error report.
    There is no partially-successful initialization to model - a collection was
    either opened or it was not.

    Holds a secret key and nothing else. No client, no connection, no session:
    each call opens what it needs and closes it again, the same choice
    ``SmtpNotificationChannel`` makes and for the same reason - a long-lived
    connection is state that outlives the request, and state that outlives the
    request is what has to be thought about at shutdown. The cost is a handshake
    per call, and this endpoint is human-paced.
    """

    def __init__(self, secret_key: str, timeout: int = DEFAULT_TIMEOUT):
        self._secret_key = secret_key
        self._timeout = timeout

    def initialize_deposit(
        self, *, reference: str, amount: Money, email: str
    ) -> PaymentIntent:
        """Open a collection, and return where the payer is sent.

        **The amount goes over the wire in the subunit**, which is the one piece
        of arithmetic in this file and the one most worth stating. Paystack takes
        an integer number of kobo, not a decimal number of naira, and a factor of
        a hundred applied in the wrong direction is a deposit of 1 NGN where
        10,000 was meant. ``Money`` guarantees at most two decimal places and
        stores a ``Decimal``, so the multiplication is exact and the conversion to
        ``int`` cannot round - there is no float anywhere on this path, which is
        the same reason ``Money`` refuses them.

        **Two refusals are interpreted here rather than reported**, and both are
        cases where the status alone is not the answer. A ``401``/``403`` means the
        key is wrong, which only this frame can say because only this frame knows a
        collection was being opened; a ``400`` carrying
        ``INVALID_PAYER_EMAIL_CODE`` means the address will never be billed, which
        earns a better sentence than "the provider refused the call". Everything
        else a provider refuses leaves through ``_refusal_sentence`` with its own
        words attached. That a 400 is *allowed* to reach this method at all is the
        ``answers`` argument in one line: a refusal is the whole of what is
        knowable to the transport, and not the whole of what is knowable here.
        """
        status, response = self._request(
            "POST",
            "/transaction/initialize",
            payload={
                "email": email,
                "amount": _subunit(amount),
                "reference": reference,
                "currency": SUPPORTED_CURRENCY,
            },
            answers=AUTHENTICATION_FAILURES | BAD_REQUEST_STATUSES,
        )

        if (
            status >= 400
            and _provider_code(response) == INVALID_PAYER_EMAIL_CODE
        ):
            # Translated rather than reported, and it is the one place this adapter
            # turns a provider's refusal into a domain refusal of its own naming -
            # which is what the vocabulary rule asks of an adapter, and the same
            # translation the lookup does for a status. The first clause is the one
            # ``app.domain.payments.payerEmail`` writes locally, so the courtesy
            # check and the authority are indistinguishable to a client.
            raise PayerEmailRefusedError(
                f"{email!r} cannot be used as a payer address; the payment "
                f"provider will not bill it"
            )

        if status >= 400 and status not in AUTHENTICATION_FAILURES:
            # A 400 this call named as interpretable and did not recognise. It goes
            # out with the provider's own words, which is what a live run bought:
            # the bug that started this was ``invalid_character_in_reference``, and
            # it arrived as a bare "refused the call with 400" - a sentence that
            # sent the reader looking at this system rather than at the reference.
            raise PaymentProviderError(_refusal_sentence(status, response))

        data = response.get("data")
        if not isinstance(data, dict):
            # Reached on a wrong secret key, which is the common case by a wide
            # margin: Paystack answers 401 with ``{"status": false, "message":
            # "Invalid key"}`` and no ``data`` at all. Naming it here is the
            # difference between a legible configuration error and a ``KeyError``
            # three frames deeper with nothing about the provider in it.
            raise InvalidPaymentIntentError(
                "the provider accepted the call but returned no transaction; "
                "the secret key is the usual reason"
            )

        try:
            authorization_url = data["authorization_url"]
            provider_reference = data["reference"]
        except KeyError as missing:
            raise InvalidPaymentIntentError(
                f"the provider's response has no {missing.args[0]}"
            ) from None

        return PaymentIntent(
            authorization_url=authorization_url,
            provider_reference=provider_reference,
        )

    def verify_signature(self, body: bytes, signature: str | None) -> bool:
        """Whether ``body`` was signed with this installation's secret key.

        **``body`` is the bytes that arrived, and that is not a detail.** A
        signature is over a byte string; verifying one against a re-serialized
        model verifies a *different* byte string, and the mismatch surfaces as a
        wrong key rather than as what it is. So the raw request body is carried
        up from the socket untouched - see the webhook route, where the reason it
        is not parsed first is written out.

        ``hmac.compare_digest`` rather than ``==``: string comparison stops at the
        first differing byte, so its running time leaks how much of a guessed
        signature was right. That is a real attack on an HMAC - it turns forging
        one from 2^512 work into 512 tries of 256 attempts - and the standard
        library has had the constant-time version since Python 3.3. The suite
        already uses it for the same reason in ``tests/conftest.py``.

        A missing signature is ``False`` rather than an error. "The header was not
        there" and "the header was wrong" are the same answer to this question,
        and the caller turns both into the same refusal - so branching on the
        difference here would be a distinction with no consumer.
        """
        if not signature:
            return False

        expected = hmac.new(
            self._secret_key.encode("utf-8"), body, hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def outcome_for(self, reference: str) -> ProviderAnswer:
        """Ask Paystack what became of a collection; see the port for the shape.

        **The reference is quoted into the path, and it is not merely tidy.** A
        reference is a string this system builds from a wallet id and a
        client-supplied key, so it is the one part of this URL that a caller has
        any influence over; unescaped, a ``/`` in it would add a path segment and
        a ``?`` would start a query string, turning a lookup about one transaction
        into a request about another. ``safe=""`` escapes every reserved
        character, including the ``:`` this system's own references are built
        with.

        **Every status Paystack documents maps to one of three answers**, and the
        mapping is written out rather than reduced to "success or not" because a
        status this adapter does not know is a vocabulary change at the far end -
        decision 6's rule, which asks for a refusal at the edge rather than a value
        that flows inward and is dropped in silence. An unrecognised status would
        otherwise be reported as "nothing has settled yet", which is a row that
        stays PENDING for ever with nobody ever told why.

        **The reference that travels into the outcome is the one asked about**,
        not the ``reference`` field of the response. They are the same string in
        every real case - ``initialize_deposit`` records the provider's own naming
        and this asks under exactly that name - and taking the response's copy
        would mean a provider could re-point a settlement at a ledger row other
        than the one the question was about. The lookup key is the fact this side
        holds; the response is what it answers *with*.
        """
        status, body = self._request(
            "GET",
            f"/transaction/verify/{quote(reference, safe='')}",
            answers=LOOKUP_FAILURES,
        )

        if status in NO_SUCH_REFERENCE_FAILURES:
            return ProviderAnswer(ProviderAnswerStatus.NO_SUCH_REFERENCE)

        data = body.get("data")
        if not isinstance(data, dict):
            # The same branch as ``initialize_deposit``'s and for the same
            # reason: a wrong secret key answers 401 with a message and no
            # ``data``, so this is where that becomes a sentence naming the key
            # rather than a ``KeyError`` further down.
            raise InvalidProviderAnswerError(
                "the provider answered the lookup with no transaction; "
                "the secret key is the usual reason"
            )

        charge = data.get("status")

        if charge == SETTLED_CHARGE_STATUS:
            return ProviderAnswer(
                ProviderAnswerStatus.SETTLED,
                ProviderOutcome(
                    event=ProviderEvent.CHARGE_SUCCEEDED,
                    reference=reference,
                    amount=_from_subunit(data["amount"]),
                ),
            )

        if charge in NOT_SETTLED_CHARGE_STATUSES:
            return ProviderAnswer(ProviderAnswerStatus.NOT_SETTLED)

        raise InvalidProviderAnswerError(
            f"the provider reported a charge of {charge!r}, which this adapter "
            f"does not know"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        answers: frozenset[int] = frozenset(),
    ) -> tuple[int, dict]:
        """One authenticated JSON call, or a ``PaymentProviderError``.

        Every failure leaves through the same door, and the door is the port's
        contract rather than tidiness: a caller has one thing to catch, and it
        cannot accidentally treat a provider outage as an ordinary result.

        **``answers`` is the statuses this call will interpret**, and it is a
        parameter because only the caller knows which those are. A status is a
        refusal when it means "I will not do this" and an answer when it means
        something about the thing asked about, and the same number is one or the
        other depending on the question: ``404`` from ``/transaction/initialize``
        would be a broken endpoint, while ``404`` from
        ``/transaction/verify/:reference`` is the provider stating it has never
        heard of that reference. That is decision 137's lesson recurring one
        method over in this same file - a refusal is the whole of what is
        knowable for a call that *creates* something, and for a call that *asks
        about* something it is often the answer itself - so the transport hands
        those statuses back instead of eating them. The status travels with the
        body so the caller has both.

        **``raise_for_status`` before the body is read**, so that a 500 with an
        HTML error page produces "the provider rejected this call" rather than a
        JSON decode error. The status is included in the message because it is
        the part a human acts on.

        **The body is still not carried, and what is carried instead is narrower
        than it looks.** A response body is a third party's text and may quote the
        request back - including the secret key, if it decides to echo headers - so
        quoting one into an error stays refused. ``_provider_words`` takes instead
        the two fields Paystack documents as its error contract, ``message`` and
        ``code``: a sentence written for a human and a stable identifier, neither
        of which is echoed request state. That narrowing is a live run's doing. The
        deposit bug that started all this was answered with
        ``invalid_character_in_reference``, and this method reported only "refused
        the call with 400" - which cost two round trips to a bare ``curl`` to
        rediscover something the provider had already said. Carrying the *code* is
        also what lets ``initialize_deposit`` translate one refusal without
        matching on prose. If a provider is ever observed quoting request state
        inside ``message``, this is the line to revisit.

        **A 401 is not this function's refusal to make.** It is the one status
        where the useful sentence cannot be written here: ``_request`` knows a
        call was rejected, and only its caller knows *what the call was for* - so
        the answer a wrong key deserves ("this deployment's key is wrong") is the
        caller's to give, and naming it in ``answers`` is what lets the response
        through for it to give. Everything else a provider refuses is reported
        here, where the status code is the whole of what is knowable.

        A wrong key that answers with something other than JSON is the one case
        that does not reach that branch, and it is a ``PaymentProviderError``
        instead - which is not a worse answer, only a less specific one, and it
        is still a refusal rather than a crash.

        No retry, and that is a decision rather than an omission. The reference
        this call carries is the provider's own idempotency key, so a retry here
        would be safe - but it would also be a retry the caller did not ask for
        and cannot see, on an endpoint whose entire job is to create something.
        When a retry is the right answer, it belongs one level up where somebody
        can decide whether the thing that failed is worth trying again. A lookup
        is a different case - it creates nothing, so a retry would be harmless -
        and it still does not belong here, for the reason reconciliation gives:
        the next run *is* the retry, and it is a run somebody can watch.
        """
        try:
            response = httpx.request(
                method,
                f"{BASE_URL}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as failure:
            raise PaymentProviderError(
                f"could not reach the payment provider: {type(failure).__name__}"
            ) from failure

        if response.status_code >= 400 and response.status_code not in answers:
            raise PaymentProviderError(
                _refusal_sentence(
                    response.status_code, _json_or_none(response)
                )
            )

        try:
            return response.status_code, response.json()
        except ValueError as failure:
            raise PaymentProviderError(
                "the payment provider returned a body that is not JSON"
            ) from failure


def _json_or_none(response: httpx.Response) -> object:
    """The response's JSON body, or ``None`` when there is not a usable one.

    Deliberately quiet. This runs on the path that is already refusing a call, and
    a diagnostic that can itself raise is worse than no diagnostic: a body that is
    absent, HTML, or not an object all come back as ``None``, and
    ``_provider_words`` then contributes nothing.
    """
    try:
        return response.json()
    except ValueError:
        return None


def _provider_words(body: object) -> str:
    """The provider's own ``message`` and ``code``, or nothing at all.

    The narrow half of the amendment ``_request``'s docstring records: two named
    fields of a documented error contract, extracted rather than quoted. Returns a
    suffix rather than a finished sentence, so the one place a refusal is put into
    words stays the one place.
    """
    if not isinstance(body, dict):
        return ""

    message = body.get("message")
    if not isinstance(message, str):
        return ""

    code = body.get("code")
    return f": {message}" + (f" ({code})" if isinstance(code, str) else "")


def _provider_code(body: object) -> str | None:
    """The provider's ``code``, when the body is an object carrying a string one.

    ``_provider_words``'s twin, and the pair exists for the same reason: these are
    the two fields of Paystack's error contract that are read anywhere in this file,
    and each has exactly one reader. ``_provider_words`` formats them for a person;
    this one is what a caller *matches* on, which is the half that has to be stable
    - so ``initialize_deposit`` compares a code rather than a sentence, and is not
    pinned to Paystack's prose.

    Quiet for the same reason its twin is: a body that is absent, HTML, a list, or
    an object whose ``code`` is not a string contributes ``None`` rather than
    raising. A diagnostic that can itself fail, on a path that exists to report a
    failure, is worse than no diagnostic.
    """
    if not isinstance(body, dict):
        return None

    code = body.get("code")
    return code if isinstance(code, str) else None


def _refusal_sentence(status: int, body: object) -> str:
    """The one place a refused call becomes a sentence.

    A named function rather than the same f-string at each raise site, because
    there are two of them - the transport for a status nobody claimed, and
    ``initialize_deposit`` for the 400 it asked to interpret - and this codebase
    has already paid for one rule with two homes. Two copies of a reference string
    disagreed with each other, and the copy that reached the wire was the wrong
    one.
    """
    return f"the payment provider refused the call with {status}{_provider_words(body)}"


def _subunit(amount: Money) -> int:
    """An amount in the provider's smallest unit - kobo, for NGN.

    A plain function rather than a method, so it can be tested without a provider
    and so that the one piece of arithmetic in this file has a name. Multiplying a
    ``Decimal`` by 100 is exact; ``int`` on the result cannot round because there
    was never a fractional part to lose, which is a property of ``Money`` rather
    than of this function.
    """
    return int(amount.amount * 100)


def _from_subunit(subunits: int) -> Money:
    """The inverse of ``_subunit``: kobo back to naira, as ``Money``.

    The direction a lookup needs and the direction that did not exist until
    reconciliation did. A verify response carries the amount in kobo like every
    other Paystack payload, and settlement compares what the provider says
    against what the ledger row asked for - so getting this wrong by a factor of
    a hundred would not merely misreport a figure, it would make every recovered
    deposit *disagree* with its own row and be refused. That is a failure that
    looks like a data problem rather than an arithmetic one, which is why the
    inverse is a named function tested beside its sibling rather than arithmetic
    written inline at the call site.

    Dividing an integer-valued ``Decimal`` by 100 is exact - the same argument
    ``_subunit`` makes in the other direction - and ``Money`` refuses anything
    with more than two decimal places, so a provider that sent a fractional kobo
    would raise here rather than round.

    The currency is ``SUPPORTED_CURRENCY`` looked up rather than a second literal
    ``Currency.NGN``, so that a deployment enabled for a second currency changes
    one constant and reads its own answers back in the currency it asked in.
    """
    return Money(Decimal(subunits) / 100, Currency(SUPPORTED_CURRENCY))
