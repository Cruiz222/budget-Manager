"""What this API does not do, asserted as loudly as what it does.

Phase 1b is safe to exist before authentication for exactly one reason: it
exposes nothing that changes a balance. That reason is a property of the *set of
routes*, and a property of a set is not something a docstring can hold - it is
one ``@router.post`` away from being false, and the commit that makes it false
will look like an ordinary feature.

So it is written down twice here. ``TestTheRouteTableIsExactlyThis`` pins the
whole surface as a literal, and ``TestHeldOperationsAreNotRouted`` names the
endpoints that must stay absent and asks for each of them by a *real* id.

**The honest limitation, stated rather than glossed:** an unrouted path returning
404 is not evidence of a decision - it is what any typo returns. These tests
cannot prove that the boundary was reasoned about. What they can do is fail the
moment one of these routes appears, which turns "we should think about whether
this is safe yet" from something somebody has to remember into something the
suite says. That is the whole of the claim, and it is worth having: a boundary
nobody can cross by accident is a boundary, and a boundary in a document is a
wish.
"""

import pytest

#: Every operation this phase exposes, as ``(method, path)``. Path parameters are
#: spelled the way FastAPI spells them in ``openapi.json``.
#:
#: This is a *literal* rather than something derived, and that is the point of it:
#: a test that computed the expected set from the application would agree with the
#: application no matter what the application did. Adding a route means editing
#: this list, which means the diff shows a human deciding that the new route is on
#: the safe side of the line - or deciding the line has moved.
EXPECTED_OPERATIONS = {
    ("get", "/health"),
    # The two unauthenticated writes, and the only routes on this list that need
    # nobody: they are how a caller comes to have a credential at all. They are
    # listed here like everything else rather than held, because the boundary is
    # about what is *routable*, and the question of whether they should be rate
    # limited is a different one that lives in the README's open list.
    ("post", "/users"),
    ("get", "/users/me"),
    ("post", "/sessions"),
    # Authorised by the token it destroys rather than by a resolved actor, which is
    # the one place in the API where those differ - see ``routes/sessions.py``.
    ("delete", "/sessions/current"),
    ("post", "/wallets"),
    ("get", "/wallets/{wallet_id}"),
    ("get", "/wallets/{wallet_id}/transactions"),
    ("get", "/wallets/{wallet_id}/funds"),
    ("post", "/wallets/{wallet_id}/funds"),
    ("get", "/wallets/{wallet_id}/plans"),
    ("post", "/plans"),
    ("get", "/plans/{plan_id}"),
    ("get", "/plans/{plan_id}/runs"),
    ("post", "/plans/{plan_id}/pause"),
    ("post", "/plans/{plan_id}/resume"),
    ("post", "/plans/{plan_id}/cancel"),
    ("put", "/plans/{plan_id}/instructions"),
}

#: The operations that are held until Phase 2b, each one because it changes a
#: balance. Named as the routes they would plausibly take, so the day somebody
#: adds one of them this list is what says "that was a decision, and here is where
#: it was written down".
#:
#: **These are still held after 2a, and that is the point of splitting the phase.**
#: 2a built the lock - an actor that can only be reached by proving you are it -
#: and deliberately spent it on nothing. Phase 1b's reason for holding a balance
#: change was that the actor was an assertion; that reason is now gone, so what
#: remains is only the work of releasing thirteen endpoints one at a time, which is
#: 2b. Nothing here is waiting on a design question any more.
#:
#: ``tick`` and ``deliver`` are held for a different reason and are listed with
#: the rest anyway, because the consequence is the same: a background job that
#: runs for the whole installation has no actor, and an endpoint that runs it
#: would have to be told one - which would make the scheduler reachable by
#: whoever could reach the port. Sessions did not change that: the CLI now proves
#: who it is for every command that needs a person, and these two still take
#: nobody, because there is nobody they could be.
HELD_OPERATIONS = [
    ("post", "/wallets/{wallet_id}/deposits"),
    ("post", "/wallets/{wallet_id}/withdrawals"),
    ("post", "/wallets/{wallet_id}/payouts"),
    ("post", "/wallets/{wallet_id}/freeze"),
    ("post", "/wallets/{wallet_id}/unfreeze"),
    ("post", "/wallets/{wallet_id}/close"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/deposits"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/lock"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/release"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/extend"),
    ("post", "/plans/{plan_id}/run"),
    ("post", "/tick"),
    ("post", "/notifications/deliver"),
]


@pytest.fixture
def real_ids(client, as_user, open_wallet, open_pot, create_plan):
    """Real wallet, pot and plan ids, so a 404 cannot pass for the wrong reason.

    This fixture is the difference between a test and a tautology. Every held
    operation below would return 404 if it were asked about a random UUID *even
    if it were implemented* - routing happens after nothing, and a route that
    exists still answers when its path parameters are meaningless. Substituting
    ids that are genuinely in the database means the only remaining explanation
    for a 404 is that the route is not there.
    """
    headers = as_user()
    wallet_id = open_wallet(headers)
    fund_name = open_pot(wallet_id, headers)
    plan = create_plan(wallet_id, headers)
    assert plan.status_code == 201, plan.text
    return {
        "wallet_id": wallet_id,
        "fund_name": fund_name,
        "plan_id": plan.json()["plan_id"],
    }


class TestTheRouteTableIsExactlyThis:
    def test_the_surface_is_exactly_the_list_above(self, app):
        """Every operation, compared as a set in both directions at once.

        Reading ``openapi.json`` rather than walking ``app.routes`` is deliberate:
        it is the document a client generator would read and the document ``/docs``
        renders, so this asserts what the API *declares*, not merely what it has
        wired. A route excluded from the schema is still a route.

        The failure is reported as the two set differences rather than as one
        unreadable dump, because when this breaks the useful thing on screen is
        the name of the route that appeared - not a list of the fifteen that did
        not change.
        """
        paths = app.openapi()["paths"]
        actual = {
            (method, path)
            for path, operations in paths.items()
            for method in operations
        }

        added = actual - EXPECTED_OPERATIONS
        removed = EXPECTED_OPERATIONS - actual

        assert not added, f"exposed but not listed: {sorted(added)}"
        assert not removed, f"listed but not exposed: {sorted(removed)}"


class TestHeldOperationsAreNotRouted:
    @pytest.mark.parametrize(
        "method, template", HELD_OPERATIONS, ids=[f"{m} {p}" for m, p in HELD_OPERATIONS]
    )
    def test_it_is_not_there(self, client, as_user, real_ids, method, template):
        """404 or 405, and the difference between them is not worth asserting.

        A path nobody routed returns 404. A path that is routed under a *different*
        method returns 405 - ``POST /wallets/{id}`` is a real path with no POST on
        it - and that is an equally good answer to the question being asked, which
        is "can this be called?". Pinning one of the two would make the test fail
        on a change that is not a boundary change.
        """
        path = template.format(**real_ids)

        response = client.request(method.upper(), path, headers=as_user())

        assert response.status_code in (404, 405), (
            f"{method.upper()} {path} answered {response.status_code} - "
            "a held operation appears to have been exposed"
        )

    def test_the_held_list_is_not_vacuous(self, client, as_user, real_ids):
        """One of the held paths, asked the same way, *does* answer.

        Without this, the whole parametrized test above would still pass if
        ``client.request`` were silently failing - which is not a hypothetical: a
        typo in the HTTP verb name or a client that was never entered as a context
        manager produces exactly the same 404s. This pins the harness down by
        showing it can distinguish a routed path from an unrouted one.
        """
        routed = client.get(f"/wallets/{real_ids['wallet_id']}", headers=as_user())

        assert routed.status_code == 200
