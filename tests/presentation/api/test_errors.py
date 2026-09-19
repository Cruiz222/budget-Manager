"""One test per row of the status table, driven through a real rejection.

The table in ``app/presentation/api/errors`` is the API's contract with a client,
and a contract written only in a docstring is a claim. Each test here sends a
request that a real service refuses for a real reason and asserts the grade the
table promises - so the table cannot drift away from the behaviour without
something going red.

**Why the grades matter enough to test.** A client does different things with
them: a 401 means prove who you are and try again, a 404 means stop and check the
id, a 409 means the thing exists and is not in a state for this, a 400 means fix
the request, a 422 means fix the shape, and a 500 means the server is broken and a
retry may help. Collapsing them - which is exactly what a single handler for
``MoneyError`` would do - throws away the only thing HTTP says about *why*, and
leaves every client to parse English out of a message.

The 401 row is not tested here. It belongs to ``test_actor.py``, which is where
every way of arriving at one lives, and splitting it across two files would put
half an answer in each. What is here is everything a *request* can be refused for
once the caller is established.

The last row is the interesting one. A 500 is not a domain refusal at all, so it
has nothing to do with the taxonomy above; what it tests is that a bug is
reported as a bug, with none of the bug in the response.
"""

from fastapi.testclient import TestClient

from app.presentation.api.app import create_app
from app.presentation.api.rate_limits import POLICIES
from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE


class BrokenFactory:
    """Storage that raises something no handler has heard of.

    A ``RuntimeError`` rather than a SQLite error, because the point is the
    *unknown* case: ``sqlite3.OperationalError`` is a real thing that can happen
    and might reasonably get a handler of its own one day, and this test would
    then be about that handler instead of about the catch-all.
    """

    def start(self):
        raise RuntimeError("the storage layer is having a bad day")


class TestNotFound:
    """404 - not there *for this actor*. The isolation suite covers the foreign case."""

    def test_an_unknown_wallet(self, client, as_user, unknown_id):
        response = client.get(f"/wallets/{unknown_id}", headers=as_user())

        assert response.status_code == 404
        assert response.json()["error"] == "WalletNotFoundError"

    def test_an_unknown_plan(self, client, as_user, unknown_id):
        response = client.get(f"/plans/{unknown_id}", headers=as_user())

        assert response.status_code == 404
        assert response.json()["error"] == "SavingsPlanNotFoundError"

    def test_an_unknown_wallet_is_a_404_and_not_a_400(self, client, as_user, unknown_id):
        """``WalletNotFoundError`` is a ``MoneyError``, so this is the ordering test.

        Every unlisted ``MoneyError`` is graded 400, which is the safe default -
        and it would swallow this one if ``NOT_FOUND`` were not checked first. A
        client told that a wallet it cannot see is a malformed request would go
        and rewrite a perfectly good request, which is the worst possible advice.
        """
        response = client.get(f"/wallets/{unknown_id}/transactions", headers=as_user())

        assert response.status_code == 404


class TestConflict:
    """409 - the resource is there and its state refuses this."""

    def test_a_pot_name_already_taken_on_this_wallet(self, client, as_user, open_wallet, open_pot):
        headers = as_user()
        wallet_id = open_wallet(headers)
        open_pot(wallet_id, headers, name="Vacation")

        response = client.post(
            f"/wallets/{wallet_id}/funds",
            json={"name": "Vacation", "kind": "personal"},
            headers=headers,
        )

        assert response.status_code == 409
        assert response.json()["error"] == "DuplicateFundNameError"

    def test_replaying_a_refused_reference(self, client, as_user, open_wallet, open_pot):
        """The second 409 on a money route, and the one that is not the wallet's.

        ``lock`` is one of the four operations with no confirmation in front of
        it, so the reference is the only thing that can refuse a replay - and this
        asserts that it does, in two steps that are different claims. The first
        call is refused for the wallet's own reason (there is nothing available to
        lock) and writes a FAILED row; the second finds that row and refuses
        rather than handing it back as though the lock had happened. Grading the
        first 409 alone would pass on a route that refused every repeat forever,
        which is why the error *names* are asserted rather than the codes.

        A 409 rather than a 400 or a 404, and the module docstring's rule decides
        it: the row exists and its state refuses this. See
        ``ReferenceAlreadyRefusedError``.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        name = open_pot(wallet_id, headers, name="Vacation")
        url = f"/wallets/{wallet_id}/funds/{name}/lock"
        body = {"amount": "1000.00", "ref": "lock-2026-09-19"}

        first = client.post(url, json=body, headers=headers)
        second = client.post(url, json=body, headers=headers)

        assert first.status_code == 409
        assert first.json()["error"] == "InsufficientFundsError"
        assert second.status_code == 409
        assert second.json()["error"] == "ReferenceAlreadyRefusedError"

    def test_pausing_a_plan_that_is_already_paused(self, client, as_user, open_wallet, create_plan):
        """The second ``pause`` is the interesting one, and the first is the control.

        Without the first assertion this test would pass if ``pause`` were broken
        outright - a 409 from a plan that was never pausable would look exactly
        like a 409 from a plan that was paused twice.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        plan_id = create_plan(wallet_id, headers).json()["plan_id"]

        first = client.post(f"/plans/{plan_id}/pause", headers=headers)
        second = client.post(f"/plans/{plan_id}/pause", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["error"] == "PlanNotActiveError"

    def test_resuming_a_plan_that_was_never_paused(self, client, as_user, open_wallet, create_plan):
        """The mirror image, because the two refusals come from different rules.

        A single test for "steering a plan in the wrong state is a 409" would pass
        with either rule implemented and the other missing.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        plan_id = create_plan(wallet_id, headers).json()["plan_id"]

        response = client.post(f"/plans/{plan_id}/resume", headers=headers)

        assert response.status_code == 409
        assert response.json()["error"] == "PlanNotPausedError"

    def test_a_wallet_in_a_currency_this_rail_cannot_collect(
        self, client, as_user, open_wallet
    ):
        """The newest row, and the grade that is least obvious of the three here.

        A 400 is ruled out because the request is well formed and there is nothing
        in it for the caller to fix; a 503 is ruled out because this installation
        serves deposits perfectly well against a wallet in the currency it
        collects. What refuses *this* request is the wallet's own state - it is
        held in a currency the rail cannot collect - which is the shape the module
        docstring describes for 409 and the row ``WalletClosedError`` already sits
        on.

        ``open_wallet`` is asked for dollars rather than a wallet being written
        into the store, and that is worth noting: creating one is still allowed,
        deliberately. What is refused is the collection, not the container.
        """
        headers = as_user()
        wallet_id = open_wallet(headers, currency="USD")

        response = client.post(
            f"/wallets/{wallet_id}/deposits",
            json={"amount": "5000.00"},
            headers=headers,
        )

        assert response.status_code == 409
        assert response.json()["error"] == "CurrencyNotCollectableError"


class TestBadRequest:
    """400 - a value in the request is not acceptable.

    These three are also the tests that keep ``translate._member`` and
    ``translate.money_in`` honest. An enum read as ``Currency("XYZ")`` raises a
    plain ``ValueError``, which is not a ``MoneyError`` - so without those two
    helpers every one of these requests would come back as a 500, telling the
    client the server had broken when they had mistyped a word.
    """

    def test_a_currency_the_system_does_not_know(self, client, as_user):
        response = client.post("/wallets", json={"currency": "XYZ"}, headers=as_user())

        assert response.status_code == 400
        assert response.json()["error"] == "UnsupportedCurrencyError"

    def test_an_amount_that_is_not_a_number(
        self, client, as_user, open_wallet, create_plan, payout_line
    ):
        headers = as_user()
        response = create_plan(
            open_wallet(headers),
            headers,
            instructions=[payout_line("banana", "March rent")],
        )

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidAmountError"

    def test_a_cadence_that_is_not_a_cadence(self, client, as_user, open_wallet, create_plan):
        headers = as_user()
        response = create_plan(
            open_wallet(headers),
            headers,
            schedule={"cadence": "fortnightly", "anchor": "2026-03-02T12:00:00"},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidScheduleCadenceError"

    def test_an_instruction_action_that_is_not_one(
        self, client, as_user, open_wallet, create_plan, payout_line
    ):
        headers = as_user()
        response = create_plan(
            open_wallet(headers),
            headers,
            instructions=[payout_line(action="teleport")],
        )

        assert response.status_code == 400
        assert response.json()["error"] == "InvalidInstructionActionError"

    def test_an_empty_instruction_list_is_the_domains_refusal_not_the_schemas(
        self, client, as_user, open_wallet, create_plan
    ):
        """A 400 and not a 422, and this is the test that decides which layer owns it.

        ``CreatePlanIn.instructions`` has no ``min_length`` on purpose: a plan with
        no lines is a plan that does nothing, and ``SavingsPlan`` already says so
        in its own words. A length check at the edge would answer the same request
        in pydantic's vocabulary, giving one question two answers - and the answer
        a client got would depend on which layer happened to notice first.
        """
        headers = as_user()
        response = create_plan(open_wallet(headers), headers, instructions=[])

        assert response.status_code == 400
        assert response.json()["error"] == "EmptyPlanInstructionsError"


class TestUnprocessable:
    """422 - the request did not have the shape the endpoint declares.

    FastAPI's own, left alone. Asserting it here is not testing the framework; it
    is pinning the *boundary* between the two failure vocabularies, which is the
    thing a client has to be able to rely on. A domain refusal is a 400 with the
    domain's words in it; a malformed request is a 422 in the framework's. Neither
    is allowed to become the other.
    """

    def test_a_required_field_is_missing(self, client, as_user):
        response = client.post("/wallets", json={}, headers=as_user())

        assert response.status_code == 422

    def test_a_field_has_the_wrong_type(self, client, as_user):
        """``wallet_id`` is a UUID, and ``"not-a-uuid"`` is not one.

        Worth its own test because it is the case a client is most likely to hit
        and the one most likely to be "helpfully" handled: parsed leniently here,
        it would become a 404 from a service that never received a usable id, and
        the client would go looking for a missing wallet instead of a typo.
        """
        response = client.get("/wallets/not-a-uuid", headers=as_user())

        assert response.status_code == 422

    def test_two_ways_to_end_a_plan_at_once(self, client, as_user, open_wallet, create_plan):
        """``ends_on`` and ``term`` are refused together, at the schema, as a 422.

        Unlike the empty-instruction case above, there is no domain rule being
        duplicated here - there is no aggregate that could be asked, because the
        term has to be resolved *before* a ``SavingsPlan`` exists. Refusing both at
        the edge is the same call the CLI makes with a mutually-exclusive
        argument group, and it comes back in the framework's vocabulary because
        that is the layer that can see it.
        """
        headers = as_user()
        response = create_plan(
            open_wallet(headers),
            headers,
            ends_on="2027-03-02",
            term={"amount": 12, "unit": "months"},
        )

        assert response.status_code == 422


class TestTooManyRequests:
    """429 - the same caller is asking too often, and the answer is *come back later*.

    **The one grade in the table whose remedy is not a change to the request.** Every
    other row here is a client doing something it can fix: present a token, correct an
    id, send a field, fix the shape. This request is well formed, the caller may be
    perfectly entitled to make it, and the only thing wrong with it is how recently
    they made the last one - which is why it is the only response in this API that
    carries ``Retry-After``, and why the test asserts the header rather than only the
    status.

    **Ruling out the two neighbours is most of what this test is for.** A 503 would
    say this installation cannot serve the request whoever asks, and a client that
    read a 429 as one would take the whole installation offline on the strength of a
    single abusive caller. A 400 would say the request is unacceptable, which sends a
    client to fix something that is not broken - and on ``POST /password-resets`` it
    would be worse than unhelpful, since it would hand back a distinction that route
    is built to withhold. So the grade is asserted against its neighbours as well as
    against itself.

    The route under it is ``POST /users``, and the budget is read from the policy
    table rather than written here: the numbers are the part explicitly expected to
    move, and a test of the grade should not break when one does.
    """

    def _spend_the_budget(self, client) -> None:
        for _ in range(POLICIES["sign_up"].subject.calls):
            client.post(
                "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
            )

    def test_it_is_a_429_and_not_either_of_its_neighbours(self, client):
        self._spend_the_budget(client)

        response = client.post(
            "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        )

        assert response.status_code == 429
        assert response.status_code not in (400, 503)

    def test_the_body_is_the_one_shape_every_failure_uses(self, client):
        """``error`` is the class name and ``detail`` is prose, as everywhere else.

        The name is asserted rather than the prose, because the name is the field a
        client branches on. The detail is deliberately uninformative about *which*
        limit was reached - see ``RateLimitedError``, where that vagueness is argued
        rather than accidental - so a test pinning it would be pinning the one part
        of this response that is meant to be able to change.
        """
        self._spend_the_budget(client)

        response = client.post(
            "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        )

        assert response.json()["error"] == "RateLimitedError"
        assert response.json()["detail"]

    def test_carries_a_retry_after_that_is_the_callers_own_remaining_window(self, client):
        """Seconds, and never zero.

        ``Retry-After: 0`` would invite an immediate retry, which is the opposite of
        what this response is for, and a value rounded down would tell a client that
        obeys the header to come back a fraction early and be refused again. The
        ceiling is the policy's own window, because the header describes the window
        that refused and nothing longer.
        """
        self._spend_the_budget(client)

        response = client.post(
            "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        )

        retry_after = int(response.headers["Retry-After"])
        assert 1 <= retry_after <= POLICIES["sign_up"].subject.window.total_seconds()

    def test_the_header_is_absent_from_every_other_refusal(self, client):
        """Only this grade carries one, which is a fact about the whole tree.

        ``ApiError`` gained a ``headers`` mapping so that this one class could carry
        ``Retry-After`` without the handler learning its name. The risk in that
        arrangement is not that it fails to forward the header - that is asserted
        above - but that it starts attaching headers to refusals that never had any.
        So an ordinary refusal is checked for their absence: a 409 from the same route
        and a 401 from a route that requires a token.

        The first request is a control and it is load-bearing, for the reason
        ``TestConflict.test_pausing_a_plan_that_is_already_paused`` gives about its
        own first assertion: nothing on this fixture has registered an account, so the
        request that makes the *second* one a duplicate is this test's own. Without
        it the "409" would be the 201 of a first sign-up, and a test that asserted
        only the header's absence would pass on a 201 - which is a refusal that does
        not exist.
        """
        first = client.post(
            "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        )
        assert first.status_code == 201, "the control: this address was not taken yet"

        duplicate = client.post(
            "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
        )
        assert duplicate.status_code == 409
        assert "Retry-After" not in duplicate.headers

        unauthorized = client.get("/users/me")
        assert unauthorized.status_code == 401
        assert "Retry-After" not in unauthorized.headers


class TestAServerFault:
    def test_a_bug_is_a_500_in_the_one_shape_every_failure_uses(self, as_user):
        """The catch-all, and the reason it is not left to the framework's default.

        Starlette's own 500 is a plain-text body, which would make a client's
        error handling two-shaped: JSON for everything it can cause and a bare
        string for everything it cannot. Routing the unknown case through the same
        ``_body`` helper keeps it a single parse.

        The header is present and the failure is still the storage layer's. That
        is the point: identity resolves *first*, so a broken database has to break
        after the caller has been named - otherwise a failure to reach storage
        would be reported as a missing header, which is a lie about the request.
        """
        broken = create_app(unit_of_work_factory=BrokenFactory())
        with TestClient(broken, raise_server_exceptions=False) as client:
            response = client.post(
                "/wallets", json={"currency": "NGN"}, headers=as_user()
            )

        assert response.status_code == 500
        assert response.json() == {
            "error": "InternalServerError",
            "detail": "internal server error",
        }

    def test_it_says_nothing_about_what_went_wrong(self, as_user):
        """``raise_server_exceptions=False`` is load-bearing, so it is worth explaining.

        Starlette always re-raises after an ``Exception`` handler has run - the
        handler's job is to produce a response for the *client*, and the re-raise
        is so the server can log it. ``TestClient`` by default catches that
        re-raise and propagates it, so the assertions below would never be
        reached. Turning it off is what lets this test see what a real client
        sees, which is the only thing this test is about.

        The message is checked because the failure mode it guards against is
        subtle: an exception handler that echoed ``str(exc)`` would leak a file
        path, a SQL fragment, or a value out of somebody else's row - to a caller
        who has proved nothing, behind a 500 that looks harmless.
        """
        broken = create_app(unit_of_work_factory=BrokenFactory())
        with TestClient(broken, raise_server_exceptions=False) as client:
            body = client.post(
                "/wallets", json={"currency": "NGN"}, headers=as_user()
            ).text

        assert "having a bad day" not in body
        assert "RuntimeError" not in body
