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

**Four methods, one transport.** The two that call out share ``_request``, so
the failure handling, the bearer token and the timeout are written once - and
each of them names the statuses it will interpret, which is the one thing about a
call that only its caller knows. See ``_request``. The other two cost nothing:
proving a webhook is standard-library HMAC over bytes, and answering what this
account will collect is a field this class was constructed with.

**This module logs, and it is the first in ``app/`` that does.** A real deposit
leaves a trail at the far end - a transaction in the Paystack dashboard with a
reference on it - and until this, nothing on this side produced a line an
operator could put beside it. The rule is narrow on purpose: **the reference and
the outcome, never the payload and never the payer.** Payloads are a third
party's text (see ``_request``), and the payer's address is the one field of
theirs this system holds in the clear. Structured logging, a level policy and
somewhere to ship these lines are checklist item i17 and remain open; what is
here is the front edge of it, configures nothing, and inherits whatever the
process configured - uvicorn in a deployment, the root logger in a test.
"""

import hashlib
import hmac
import logging
from decimal import Decimal
from urllib.parse import quote

import httpx

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.money import Money
from app.domain.payments.exception import (
    InvalidPaymentIntentError,
    InvalidTransferIntentError,
    InvalidProviderAnswerError,
    PayerEmailRefusedError,
    PaymentProviderError,
    PaymentProviderUnavailableError,
)
from app.domain.payments.paymentIntent import PaymentIntent
from app.domain.payments.transferIntent import TransferIntent
from app.domain.payments.paymentProvider import PaymentProvider
from app.domain.payments.providerAnswer import ProviderAnswer
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.payments.providerEvent import ProviderEvent
from app.domain.payments.providerOutcome import ProviderOutcome

logger = logging.getLogger(__name__)

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

#: The currencies this integration is exercised against, and deliberately the
#: only ones it will send by default.
#:
#: Not a limitation of the code - ``Money`` carries its currency and the request
#: below sends the amount's own - but a statement about what has been verified: a
#: provider account is enabled for particular currencies, and sending one the
#: account is not enabled for fails at *their* end with a message that reads like
#: ours is broken. So this is the default for the constructor's ``currencies``,
#: where enabling a second one is a deliberate line at the composition root
#: rather than a value that happened to arrive.
#:
#: **A ``Currency`` here rather than the three-letter string it used to be**, and
#: the change is not cosmetic. The set is now what ``initialize_deposit`` checks
#: against and what ``supported_currencies`` reports, so a caller can be refused
#: *before* a payer is sent anywhere - and a comparison against an enum member is
#: one the type checker can see, where a comparison against a loose string was
#: not. The value sent on the wire is still the three-letter code, read off the
#: ``Money`` being collected rather than off this set.
#:
#: **The set stopped being decorative the day it was read.** This constant used to
#: describe a payload it did not control: ``_subunit(amount)`` derived the number
#: from the amount's currency while the label beside it came from here, so a
#: wallet holding USD posted the *number* one hundred and the *label* NGN. See
#: the port's ``supported_currencies`` and the decision recorded for it.
SUPPORTED_CURRENCIES: frozenset[Currency] = frozenset({Currency.NGN})


class PaystackPaymentProvider(PaymentProvider):
    """The Paystack implementation of the payment port.

    Raises ``PaymentProviderError`` on every failure rather than returning a
    status, because that is the contract the port states: the caller records the
    exception and refuses the request, so the exception *is* the error report.
    There is no partially-successful initialization to model - a collection was
    either opened or it was not.

    Holds a secret key and where a payer comes back to, and nothing else. No
    client, no connection, no session: each call opens what it needs and closes it
    again, the same choice ``SmtpNotificationChannel`` makes and for the same
    reason - a long-lived connection is state that outlives the request, and state
    that outlives the request is what has to be thought about at shutdown. The
    cost is a handshake per call, and this endpoint is human-paced.
    """

    def __init__(
        self,
        secret_key: str,
        timeout: int = DEFAULT_TIMEOUT,
        *,
        currencies: frozenset[Currency] = SUPPORTED_CURRENCIES,
        callback_url: str | None = None,
    ):
        self._secret_key = secret_key
        self._timeout = timeout
        # The currencies this *account* is enabled for, which is why it is a
        # parameter and not just the constant above. ``timeout`` is the precedent:
        # a per-deployment fact that has a sane default and that a test needs to
        # vary. Varying this one is how a test proves the label follows the amount
        # rather than a constant - which is the bug this file used to have, and it
        # is not provable against a provider that only ever collects one currency.
        self._currencies = currencies
        # Where the payer is sent once they have paid, or ``None`` for nowhere in
        # particular - which is what every installation did before the setting
        # existed. ``currencies`` and ``timeout`` are the precedent: a
        # per-deployment fact, keyword-only, with a default that keeps every
        # existing construction working.
        #
        # **It is an absolute address rather than a path**, because Paystack is
        # the one that will send a browser to it: a relative path would be
        # resolved against *their* origin, and the payer would land on
        # paystack.co. So the absolute form is not a preference a caller may get
        # wrong, and it is why this is a wiring concern rather than one this file
        # could fix up - the composition root is the only frame that knows the
        # public origin, and this parameter is how that fact arrives.
        self._callback_url = callback_url

    def supported_currencies(self) -> frozenset[Currency]:
        """What this account will collect in. See the port for the contract.

        Returns the set as given rather than a copy, which is safe for exactly one
        reason: a ``frozenset`` cannot be mutated in place, so a caller holding it
        can add nothing to it. That is the whole of why the port's signature says
        ``frozenset`` instead of ``set``, and it means this method needs no
        defensive line.
        """
        return self._currencies

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

        **Both refusals are interpreted here rather than reported**, and both are
        cases where the status alone is not the answer. A ``401``/``403`` means the
        key is wrong, which only this frame can say because only this frame knows a
        collection was being opened; a ``400`` carrying
        ``INVALID_PAYER_EMAIL_CODE`` means the address will never be billed, which
        earns a better sentence than "the provider refused the call". Everything
        else a provider refuses leaves through ``_refusal_sentence`` with its own
        words attached. That a 400 is *allowed* to reach this method at all is the
        ``answers`` argument in one line: a refusal is the whole of what is
        knowable to the transport, and not the whole of what is knowable here.

        **The currency sent is the amount's own, and that is a repair rather than a
        detail.** This method used to send ``_subunit(amount)`` beside a constant
        ``"currency"``, which is two decisions about one payload: the number came
        from the ``Money`` and the label came from a module constant, and nothing
        connected them. For a wallet holding USD the result was a collection for
        one hundred *naira* filed against a ledger row of one hundred *dollars* -
        and because ``SettlePayment`` compares the two, the row could never
        settle: the payer's money was taken and nothing was ever credited. Sending
        ``amount.currency.value`` makes the disagreement unrepresentable, because
        there is now one object deciding both.

        **A payer is sent back somewhere, when this installation has said where.**
        ``callback_url`` is added to the payload only when one was given at
        construction, and the conditional is the point rather than a guard: an
        installation with no public address must send byte-for-byte the payload it
        sent before this field existed, because ``"callback_url": null`` is a third
        thing - not "nowhere in particular" but "here is a field whose value is
        nothing" - and a provider is free to refuse that for reasons it will not
        explain. So the absent case is an absent key.

        **What this buys is a browser, not a settlement.** Paystack sends the payer
        to this address once they have finished paying; whether the deposit is
        credited is decided by ``/webhooks/paystack`` and by nothing else, so a
        person who lands back here may well see their balance unchanged for a few
        seconds. That is the honest reading of the return and it is why the setting
        is optional: an installation without it loses a courtesy, not a payment.

        **The check above the request is a backstop, not the door.** The door is
        ``InitiateDeposit``, which asks ``supported_currencies`` and refuses before
        a payer is sent anywhere - and that is the refusal a client sees. This one
        is unreachable from that path, and it exists so that a caller which skips
        the door cannot relabel a collection silently, which is precisely how the
        bug above survived: no single frame was wrong, and the two halves were.
        """
        if amount.currency not in self._currencies:
            raise PaymentProviderError(
                f"this account is not enabled to collect {amount.currency.value}; "
                f"it collects {_currency_names(self._currencies)}"
            )

        payload = {
            "email": email,
            "amount": _subunit(amount),
            "reference": reference,
            "currency": amount.currency.value,
        }
        if self._callback_url is not None:
            # Added rather than passed as ``None``. See the docstring: the absent
            # case is an absent key, not an empty value.
            payload["callback_url"] = self._callback_url

        status, response = self._request(
            "POST",
            "/transaction/initialize",
            payload=payload,
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
            # **Two unrelated facts reach this line, and one sentence used to
            # describe both of them.** ``AUTHENTICATION_FAILURES`` is in
            # ``answers``, so a ``401``/``403`` travels past the refusal branch
            # above and arrives here - Paystack answers a rejected key with
            # ``{"status": false, "message": "Invalid key"}`` and no ``data`` at
            # all, which is the common case by a wide margin. The other way in is
            # a ``2xx`` whose body carried no transaction, which is the provider
            # or this adapter failing and says nothing about the key.
            #
            # Both used to leave through one message that opened *"the provider
            # accepted the call"* - false in the first case, where the provider
            # had just refused it - and neither left a log line. **A live run
            # paid for that**: an installation holding a bad key and an
            # installation whose provider answered an empty ``2xx`` produced
            # byte-identical output, no ledger row and no log, and the only way
            # to tell them apart was to stop using this API and point ``curl`` at
            # Paystack instead. The provider had said which it was, and this
            # frame - which held both the status and the provider's own words -
            # was the one place it could not be read.
            #
            # So the status decides the sentence now, and ``_refusal_sentence``
            # still writes the refusal half so the one place a refusal becomes
            # words stays one place.
            words = _provider_words(response)
            logger.warning(
                "the provider answered the initialize call with %s and no "
                "transaction%s",
                status,
                words,
            )
            if status >= 400:
                raise InvalidPaymentIntentError(
                    f"{_refusal_sentence(status, response)}; a rejected secret key "
                    "is the usual reason"
                )
            raise InvalidPaymentIntentError(
                f"the payment provider answered {status} with no transaction in "
                "the body; that is the provider or this adapter failing, and not "
                "a key this installation could correct"
            )

        try:
            authorization_url = data["authorization_url"]
            provider_reference = data["reference"]
        except KeyError as missing:
            raise InvalidPaymentIntentError(
                f"the provider's response has no {missing.args[0]}"
            ) from None

        # The one line a real deposit most needs, and the one this file had no way
        # of producing until now: the reference the payer will be charged under,
        # and the name Paystack will file it under. Those two are the join between
        # this system's ledger and the provider's dashboard, and when a payer
        # rings up about a payment the first question is whether a collection was
        # opened at all - which is a question this line answers without anybody
        # reading a database. At ``info`` because it happens once per deposit
        # rather than once per request, and because a deposit is the event this
        # whole service exists to make happen.
        logger.info(
            "opened a collection under %s (the provider files it as %s)",
            reference,
            provider_reference,
        )

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

        **The currency comes back off the response too**, which is the half of the
        repair ``initialize_deposit`` carries the note for. Settlement compares
        the amount this returns against the ledger row, so a currency taken from a
        constant here would refuse every recovered deposit in any currency but
        that one - reading its own assumption back as the provider's answer. See
        ``_currency_of``.
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
                    amount=_from_subunit(data["amount"], _currency_of(data)),
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
        """One authenticated JSON call, or a payment error.

        Every failure leaves through the same door, and the door is the port's
        contract rather than tidiness: a caller has one thing to catch, and it
        cannot accidentally treat a provider outage as an ordinary result. The
        door is ``PaymentError`` - which is the tree, not one class - and there
        are two rooms behind it. See below.

        **Two failures, and the split between them is a real-money audit's
        doing.** ``PaymentProviderError`` is the provider *refusing*: it read the
        request and said no, the request will be refused again, and the caller
        has something to change. ``PaymentProviderUnavailableError`` is the
        provider being unaskable: a transport failure, a ``5xx``, or a ``200``
        whose body is not JSON - none of which says anything about the request,
        all of which may work in a minute. They were one class, and the cost was
        that a **timeout told a payer their deposit request was malformed** while
        Paystack may have created the collection anyway. The grades follow the
        split: 400 for a refusal, 503 for the rail.

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

        **The status is checked before the body is read**, so that a 500 with an
        HTML error page produces "the provider failed this call with 500" rather
        than a JSON decode error. The status is included in the message because it
        is the part a human acts on. The 5xx branch is tested first, ahead of
        ``answers``, because a status this file was told to interpret is still the
        provider breaking rather than answering something.

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
        that does not reach that branch, and it is a
        ``PaymentProviderUnavailableError`` instead - a body that is not JSON is
        the far end being broken rather than a request being refused, and the
        specific "your key is wrong" sentence is not reachable when there is no
        JSON to read a message out of anyway.

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
            # The line an operator needs most and had none of: which call to the
            # provider stopped working, and how. The path carries the reference on
            # the lookup and nothing on the initialize, which is the join key to
            # Paystack's dashboard either way.
            logger.warning(
                "could not reach the payment provider at %s %s: %s",
                method,
                path,
                type(failure).__name__,
            )
            raise PaymentProviderUnavailableError(
                f"could not reach the payment provider: {type(failure).__name__}"
            ) from failure

        if response.status_code >= 500:
            # Before ``answers`` is consulted, because a status this file was told
            # to interpret is still the provider breaking rather than answering -
            # ``answers`` names statuses that mean something about the thing asked
            # about, and none of them is a 5xx. See the docstring.
            logger.warning(
                "the payment provider failed %s %s with %s",
                method,
                path,
                response.status_code,
            )
            raise PaymentProviderUnavailableError(
                _refusal_sentence(response.status_code, _json_or_none(response))
            )

        if response.status_code >= 400 and response.status_code not in answers:
            logger.warning(
                "the payment provider refused %s %s with %s",
                method,
                path,
                response.status_code,
            )
            raise PaymentProviderError(
                _refusal_sentence(
                    response.status_code, _json_or_none(response)
                )
            )

        try:
            return response.status_code, response.json()
        except ValueError as failure:
            logger.warning(
                "the payment provider answered %s %s with a body that is not JSON",
                method,
                path,
            )
            raise PaymentProviderUnavailableError(
                "the payment provider returned a body that is not JSON"
            ) from failure


    def initiate_transfer(
        self,
        *,
        reference: str,
        amount: Money,
        destination: Destination,
    ) -> TransferIntent:
        """Ask Paystack to initiate an external transfer."""
        recipient_payload = {
            "type": "nuban",
            "name": destination.name,
            "account_number": destination.identifier,
            "bank_code": destination.detail("bank_code"),
            "currency": amount.currency.value,
        }

        _, recipient_response = self._request(
            "POST",
            "/transferrecipient",
            payload=recipient_payload,
            answers=AUTHENTICATION_FAILURES | BAD_REQUEST_STATUSES,
        )

        recipient_data = recipient_response.get("data")
        recipient_code = recipient_data["recipient_code"]

        transfer_payload = {
            "source": "balance",
            "amount": _subunit(amount),
            "recipient": recipient_code,
            "reference": reference,
            "currency": amount.currency.value,
        }

        _, transfer_response = self._request(
            "POST",
            "/transfer",
            payload=transfer_payload,
            answers=AUTHENTICATION_FAILURES | BAD_REQUEST_STATUSES,
        )

        transfer_data = transfer_response.get("data")
        transfer_code = transfer_data["transfer_code"]

        return TransferIntent(provider_reference=transfer_code)


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


def _from_subunit(subunits: int, currency: Currency) -> Money:
    """The inverse of ``_subunit``: subunits back to whole units, as ``Money``.

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

    **The currency is a parameter, and it used to be a constant.** It was read
    from ``SUPPORTED_CURRENCY`` on the argument that a deployment enabled for a
    second currency would then read its answers back in the currency it asked in
    - and that argument was wrong in the one way that matters, because the
    *asking* was done with a hard-coded label too. The two constants agreed with
    each other and neither agreed with the amount, which is the shape of the bug
    ``initialize_deposit`` now carries the note about: a value that is read from
    the same place it is written is a value that cannot notice it is wrong. The
    caller reads this off the response, so the answer comes back in the currency
    the *provider* says the charge was in, which is the only figure settlement
    can honestly compare against a row.

    The subunit factor is not a parameter and must not become one. Kobo-per-naira
    is 100 because Paystack says so for every currency it settles in; a currency
    with a different minor-unit exponent would need this whole pair revisited
    rather than a second argument, and silently passing a different factor here
    would be the same class of mistake in a new place.
    """
    return Money(Decimal(subunits) / 100, currency)


def _currency_of(data: dict) -> Currency:
    """The currency a settled charge was taken in, off the provider's own answer.

    **Read rather than assumed, and the assumption was the bug.** This used to be
    the constant ``SUPPORTED_CURRENCY``, which meant the two halves of a
    settlement were decided in different places: the number came out of the
    response and the label came out of this file. For a charge this installation
    opened in NGN the two agreed by luck; for any other they could not, and the
    disagreement would arrive as ``SettlementOutcome.AMOUNT_DISAGREES`` against a
    row that was in fact perfectly correct. A recovered deposit would have been
    reported as a mismatch for ever by a reconciler that was reading its own
    constant rather than the provider's answer.

    Translated into ``InvalidProviderAnswerError`` rather than left as the
    ``ValueError`` ``Currency`` raises, and the reason is the one this file
    already gives for its other translations: an exception type is part of a
    vocabulary, and a bare ``ValueError`` escaping an adapter would report as an
    unknown server fault. What it means is narrower and more useful - the provider
    answered with something this adapter cannot read.

    The missing case is deliberately not distinguished from an unknown one. A
    settled charge with no currency at all and one quoting a currency this system
    has never heard of are the same fact to a caller: the answer cannot be read,
    and settlement must not guess what it meant.
    """
    currency = data.get("currency")
    if not isinstance(currency, str):
        raise InvalidProviderAnswerError(
            "the provider reported a settled charge with no currency"
        )

    try:
        return Currency(currency)
    except ValueError:
        raise InvalidProviderAnswerError(
            f"the provider reported a charge in {currency!r}, which this system "
            f"does not know"
        ) from None


def _currency_names(currencies: frozenset[Currency]) -> str:
    """A set of currencies as a readable list, for a sentence a person reads.

    Sorted, and that is the whole of why it is a function rather than an f-string
    at the raise site. A ``frozenset`` has no order, so a message built by
    iterating one names the same currencies in a different order on different
    runs - and a sentence that changes while nothing has changed is one a reader
    learns to distrust, which is the last thing a refusal wants. Sorted by the
    currency's own code rather than by insertion, so it does not depend on how
    the set was built either.
    """
    return ", ".join(sorted(currency.value for currency in currencies))
