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
        response = client.post(
            "/wallets",
            json={"currency": "NGN"},
            headers=as_user(),
        )

        assert response.status_code == 201
        wallet = response.json()

        assert wallet["currency"] == "NGN"
        assert wallet["available_balance"] == {
            "amount": "0.00",
            "currency": "NGN",
    }
        assert wallet["locked_balance"] == {
            "amount": "0.00",
            "currency": "NGN",
    }
        assert wallet["funds"] == []

    def test_a_known_currency_outside_the_mvp_offer_is_refused(
    self,
    client,
    as_user,
):
        """USD is understood by the domain but is not offered by this MVP."""
        response = client.post(
            "/wallets",
            json={"currency": "USD"},
            headers=as_user(),
    )

        assert response.status_code == 400
        assert response.json()["error"] == "CurrencyNotOfferedError"
        assert "NGN" in response.json()["detail"]
        assert "USD" in response.json()["detail"]

        listed = client.get("/wallets", headers=as_user()).json()
        assert listed == []
    

    def test_the_caller_and_nobody_else_can_read_it_back(self, client, as_user, open_wallet):
        wallet_id = open_wallet(as_user(ALICE))

        assert client.get(f"/wallets/{wallet_id}", headers=as_user(ALICE)).status_code == 200
        assert client.get(f"/wallets/{wallet_id}", headers=as_user(BOB)).status_code == 404


class TestOneWalletPerCurrency:
    """Decision 267, at the layer that decides it.

    **The rule is asserted here and not only over HTTP**, because this is where it
    lives: the route calls ``open_wallet`` and translates whatever comes back, so a
    test that only ever posts to ``/wallets`` would pass against a route that did
    the checking itself - and the CLI and the browser would each then be a door
    with its own copy of the rule. There is one copy, and it is this one.

    The four claims: a duplicate is refused; a currency outside the MVP offer is
    refused; either refusal persists nothing; and a closed wallet does not count
    as holding its currency.
    """

    def test_a_second_wallet_in_a_currency_already_held_is_refused(
        self, client, as_user, open_wallet
    ):
        first = open_wallet(as_user())

        response = client.post("/wallets", json={"currency": "NGN"}, headers=as_user())

        assert response.status_code == 409
        assert response.json()["error"] == "DuplicateWalletCurrencyError"
        # The message names the wallet they already have, so a caller who did not
        # know can go and read it instead of guessing which one was kept.
        assert first in response.json()["detail"]

    def test_the_refusal_leaves_the_account_exactly_as_it_was(
        self, client, as_user, open_wallet
    ):
        """**A refusal that wrote a row would be worse than no rule at all.** The
        check runs before the save, and this is the assertion that it still does -
        the listing is unchanged and no second row exists to be found later.
        """
        opened = open_wallet(as_user())

        client.post("/wallets", json={"currency": "NGN"}, headers=as_user())

        listed = client.get("/wallets", headers=as_user()).json()
        assert [one["wallet_id"] for one in listed] == [opened]

    

    def test_a_closed_wallet_does_not_hold_its_currency(self, client, as_user, open_wallet):
        """**Closing frees the currency, and this is where that is visible.**

        Without it, a closed wallet would burn its currency for the account's
        lifetime and closing one by mistake would be unfixable through the API -
        which is why the rule is written as "a wallet that is not closed" in both
        places that enforce it.

        Closing is the two-step confirmation every irreversible transition is, so
        the wallet is closed the way a client closes it - ask, then answer - rather
        than by writing a status into a column. ``test_deposits.py`` closes a wallet
        the same way for the same reason.
        """
        wallet_id = open_wallet(as_user())
        closing = client.post(f"/wallets/{wallet_id}/close", headers=as_user())
        assert closing.status_code == 201, closing.text
        confirmed = client.post(
            f"/confirmations/{closing.json()['confirmation_id']}/confirm",
            headers=as_user(),
        )
        assert confirmed.status_code == 201, confirmed.text

        reopened = client.post("/wallets", json={"currency": "NGN"}, headers=as_user())

        assert reopened.status_code == 201, reopened.text
        assert reopened.json()["wallet_id"] != wallet_id

    def test_the_closed_wallet_is_still_listed_beside_the_new_one(
        self, client, as_user, open_wallet
    ):
        """**The currency comes back without the history going anywhere.** A closed
        wallet is still a wallet - it is listed, it can be read, and its ledger is
        whole - and the rule only stops it counting as *holding* its currency. A
        person who closed a wallet to open a fresh one in the same currency should
        still be able to see what the old one did.
        """
        wallet_id = open_wallet(as_user())
        closing = client.post(f"/wallets/{wallet_id}/close", headers=as_user())
        client.post(
            f"/confirmations/{closing.json()['confirmation_id']}/confirm",
            headers=as_user(),
        )
        fresh = open_wallet(as_user())

        listed = client.get("/wallets", headers=as_user()).json()

        assert [one["wallet_id"] for one in listed] == [wallet_id, fresh]
        assert [one["status"] for one in listed] == ["closed", "active"]

    def test_a_currency_that_is_no_longer_served_is_a_400_naming_it(
        self, client, as_user
    ):
        """``GHS`` used to open a wallet here. Decision 267 removed the member, so
        the same request is now refused before it reaches the service - and the
        message lists what is left, which is how a client learns the list without
        reading the code.
        """
        response = client.post("/wallets", json={"currency": "GHS"}, headers=as_user())

        assert response.status_code == 400
        assert response.json()["error"] == "UnsupportedCurrencyError"
        assert "NGN, USD" in response.json()["detail"]


class TestListingWallets:
    """``GET /wallets``, which is the read that had no way in before.

    **A client that has not been told a wallet id could not learn one**, and that
    was true of the CLI too - it never mattered there because a person at a
    terminal types the id they were handed when the wallet was opened. Nobody
    types a UUID into a browser, so the gap this closes is not one the browser
    client invented; it is one it was the first client to run into.
    """

    def test_a_person_who_has_opened_nothing_gets_an_empty_list(self, client, as_user):
        """**A list and not a 404**, which is the one place this differs from the
        read beside it. ``GET /wallets/{id}`` is asked about a wallet the caller
        named, so finding nothing is a contradiction; this is asked by somebody
        who does not know what they hold, so "nothing" is merely true - and
        answering it with an error would make the first page a new person sees an
        error page.
        """
        response = client.get("/wallets", headers=as_user())

        assert response.status_code == 200
        assert response.json() == []

    def test_it_gives_back_what_they_opened(self, client, as_user, open_wallet):
        opened = open_wallet(as_user())

        listed = client.get("/wallets", headers=as_user()).json()

        assert [one["wallet_id"] for one in listed] == [opened]

    def test_the_entries_are_the_ones_the_other_route_returns(
        self, client, as_user, open_wallet
    ):
        """**A list of the same resource rather than a summary of it.**

        A thin listing - ids and currencies - would be cheaper to write and would
        make the page that uses it fetch each wallet again to draw a balance. The
        claim is asserted by comparing against the read beside it rather than
        against a written-out body, so a field added to ``WalletOut`` cannot pass
        here and fail there.
        """
        wallet_id = open_wallet(as_user())

        listed = client.get("/wallets", headers=as_user()).json()
        single = client.get(f"/wallets/{wallet_id}", headers=as_user()).json()

        assert listed == [single]

    def test_it_is_oldest_first(self, client, as_user, open_wallet):
        """The older closed wallet remains before its active replacement.

        Closing updates the existing row rather than deleting or recreating it.
        The replacement wallet is a new row, so it belongs at the end of the
        listing. This proves that changing status does not change creation order.
        """
        first = open_wallet(as_user())

        closing = client.post(
            f"/wallets/{first}/close",
            headers=as_user(),
        )
        assert closing.status_code == 201, closing.text

        confirmed = client.post(
            f"/confirmations/{closing.json()['confirmation_id']}/confirm",
            headers=as_user(),
        )
        assert confirmed.status_code == 201, confirmed.text

        second = open_wallet(as_user())

        listed = client.get("/wallets", headers=as_user()).json()

        assert [one["wallet_id"] for one in listed] == [first, second]
        assert [one["status"] for one in listed] == ["closed", "active"]
        
        
    def test_it_does_not_show_a_strangers_wallet(self, client, as_user, open_wallet):
        """**Ownership is not a parameter and cannot be.** The service was built
        for one actor before the function ran, so there is no query string, no
        body and no path through which a caller could ask about somebody else -
        which is why the assertion is about absence rather than about a refusal.
        """
        open_wallet(as_user(BOB))
        mine = open_wallet(as_user(ALICE))

        listed = client.get("/wallets", headers=as_user(ALICE)).json()

        assert [one["wallet_id"] for one in listed] == [mine]

    def test_every_entry_names_the_caller_as_its_owner(self, client, as_user, open_wallet):
        """**The same claim from inside the body rather than from the count.**

        The test above asserts a stranger's wallet is absent, which a client
        could satisfy by rendering somebody's wallet under the caller's id. This
        one reads the owner field and compares it against ``/users/me`` - the
        same comparison ``test_it_belongs_to_the_caller`` makes about the single
        read, made again about the listing because it is a second route that
        could get it wrong on its own.

        **Every ``as_user`` here names its address, and the listing is ``ALICE``'s.**
        ``as_user()`` is the suite's standard *third* account rather than one of
        these two - see its own docstring - so listing with the bare call would
        ask ``test@example.com`` what *it* holds and get an empty list. A test
        written that way would fail on a correct listing and would still fail on
        a listing that leaked BOB's wallet, which is the worst kind of assertion.
        """
        open_wallet(as_user(BOB))
        open_wallet(as_user(ALICE))

        mine = client.get("/users/me", headers=as_user(ALICE)).json()["user_id"]
        listed = client.get("/wallets", headers=as_user(ALICE)).json()

        assert [one["user_id"] for one in listed] == [mine]

    def test_it_needs_a_session_like_everything_else(self, client):
        """The one route in this file whose actor could plausibly have been
        optional - a listing with no wallet named in it reads as a listing of the
        installation's wallets if nobody is asked who is asking."""
        assert client.get("/wallets").status_code == 401


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

        A *new* wallet has no history, and this is what that looks like: an empty
        list rather than a 404, because "there is nothing here" and "there is no
        such wallet" are different answers and only the second is an error. See
        ``routes.wallets.list_transactions`` for why the two are worth
        distinguishing.

        **This used to read "nothing can put money into a wallet in this phase,
        so this is what every ledger looks like", and Phase 2b made that false.**
        A withdrawal, a payout, a lock and a release all write rows now, and the
        wallet this test opens is empty only because it is new. The test did not
        need to change - the wallet it builds genuinely has no movements - but
        the reason it passes did, and a docstring explaining the wrong reason is
        how a later reader learns the wrong thing about their own ledger.
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
