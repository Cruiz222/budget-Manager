"""Opening and reading wallets, over HTTP.

The happy paths, and they are worth having in their own module rather than
scattered through the error and isolation suites. Those two are about refusals,
and a suite made only of refusals passes perfectly against an API that refuses
everything - so this is where the thing is shown to actually work.

The shape of the resource is checked here too, because it is a decision and not
an accident: which fields travel together, what they are called, and what the
balances look like before anything has happened.
"""

from tests.presentation.api.conftest import ALICE, BOB


class TestOpeningAWallet:
    def test_it_is_created_and_says_so(self, client, as_user):
        """201, not 200, and the body is the new resource.

        A ``POST`` that creates something and answers 200 leaves the client to
        guess whether it just made a wallet or found an existing one - and this
        endpoint does not look for an existing one, so the guess would be wrong
        every time.
        """
        response = client.post("/wallets", json={"currency": "NGN"}, headers=as_user())

        assert response.status_code == 201
        assert response.json()["status"] == "active"

    def test_it_belongs_to_the_caller(self, client, as_user):
        """The owner is the actor, and there is nowhere in the request to say otherwise.

        Compared against ``/users/me`` rather than against a constant, because the
        claim being tested is "these are the same account", not "this is a known
        UUID". A wallet opened by a stranger's header would satisfy the second
        claim and fail this one.
        """
        me = client.get("/users/me", headers=as_user(ALICE)).json()

        wallet = client.post(
            "/wallets", json={"currency": "NGN"}, headers=as_user(ALICE)
        ).json()

        assert wallet["user_id"] == me["user_id"]

    def test_it_starts_empty_in_the_currency_that_was_asked_for(self, client, as_user):
        """Zero, as strings, in the requested currency.

        ``"0.00"`` rather than ``0`` is the wire format doing its job on the very
        first response a client ever sees - and it is the response most likely to
        be special-cased by a client that assumes a fresh wallet is the one case
        where a number would do.
        """
        wallet = client.post(
            "/wallets", json={"currency": "USD"}, headers=as_user()
        ).json()

        assert wallet["currency"] == "USD"
        assert wallet["available_balance"] == {"amount": "0.00", "currency": "USD"}
        assert wallet["locked_balance"] == {"amount": "0.00", "currency": "USD"}
        assert wallet["funds"] == []

    def test_opening_two_gives_two_wallets(self, client, as_user, open_wallet):
        """Nothing deduplicates wallets, and the tests below depend on knowing it.

        Said out loud because "one wallet per user" is a plausible product rule
        somebody might add later, and the isolation suite's ``test_two_users_are_two_accounts``
        would keep passing while several tests here quietly became tests of the
        same wallet twice.
        """
        first = open_wallet(as_user())
        second = open_wallet(as_user())

        assert first != second

    def test_the_caller_and_nobody_else_can_read_it_back(self, client, as_user, open_wallet):
        wallet_id = open_wallet(as_user(ALICE))

        assert client.get(f"/wallets/{wallet_id}", headers=as_user(ALICE)).status_code == 200
        assert client.get(f"/wallets/{wallet_id}", headers=as_user(BOB)).status_code == 404


class TestReadingAWallet:
    def test_reading_it_back_gives_the_same_wallet(self, client, as_user):
        """The round trip is closed, which is the least a client can expect.

        ``POST`` returns a wallet and ``GET`` returns a wallet, and if the two
        disagreed then one of them would be rendering something the other does not
        store - the kind of difference that shows up as a client that "sometimes"
        shows a stale balance.
        """
        headers = as_user()
        created = client.post("/wallets", json={"currency": "NGN"}, headers=headers).json()

        read = client.get(f"/wallets/{created['wallet_id']}", headers=headers).json()

        assert read == created

    def test_a_new_wallet_has_no_movements(self, client, as_user, open_wallet):
        """An empty list, not a 404 - and the difference is the whole point.

        Nothing can put money into a wallet in this phase, so this is what every
        ledger looks like: a wallet with no history rather than a wallet whose
        history could not be found. See ``routes.wallets.list_transactions`` for
        why the two are worth distinguishing.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)

        response = client.get(f"/wallets/{wallet_id}/transactions", headers=headers)

        assert response.status_code == 200
        assert response.json() == []


class TestOpeningAPot:
    def test_it_is_created_and_listed_with_its_wallet(self, client, as_user, open_wallet):
        """The pot travels with the wallet, and is also its own endpoint.

        Both, deliberately - see ``routes.funds``. What this checks is that they
        agree: a client polling the pots endpoint and a client reading the wallet
        must not be looking at two different sets.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        created = client.post(
            f"/wallets/{wallet_id}/funds",
            json={"name": "Vacation", "kind": "personal"},
            headers=headers,
        )

        assert created.status_code == 201
        wallet = client.get(f"/wallets/{wallet_id}", headers=headers).json()
        listed = client.get(f"/wallets/{wallet_id}/funds", headers=headers).json()

        assert wallet["funds"] == [created.json()]
        assert listed == [created.json()]

    def test_a_pot_with_no_date_is_open(self, client, as_user, open_wallet):
        """``is_open`` and ``maturity_date`` are two fields pointing one way.

        ``None`` means *no maturity ever*, which is why the boolean is sent next
        to it rather than left to be inferred: a client that read ``null`` as
        "not known yet" would draw exactly the wrong conclusion about whether the
        money can move. The pair is asserted together, because either one alone
        can be right while the two disagree.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)

        pot = client.post(
            f"/wallets/{wallet_id}/funds",
            json={"name": "Vacation", "kind": "personal"},
            headers=headers,
        ).json()

        assert pot["maturity_date"] is None
        assert pot["is_open"] is True

    def test_a_pot_with_a_date_is_not_open(self, client, as_user, open_wallet):
        """The other side of the same fact, on a pot that has a date."""
        headers = as_user()
        wallet_id = open_wallet(headers)

        pot = client.post(
            f"/wallets/{wallet_id}/funds",
            json={"name": "Rent", "kind": "business", "maturity_date": "2027-01-01"},
            headers=headers,
        ).json()

        assert pot["is_open"] is False
        assert pot["maturity_date"] == "2027-01-01"

    def test_it_starts_empty(self, client, as_user, open_wallet):
        """A pot is a shape, and in this phase it stays one.

        Opening a pot writes no ledger row and moves no money - which is the
        reason it is on the safe side of the phase boundary at all. Its balance
        being zero is that decision, visible.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)

        pot = client.post(
            f"/wallets/{wallet_id}/funds",
            json={"name": "Vacation", "kind": "personal"},
            headers=headers,
        ).json()

        assert pot["balance"] == {"amount": "0.00", "currency": "NGN"}
        assert pot["first_funded_at"] is None
