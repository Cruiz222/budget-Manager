"""Reading and writing ``/users/me/profile``, over HTTP.

Two routes and no id anywhere, so this file has less to say about isolation than
its neighbours and more to say about *shape*: what a profile that does not exist
looks like over the wire, what a tier is worth in ceilings, and what a client
cannot ask for.

**The claim that has no counterpart in ``test_isolation.py``** is the one about
the interface itself: every other resource here is reachable by an id a caller
could substitute, which is why "not yours" and "does not exist" have to collapse
into one 404. A profile is reachable only through the literal word ``me``, so
there is no substitution to collapse - and the test that matters is that no
route takes an id, including for the caller's *own* account. See
``TestAProfileHasNoIdToSubstitute``.

The limits asserted below are built from ``LIMITS`` rather than typed out. A
test that hard-coded ``"50000.00"`` would keep passing after somebody changed
the table and would be checking a second copy of the numbers instead of the ones
in force - which is the one thing a test about a limit must not do.
"""

from app.domain.identity.tier import Tier, limits_for
from app.domain.money.currency import Currency
from tests.presentation.api.conftest import ALICE, BOB

#: A whole profile: the seven fields a client sends, four of which are what the
#: tier counts. Every test that wants a complete profile starts here and changes
#: the one field it is about.
COMPLETE = {
    "display_name": "Ada",
    "legal_first_name": "Adaeze",
    "legal_last_name": "Okafor",
    "date_of_birth": "1990-05-17",
    "phone": "+2348000000000",
    "country": "NG",
    "address_line": "12 Marina Road, Lagos",
}


def money_json(money):
    """An amount as this wire spells it, or ``None`` for a ceiling that is not there.

    ``:.2f`` is ``money_out``'s format spec, reproduced rather than imported -
    a helper that called the translator would assert that the translator agrees
    with itself. The strings the API sends are the ones a client parses, and
    they are what a test should be comparing against a literal format.
    """
    if money is None:
        return None
    return {"amount": f"{money.amount:.2f}", "currency": money.currency.value}


def expected_limits(tier):
    """Each currency's three ceilings, read off the table for this tier."""
    return [
        {
            "currency": currency.value,
            "per_transaction": money_json(limits_for(tier, currency).per_transaction),
            "daily_outflow": money_json(limits_for(tier, currency).daily_outflow),
            "max_balance": money_json(limits_for(tier, currency).max_balance),
        }
        for currency in Currency
    ]


def save_profile(client, headers, **fields):
    """``PUT`` a profile, with ``COMPLETE`` as the body and ``fields`` over it."""
    return client.put(
        "/users/me/profile", json={**COMPLETE, **fields}, headers=headers
    )


class TestReadingAProfile:
    def test_a_profile_nobody_has_filled_in_is_a_200_and_not_a_404(
        self, client, as_user
    ):
        """The ordinary state, and the one decision this response makes.

        Every account that existed before this feature is in this state, and so
        is every account whose holder has not filled the form in - so a 404
        would make the majority case look like a fault, and would be
        indistinguishable from the route not existing. What comes back instead
        is an empty form and the ceilings that account is already under.
        """
        response = client.get("/users/me/profile", headers=as_user())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["profile"] is None
        assert body["tier"] == "unverified"
        assert body["is_complete"] is False

    def test_it_is_this_accounts_own_standing(self, client, as_user):
        """Compared against ``/users/me`` rather than against a constant.

        The claim is "these are the same account", not "this is a known UUID" -
        so the ids are read from the two endpoints and compared, the way
        ``test_wallets.py`` does it. A response carrying somebody else's uuid
        would satisfy an assertion against a fixed string and fail this one.
        """
        me = client.get("/users/me", headers=as_user(ALICE)).json()

        body = client.get("/users/me/profile", headers=as_user(ALICE)).json()

        assert body["user_id"] == me["user_id"]

    def test_the_ceilings_are_the_ones_the_unverified_tier_imposes(
        self, client, as_user
    ):
        """Every currency, not the wallets this account holds.

        One row per ``Currency`` member, and the count is asserted rather than
        implied by the equality - a translator that sent three of five would
        pass a comparison against a similarly shortened expectation only if the
        expectation were written from the response, which is what this file
        exists not to do.
        """
        body = client.get("/users/me/profile", headers=as_user()).json()

        assert body["limits"] == expected_limits(Tier.UNVERIFIED)
        assert len(body["limits"]) == len(list(Currency))

    def test_asking_without_a_session_is_refused_at_the_door(self, client):
        """401 ``MissingCredentialsError``, and no profile is reached.

        The refusal comes from the dependency rather than from a use case that
        looked something up first - which is the property that makes this safe:
        a caller with no session never gets as far as naming an account, so
        there is nothing for a 404 to leak.
        """
        response = client.get("/users/me/profile")

        assert response.status_code == 401
        assert response.json()["error"] == "MissingCredentialsError"


class TestGivingAProfile:
    def test_a_complete_profile_is_stored_and_moves_the_tier(
        self, client, as_user
    ):
        """200, and the response is the *new* standing rather than the request.

        One round trip, because the question a person has just after pressing
        save is whether it changed anything - and answering it with the tier
        they now hold is the whole reason this endpoint is not a bare 204.
        """
        response = save_profile(client, as_user())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tier"] == "identified"
        assert body["is_complete"] is True
        assert body["limits"] == expected_limits(Tier.IDENTIFIED)
        assert body["profile"]["display_name"] == "Ada"
        assert body["profile"]["date_of_birth"] == "1990-05-17"

    def test_what_was_written_is_what_is_read_back(self, client, as_user):
        """The round trip, as one comparison of two whole bodies.

        Compared whole rather than field by field, so a field that stopped
        travelling - or one that arrived renamed - fails here rather than in a
        test that happened to look at it.
        """
        written = save_profile(client, as_user())

        read = client.get("/users/me/profile", headers=as_user())

        assert read.json() == written.json()

    def test_it_belongs_to_the_account_the_token_names(self, client, as_user):
        """Two accounts, two profiles, and neither can see the other's name."""
        save_profile(client, as_user(ALICE), display_name="Ada")

        save_profile(client, as_user(BOB), display_name="Bola")

        assert (
            client.get("/users/me/profile", headers=as_user(ALICE)).json()["profile"][
                "display_name"
            ]
            == "Ada"
        )
        assert (
            client.get("/users/me/profile", headers=as_user(BOB)).json()["profile"][
                "display_name"
            ]
            == "Bola"
        )

    def test_a_display_name_alone_is_accepted_and_is_not_enough_for_a_tier(
        self, client, as_user
    ):
        """The one required field, and not one of the four that raise the tier.

        A profile with nothing in it is refused - no profile without a display
        name exists - so this is the smallest thing a person can store, and it
        leaves them exactly where they were: unverified, with the unverified
        ceilings and four fields still missing.
        """
        response = client.put(
            "/users/me/profile",
            json={"display_name": "Ada"},
            headers=as_user(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["profile"]["display_name"] == "Ada"
        assert body["profile"]["legal_first_name"] is None
        assert body["tier"] == "unverified"
        assert body["limits"] == expected_limits(Tier.UNVERIFIED)

    def test_a_partial_profile_then_a_complete_one_moves_the_tier(
        self, client, as_user
    ):
        """Two writes to one row, which is what makes the first one an edit later.

        Nothing here creates a second profile: the second ``PUT`` finds the row
        the first one wrote and edits it, which is what ``created_at``
        surviving proves - a create-only endpoint would have written a second
        row, and the one fact it would have moved is the one that means "when
        did you first give us this".
        """
        partial = client.put(
            "/users/me/profile",
            json={"display_name": "Ada", "legal_first_name": "Adaeze"},
            headers=as_user(),
        ).json()

        body = save_profile(client, as_user()).json()

        assert partial["tier"] == "unverified"
        assert body["tier"] == "identified"
        assert body["profile"]["created_at"] == partial["profile"]["created_at"]

    def test_sending_the_same_body_twice_leaves_the_same_state(
        self, client, as_user
    ):
        """The ``PUT`` claim, and ``created_at`` is the field that tests it.

        ``updated_at`` moving is expected - it is a save. ``created_at`` not
        moving is the assertion, and it is the one a create-or-update written as
        a delete-and-recreate would fail: "when did you first give us this" is a
        fact about the row that no amount of editing changes.
        """
        first = save_profile(client, as_user()).json()
        second = save_profile(client, as_user()).json()

        assert second["tier"] == first["tier"]
        assert second["profile"]["created_at"] == first["profile"]["created_at"]
        assert second["profile"]["updated_at"] >= first["profile"]["updated_at"]

    def test_a_country_is_folded_to_its_code(self, client, as_user):
        """``"ng"`` in, ``"NG"`` out - the normalisation is the domain's.

        Asserted over the wire because this is the boundary where a client's
        lowercased input either is or is not normalised: a route that stored
        what it was handed would leave two accounts in two spellings of one
        country, and nothing downstream would notice.
        """
        body = save_profile(client, as_user(), country="ng").json()

        assert body["profile"]["country"] == "NG"


class TestARefusal:
    def test_a_country_that_is_not_a_code_is_a_400_in_the_domains_words(
        self, client, as_user
    ):
        response = save_profile(client, as_user(), country="Nigeria")

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidProfileCountryError"

    def test_a_blank_display_name_is_a_400_naming_the_field(
        self, client, as_user
    ):
        """The only required field, so this is the body a client must handle.

        ``display_name`` is stripped by the aggregate, and a stripped-empty
        value is refused rather than stored - which is why the four-space body
        below is a refusal and not a profile whose name is whitespace.
        """
        response = save_profile(client, as_user(), display_name="    ")

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidProfileDisplayNameError"

    def test_a_refused_write_leaves_nothing_behind(self, client, as_user):
        """The refusal is a refusal, not a half-applied row.

        The aggregate is built before a statement is issued, so a bad field
        aborts the call with the database exactly as it was - and that is
        asserted by *reading back*, because a row written and then rolled back
        and a row never written look identical from the response alone.
        """
        save_profile(client, as_user(), country="Nigeria")

        body = client.get("/users/me/profile", headers=as_user()).json()

        assert body["profile"] is None
        assert body["tier"] == "unverified"

    def test_a_refused_edit_leaves_the_stored_profile_alone(self, client, as_user):
        """The harder half: the row exists, and it must come back unchanged.

        This is ``Profile.revise``'s argument for validating before mutating. A
        refusal that had already written the six good fields would leave a
        profile nobody asked for, and the response would be identical.
        """
        save_profile(client, as_user(), country="NG")

        save_profile(client, as_user(), country="Nigeria")

        body = client.get("/users/me/profile", headers=as_user()).json()
        assert body["profile"]["country"] == "NG"
        assert body["tier"] == "identified"


class TestAProfileIsNotASecretAndATierIsNotARequest:
    def test_the_profile_is_readable_by_its_owner_at_any_time(self, client, as_user):
        """No password, no confirmation, no expiry - a profile is not a credential.

        Worth saying out loud because the two flows built immediately before this
        one *are* like that, and the shape of this response - a legal name, a
        date of birth, a phone number - invites the same treatment. What protects
        it is the session, and nothing else needs to.
        """
        save_profile(client, as_user())

        for _ in range(3):
            assert client.get("/users/me/profile", headers=as_user()).status_code == 200

    def test_a_body_cannot_raise_a_tier(self, client, as_user):
        """A ``tier`` key is ignored, and the state that follows is unverified.

        The load-bearing half is the second assertion. A response that ignored
        the key while a service had stored it would pass the first one, so what
        is checked is the tier *after* a fresh read - the tier is derived from
        which fields are present, and there is no field anywhere that a client
        can send that raises it.
        """
        response = client.put(
            "/users/me/profile",
            json={"display_name": "Ada", "tier": "identified"},
            headers=as_user(),
        )

        assert response.json()["tier"] == "unverified"
        assert client.get("/users/me/profile", headers=as_user()).json()["tier"] == (
            "unverified"
        )


class TestAProfileHasNoIdToSubstitute:
    def test_no_route_reaches_a_profile_by_a_user_id(
        self, client, as_user, unknown_id
    ):
        """404 for a stranger's id *and* for the caller's own, with one body.

        This is the plan's "a foreign profile is a 404 identical to a
        nonexistent one" read against the interface that actually exists. There
        is no id-addressed profile route at all, so the substitution cannot be
        written down - and the strongest version of that claim is that the
        caller cannot fetch even *their own* profile this way, which is what the
        first id below is.

        Both answers are compared, not just asserted absent, because "identical
        to a nonexistent one" is a claim about two responses rather than about
        one status code.
        """
        mine = client.get("/users/me", headers=as_user()).json()["user_id"]

        answers = [
            client.get(f"/users/{user_id}/profile", headers=as_user())
            for user_id in (mine, unknown_id)
        ]

        assert [response.status_code for response in answers] == [404, 404]
        assert answers[0].json() == answers[1].json()

    def test_no_route_writes_a_profile_by_a_user_id(
        self, client, as_user, unknown_id
    ):
        """The write half, which is the one that would matter more.

        A readable-by-id route leaks one person's details; a writable one lets a
        caller give somebody else's account a tier. Neither exists, and the body
        here is a complete valid profile so that a 404 cannot be an
        ``InvalidProfileDisplayNameError`` in disguise.
        """
        response = client.put(
            f"/users/{unknown_id}/profile", json=COMPLETE, headers=as_user()
        )

        assert response.status_code == 404
