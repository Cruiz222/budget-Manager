"""The webhook, over the wire: the one route in this API nobody signs in to.

**This is the file the feature lives or dies in**, and the claim it has to make
is narrower than "settlement works" - ``test_settle_payment.py`` already covers
that. What is only true here is the *wire*: that the bytes which arrived are the
bytes that were signed, that a request which cannot be shown to have come from
the provider is refused before a single field of it is read, and that every
honest answer a provider can be given is a 200 it will not retry.

Three things about how this file is built are worth stating, because each is a
decision rather than a style:

**The signing rule is written once, in ``tests/conftest.py``, and these tests are
on the other side of it.** ``sign_webhook`` builds a request a real verifier will
accept; nothing here asks it whether a signature is *good*, because that is the
adapter's question and asking it of the test's own helper would be asking the
thing under test. The tampering test below is the sharpest consequence: it signs
one body, sends a different one, and lets the real HMAC path say no.

**The deposit is opened through the API rather than seeded into the ledger.** A
webhook names the provider reference this system chose, so a test that wanted to
seed one would have to know how that reference is constructed - and the reason
the value is asserted from the response instead is that reconstructing it would
be the test agreeing with the implementation about the one thing the provider is
being trusted to echo back.

**The transfer cases do seed the ledger**, because a payout's reference is minted
by a different path that is not what this file is about - and the seed uses the
wallet's own methods (``apply_deposit`` then ``withdraw``) rather than writing a
balance directly, so the state a test starts from is a state the domain would
actually produce: debited, row PENDING, which is what "held" means.
"""

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.application.payments.settledPayment import SettlementOutcome
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from tests.conftest import TEST_PAYSTACK_SECRET

URL = "/webhooks/paystack"

#: Paystack's header, spelled the way the wire spells it. Written out rather
#: than imported from the route, for the reason ``as_user`` spells out
#: ``Authorization``: a client knows the scheme from the protocol, and a test
#: that read it from the implementation would pass even if the implementation
#: had the spelling wrong.
HEADER = "x-paystack-signature"

#: The three event names this file posts, and the two that are always absent
#: from a deposit's story. Kept as constants so a typo is a ``NameError`` rather
#: than a test that quietly asserts the ``event_ignored`` path instead of the one
#: it meant to.
CHARGE = "charge.success"
TRANSFER_SUCCEEDED = "transfer.success"
TRANSFER_FAILED = "transfer.failed"
TRANSFER_REVERSED = "transfer.reversed"

NGN = Currency.NGN

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)

#: What every transfer test is about, written once in each of the two
#: vocabularies it has to be written in at once.
#:
#: A payout's amount is recorded in naira and comes back off the wire in kobo,
#: and ``SettlePayment`` compares the two before it does anything else - so a
#: test whose two numbers drifted apart would be answered ``amount_disagrees``.
#: That is a real refusal this file tests for deliberately, two classes up, and
#: arriving at it by accident here would look like a passing assertion about a
#: transfer while actually being an assertion about the mismatch rule. The seed
#: reads the first and the events read the second, which is why both are named
#: rather than one being derived from the other in a helper.
PAYOUT = "2500.00"
PAYOUT_KOBO = 250000

#: What the wallet holds before the payout is requested. The balance each
#: transfer test asserts is either this or this minus ``PAYOUT``.
FUNDED = "10000.00"


def an_event(name, reference, amount=500000, currency="NGN"):
    """A Paystack event body, shaped the way Paystack shapes one.

    ``amount`` defaults to five thousand naira expressed the way the wire
    expresses it - an integer number of kobo - because the conversion is half of
    what the deposit test is checking. Writing ``5000`` here would be the bug the
    test exists to catch, wearing the costume of a passing test.
    """
    return {
        "event": name,
        "data": {"reference": reference, "amount": amount, "currency": currency},
    }


@pytest.fixture
def post_event(client, sign_webhook):
    """Send an event to the webhook, signed unless told otherwise.

        post_event(an_event(CHARGE, ref))                    # what Paystack sends
        post_event(body, secret="sk_test_other")             # signed by nobody
        post_event(body, send_header=False)                  # no signature at all
        post_event(altered, signed_body=original)            # altered after signing

    ``payload`` may be ``bytes`` when a test needs a body that is not JSON at
    all; anything else is serialized first. The dials are keywords because each
    one is a different failure being provoked, and a positional argument would
    make ``post_event(body, False)`` read as "sign it with False".
    """

    def _post(
        payload,
        *,
        secret=TEST_PAYSTACK_SECRET,
        send_header=True,
        signed_body=None,
        signature=None,
    ):
        body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )
        headers = {}
        if send_header:
            headers[HEADER] = (
                sign_webhook(
                    body if signed_body is None else signed_body,
                    secret,
                )
                if signature is None
                else signature
            )
        return client.post(URL, content=body, headers=headers)

    return _post


@pytest.fixture
def a_deposit(client, as_user, open_wallet):
    """Open a wallet and a collection against it, and hand back all three facts.

    Returns ``(headers, wallet_id, intent)`` where ``intent`` is the route's own
    response - so the reference a webhook must name is read from what the client
    was told, not reconstructed from the wallet id and the key it sent.

    That is not fastidiousness. ``Transactions.provider_reference`` exists as a
    column separate from ``internal_reference`` precisely because a provider may
    report a movement under a name this system did not choose, and a test that
    rebuilt the name from the key would pass on the day those two columns
    diverged - which is the day this test would need to fail.
    """

    def _open(amount="5000.00", ref="dep-1", email=None):
        headers = as_user() if email is None else as_user(email)
        wallet_id = open_wallet(headers)
        response = client.post(
            f"/wallets/{wallet_id}/deposits",
            json={"amount": amount, "ref": ref},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return headers, wallet_id, response.json()

    return _open


@pytest.fixture
def a_pending_payout(client, app, as_user, open_wallet):
    """A wallet with a payout already requested: debited, row PENDING.

    Returns ``(headers, wallet_id, reference)``. The state is built with the
    wallet's own methods - fund it, then ``withdraw`` - so what a test starts
    from is what a real requested payout leaves behind rather than a balance
    written into a column. The row's status is a dial because a reversal is only
    meaningful against a row that *succeeded*, and that is the difference
    ``ProviderEvent.TRANSFER_REVERSED`` exists to assert.

    Seeded rather than driven through the API because a payout is now a two-step
    confirmation, and the three steps that would take are the confirmation
    feature's subject rather than this file's. What this file needs is a PENDING
    transfer and a reference the provider would know, and it says so here.
    """

    def _seed(amount=PAYOUT, reference="ps_payout_1", funded=FUNDED,
              status=TransactionStatus.PENDING):
        headers = as_user()
        wallet_id = open_wallet(headers)
        owner = UUID(
            client.get(f"/wallets/{wallet_id}", headers=headers).json()["user_id"]
        )
        money = Money(Decimal(amount), NGN)

        uow = app.state.unit_of_work_factory.start()
        try:
            wallet = uow.wallets.get_owned(UUID(wallet_id), owner)
            wallet.apply_deposit(Money(Decimal(funded), NGN))
            wallet.withdraw(money)
            uow.wallets.save(wallet)
            uow.transactions.save(
                Transaction(
                    wallet_id=UUID(wallet_id),
                    type=TransactionType.PAYOUT,
                    amount=money,
                    internal_reference=f"{wallet_id}:{reference}",
                    provider_reference=reference,
                    destination=DESTINATION,
                    status=status,
                    # A finished row must carry its completion, which the
                    # aggregate enforces in the constructor - so a seed that
                    # forgot would fail here rather than three assertions later.
                    completed_at=(
                        None
                        if status is TransactionStatus.PENDING
                        else datetime(2026, 1, 2)
                    ),
                )
            )
            uow.commit()
        except BaseException:
            uow.rollback()
            raise

        return headers, wallet_id, reference

    return _seed


# --- the signature ----------------------------------------------------------


class TestTheSignature:
    """The whole of this API's authority over a webhook, in five tests.

    Note what is *not* asserted anywhere below: which of the failures it was.
    An absent header, a wrong key and an altered body are one answer to the
    provider, and a test that demanded three would be demanding the response
    leak exactly what ``InvalidWebhookSignatureError``'s docstring says it must
    not.
    """

    def test_a_correct_signature_settles_the_deposit(
        self, post_event, a_deposit, balance_of
    ):
        """The happy path, end to end, and the balance is the assertion.

        A 200 with the right word in it would still be a lie if nothing moved,
        so the credit is read back through the API rather than inferred from the
        outcome name.
        """
        headers, wallet_id, intent = a_deposit()
        assert balance_of(wallet_id, headers) == "0.00"

        response = post_event(an_event(CHARGE, intent["provider_reference"]))

        assert response.status_code == 200, response.text
        assert response.json()["outcome"] == "deposit_credited"
        assert balance_of(wallet_id, headers) == "5000.00"

    def test_a_signature_from_another_key_is_refused_and_settles_nothing(
        self, post_event, a_deposit, balance_of
    ):
        headers, wallet_id, intent = a_deposit()

        response = post_event(
            an_event(CHARGE, intent["provider_reference"]),
            secret="sk_test_" + "f" * 32,
        )

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidWebhookSignatureError"
        assert balance_of(wallet_id, headers) == "0.00"

    def test_a_body_altered_after_signing_is_refused(
        self, post_event, a_deposit, balance_of
    ):
        """**This is the test that proves the bytes are the bytes.**

        One body is signed, a different one is sent, and the difference is a
        single character in the amount - the smallest change that would still
        parse. A verifier that hashed a re-serialized model rather than the
        received bytes would accept this, and the failure it would hide is a
        forged deposit of any size the attacker likes.

        The signature is computed over the *original* bytes and travels with the
        altered ones, which is exactly what an attacker can do with a genuine
        webhook they have seen.
        """
        headers, wallet_id, intent = a_deposit()
        honest = an_event(CHARGE, intent["provider_reference"], amount=500000)
        tampered = an_event(CHARGE, intent["provider_reference"], amount=500001)

        response = post_event(tampered, signed_body=json.dumps(honest).encode())

        assert response.status_code == 401
        assert balance_of(wallet_id, headers) == "0.00"

    def test_no_header_is_refused(self, post_event, a_deposit, balance_of):
        headers, wallet_id, intent = a_deposit()

        response = post_event(
            an_event(CHARGE, intent["provider_reference"]), send_header=False
        )

        assert response.status_code == 401
        assert balance_of(wallet_id, headers) == "0.00"

    def test_an_empty_signature_is_refused(self, post_event, a_deposit):
        """A header present and blank, which is not the same request as no header.

        Both are refused, and the reason to pin the second separately is that
        they arrive by different routes: ``Header(default=None)`` gives ``None``
        for an absent header and ``""`` for a blank one, so a verifier written as
        ``if signature is None`` would let this one through to be compared
        against a digest it cannot equal - which happens to be safe today and is
        safe for a reason nobody wrote down.
        """
        _, _, intent = a_deposit()

        response = post_event(
            an_event(CHARGE, intent["provider_reference"]), signature=""
        )

        assert response.status_code == 401

    def test_the_three_failures_are_one_answer(
        self, post_event, a_deposit, sign_webhook
    ):
        """Absent, wrong and altered produce byte-identical responses.

        The docstring on ``InvalidWebhookSignatureError`` claims that telling a
        stranger *how* their forgery failed is the one piece of help they should
        not get, and this is that claim as an assertion. It is also the test that
        would fail if somebody later added a helpful "no signature header"
        message - which is the sort of change that looks like an improvement.
        """
        _, _, intent = a_deposit()
        event = an_event(CHARGE, intent["provider_reference"])
        body = json.dumps(event).encode()

        absent = post_event(event, send_header=False)
        wrong = post_event(event, secret="sk_test_" + "f" * 32)
        altered = post_event(event, signed_body=body + b" ")

        assert absent.status_code == wrong.status_code == altered.status_code == 401
        assert absent.text == wrong.text == altered.text


class TestAnUnconfiguredInstall:
    def test_a_webhook_is_refused_with_a_503(self, unconfigured_client, sign_webhook):
        """**A 503 rather than a 401, and the difference is the whole test.**

        There is no key, so no signature can verify - and calling that
        *unauthenticated* would blame the provider for the installation's state.
        A 401 tells Paystack to stop sending; a 503 tells it to try again, which
        is what we want, because the operator who forgot to set the key will set
        it and the events must then land.

        The body is signed, deliberately. A test that sent an unsigned body here
        would pass whether the refusal came from the missing key or from the
        missing header, and it would be asserting the wrong one.
        """
        event = an_event(CHARGE, "ps_whatever")
        body = json.dumps(event).encode()

        response = unconfigured_client.post(
            URL, content=body, headers={HEADER: sign_webhook(body)}
        )

        assert response.status_code == 503
        assert response.json()["error"] == "PaymentsUnconfiguredError"


# --- what the four events do ------------------------------------------------


class TestACharge:
    def test_the_amount_travels_in_kobo_and_the_credit_is_in_naira(
        self, post_event, a_deposit, balance_of
    ):
        """The one place a factor of a hundred could be applied the wrong way.

        Posting five hundred thousand of the provider's subunits must credit five
        thousand of ours. A conversion gone backwards would credit five million
        and would do it silently, which is why the exact figure is asserted
        rather than an inequality.
        """
        headers, wallet_id, intent = a_deposit(amount="5000.00")

        post_event(an_event(CHARGE, intent["provider_reference"], amount=500000))

        assert balance_of(wallet_id, headers) == "5000.00"

    def test_a_replay_settles_once(self, post_event, a_deposit, balance_of):
        """The provider will send this again, and the second one must be free.

        A webhook is retried by design - that is what the 200 is for - so a
        delivery that arrives twice is the ordinary case rather than an attack.
        The second answer is the row's own status read back, which is why no
        table of seen events exists.
        """
        headers, wallet_id, intent = a_deposit()
        event = an_event(CHARGE, intent["provider_reference"])

        first = post_event(event)
        second = post_event(event)

        assert first.json()["outcome"] == "deposit_credited"
        assert second.status_code == 200
        assert second.json()["outcome"] == "already_settled"
        assert balance_of(wallet_id, headers) == "5000.00"

    def test_an_unknown_reference_is_acknowledged_and_moves_nothing(
        self, post_event, a_deposit, balance_of
    ):
        """A reference this deployment has never heard of is not an error.

        It is legible and unknown, which is a different thing from unreadable -
        and the honest answer is a 200 that says so, because a provider told "no"
        will eventually stop telling us anything. The balance is asserted to make
        the point that acknowledging is not the same as applying.
        """
        headers, wallet_id, _ = a_deposit()

        response = post_event(an_event(CHARGE, "ps_never_heard_of"))

        assert response.status_code == 200
        assert response.json()["outcome"] == "unknown_reference"
        assert balance_of(wallet_id, headers) == "0.00"

    def test_an_amount_that_disagrees_is_acknowledged_and_moves_nothing(
        self, post_event, a_deposit, balance_of
    ):
        """The row records what was asked for; the event says what arrived.

        They can only disagree through a bug or tampering, because the provider's
        checkout does not do partial payments - so the row is left PENDING for a
        reconciler rather than credited with the smaller number, and rather than
        failing loudly enough for the provider to retry something that will never
        succeed.
        """
        headers, wallet_id, intent = a_deposit(amount="5000.00")

        response = post_event(
            an_event(CHARGE, intent["provider_reference"], amount=400000)
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "amount_disagrees"
        assert balance_of(wallet_id, headers) == "0.00"


class TestATransfer:
    """Three events about money going out, and the balance is what tells them apart.

    All three pay out differently on the wallet, which is the only reason they
    are three events rather than one: a transfer that succeeded ends the row (the
    debit happened when it was requested), one that failed gives the debit back,
    and one that was reversed gives back money that had genuinely left.
    """

    def test_a_transfer_that_succeeded_settles_the_row_and_moves_nothing(
        self, post_event, a_pending_payout, balance_of
    ):
        headers, wallet_id, reference = a_pending_payout()

        response = post_event(an_event(TRANSFER_SUCCEEDED, reference, PAYOUT_KOBO))

        assert response.status_code == 200
        assert response.json()["outcome"] == "transfer_settled"
        # 10,000 funded, 2,500 held - and succeeding changes neither number.
        assert balance_of(wallet_id, headers) == "7500.00"

    def test_a_transfer_that_failed_gives_the_hold_back(
        self, post_event, a_pending_payout, balance_of
    ):
        """**Decision 106 closing, over the wire.**

        Before this event existed there was no code path anywhere that credited
        money back for a movement that failed - a hold could only ever become a
        hole. The balance returning to the full ten thousand is that gap shut.
        """
        headers, wallet_id, reference = a_pending_payout()

        response = post_event(an_event(TRANSFER_FAILED, reference, PAYOUT_KOBO))

        assert response.status_code == 200
        assert response.json()["outcome"] == "hold_released"
        assert balance_of(wallet_id, headers) == "10000.00"

    def test_a_transfer_that_came_back_credits_the_settled_payout(
        self, post_event, a_pending_payout, balance_of
    ):
        """A reversal is about a row that succeeded, which is the whole difference.

        Seeded SUCCESSFUL because that is what the bank returns - money that
        landed and was then pulled back - and the row going REVERSED rather than
        FAILED is what tells the two apart in the ledger afterwards.
        """
        headers, wallet_id, reference = a_pending_payout(
            status=TransactionStatus.SUCCESSFUL
        )

        response = post_event(an_event(TRANSFER_REVERSED, reference, PAYOUT_KOBO))

        assert response.status_code == 200
        assert response.json()["outcome"] == "payment_reversed"
        assert balance_of(wallet_id, headers) == "10000.00"

    def test_a_failed_transfer_is_reported_under_its_own_reference(
        self, post_event, a_pending_payout
    ):
        """The answer carries the reference, because a log line needs it.

        The provider is retrying deliveries of many events at once and reads
        nothing of the response - so the field exists for whoever is reading the
        server's log at three in the morning asking *which* payout failed.
        """
        _, _, reference = a_pending_payout()

        response = post_event(an_event(TRANSFER_FAILED, reference, PAYOUT_KOBO))

        assert response.json()["reference"] == reference


class TestTheVocabulary:
    """Which names go over the wire, and what happens to the ones that do not."""

    def test_a_charge_about_a_transfer_is_refused_by_kind(
        self, post_event, a_pending_payout, balance_of
    ):
        """The dispatch is on the event, not on the row - and this proves it.

        A row's own type is what a plausible implementation would branch on, and
        a charge arriving about a payout would then credit an outbound movement.
        The balance is asserted because refusing is only half the claim.
        """
        headers, wallet_id, reference = a_pending_payout()

        response = post_event(an_event(CHARGE, reference, PAYOUT_KOBO))

        assert response.status_code == 200
        assert response.json()["outcome"] == "wrong_kind"
        assert balance_of(wallet_id, headers) == "7500.00"

    def test_an_event_this_installation_does_not_settle_is_acknowledged(
        self, post_event
    ):
        """**A provider adding an event is a Tuesday, not an incident.**

        ``invoice.payment_failed`` is real, is not one of the four names in
        ``EVENTS``, and must not be an error: a provider told "no" retries, and
        it would retry forever an event that will be equally uninteresting every
        time. Note the body carries no ``data`` at all - an event this system
        does not act on has no fields worth validating, and refusing one for a
        missing reference would be this API inventing a requirement for a message
        it ignores anyway.
        """
        response = post_event({"event": "invoice.payment_failed"})

        assert response.status_code == 200
        assert response.json()["outcome"] == "event_ignored"

    def test_the_outcome_is_the_domain_word_not_a_second_vocabulary(self, post_event):
        """``outcome`` carries the ``SettlementOutcome`` value verbatim.

        Checked against the enum rather than against a string literal, because
        the string literal is the thing at risk: a route that renamed the members
        on the way out would give a client two names for one fact, and the test
        that compared literals would keep passing while it did.
        """
        response = post_event({"event": "invoice.payment_failed"})

        assert response.json()["outcome"] == SettlementOutcome.EVENT_IGNORED.value


# --- bodies that cannot be read ---------------------------------------------


class TestABodyThatCannotBeRead:
    """Every one of these is a signed 400, and every one settles nothing.

    **The signature is deliberately valid throughout this class.** These are not
    forgery tests - they are what happens when Paystack itself sends something
    this code cannot read, which is a bug on one side or the other and is a 400
    either way, because a body that cannot be parsed will not parse on the
    provider's fourth attempt either.

    A 400 rather than a 500 is the load-bearing part: a provider retries a 5xx
    and gives up on a 4xx, so a 500 here would be a retry storm over a bug.
    """

    def test_a_body_that_is_not_json_is_a_400(self, post_event):
        response = post_event(b"this is not json at all")

        assert response.status_code == 400
        assert response.json()["error"] == "MalformedWebhookError"

    def test_a_body_that_is_not_an_object_is_a_400(self, post_event):
        """A JSON list parses, and is still not an event.

        The difference between "did not parse" and "parsed into the wrong shape"
        is not something a caller could act on, so both are one refusal.
        """
        response = post_event([1, 2, 3])

        assert response.status_code == 400

    def test_an_event_with_no_name_is_a_400(self, post_event):
        response = post_event({"data": {"reference": "x", "amount": 1}})

        assert response.status_code == 400

    def test_an_event_with_no_reference_is_a_400(self, post_event, a_deposit):
        """A known event that cannot name its row.

        This is the 400 the *unrecognised* case deliberately does not get: a
        ``charge.success`` is a message this system promises to settle, and one
        that names no movement is one it cannot keep that promise about.
        """
        _, _, intent = a_deposit()
        event = an_event(CHARGE, intent["provider_reference"])
        del event["data"]["reference"]

        response = post_event(event)

        assert response.status_code == 400

    def test_an_event_with_no_amount_is_a_400(self, post_event, a_deposit):
        _, _, intent = a_deposit()
        event = an_event(CHARGE, intent["provider_reference"])
        del event["data"]["amount"]

        response = post_event(event)

        assert response.status_code == 400

    def test_a_boolean_amount_is_a_400(self, post_event, a_deposit):
        """``True`` is an ``int`` in Python, and one kobo is not what Paystack sent.

        ``isinstance(True, int)`` is ``True``, so the obvious check would read
        ``"amount": true`` as a single subunit and credit a hundredth of a naira.
        ``Money`` makes the same explicit exclusion at its own centre for the same
        reason, and this is the wire's end of it.
        """
        _, _, intent = a_deposit()
        event = an_event(CHARGE, intent["provider_reference"])
        event["data"]["amount"] = True

        response = post_event(event)

        assert response.status_code == 400

    def test_a_zero_amount_is_a_400(self, post_event, a_deposit):
        """An event claiming nothing arrived cannot describe a movement.

        ``ProviderOutcome`` refuses it rather than letting it through to be
        compared against a row - an outcome that could never disagree with
        anything is the same thing as the mismatch rule not applying.
        """
        _, _, intent = a_deposit()

        response = post_event(
            an_event(CHARGE, intent["provider_reference"], amount=0)
        )

        assert response.status_code == 400

    def test_an_unknown_currency_is_a_400(self, post_event, a_deposit):
        """``Currency`` is a plain enum, so this raises a built-in ``ValueError``.

        **Found while writing this test, and it was a hole.** Every other refusal
        on this path is a ``MoneyError`` and is caught; a currency name the enum
        has never heard of is not, so it escaped to the catch-all handler as a
        500 - on the one endpoint where a 5xx makes a provider retry forever. The
        fix is in ``_outcome_from``, and this test is what keeps it fixed.
        """
        _, _, intent = a_deposit()

        response = post_event(
            an_event(CHARGE, intent["provider_reference"], currency="XYZ")
        )

        assert response.status_code == 400
        assert response.json()["error"] == "MalformedWebhookError"


class TestTheOrderOfTheChecks:
    def test_the_signature_is_checked_before_the_body_is_read(self, post_event):
        """**A bad signature on an unreadable body is a 401, not a 400.**

        The ordering is the claim, and this is the only test that can see it: a
        body that is not JSON would be refused as malformed if it were read
        first, and the fact that it comes back 401 instead proves nothing below
        the signature ever ran.

        Why it matters is written out on the route - a stranger's malformed
        payload must not be reported back in this API's error vocabulary, because
        that is a free oracle for anybody probing. It is a small thing to leak,
        and it is free not to.
        """
        response = post_event(b"not json", secret="sk_test_" + "f" * 32)

        assert response.status_code == 401


# --- the deposit route's own half -------------------------------------------
#
# One test lives here rather than in ``test_deposits.py`` because it is not about
# the deposit route - it is about the *pair*, and it cannot be written from
# either side alone. The route is the only thing that can create the row; the
# webhook is the only thing that can settle it.


class TestTheTwoHalvesTogether:
    def test_the_reference_a_client_is_told_is_the_one_that_settles_it(
        self, post_event, a_deposit, balance_of
    ):
        """The loop closed: ask for money, be told a name, hear that name back.

        This is verification step 2 and 4 of the plan in one test, and it is the
        smallest complete statement of the feature - the route credits nothing,
        the provider's word credits it, and the name that links the two is the
        one the client was handed rather than one the test derived.
        """
        headers, wallet_id, intent = a_deposit(amount="1200.00")
        reference = intent["provider_reference"]

        # Nothing has arrived yet, and the row says so.
        assert intent["status"] == "pending"
        assert balance_of(wallet_id, headers) == "0.00"

        post_event(an_event(CHARGE, reference, amount=120000))

        assert balance_of(wallet_id, headers) == "1200.00"

    def test_a_settlement_is_not_reachable_with_a_session(
        self, post_event, a_deposit, balance_of
    ):
        """**The payer holds a token, and the token cannot credit anything.**

        This is the claim the deposit route's docstring makes - that a session
        would have been enough to *reach* the route and would still have been
        wrong, because the request is a lie about where money came from rather
        than a question about who is asking. Here it is from the other end: the
        payer has a live session, has just been handed an authorization URL, and
        the only thing that makes the money real is a signature they cannot
        produce.

        Note what the two calls differ by: the same event, signed versus not. And
        note the order they are made in - the unsigned call is refused and the
        balance is read back **before** the signed one is sent. The two calls are
        byte-identical apart from the header, so a test that posted the signed one
        first and asserted the balance afterwards would be asserting that money
        moved at all rather than that the *signature* is what moved it. The zero
        has to be read while zero is still the truth.
        """
        headers, wallet_id, intent = a_deposit()
        event = an_event(CHARGE, intent["provider_reference"])

        unsigned = post_event(event, send_header=False)

        assert unsigned.status_code == 401
        assert balance_of(wallet_id, headers) == "0.00"

        signed = post_event(event)

        assert signed.status_code == 200
        assert balance_of(wallet_id, headers) == "5000.00"
