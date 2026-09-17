"""The money, over a real socket.

Phase 1b routed nothing that changes a balance, so this file could not have
existed; Phase 2a built a lock that could prove who was asking, and Phase 2b
spent it. What is here is the other half of the plan's verification list - the
part that is about the *wire* rather than about the domain: a request goes in,
something moves in SQLite, and what comes back says what happened.

Two things are true of every test below and shaped the whole file.

**There is no way to put money into a wallet over HTTP.** That is not an
oversight - it is the held deposit, and ``test_boundary.py`` says why: the only
party who can honestly say money arrived is the party that sent it. So the
``funded`` fixture below opens a wallet through the API and then credits it
through the *use case*, over the same database file. It is the same seam
``tests/conftest.signed_in`` uses for accounts, and for the same reason: the
precondition is built through the layer that can build it, and everything the
test is actually about happens over the wire.

**A pending movement is the interesting case, and it is the one with no exit.**
A withdrawal and a payout debit the wallet and leave the row PENDING, because
their far end is a bank account nothing here has contacted. So several tests
below assert the *absence* of things - no ``completed_at``, no receipt - and
those are not softened assertions. They are the shape of the phase.

**And since the confirmation, every one of those movements takes two requests.**
The three money routes record a request; ``POST /confirmations/{id}/confirm``
carries it out. That split is asserted here rather than assumed, because it is
the one property of this API a reader would otherwise have to take from the
docstrings: a 201 from ``/withdrawals`` means *a question was recorded*, and the
balance that did not change is the proof. Every movement below is therefore
tested as two halves, and the halves are tested against each other - the first
moving nothing, the second moving exactly what the first named.
"""

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.composition_root import build_wallet_service
from app.domain.identity.session import Session, hash_session_token
from app.domain.identity.tier import Tier, limits_for
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_EMAIL
from tests.presentation.api.conftest import ALICE, BOB

#: The account a payout is addressed to, in the shape ``DestinationIn`` takes.
#:
#: Written out rather than imported from ``tests/conftest.BANK_DESTINATION``
#: because that constant is a *domain* object and this is a request body - they
#: are the same fact in two vocabularies, and a test that rendered one from the
#: other would stop noticing when the wire format drifted from the domain's.
PAYEE = {
    "kind": "bank_account",
    "identifier": "0123456789",
    "name": "Chinedu Okafor",
    "details": {"bank_code": "058"},
}

#: A maturity date far enough out that nothing in this file reaches it by
#: accident, for the pots that are meant to still be sealed.
FUTURE = "2030-01-01"


def a_user_id(client, headers) -> UUID:
    """The user behind a token, read from the API rather than assumed.

    ``as_user()`` registers an account through ``POST /users``, which mints the
    id server-side - so a test that needs to name that user to a *service* has
    to ask for it. ``GET /users/me`` is the asking, and going through the API
    rather than reaching into the sessions table keeps this test file's only
    knowledge of identity one the API itself publishes.
    """
    return UUID(client.get("/users/me", headers=headers).json()["user_id"])


@pytest.fixture
def funded(client, as_user, open_wallet, db_path):
    """Open a wallet over HTTP, then credit it through the use case.

    See the module docstring for why the second half is not a request: there is
    no deposit endpoint in this phase, and the alternative - a test that could
    not fund a wallet at all - would leave every money route untested.

    The service is built with the id ``GET /users/me`` reports, so the wallet
    this deposits into is genuinely the caller's own. A wallet credited by
    somebody else would make every assertion below a 404, and the failure would
    read as a routing bug.
    """

    def _fund(amount="10000.00", headers=None, currency="NGN") -> str:
        headers = as_user() if headers is None else headers
        wallet_id = open_wallet(headers, currency)
        service = build_wallet_service(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            actor=a_user_id(client, headers),
        )
        service.deposit(
            UUID(wallet_id),
            Money(Decimal(amount), Currency[currency]),
            str(uuid4()),
        )
        return wallet_id

    return _fund


@pytest.fixture
def sealed_pot(client, as_user, funded, open_pot):
    """A wallet with money locked in a pot that has *not* come due.

    The refusal is the point: ``FundNotMaturedError`` is the one 409 in this API
    that leaves a FAILED ledger row behind, so the pot has to be genuinely
    sealed for that path to be exercised rather than described.
    """

    def _make(headers=None, amount="5000.00", name="Savings", **extra) -> str:
        headers = as_user() if headers is None else headers
        wallet_id = funded(headers=headers)
        open_pot(wallet_id, headers, name=name, maturity_date=FUTURE, **extra)
        locked = client.post(
            f"/wallets/{wallet_id}/funds/{name}/lock",
            json={"amount": amount},
            headers=headers,
        )
        assert locked.status_code == 201, locked.text
        return wallet_id

    return _make


@pytest.fixture
def open_pot_with_money(client, as_user, funded, open_pot):
    """A wallet with money locked in a pot that is open at every moment.

    "Open" means no maturity date at all, not a date in the past - the two are
    different states and ``FundOut.is_open`` says so. A pot with no date can be
    released at any moment, which is what makes the release and locked-payout
    tests below about settlement rather than about maturity.
    """

    def _make(headers=None, amount="5000.00", name="Savings", **extra) -> str:
        headers = as_user() if headers is None else headers
        wallet_id = funded(headers=headers)
        open_pot(wallet_id, headers, name=name, **extra)
        locked = client.post(
            f"/wallets/{wallet_id}/funds/{name}/lock",
            json={"amount": amount},
            headers=headers,
        )
        assert locked.status_code == 201, locked.text
        return wallet_id

    return _make


def wallet_at(client, wallet_id, headers) -> dict:
    response = client.get(f"/wallets/{wallet_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def post(client, path, headers, **body):
    return client.post(path, json=body, headers=headers)


# --- the two halves of every movement ---------------------------------------
#
# Everything below asks a question and then answers it, and these four helpers
# are how a test says which half it is about.


def request_move(client, path, headers, **body):
    """Ask for a movement. Returns the response - normally a 201 with a request.

    Deliberately not asserting the status: a test that needs a 200 because the
    reference was already used has to be able to say so, and one that needs a
    refusal has to be able to make a bad request. What every caller does get for
    free is that the *shape* is written once.
    """
    return client.post(path, json=body, headers=headers)


def confirm(client, confirmation_id, headers):
    """Answer a request. **This is the call that moves money.**"""
    return client.post(f"/confirmations/{confirmation_id}/confirm", headers=headers)


def confirmed(client, path, headers, **body) -> dict:
    """Ask and answer in one go, for the tests that are not about the split.

    Asserts both halves landed, so a test that only cares about the resulting
    ledger row does not have to restate the two-step shape - while a test that
    *is* about the split uses the two halves directly, and several do.
    """
    requested = request_move(client, path, headers, **body)
    assert requested.status_code == 201, requested.text
    answered = confirm(client, requested.json()["confirmation_id"], headers)
    assert answered.status_code == 201, answered.text
    return answered.json()


def refused(client, path, headers, **body):
    """Ask for a movement and answer it, with the wallet saying no.

    **The refusal arrives as an error response, not as a 201 carrying a FAILED
    row**, and that is worth a helper because it reads against expectation. The
    attempt really does write a FAILED ledger row and really does commit it -
    that is ``_run``'s ``except MoneyError: uow.commit()`` - and then it re-raises
    the domain's own error, which ``errors._grade`` renders as a 409. So "the
    movement failed" and "the request was refused" are one event seen from two
    places, and a test that wants both reads this response and the ledger
    separately.

    The 201 is asserted on the *request*, because a request is never refused for
    a reason the wallet knows about - see
    ``TestRequestingAWithdrawal.test_a_request_beyond_the_balance_is_still_recorded``.
    """
    requested = request_move(client, path, headers, **body)
    assert requested.status_code == 201, requested.text
    return confirm(client, requested.json()["confirmation_id"], headers)


def expire(db_path, confirmation_id) -> None:
    """Push a confirmation's whole window into the past, in the store.

    Rewritten rather than waited for, and it is the same trick ``_rewrite_session``
    below uses: the row is the API's own, and the only thing changed is when the
    window falls.

    **Both moments move, and that is the aggregate's rule turning up in a test.**
    ``Confirmation`` refuses a row whose ``expires_at`` is not strictly after its
    ``created_at`` - a request born dead - and the window runs fifteen minutes
    from the moment the request was made. So a confirmation made *this instant*
    cannot be aged by writing a past ``expires_at`` on its own: every such value
    is before ``created_at``, the row is refused at load, and what the test gets
    back is a 400 about the window rather than the expiry it asked for. Moving
    the creation an hour back and the expiry fifty minutes back keeps the
    interval the shape the aggregate demands, and puts both ends in 2000 - the
    past by any clock this suite will ever run against.

    That the domain can say this at all is worth noticing: the invariant is
    checked on the way *in* from the store as well as on the way out, so a
    hand-edited row that no code could have produced fails loudly at load. The
    cost is exactly this - a test aging a row has to write a row the domain would
    have written.

    Raw SQL rather than ``uow.confirmations.save``, and that is not laziness:
    ``save`` deliberately writes only ``status`` and ``transaction_id``, so that
    no caller can rewrite what a request said it would do. Its docstring argues
    exactly that, which means there is no supported way to age a request - and a
    test that aged one through the repository would be exercising the very
    transition the design exists to prevent.
    """
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE confirmations SET created_at = ?, expires_at = ? "
            "WHERE confirmation_id = ?",
            ("2000-01-01T00:00:00", "2000-01-01T00:15:00", str(confirmation_id)),
        )
        connection.commit()
    finally:
        connection.close()


# --- withdrawals ------------------------------------------------------------
#
# The first operation in this API whose far end is outside it, and therefore the
# first whose row is left PENDING. Everything asserted here follows from that one
# fact: the money is gone from the balance and the transaction is not finished.
#
# It is also the first that takes two requests, and what the split buys is visible
# in these two classes and nowhere else: asking moves nothing, answering moves
# everything, and a client that stopped after the first call has lost nothing but
# the wait.


class TestRequestingAWithdrawal:
    def test_the_request_moves_nothing_and_comes_back_awaiting(
        self, client, as_user, funded
    ):
        """A 201 that debits nothing, which is the whole feature in one test.

        The balance is read back and compared against what it was, rather than
        only the response body being inspected - because "nothing moved" is a
        claim about the *wallet*, and a response that said ``awaiting`` while the
        wallet had already been debited would pass a body-only check.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "awaiting"
        assert body["kind"] == "withdrawal"
        assert body["amount"] == {"amount": "4000.00", "currency": "NGN"}
        assert body["destination"] is None
        assert body["wallet_id"] == wallet_id
        assert wallet_at(client, wallet_id, headers)["available_balance"] == {
            "amount": "10000.00",
            "currency": "NGN",
        }

    def test_the_request_writes_no_ledger_row(self, client, as_user, funded):
        """The other half of "nothing moved", read from the ledger.

        A debit is not the only way money could appear to have moved: a FAILED
        row or a PENDING one would both tell a client that something had been
        attempted. Nothing has. The request is not an attempt, and the ledger
        still holds only the deposit.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )

        ledger = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()
        assert [row["type"] for row in ledger] == ["deposit"]

    def test_a_request_beyond_the_balance_is_still_recorded(
        self, client, as_user, funded
    ):
        """The absent balance check, stated as a test rather than a docstring.

        It looks wrong and it is the design. The balance that decides is the one
        at confirm time, so a check here would be a second and weaker copy of the
        wallet's own rule, free to disagree with it. The refusal is not lost - it
        arrives one call later, from the wallet, in the wallet's own words.

        The case this makes work is real: a request made against an empty wallet
        is answerable once the wallet has been topped up.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="1000.00")

        response = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="1000.01"
        )

        assert response.status_code == 201, response.text
        assert response.json()["status"] == "awaiting"

    def test_a_malformed_amount_is_refused_by_the_domain(
        self, client, as_user, funded
    ):
        """A bare string is read in the wallet's currency here, so the *wallet*
        has to be read first - which is the extra read the route's docstring
        names. "not a number" survives pydantic and dies in ``Money``, and the
        distinction matters: this is a 400, not a 422, because the request shape
        is fine and the value is not.

        This one *is* refused at request time, and it is not a balance check: the
        amount cannot be stored at all, so there is no request to record.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="not a number"
        )

        assert response.status_code == 400

    def test_the_same_ref_returns_the_same_request(self, client, as_user, funded):
        """The idempotency key, one level up from where it used to act.

        It used to key the ledger row; it now keys the request that will produce
        one. A client that never saw the first response gets *its* request back -
        a 200 carrying the id the first call returned - rather than a second
        request that would become a second withdrawal.

        The 200 is the only thing telling the two answers apart, and the body is
        identical on purpose: it is the same request, and a client that looked
        only at the body should not read the second answer as a failure.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        body = {"amount": "4000.00", "ref": "withdrawal-2026-09-12"}

        first = request_move(client, f"/wallets/{wallet_id}/withdrawals", headers, **body)
        second = request_move(client, f"/wallets/{wallet_id}/withdrawals", headers, **body)

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["confirmation_id"] == second.json()["confirmation_id"]
        assert (
            wallet_at(client, wallet_id, headers)["available_balance"]["amount"]
            == "10000.00"
        )

    def test_a_reused_ref_returns_the_amount_it_was_made_with(
        self, client, as_user, funded
    ):
        """Why the CLI previews the *record* and not the command line.

        A second request under a taken key is answered with the first request -
        which is the point - and the first request's amount is the one that will
        move. A presentation that previewed the amount just typed would be
        previewing a movement that is not going to happen, and asking a person to
        confirm a number the system had already replaced.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        first = request_move(
            client,
            f"/wallets/{wallet_id}/withdrawals",
            headers,
            amount="4000.00",
            ref="reused",
        )

        second = request_move(
            client,
            f"/wallets/{wallet_id}/withdrawals",
            headers,
            amount="9000.00",
            ref="reused",
        )

        assert second.json()["confirmation_id"] == first.json()["confirmation_id"]
        assert second.json()["amount"]["amount"] == "4000.00"

    def test_two_actors_may_use_the_same_ref(self, client, as_user, funded):
        """The leak the wallet-scoped key closed, from the outside.

        The key is scoped to the *wallet*, and a wallet has exactly one owner, so
        Alice's ``rent`` and Bob's ``rent`` are two requests on two wallets.
        Scoping by owner would have left one case open: one user posting the same
        key to two of their own wallets.
        """
        alice = as_user(ALICE)
        bob = as_user(BOB)
        alice_wallet = funded(headers=alice)
        bob_wallet = funded(headers=bob)

        first = request_move(
            client,
            f"/wallets/{alice_wallet}/withdrawals",
            alice,
            amount="2500.00",
            ref="rent",
        )
        second = request_move(
            client,
            f"/wallets/{bob_wallet}/withdrawals",
            bob,
            amount="2500.00",
            ref="rent",
        )

        assert first.status_code == second.status_code == 201
        assert first.json()["confirmation_id"] != second.json()["confirmation_id"]


class TestConfirmingAWithdrawal:
    def test_it_debits_the_wallet_and_comes_back_pending(self, client, as_user, funded):
        """The hold, stated as both halves at once.

        The balance moved and the row did *not* settle, and neither of those
        alone is the interesting claim. A debit without a pending row is money
        that vanished; a pending row without a debit is a promise this system
        has not kept. Together they are a hold, which is what this phase added.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["transaction"]["status"] == "pending"
        assert body["transaction"]["completed_at"] is None
        assert body["transaction"]["type"] == "withdrawal"
        assert body["transaction"]["amount"] == {"amount": "4000.00", "currency": "NGN"}
        assert body["confirmation"]["status"] == "confirmed"
        # The wallet travels with the answer, because it is the next question -
        # and this is the only place the balance after the movement exists.
        assert body["wallet"]["available_balance"] == {
            "amount": "6000.00",
            "currency": "NGN",
        }
        assert wallet_at(client, wallet_id, headers)["available_balance"] == {
            "amount": "6000.00",
            "currency": "NGN",
        }

    def test_the_held_money_cannot_be_spent_twice(self, client, as_user, funded):
        """What makes the hold a hold rather than a note.

        The wallet is debited at the moment the row is written, so a second
        withdrawal is checked against the *remaining* balance. If the debit were
        deferred to settlement - which is the other way this could have been
        built - this second request would be approved against money that is
        already spoken for, and the wallet would be overdrawn by the time both
        settled.

        The refusal is a 409 rather than a FAILED row in a 201, because the
        domain's error is re-raised after the row is committed. See ``refused``.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="10000.00")
        first = confirmed(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="8000.00"
        )
        assert first["transaction"]["status"] == "pending"

        second = refused(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="8000.00"
        )

        assert second.status_code == 409
        assert second.json()["error"] == "InsufficientFundsError"
        assert (
            wallet_at(client, wallet_id, headers)["available_balance"]["amount"]
            == "2000.00"
        )

    def test_a_second_confirm_of_the_same_request_is_a_409(
        self, client, as_user, funded
    ):
        """The gate, over the wire. This is the assertion the feature rests on.

        A request is answerable once. The second confirm finds the row already
        spent - not by reading it and deciding, but because the update that would
        spend it matches nothing - and the balance does not move again. Without
        this, "confirm twice" would be "withdraw twice", and the prompt would have
        bought nothing at all.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )
        confirmation_id = requested.json()["confirmation_id"]
        assert confirm(client, confirmation_id, headers).status_code == 201

        again = confirm(client, confirmation_id, headers)

        assert again.status_code == 409
        assert again.json()["error"] == "ConfirmationAlreadyUsedError"
        assert (
            wallet_at(client, wallet_id, headers)["available_balance"]["amount"]
            == "6000.00"
        )

    def test_an_expired_request_is_refused(self, client, as_user, funded, db_path):
        """Fifteen minutes is a window, not a formality.

        Checked rather than swept - nothing goes looking for stale rows - so what
        makes this a refusal is the comparison the claim makes against its own
        clock. The wallet is asserted untouched afterwards, because a refusal that
        had already debited would be worse than no expiry at all.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )
        confirmation_id = requested.json()["confirmation_id"]
        expire(db_path, confirmation_id)

        response = confirm(client, confirmation_id, headers)

        assert response.status_code == 409
        assert response.json()["error"] == "ConfirmationExpiredError"
        assert (
            wallet_at(client, wallet_id, headers)["available_balance"]["amount"]
            == "10000.00"
        )

    def test_an_expired_request_reads_back_expired(
        self, client, as_user, funded, db_path
    ):
        """And the read says so, without writing anything to say it.

        ``EXPIRED`` is derived rather than stored, which is why looking at a
        request does not spend it and why this ``GET`` is not a write. A client
        finds out *when* its request lapsed from ``expires_at``, rather than only
        that it did.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )
        confirmation_id = requested.json()["confirmation_id"]
        expire(db_path, confirmation_id)

        response = client.get(f"/confirmations/{confirmation_id}", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "expired"

    def test_reading_a_live_request_does_not_spend_it(self, client, as_user, funded):
        """The other half of "expiry is derived": looking is not doing.

        If the read wrote ``EXPIRED`` over a stale row - which is what a stored
        status would have to do - then a client polling its own request would be
        changing what it was reading, and this endpoint would be a write. The
        request is asserted still answerable after being read.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00"
        )
        confirmation_id = requested.json()["confirmation_id"]

        read = client.get(f"/confirmations/{confirmation_id}", headers=headers)
        answered = confirm(client, confirmation_id, headers)

        assert read.json()["status"] == "awaiting"
        assert answered.status_code == 201

    def test_the_ledger_shows_the_pending_row(self, client, as_user, funded):
        """The row is readable from the wallet's own history, unsettled.

        Worth its own test because the balance and the ledger are two different
        reads that a client reconciles, and a pending row is the only thing that
        explains why a wallet holding 6000 has had 10000 put into it.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        confirmed(client, f"/wallets/{wallet_id}/withdrawals", headers, amount="4000.00")

        ledger = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()

        assert [row["type"] for row in ledger] == ["deposit", "withdrawal"]
        assert ledger[-1]["status"] == "pending"
        assert ledger[-1]["completed_at"] is None

    def test_the_stored_ref_is_namespaced_to_the_wallet(self, client, as_user, funded):
        """Which is why the schema's docstring tells a client to keep its own key.

        The *request* holds the key as sent - it is scoped by its ``wallet_id``
        column instead, so a prefix there would be redundant. The ledger row it
        produces still carries the namespaced form, which is where the scoping has
        always lived.

        A client that sent the transaction's echoed string back as its next
        ``ref`` would not match the request it came from - it would be a new key,
        and for a withdrawal a second one. The prefix being visible is the price
        of the scoping, and it is visible deliberately: a value a client must not
        reuse is a value a client should be able to see.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        answered = confirmed(
            client,
            f"/wallets/{wallet_id}/withdrawals",
            headers,
            amount="4000.00",
            ref="client-key-7",
        )

        assert answered["confirmation"]["internal_reference"] == "client-key-7"
        assert answered["transaction"]["internal_reference"] == (
            f"{wallet_id}.client-key-7"
        )

    def test_an_amount_beyond_the_balance_fails_the_row(self, client, as_user, funded):
        """Refused at confirm time, by the wallet - which is where the balance
        rule lives, and the only place it is written down.

        Two things are true at once and this is the test that holds them
        together: the client is told no (a 409), and the ledger remembers that
        somebody tried (a FAILED row). The row is the audit trail and the 409 is
        the answer, and neither substitutes for the other.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="1000.00")

        response = refused(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="1000.01"
        )

        assert response.status_code == 409
        assert response.json()["error"] == "InsufficientFundsError"
        ledger = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()
        assert [row["type"] for row in ledger] == ["deposit", "withdrawal"]
        assert ledger[-1]["status"] == "failed"

    def test_a_withdrawal_over_the_transaction_ceiling_is_a_400_naming_the_limit(
        self, client, as_user, funded
    ):
        """The whole chain, over the wire, which is why it is asserted here.

        A session resolves an actor, the actor's own profile decides a tier, the
        tier's table decides a ceiling, the ceiling refuses *inside* the operation
        that was going to move the money, and ``errors._grade`` renders it as a
        400 carrying the error's own name. No domain test can reach four of those
        five steps, and the balance is deliberately ample - the wallet is not the
        reason this is refused, so the ceiling is the only thing left that can be.

        **A 400 rather than a 409**, which reads against the test above and is
        decision 182's fallthrough doing its job: a limit is a well-formed request
        that the account is not allowed to make, which is the class the grade was
        written for. Nothing was translated for this error, and that is the point.

        The FAILED row is the other half, and it is why the guard sits inside
        ``execute`` rather than before it: a refusal that reached the wire without
        reaching the ledger would be a control the audit trail never hears about.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="300000.00")
        limit = limits_for(Tier.UNVERIFIED, Currency.NGN).per_transaction

        response = refused(
            client,
            f"/wallets/{wallet_id}/withdrawals",
            headers,
            amount=str(limit.amount + Decimal("1")),
        )

        assert response.status_code == 400
        assert response.json()["error"] == "TierLimitExceededError"
        assert str(limit) in response.json()["detail"]
        ledger = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()
        assert [row["type"] for row in ledger] == ["deposit", "withdrawal"]
        assert ledger[-1]["status"] == "failed"

    def test_a_refused_confirm_spends_the_request(self, client, as_user, funded):
        """The refusal is committed, and the request is used up with it.

        This is the assertion that closes the trap the plan found: a refused
        attempt writes a FAILED row, and ``WalletOperation.execute`` deduplicates
        on a **global** lookup that returns the row whatever its status. If the
        request stayed answerable, a retry would re-enter under the same
        reference, be handed the old FAILED row back, move nothing and report
        success - and a receipt would be composed for it.

        So the attempt spends the request and the client asks again. The rule is
        not "an attempt spends it": it is that the spend commits whenever the
        refusal is recorded.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="1000.00")
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="1000.01"
        )
        confirmation_id = requested.json()["confirmation_id"]

        refused_once = confirm(client, confirmation_id, headers)
        again = confirm(client, confirmation_id, headers)

        assert refused_once.status_code == 409
        assert refused_once.json()["error"] == "InsufficientFundsError"
        assert again.status_code == 409
        assert again.json()["error"] == "ConfirmationAlreadyUsedError"

    def test_the_failure_writes_nothing_to_the_balance(self, client, as_user, funded):
        """Refused before the hold, so the wallet is untouched.

        ``_apply``'s contract is to raise *before* mutating, which is why the
        rejected path needs no unwinding - and this is the wire-level check that
        the contract holds. A failed withdrawal that had already debited would
        leave the money stranded in the same way a rolled-back one would not.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="1000.00")

        refused(client, f"/wallets/{wallet_id}/withdrawals", headers, amount="1000.01")

        assert wallet_at(client, wallet_id, headers)["available_balance"] == {
            "amount": "1000.00",
            "currency": "NGN",
        }
        assert (
            client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()[-1][
                "status"
            ]
            == "failed"
        )

    def test_a_frozen_wallet_refuses_the_confirm(self, client, as_user, funded):
        """A freeze stops the money leaving, and it is the confirm that is refused.

        Note the contrast with the pending withdrawal elsewhere in this file: a
        freeze stops the money leaving at all, while a pending withdrawal has
        already taken it. Both leave the wallet unable to spend, for entirely
        different reasons - which is what a client rendering "your money is
        unavailable" would need to get right.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="100.00"
        )
        post(client, f"/wallets/{wallet_id}/freeze", headers)

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletFrozenError"

    def test_a_closed_wallet_refuses_the_confirm(self, client, as_user, open_wallet):
        """Which is what makes the emptiness rule a protection rather than a
        formality. If a closed wallet still paid out, refusing to close a
        non-empty one would cost the owner one command and protect nothing.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        closing = request_move(client, f"/wallets/{wallet_id}/close", headers)
        assert (
            confirm(client, closing.json()["confirmation_id"], headers).status_code
            == 201
        )

        requested = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="100.00"
        )
        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletClosedError"


# --- payouts ----------------------------------------------------------------


class TestRequestingAPayout:
    def test_from_the_available_balance_says_where_the_money_will_go(
        self, client, as_user, funded
    ):
        """A payout is a withdrawal with a named counterparty, on the wire too.

        The ``destination`` travels back in the response rather than only being
        stored, because a person being asked to approve a payment has to be able
        to see where it is going - and the whole value of asking is that they can
        check that before it happens rather than after.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = request_move(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "awaiting"
        assert body["kind"] == "payout_from_available"
        assert body["destination"]["identifier"] == "0123456789"
        assert body["fund_name"] is None
        assert wallet_at(client, wallet_id, headers)["available_balance"] == {
            "amount": "10000.00",
            "currency": "NGN",
        }

    def test_the_source_decides_the_kind(self, client, as_user, open_pot_with_money):
        """``source`` picks the *balance*, so it has to be recorded on the request.

        It is a branch in the route rather than a field handed to one operation,
        and the request has to carry the answer because the confirm call is told
        nothing but an id. A payout request that did not remember its source could
        not be answered as the operation it was.
        """
        headers = as_user()
        wallet_id = open_pot_with_money(headers=headers, amount="5000.00")

        response = request_move(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
            source="locked",
            fund_name="Savings",
        )

        assert response.status_code == 201, response.text
        assert response.json()["kind"] == "payout_from_locked"
        assert response.json()["fund_name"] == "Savings"

    def test_a_pot_named_without_a_locked_source_is_a_422(
        self, client, as_user, funded
    ):
        """A self-contradicting request, refused before any wallet is loaded.

        A 422 rather than a 409, and the schema's own docstring gives the reason:
        an available-source payout has no use for a pot name, so nothing further
        down would refuse it - the field would simply be ignored. A caller who
        named a pot and was quietly charged from the available balance instead
        would have been told nothing at all, which is worse than a late refusal.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = request_move(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
            source="available",
            fund_name="Savings",
        )

        assert response.status_code == 422

    def test_an_unknown_source_is_a_422(self, client, as_user, funded):
        """``source`` is a closed vocabulary in the request *shape*.

        Unlike an instruction's ``action``, which travels to the domain and gets
        the domain's own answer, a source is what this route branches on - so an
        unknown one has no owner left to refuse it and would fall through to
        nothing. Hence a ``Literal``, and hence a 422 here rather than a 400.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = request_move(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
            source="somewhere",
        )

        assert response.status_code == 422

    def test_a_destination_missing_its_bank_code_is_refused(
        self, client, as_user, funded
    ):
        """The rail's own requirements, enforced rather than assumed.

        ``bank_account`` requires a ``bank_code`` detail, so a destination that
        omits ``details`` is not a vague request - it is one this system cannot
        execute. The refusal comes from the domain, which is where the knowledge
        of what a bank account needs lives, and it comes at *request* time: a
        request naming an account that could never be paid is not a question
        worth recording.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = request_move(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination={
                "kind": "bank_account",
                "identifier": "0123456789",
                "name": "Chinedu Okafor",
            },
        )

        assert response.status_code == 400


class TestConfirmingAPayout:
    def test_from_the_available_balance_settles_nothing_yet(
        self, client, as_user, funded
    ):
        headers = as_user()
        wallet_id = funded(headers=headers)

        answered = confirmed(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
        )

        assert answered["transaction"]["status"] == "pending"
        assert answered["transaction"]["type"] == "payout"
        assert answered["transaction"]["completed_at"] is None
        assert answered["transaction"]["destination"]["identifier"] == "0123456789"
        assert wallet_at(client, wallet_id, headers)["available_balance"] == {
            "amount": "7500.00",
            "currency": "NGN",
        }

    def test_from_a_named_pot_spends_the_pot(self, client, as_user, open_pot_with_money):
        """The locked source, and the pot's own balance is what moved.

        The available balance is untouched here and the pot is 2500 lighter,
        which is the difference the source makes - and the reason the request has
        to remember which source it was.
        """
        headers = as_user()
        wallet_id = open_pot_with_money(headers=headers, amount="5000.00")

        answered = confirmed(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
            source="locked",
            fund_name="Savings",
        )

        assert answered["transaction"]["status"] == "pending"
        wallet = wallet_at(client, wallet_id, headers)
        assert wallet["available_balance"]["amount"] == "5000.00"
        assert wallet["locked_balance"]["amount"] == "2500.00"
        assert wallet["funds"][0]["balance"]["amount"] == "2500.00"

    def test_from_locked_with_no_pot_is_a_different_operation(
        self, client, as_user, open_pot_with_money
    ):
        """The pooled draw, which is not the same command with a default.

        Leaving ``fund_name`` out spends matured pots oldest first, and it exists
        for a plan saved before pots could be named. Asserted separately from the
        named-pot case above because a reader would otherwise reasonably assume
        the field is optional decoration on one operation rather than a choice
        between two.
        """
        headers = as_user()
        wallet_id = open_pot_with_money(headers=headers, amount="5000.00")

        answered = confirmed(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="2500.00",
            destination=PAYEE,
            source="locked",
        )

        assert answered["transaction"]["status"] == "pending"
        assert (
            wallet_at(client, wallet_id, headers)["locked_balance"]["amount"] == "2500.00"
        )

    def test_a_pot_that_has_not_come_due_refuses_the_confirm(
        self, client, as_user, sealed_pot
    ):
        """The commitment rule, reaching the client as a 409.

        A sealed pot is money promised to a future date, and a payout that spent
        it would be spending somebody's savings against their own plan. This is
        the refusal ``FundNotMaturedError`` exists for.

        Note the refusal is *not* available at request time, and should not be: a
        request may name a pot that has not come due yet, and naming one is not
        the same as spending it. The moment that decides is the moment the money
        moves - which is also why a person who sits at the prompt long enough for
        a pot to mature gets the payout rather than a stale refusal.
        """
        headers = as_user()
        wallet_id = sealed_pot(headers=headers, amount="5000.00")
        requested = request_move(
            client,
            f"/wallets/{wallet_id}/payouts",
            headers,
            amount="1000.00",
            destination=PAYEE,
            source="locked",
            fund_name="Savings",
        )
        assert requested.status_code == 201, requested.text

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 409
        assert response.json()["error"] == "FundNotMaturedError"


# --- freeze and unfreeze ----------------------------------------------------
#
# The two transitions that are *not* confirmed, and their absence from the
# confirmation is the decision rather than an omission: both are reversible, both
# stay inside the wallet, and a prompt in front of them would only teach people to
# answer prompts without reading them. They behave exactly as they did.


class TestFreezingAndUnfreezing:
    def test_freezing_returns_the_wallet_it_froze(self, client, as_user, funded):
        """The body rather than a 204, because the caller's next question is the
        balance - and freezing does not change it. Returning the wallet says
        both things at once.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = post(client, f"/wallets/{wallet_id}/freeze", headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "frozen"
        assert response.json()["available_balance"]["amount"] == "10000.00"

    def test_unfreezing_returns_it_to_active(self, client, as_user, funded):
        headers = as_user()
        wallet_id = funded(headers=headers)
        post(client, f"/wallets/{wallet_id}/freeze", headers)

        response = post(client, f"/wallets/{wallet_id}/unfreeze", headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "active"

    def test_freezing_needs_no_confirmation(self, client, as_user, funded):
        """One call, one status change, no request recorded.

        Asserted because the *absence* of a confirmation is a decision a reader
        should be able to see holding, rather than infer from the docstrings. If
        freezing had grown a prompt, this test would still pass on the status
        code and fail on the second freeze - which is what it checks.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)

        post(client, f"/wallets/{wallet_id}/freeze", headers)
        again = post(client, f"/wallets/{wallet_id}/freeze", headers)

        assert again.status_code == 409
        assert again.json()["error"] == "WalletAlreadyFrozenError"

    def test_freezing_twice_is_a_409(self, client, as_user, funded):
        headers = as_user()
        wallet_id = funded(headers=headers)
        post(client, f"/wallets/{wallet_id}/freeze", headers)

        response = post(client, f"/wallets/{wallet_id}/freeze", headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletAlreadyFrozenError"

    def test_unfreezing_an_active_wallet_is_a_409(self, client, as_user, funded):
        headers = as_user()
        wallet_id = funded(headers=headers)

        response = post(client, f"/wallets/{wallet_id}/unfreeze", headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletAlreadyActiveError"

    def test_a_frozen_wallet_can_still_be_unfrozen_by_its_owner_only(
        self, client, as_user, funded
    ):
        """Freezing is reversible, and the reversibility is scoped like everything
        else: Bob cannot unfreeze Alice's wallet because he cannot see it.
        """
        alice = as_user(ALICE)
        wallet_id = funded(headers=alice)
        post(client, f"/wallets/{wallet_id}/freeze", alice)

        response = post(client, f"/wallets/{wallet_id}/unfreeze", as_user(BOB))

        assert response.status_code == 404
        assert response.json()["error"] == "WalletNotFoundError"


# --- close ------------------------------------------------------------------


class TestRequestingAClose:
    def test_it_records_a_request_and_closes_nothing(
        self, client, as_user, open_wallet
    ):
        """A close is confirmed even though it moves nothing, and this says why.

        It is the one transition in this API with no way back. The emptiness rule
        protects money from being stranded; nothing protects an owner from
        closing the wrong wallet, and the wallet that comes back still reads
        ``active`` - which is the assertion that matters.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)

        response = request_move(client, f"/wallets/{wallet_id}/close", headers)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "awaiting"
        assert body["kind"] == "close"
        assert body["amount"] is None
        assert body["destination"] is None
        assert wallet_at(client, wallet_id, headers)["status"] == "active"


class TestConfirmingAClose:
    """The only status change with a precondition, over the wire.

    Note what "empty" means here and why every test in this class either opens a
    wallet without funding it or funds it and empties it deliberately. There is
    no way to *drain* a wallet over HTTP - a withdrawal leaves the money held
    rather than gone - so the reachable empty state is a wallet that was never
    credited. That is the same state from the close's point of view, and it is
    the only one this phase can produce.
    """

    def test_a_wallet_that_was_never_funded_closes(
        self, client, as_user, open_wallet
    ):
        """A close answers with no transaction, because it writes no ledger row.

        ``None`` here means *this operation writes no row* - not *the row is
        unknown* and not *the row is still being made*. A client that rendered
        the absent transaction as pending would be describing a payment that does
        not exist.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        requested = request_move(client, f"/wallets/{wallet_id}/close", headers)

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 201, response.text
        assert response.json()["transaction"] is None
        assert response.json()["confirmation"]["status"] == "confirmed"
        assert response.json()["wallet"]["status"] == "closed"
        assert wallet_at(client, wallet_id, headers)["status"] == "closed"

    def test_a_wallet_with_money_in_it_is_a_409(self, client, as_user, funded):
        headers = as_user()
        wallet_id = funded(headers=headers, amount="10000.00")
        requested = request_move(client, f"/wallets/{wallet_id}/close", headers)
        assert requested.status_code == 201

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletNotEmptyError"

    def test_a_refused_close_does_not_spend_the_request(
        self, client, as_user, funded
    ):
        """The exception to "the attempt spends it", and it is the rule read right.

        The rule is not "an attempt spends the request" - it is that **the spend
        commits whenever the refusal is recorded**. A withdrawal writes a FAILED
        row when it refuses, and that row is what the request is spent against.
        A close writes nothing at all, so its whole unit rolls back, the claim
        included, and the request stays answerable.

        That is the right answer for a person, too: the refusal says "move your
        money out first", and emptying the wallet and answering again is exactly
        what they are going to do next. Spending the request would make them ask
        again for no reason.
        """
        headers = as_user()
        wallet_id = funded(headers=headers, amount="10000.00")
        requested = request_move(client, f"/wallets/{wallet_id}/close", headers)
        confirmation_id = requested.json()["confirmation_id"]

        refused_once = confirm(client, confirmation_id, headers)
        # The wallet is untouched, so the *same* request is still answerable -
        # and asking again would find the same refusal rather than a spent row.
        again = confirm(client, confirmation_id, headers)

        assert refused_once.status_code == 409
        assert refused_once.json()["error"] == "WalletNotEmptyError"
        assert again.status_code == 409
        assert again.json()["error"] == "WalletNotEmptyError"

    def test_money_in_a_pot_also_refuses_the_close(
        self, client, as_user, open_pot_with_money
    ):
        """The half a balance-only reading of "empty" would let through.

        The available balance here is 5000 and the pot holds 5000, so a client
        reading only ``available_balance`` would see money in the way. The
        sharper case is a wallet whose available balance is zero with a funded
        pot - which is what the domain suite covers with ``locked``. What this
        test adds is that the *response* names the right refusal for the mixed
        state, so a caller knows which of the two things to go and deal with.
        """
        headers = as_user()
        wallet_id = open_pot_with_money(headers=headers, amount="5000.00")
        requested = request_move(client, f"/wallets/{wallet_id}/close", headers)

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletNotEmptyError"

    def test_a_live_plan_refuses_the_close_and_names_it(
        self, client, as_user, open_wallet, create_plan
    ):
        """A different 409, because it has a different remedy.

        The wallet here is *empty*: it passes every rule the domain knows about.
        What refuses it is a plan that is due to spend from it, which the domain
        cannot see - it does not import planning - so this is the one close
        refusal that lives in the application layer.

        Two classes at one status is deliberate and the reason is on
        ``errors.CONFLICT``: "move the money out" and "cancel the plan" are
        different instructions, and a caller given one name for both would have
        to guess which their wallet needed.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        plan = create_plan(wallet_id, headers, source="available", name="Rent 2026")
        assert plan.status_code == 201, plan.text
        requested = request_move(client, f"/wallets/{wallet_id}/close", headers)

        response = confirm(client, requested.json()["confirmation_id"], headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletHasActivePlansError"
        assert "Rent 2026" in response.json()["detail"]

    def test_confirming_a_spent_close_is_a_409(self, client, as_user, open_wallet):
        """A *successful* close is spent like any other, and this is the contrast
        with the refused one above.

        The asymmetry looks odd for a moment and it is exact: a refused close
        changes nothing, so there is nothing to spend the request against; a
        successful one is a fact, and the request that produced it is finished.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        requested = request_move(client, f"/wallets/{wallet_id}/close", headers)
        confirmation_id = requested.json()["confirmation_id"]
        assert confirm(client, confirmation_id, headers).status_code == 201

        again = confirm(client, confirmation_id, headers)

        assert again.status_code == 409
        assert again.json()["error"] == "ConfirmationAlreadyUsedError"

    def test_a_closed_wallet_refuses_a_freeze(self, client, as_user, open_wallet):
        """Freezing a closed wallet is refused because *the wallet is closed* -
        ``WalletClosedError``, not ``WalletAlreadyFrozenError``.

        Both grade 409, so the wire only shows this in the ``error`` name. It is
        asserted because the two names send a caller to different places: one
        says "this wallet is finished", the other says "it is already frozen",
        and a frozen wallet is a state they can undo.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        closing = request_move(client, f"/wallets/{wallet_id}/close", headers)
        confirm(client, closing.json()["confirmation_id"], headers)

        response = post(client, f"/wallets/{wallet_id}/freeze", headers)

        assert response.status_code == 409
        assert response.json()["error"] == "WalletClosedError"

    def test_an_empty_closed_wallet_reads_back_closed(
        self, client, as_user, open_wallet
    ):
        headers = as_user()
        wallet_id = open_wallet(headers)
        closing = request_move(client, f"/wallets/{wallet_id}/close", headers)
        confirm(client, closing.json()["confirmation_id"], headers)

        assert wallet_at(client, wallet_id, headers)["status"] == "closed"


# --- locking, releasing and extending ---------------------------------------
#
# The three pot operations, and they are in this file rather than with the other
# pot routes for the reason ``routes/funds.py`` gives: these move money. What
# separates them from the wallet's own money routes is settlement - a pot is
# *inside* the wallet, so a lock and a release are finished the moment they
# happen and come back SUCCESSFUL.
#
# They are also all *not* confirmed, and the three together are the other half of
# the "money leaving only" decision: every one of them is reversible and stays
# inside the wallet, so a mis-tap is undone by typing the opposite command.


class TestLockingAndReleasing:
    def test_locking_moves_money_into_the_pot_and_settles(self, client, as_user, funded, open_pot):
        """SUCCESSFUL, immediately - and that is the whole contrast with a payout.

        Both moved 5000 out of the available balance, and one is finished while
        the other is not. The difference is not the size or the direction; it is
        that the pot is inside this wallet and the bank account is not.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        open_pot(wallet_id, headers, name="Savings")

        response = post(
            client, f"/wallets/{wallet_id}/funds/Savings/lock", headers, amount="5000.00"
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "successful"
        assert body["type"] == "lock_funds"
        assert body["completed_at"] is not None
        assert body["fund_id"] is not None
        assert wallet_at(client, wallet_id, headers)["locked_balance"]["amount"] == "5000.00"

    def test_releasing_moves_it_back_and_settles(
        self, client, as_user, open_pot_with_money
    ):
        headers = as_user()
        wallet_id = open_pot_with_money(headers=headers, amount="5000.00")

        response = post(
            client,
            f"/wallets/{wallet_id}/funds/Savings/release",
            headers,
            amount="5000.00",
        )

        assert response.status_code == 201, response.text
        assert response.json()["status"] == "successful"
        assert response.json()["type"] == "unlock_funds"
        wallet = wallet_at(client, wallet_id, headers)
        assert wallet["available_balance"]["amount"] == "10000.00"
        assert wallet["locked_balance"]["amount"] == "0.00"

    def test_releasing_a_sealed_pot_is_refused_and_leaves_a_row(
        self, client, as_user, sealed_pot
    ):
        """The one refusal in this API that is recorded before it is re-raised.

        Two different refusals can come out of one route, and only one of them
        leaves a trace: an unknown pot name is refused before the operation is
        built, and a pot that exists but has not come due is refused *inside* it.
        A wallet should remember that somebody tried to break a commitment early,
        which is why the FAILED row is there rather than being tidied away.
        """
        headers = as_user()
        wallet_id = sealed_pot(headers=headers, amount="5000.00")

        response = post(
            client,
            f"/wallets/{wallet_id}/funds/Savings/release",
            headers,
            amount="1000.00",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "FundNotMaturedError"
        ledger = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()
        assert ledger[-1]["status"] == "failed"
        assert wallet_at(client, wallet_id, headers)["locked_balance"]["amount"] == "5000.00"

    def test_an_unknown_pot_is_a_404_and_leaves_nothing(
        self, client, as_user, funded
    ):
        """The other refusal, which is *not* recorded - because nothing happened.

        The pot is looked up in the operation's constructor, so a request naming
        one that does not exist never reaches the ledger. That is the right
        answer rather than an inconsistency with the test above: the sealed-pot
        attempt was a real instruction this wallet refused, and this one was a
        name that refers to nothing.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        before = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()

        response = post(
            client, f"/wallets/{wallet_id}/funds/Nope/lock", headers, amount="100.00"
        )

        assert response.status_code == 404
        assert response.json()["error"] == "FundNotFoundError"
        after = client.get(f"/wallets/{wallet_id}/transactions", headers=headers).json()
        assert after == before

    def test_locking_more_than_the_wallet_holds_is_a_409(self, client, as_user, funded, open_pot):
        headers = as_user()
        wallet_id = funded(headers=headers, amount="1000.00")
        open_pot(wallet_id, headers, name="Savings")

        response = post(
            client, f"/wallets/{wallet_id}/funds/Savings/lock", headers, amount="1000.01"
        )

        assert response.status_code == 409
        assert response.json()["error"] == "InsufficientFundsError"

    def test_extending_pushes_the_date_and_returns_the_pot(
        self, client, as_user, funded, open_pot
    ):
        """A pot rather than a transaction, because extending moves no money.

        No ledger row and no ``ref`` in the request: there is nothing to
        deduplicate and nothing a retry could double.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        open_pot(wallet_id, headers, name="Savings", maturity_date="2027-01-01")

        response = post(
            client,
            f"/wallets/{wallet_id}/funds/Savings/extend",
            headers,
            new_date="2028-01-01",
        )

        assert response.status_code == 200, response.text
        assert response.json()["maturity_date"] == "2028-01-01"
        assert response.json()["name"] == "Savings"

    def test_extending_to_the_same_date_is_a_409(self, client, as_user, funded, open_pot):
        """There is no way to pull a maturity date earlier, so "the same date" is
        not a no-op - it is a request this aggregate refuses rather than silently
        accepting and doing nothing with.
        """
        headers = as_user()
        wallet_id = funded(headers=headers)
        open_pot(wallet_id, headers, name="Savings", maturity_date="2027-01-01")

        response = post(
            client,
            f"/wallets/{wallet_id}/funds/Savings/extend",
            headers,
            new_date="2027-01-01",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "MaturityNotExtendedError"


# --- isolation --------------------------------------------------------------
#
# The shape is Phase 1a's and it is restated here rather than assumed: another
# user's wallet id returns the *same* 404 a random UUID gets, on every route.
# A route that answered differently would be an oracle for which ids are real.
#
# The confirmation routes are here for a sharper version of the same reason. A
# request id is the only thing needed to *answer* a request, so a confirm route
# that distinguished "not yours" from "does not exist" would be an oracle for
# which ids are worth guessing - and the answer to a guess is a movement.


#: Every money route, as ``(method, template, body)``. A literal for the same
#: reason ``test_boundary.EXPECTED_OPERATIONS`` is: a list computed from the
#: application would agree with the application whatever it did.
MONEY_ROUTES = [
    ("post", "/wallets/{wallet_id}/withdrawals", {"amount": "100.00"}),
    (
        "post",
        "/wallets/{wallet_id}/payouts",
        {"amount": "100.00", "destination": PAYEE},
    ),
    ("post", "/wallets/{wallet_id}/freeze", None),
    ("post", "/wallets/{wallet_id}/unfreeze", None),
    ("post", "/wallets/{wallet_id}/close", None),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/lock", {"amount": "100.00"}),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/release", {"amount": "100.00"}),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/extend", {"new_date": "2028-01-01"}),
]

#: A UUID that names no request, spelled out rather than generated, because it
#: appears inside the paths below and a path built from a fixture could not be a
#: module-level literal.
MISSING = "00000000-0000-0000-0000-000000000000"

#: The confirmation routes, addressed by a request id rather than a wallet id.
#:
#: Separate from the list above because the path parameter means something
#: different and the fixture is different, not because the claim is: the same
#: 404-for-both rule applies, and it matters more here. Answering a request is
#: the only call in the API whose whole authorisation is knowing an id.
#:
#: The paths are written out with ``MISSING`` in them rather than templated,
#: because these sweeps need a *path* and nothing else - there is no wallet to
#: open first, so the fixture that makes ``MONEY_ROUTES`` usable does not apply.
#: ``test_another_users_request_is_the_same_404_as_a_missing_one`` substitutes
#: Alice's real id into the same string, which is the one place the difference
#: matters.
CONFIRMATION_ROUTES = [
    ("post", f"/confirmations/{MISSING}/confirm", None),
    ("get", f"/confirmations/{MISSING}", None),
]


class TestTheMoneyRoutesAreScoped:
    @pytest.mark.parametrize(
        "method, template, body",
        MONEY_ROUTES,
        ids=[f"{m} {p}" for m, p, _ in MONEY_ROUTES],
    )
    def test_another_users_wallet_is_the_same_404_as_a_missing_one(
        self, client, as_user, open_pot_with_money, unknown_id, method, template, body
    ):
        """Compared against a random UUID rather than against a literal status.

        Asserting "404" alone would pass even if the two answers differed in
        body, and the body is where a leak would live - ``WalletNotFoundError``
        for a stranger and for a typo are deliberately the same object. Comparing
        the whole response is what makes "no branch" the claim rather than "no
        branch in the status line".

        The pot name is real, and on the stranger's own wallet it resolves. That
        matters: a 404 for an unknown *pot* would be a passing test for the wrong
        reason, so the pot exists and belongs to the caller who is asking.
        """
        bob = as_user(BOB)
        alice_wallet = open_pot_with_money(headers=as_user(ALICE), amount="5000.00")
        # Bob's own pot, so `{fund_name}` is a name that resolves for him.
        open_pot_with_money(headers=bob, amount="5000.00")

        foreign = client.request(
            method.upper(),
            template.format(wallet_id=alice_wallet, fund_name="Savings"),
            json=body,
            headers=bob,
        )
        missing = client.request(
            method.upper(),
            template.format(wallet_id=unknown_id, fund_name="Savings"),
            json=body,
            headers=bob,
        )

        assert foreign.status_code == 404, foreign.text
        assert foreign.status_code == missing.status_code
        assert foreign.json() == missing.json()

    @pytest.mark.parametrize(
        "method, path, body",
        CONFIRMATION_ROUTES,
        ids=[f"{m} {p}" for m, p, _ in CONFIRMATION_ROUTES],
    )
    def test_another_users_request_is_the_same_404_as_a_missing_one(
        self, client, as_user, funded, method, path, body
    ):
        """Alice's confirmation id, answered by Bob, reads as one that never was.

        The whole authorisation for answering a request is knowing its id, so an
        oracle here would be worth more than an oracle anywhere else in the API.
        Both calls are made by Bob - one against Alice's real request and one
        against a UUID that names nothing - and the two answers are compared
        whole.
        """
        alice = as_user(ALICE)
        bob = as_user(BOB)
        wallet_id = funded(headers=alice)
        alice_request = request_move(
            client, f"/wallets/{wallet_id}/withdrawals", alice, amount="100.00"
        )
        assert alice_request.status_code == 201, alice_request.text

        foreign = client.request(
            method.upper(),
            path.replace(MISSING, alice_request.json()["confirmation_id"]),
            json=body,
            headers=bob,
        )
        missing = client.request(method.upper(), path, json=body, headers=bob)

        assert foreign.status_code == 404, foreign.text
        assert foreign.status_code == missing.status_code
        assert foreign.json() == missing.json()
        # And Alice's request is untouched by Bob's attempt to answer it.
        assert confirm(
            client, alice_request.json()["confirmation_id"], alice
        ).status_code == 201

    @pytest.mark.parametrize(
        "method, template, body",
        MONEY_ROUTES,
        ids=[f"{m} {p}" for m, p, _ in MONEY_ROUTES],
    )
    def test_every_one_of_them_needs_a_token(
        self, client, as_user, open_wallet, unknown_id, method, template, body
    ):
        """No header at all, on a wallet that genuinely exists.

        The id is real so the 401 cannot be a 404 in disguise - the route has to
        be *reached* before authentication can refuse it, and a random UUID would
        let a missing dependency look like a missing route.
        """
        wallet_id = open_wallet(as_user())

        response = client.request(
            method.upper(),
            template.format(wallet_id=wallet_id, fund_name="Savings"),
            json=body,
        )

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(
        "method, template, body",
        MONEY_ROUTES,
        ids=[f"{m} {p}" for m, p, _ in MONEY_ROUTES],
    )
    def test_and_a_token_that_means_nothing(
        self, client, as_user, open_wallet, method, template, body
    ):
        wallet_id = open_wallet(as_user())

        response = client.request(
            method.upper(),
            template.format(wallet_id=wallet_id, fund_name="Savings"),
            json=body,
            headers={"Authorization": "Bearer not-a-real-token"},
        )

        assert response.status_code == 401, response.text
        assert response.json()["error"] == "InvalidSessionError"

    @pytest.mark.parametrize(
        "method, path, body",
        CONFIRMATION_ROUTES,
        ids=[f"{m} {p}" for m, p, _ in CONFIRMATION_ROUTES],
    )
    def test_the_confirmation_routes_need_a_token_too(
        self, client, method, path, body
    ):
        """No header at all, on the two routes that answer a request.

        The path parameter is a real-looking UUID rather than a name, so the 401
        cannot be a routing miss in disguise - and the two routes are swept
        separately from the eight above because a missing dependency on *these*
        would be the more serious of the two: an unauthenticated confirm is a way
        to spend somebody else's request.
        """
        response = client.request(method.upper(), path, json=body)

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(
        "method, path, body",
        CONFIRMATION_ROUTES,
        ids=[f"{m} {p}" for m, p, _ in CONFIRMATION_ROUTES],
    )
    def test_and_the_confirmation_routes_refuse_a_token_that_means_nothing(
        self, client, as_user, method, path, body
    ):
        response = client.request(
            method.upper(),
            path,
            json=body,
            headers={"Authorization": "Bearer not-a-real-token"},
        )

        assert response.status_code == 401, response.text
        assert response.json()["error"] == "InvalidSessionError"

    def test_and_a_token_that_has_expired(
        self, client, as_user, open_wallet, db_path
    ):
        """One route rather than all ten, because expiry is not per-route.

        The ten-route versions above check that the *dependency* is on every
        route; this checks that the dependency refuses an old token. Running it
        ten times would prove the same thing ten times at the cost of ten session
        rewrites, and the rewrite is the fragile part - it moves a row in the
        store, so a mistake in it would produce ten identical confusing failures
        instead of one.

        The row is rewritten rather than mocked at the boundary: the token is the
        one the API issued, and the only thing changed is when the session was
        said to have begun. ``issued_at`` moves with ``expires_at`` because
        ``Session`` refuses a window that closes before it opens.
        """
        headers = as_user(ALICE)
        wallet_id = open_wallet(headers)
        token = headers["Authorization"].removeprefix("Bearer ")
        issued = datetime.now() - timedelta(days=60)
        _rewrite_session(
            db_path, token, issued_at=issued, expires_at=issued + timedelta(days=30)
        )

        response = post(
            client, f"/wallets/{wallet_id}/withdrawals", headers, amount="100.00"
        )

        assert response.status_code == 401
        assert response.json()["error"] == "InvalidSessionError"


def _rewrite_session(db_path, token, *, issued_at, expires_at) -> None:
    """Move an existing session's window, keeping its id and its token.

    The same helper ``test_actor.py`` uses, and duplicated rather than shared for
    the reason that file gives: reusing the row means the token under test is
    still the one the API handed out, so the only variable is the clock. Lifting
    it into a fixture would put a store-manipulating helper in ``conftest`` where
    nine modules would see it, for the one test here that needs it.
    """
    uow = SqliteUnitOfWorkFactory(db_path).start()
    try:
        session = uow.sessions.find_by_token_hash(hash_session_token(token))
        assert session is not None, "as_user() did not leave a session behind"
        uow.sessions.save(
            Session(
                session_id=session.session_id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                issued_at=issued_at,
                expires_at=expires_at,
            )
        )
        uow.commit()
    finally:
        uow.rollback()
