"""What this API does not do, asserted as loudly as what it does.

Through Phase 1b this API was safe to exist before authentication for exactly one
reason: it exposed nothing that changes a balance. Phase 2a built the lock and
Phase 2b spent it, so **that reason is gone and the property is now different.**
Money moves through this API, and what keeps that honest is no longer "nothing
changes" but three separate claims, each written down here:

    the exposed set    ``TestTheRouteTableIsExactlyThis``
    the held set       routes that a later phase will add, with the phase named
    the never set      routes this API will not grow, with the reason named

**A fourth claim joined those three in 3a, and it is not about a set at all: not
every route on the wire is authenticated by a token.** ``POST /webhooks/paystack``
is exposed, is reachable by anybody who can open a socket, and is authorised by a
signature - which is a different question from "who is asking" and is answered in
a different place. It is worth naming here because this file is where somebody
comes to find out what the API's trust boundary is, and "every route in
``EXPECTED_OPERATIONS`` requires a bearer token" is a sentence that used to be
true and is now false. The signature is checked before anything is parsed, no
route can be *started* through it, and ``routes/webhooks.py`` argues the whole of
it.

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
    # Two of the seven unauthenticated writes, and the only routes on this list
    # that need nobody: they are how a caller comes to have a credential at all.
    # (The other five arrived later - the email change's confirm, the two
    # password-reset routes and, last, the two phone-verification routes. They sit
    # at the ends of this set rather than here because they are authorised by
    # something else entirely: a code mailed to an address, or texted to a handset.
    # What they have in common with these two is only the absence of a bearer
    # token, and what they have in common with *each other* is a feature.)
    # They are listed here like everything else rather than held, because the
    # boundary is about what is *routable*, and the question of whether they should
    # be rate limited is a different one that lives in the README's open list -
    # where the reset request has now joined it as the entry that most needs one.
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
    # --- money arriving, added in 3a ----------------------------------------
    #
    # The deposit, and it is the route this list spent two phases holding. It is
    # exposed now because the thing that was missing is here: **a deposit credits
    # nothing until a party outside this system says the money arrived**, and that
    # party is now able to say so.
    #
    # Note what this route is *not* authorised by. A session token would have been
    # enough to reach it in 2b and would still have been wrong, because the caller
    # *is* authorised - the request is a lie about where money came from, not a
    # question about who is asking. What makes it honest is that the credit waits
    # for Paystack, and Paystack proves itself with a signature. See the deposit
    # route's docstring and ``/webhooks/paystack`` below.
    ("post", "/wallets/{wallet_id}/deposits"),
    # --- the second kind of authority, added in 3a ---------------------------
    #
    # **The one route in this API that no person calls, and the reason it needs
    # its own category here.** The two lists below say what is absent; the list
    # above says what is exposed; and every entry in the latter that is
    # *authenticated at all* is authenticated by a bearer token. This one is not,
    # and cannot be: a payment provider reporting a movement holds no session.
    #
    # It is neither of the two shapes already on this page. ``POST /tick`` is
    # actorless *by nature* - it runs over every plan and there is nobody it could
    # act as, which is why it is in ``NEVER_ROUTED_OPERATIONS``. This is
    # actorless too, but the absence of an actor is not what authorises it: a
    # signature is, and a signature is verified. So it is exposed, it is
    # unauthenticated in the token sense, and the thing standing between a
    # stranger and the ledger is an HMAC rather than a 401.
    #
    # Anything reachable here can *settle* a movement - credit a wallet, fail a
    # transfer, reverse a settled one - and nothing here can *start* one. That
    # asymmetry is the whole safety argument for putting it on the wire at all,
    # and it is what the signature is protecting.
    ("post", "/webhooks/paystack"),
    # --- moving an account's address, added after the live run --------------
    #
    # Two routes for one feature, and they are split across two prefixes because
    # they are authorised by different things. The request is under ``/users/me``
    # and takes a bearer token *and* the account's password: a path nested under
    # an account means "you must be that account to touch it". The confirm is not
    # under it, and takes no token at all.
    #
    # **The confirm used to be the third unauthenticated write in this API and the
    # only one authorised by a thing mailed rather than presented at the time.**
    # Both halves of that sentence are now out of date, and the two entries below
    # are why: there are seven unauthenticated writes rather than three, and the
    # reset confirm is authorised by a mailed code in the same sense this one is.
    # What is still true, and is the part that matters, is that the code exists
    # only because somebody already presented the account's password to mint it,
    # and it was mailed to the address being moved to - so the proof is already
    # spent on this change, and requiring a session on top would refuse the person
    # who asked at a desk and opened the mail on a phone. See
    # ``routes/email_changes.py``.
    #
    # It is a *write*, unlike the ``GET`` on a confirmation above, and the reason
    # is the same one that makes the comparison worth drawing: looking at a
    # pending change spends nothing, and confirming one spends it. That is what
    # ``EmailChangeStatus.CONFIRMED`` means.
    ("post", "/users/me/email-changes"),
    ("post", "/email-changes/confirm"),
    # --- setting a forgotten password, added last ---------------------------
    #
    # Two routes for one feature, on **bare plural prefixes**, and unlike every
    # other entry on this page that absence is the decision rather than an
    # oversight. ``/users/me`` and ``/wallets/{id}`` both answer "whose is this?"
    # somewhere in their paths; these two cannot, because the caller is a person
    # who cannot log in. The account is inferred from an address and the mail goes
    # to whatever mailbox that address names, so a path claiming ``/users/me``
    # would promise a check this feature is unable to perform.
    #
    # **The request is the fifth unauthenticated write in this API and the first
    # aimed at an account its caller has no claim on.** Rank the five and the ranking
    # is the argument:
    # ``POST /users`` creates an account nobody had, ``POST /sessions`` exchanges a
    # secret the caller already knows, ``POST /email-changes/confirm`` acts on an
    # account the caller proved by password and then by mailbox, and
    # ``POST /password-resets/confirm`` does the same by mailbox alone - and the
    # last two differ only in which secret the mailbox lets its reader replace.
    # This one is different in kind: it takes an address, proves nothing, writes a
    # row against an account, and causes a mail. What it cannot do is read an
    # account, change one, or learn whether one exists - the response is
    # byte-identical either way - and every consequence of it requires the mailbox
    # the mail arrived in.
    #
    # It is also the route that most needs rate limiting, and it is named in the
    # README's open list for that rather than quietly shipped. The honest gap is
    # that the response is identical and the *latency* is not: an SMTP round trip
    # happens on one arm and not the other. No status code could close that, and a
    # limiter is the thing that would.
    ("post", "/password-resets"),
    #
    # The confirm, which answers with a replacement password rather than a new
    # address. Same lifecycle, same three refusals mapped to the same three
    # statuses, and the sharpest thing in this API a mailed value authorises: every
    # session on the account is deleted by it. The test beside this file's
    # ``test_the_old_bearer_token_is_refused`` is the claim in one line - the token
    # a client is holding stops working at the door.
    #
    # Note what it does not do, because it is the temptation the shape invites:
    # the account it acts on is read off the claimed row and never off the request,
    # so there is no id to substitute. The absence of a ``current_actor`` here is
    # not "authentication is optional"; it is that the code *is* the authorisation,
    # and a client holding a stale token must be neither helped nor refused by it.
    ("post", "/password-resets/confirm"),
    # --- who the account holder is, added last -------------------------------
    #
    # Two routes, and they are the only pair here on a prefix that **is** an
    # identity rather than naming one: the path segment is the literal word
    # ``me``, resolved from the bearer token like every other scoped route. So
    # there is no ``/users/{user_id}/profile`` for a caller to aim at, and the
    # 404-for-a-foreign-resource rule every other resource needs here is not
    # absent - it is unrepresentable, because there is no id to substitute.
    #
    # That matters more for this feature than for the ones above, because what
    # these routes read is a legal name, a date of birth and a phone number, and
    # what they publish is a *limit*. A wrongly resolved actor here would not
    # only be a leak; it would be a ceiling applied to the wrong account, and
    # both presentations enforce that ceiling by reading the profile at the
    # moment money moves. See ``ProfileService``.
    #
    # ``PUT`` rather than ``POST``: the resource is the account's single profile
    # row and the body carries the whole of it, so the same body sent twice
    # leaves the same state. **No tier is accepted anywhere in either
    # request** - it is derived from which fields a profile holds and stored
    # nowhere, which is what makes completing a profile the only way to raise
    # one.
    ("get", "/users/me/profile"),
    ("put", "/users/me/profile"),
    # --- signing up with a number, added last --------------------------------
    #
    # The third feature here on bare plural prefixes, and the reason is one step
    # further out than the reset pair's. There the caller cannot log in; here the
    # caller may not have an account to log into, because the account is what
    # answering the code creates. A path under ``/users/me`` would promise a check
    # there is nothing to perform it against.
    #
    # **The request is the sixth unauthenticated write in this API and the only
    # one that costs the installation money per call.** The reset request beside it
    # sends a mail through an account the operator already pays for; this one sends
    # a *text*, billed per message, through a provider, on an endpoint with no actor
    # and no rate limiter. It is the strongest entry in the README's rate-limiting
    # item, which is why it is named here rather than left to the route's own
    # comments.
    #
    # What it cannot do is the half worth checking against the reset request: it
    # cannot read anything, cannot name an account, and cannot learn whether one
    # exists - it does not ask. What it holds is a row keyed on the number it was
    # given, which the same number supersedes, so the state a stranger can create is
    # one pending verification per handset and it is only answerable from that
    # handset.
    ("post", "/phone-verifications"),
    #
    # The confirm, which is the only route in this API that *creates an account* on
    # the strength of something other than a password presented with it. The
    # credential is a code that was texted, the number is read off the claimed row
    # rather than off the request, and the account it makes has no address - so the
    # next thing its holder will do is set one, which is the email-change request
    # above and is reachable because that flow handles an account that starts with
    # no address at all. It mints no session, on ``POST /users``' precedent: the
    # caller holds two facts it can log in with, and the honest next step is
    # ``POST /sessions``.
    ("post", "/phone-verifications/confirm"),
}

#: Balance changes that are still held, each one because the movement's far end
#: is *outside* this system and nothing here can yet authorise it.
#:
#: **One entry left, down from two as of 3a**, and the one that remains is here
#: for a different reason from the deposit that just left.
#:
#: ``POST /wallets/{id}/deposits`` was the other, and it moved to
#: ``EXPECTED_OPERATIONS`` when the webhook landed - which is the only thing that
#: could have moved it. The paragraph that used to sit here said a deposit was
#: held because "the only party that can honestly say money arrived is the one
#: that sent it - Paystack, in Phase 3, proving itself with a signature". Phase 3
#: arrived, and the route is now honest for exactly that reason: it opens a
#: collection and credits nothing.
#:
#: **A deposit into a pot is still held, and it is not the same feature.** The
#: one above is money arriving into a wallet, where the destination is decided
#: when the collection is opened and the provider's event supplies nothing but an
#: amount. Potting it would mean deciding *after* the money landed which pot it
#: was for - by which time the payer has already chosen nothing and the wallet
#: has already been told the money is available. That is a routing decision this
#: system does not have, and inventing one is not a matter of lowering a flag.
#: The CLI keeps its pot-deposit command, and its ``deposit``, for the reason it
#: always had both: a developer's tool on their own database is not a wire
#: protocol and nothing it does is reachable by anybody else.
HELD_OPERATIONS = [
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
        did in 1b: the parametrize list has shrunk from fifteen entries to four,
        so a broken harness would hide less and less.
        """
        routed = client.get(f"/wallets/{real_ids['wallet_id']}", headers=as_user())

        assert routed.status_code == 200
