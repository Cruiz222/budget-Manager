"""What this API does not do, asserted as loudly as what it does.

Through Phase 1b this API was safe to exist before authentication for exactly one
reason: it exposed nothing that changes a balance. Phase 2a built the lock and
Phase 2b spent it, so **that reason is gone and the property is now different.**
Money moves through this API, and what keeps that honest is no longer "nothing
changes" but three separate claims, each written down here:

    the exposed set    ``TestTheRouteTableIsExactlyThis``
    the held set       routes that a later phase will add, with the phase named
    the never set      routes this API will not grow, with the reason named

The first is a property of a *set of routes*, and a property of a set is not
something a docstring can hold - it is one ``@router.post`` away from being
false, and the commit that makes it false will look like an ordinary feature. So
``EXPECTED_OPERATIONS`` pins the whole surface as a literal.

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
    # --- the money, added in 2b -------------------------------------------
    #
    # The five wallet operations, and the split between them is settlement
    # rather than safety: freeze, unfreeze and close change no balance at all,
    # while a withdrawal and a payout *debit* one and leave the row PENDING,
    # because the far end is a bank account nothing here has contacted. A
    # client must not read the 201 as "the money arrived"; the body says
    # ``pending`` and there is nothing in this phase to wait on.
    #
    # The last three of these do not move money any more. Since the second-level
    # confirmation, ``withdrawals``, ``payouts`` and ``close`` record a *request*
    # and answer with it; the money moves at ``/confirmations/{id}/confirm``
    # below, which is the only route in this API that spends what one of these
    # recorded. The paths and the 201 are unchanged, which is deliberate: the
    # resource a client addresses is still "a withdrawal from this wallet" - what
    # changed is that creating one is now a question rather than a movement.
    ("post", "/wallets/{wallet_id}/withdrawals"),
    ("post", "/wallets/{wallet_id}/payouts"),
    ("post", "/wallets/{wallet_id}/freeze"),
    ("post", "/wallets/{wallet_id}/unfreeze"),
    ("post", "/wallets/{wallet_id}/close"),
    # --- answering one, added with the confirmation -------------------------
    #
    # Two routes, and they are the second half of the three above. ``confirm`` is
    # the only operation in this API that carries out a decision somebody made
    # earlier rather than one they are making now - and it takes no body, because
    # everything about the movement is already on the request it names. See
    # ``routes/confirmations.py``.
    #
    # ``GET`` is here rather than held because a request that cannot be read back
    # is a request a client cannot debug: an ``expired`` answer is the only way
    # to find out that a prompt was left sitting too long, and it costs no write
    # to give - expiry is derived, so looking at a request does not spend it.
    ("post", "/confirmations/{confirmation_id}/confirm"),
    ("get", "/confirmations/{confirmation_id}"),
    # The three pot operations, which settle immediately - a lock and a release
    # move money between the wallet's own balances, so there is nothing left to
    # confirm and the row comes back SUCCESSFUL.
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/lock"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/release"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/extend"),
}

#: Balance changes that are still held, each one because the movement's far end
#: is *outside* this system and nothing here can yet authorise it.
#:
#: **These two are the whole list as of 2b**, down from thirteen, and the single
#: reason they share is worth stating precisely, because it is not "we ran out of
#: time". A deposit is money arriving from outside, and the only party that can
#: honestly say money arrived is the one that sent it - Paystack, in Phase 3,
#: proving itself with a signature. Exposed now, ``POST /wallets/{id}/deposits``
#: would let any authenticated caller credit their own wallet for free, which is
#: not a boundary this API can draw with a session token: the caller *is*
#: authorised, and the request is still a lie about where money came from.
#:
#: The CLI keeps its ``deposit`` command for the same reason it always had it. A
#: developer's tool operating on their own database is not a wire protocol, and
#: nothing it does is reachable by anybody else.
HELD_OPERATIONS = [
    ("post", "/wallets/{wallet_id}/deposits"),
    ("post", "/wallets/{wallet_id}/funds/{fund_name}/deposits"),
]

#: Operations this API will not grow, which is a stronger statement than "not
#: yet" and is why they live in their own list rather than among the held ones.
#:
#: ``tick`` and ``deliver`` are installation-wide background jobs with no actor -
#: they run from cron, over every plan in the database, and the CLI proves who it
#: is for every command that *needs* a person while these two still take nobody,
#: because there is nobody they could be. An endpoint that ran them would have to
#: be told an actor, and the only actor that could mean anything is "whoever
#: holds the token" - which would make the scheduler reachable by anyone who
#: could reach the port. Sessions did not change that; they are what makes it
#: answerable.
#:
#: ``POST /plans/{id}/run`` is the same shape of answer for a different reason.
#: It is not actorless - a plan has an owner, and decision 56 gives the scheduler
#: no privilege by minting one ``ExecutePlanRun`` per plan acting as that plan's
#: user. It is simply not a request anybody makes: a plan fires when a clock says
#: so, and letting a client name the moment would let it pay itself early, run an
#: occurrence twice, or drive a schedule the user set up and forgot about. The
#: scheduler is the only caller, and it is not on the wire.
NEVER_ROUTED_OPERATIONS = [
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


class TestTheAbsentOperationsAreStillAbsent:
    """Both absent lists, checked the same way and kept apart in the report.

    One test body over the union rather than two, because the question asked of
    every entry is identical - "can this be called?" - and duplicating the
    request would only give two places for the harness to rot. What differs
    between the lists is the *reason*, and a reason is not something this test
    can check; it is what the list-level docstrings above are for. The ids in the
    parametrize keep the two groups distinguishable in the output, so a failure
    says which kind of promise was broken.
    """

    @pytest.mark.parametrize(
        "method, template",
        HELD_OPERATIONS + NEVER_ROUTED_OPERATIONS,
        ids=[f"held {m} {p}" for m, p in HELD_OPERATIONS]
        + [f"never {m} {p}" for m, p in NEVER_ROUTED_OPERATIONS],
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
            "an absent operation appears to have been exposed"
        )

    def test_the_absent_list_is_not_vacuous(self, client, as_user, real_ids):
        """One of the absent paths, asked the same way, *does* answer.

        Without this, the whole parametrized test above would still pass if
        ``client.request`` were silently failing - which is not a hypothetical: a
        typo in the HTTP verb name or a client that was never entered as a context
        manager produces exactly the same 404s. This pins the harness down by
        showing it can distinguish a routed path from an unrouted one.

        It is the same check it has always been, and it matters more now than it
        did in 1b: the parametrize list has shrunk from fifteen entries to five,
        so a broken harness would hide less and less.
        """
        routed = client.get(f"/wallets/{real_ids['wallet_id']}", headers=as_user())

        assert routed.status_code == 200
