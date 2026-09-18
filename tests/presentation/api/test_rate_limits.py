"""What the limits do on the wire: who gets refused, when, and in what shape.

The unit tests for the counter and the two layers live under
``tests/infrastructure/rate_limiting/``. What is left for this file is the part
only an HTTP request can answer - that a limit is *reached* before the work is
done, that the refusal is the house shape, and that the arrangement on
``POST /password-resets`` did not reintroduce the one leak that endpoint exists to
close.

**Every test here is about a refusal rather than about a rate**, and the
distinction decides how they are written. A test that drove a route at exactly its
policy rate to watch the boundary would be a test of the number in the policy
table, which is the one thing in this feature that is explicitly expected to move.
What must not move is the behaviour around it: that refusals are counted, that the
two dimensions are counted separately, and that a refusal never substitutes for an
authentication failure. Those are what is pinned here, and each is asserted against
the policy's own constants rather than against a copied number, so moving a limit
does not break a test that was never about the limit.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import composition_root
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.infrastructure.settings import EmailSettings
from app.presentation.api.app import create_app
from app.presentation.api.rate_limits import INSTALLATION, POLICIES
from tests.conftest import TEST_USER_PASSWORD
from tests.presentation.api.conftest import ALICE, BOB

#: An address at ``example.com`` that no test registers, for the arm of
#: ``POST /password-resets`` that names no account.
NOBODY = "nobody@example.com"

#: A complete mail account, so the reset route takes the mailing branch rather than
#: the 503 one. The values are never dialled - the adapter is replaced below - but
#: they must be *complete*, or the absence of settings is what the fake would stand
#: in for and the mailing path would never be reached.
#:
#: Declared here rather than imported from ``test_password_resets.py``, where an
#: identical constant exists. That file's fixture is a recording mailbox built for
#: assertions about envelopes; this one only needs the route to not be the
#: unconfigured one, and a shared import would tie two files together that are
#: asking different questions of the same setting.
MAIL = EmailSettings(
    host="smtp.example.com",
    port=2525,
    sender="budget@example.com",
    recipient="budget@example.com",
    starttls=False,
)


@pytest.fixture
def mail_client(db_path, password_hasher, build_channel, monkeypatch):
    """An installation *with* a mail account, so both arms of the reset route are real.

    The whole reason this fixture exists is the invariant test at the bottom of this
    file. On an installation with no mail, ``RequestPasswordReset`` raises before it
    looks at the address at all - so both the address that names an account and the
    one that does not are a 503, and a limiter that counted one arm and not the other
    would be indistinguishable from one that counted both. Configured mail is what
    makes the two arms two arms.

    The channel is faked at the single place it is reached, which is
    ``composition_root``'s adapter - so the settings resolution, the use case, the
    row and the route are all the real ones and only the socket is gone.
    """
    monkeypatch.setattr(
        composition_root,
        "SmtpNotificationChannel",
        lambda **kwargs: build_channel(),
    )
    with TestClient(
        create_app(
            unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
            settings=MAIL,
            password_hasher=password_hasher,
        )
    ) as client:
        yield client


def sign_up(client, email: str) -> int:
    """Attempt a registration and return its status, without asserting what it was."""
    return client.post(
        "/users", json={"email": email, "password": TEST_USER_PASSWORD}
    ).status_code


def stored_count(db_path: str, bucket: str, subject: str) -> int | None:
    """The cold layer's count for one key, read straight out of the table.

    Read after a ``flush``, which is the only moment the store has an opinion - the
    counters themselves live in the process. The table is read rather than the
    repository because what is being asserted is a number on disk, and going through
    ``live`` would filter it by a liveness question this test is not asking.
    """
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT count FROM rate_limits WHERE bucket = ? AND subject = ?",
            (bucket, subject),
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else row[0]


def test_a_refused_sign_up_is_a_429_in_the_house_shape(client):
    """The refusal looks like every other refusal this API makes, plus one header.

    ``error`` is the class name a client branches on and ``detail`` is prose, which
    is the shape ``_body`` gives all of them - and the ``Retry-After`` is the one
    thing this grade adds, because *wait* is the only remedy it has. The detail is
    asserted verbatim because it is deliberately uninformative about *which* limit
    was reached: see ``RateLimitedError``, where that vagueness is argued rather than
    accidental.
    """
    budget = POLICIES["sign_up"].subject.calls
    for _ in range(budget):
        sign_up(client, ALICE)

    response = client.post(
        "/users", json={"email": ALICE, "password": TEST_USER_PASSWORD}
    )

    assert response.status_code == 429
    assert response.json() == {
        "error": "RateLimitedError",
        "detail": "too many requests; try again shortly",
    }
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= POLICIES["sign_up"].subject.window.total_seconds()


def test_the_refusals_are_counted_too_not_only_the_admissions(client):
    """The sixth attempt at one address is refused, and the five before it were spent.

    **This is the test that decides whether the limiter bounds anything.** Only the
    first attempt can ever create the account, so the other four are 409s - and if a
    refused request did not cost a credit, the fifth 409 would still be an
    admission, the sixth would be a duplicate again, and a caller could sit on this
    endpoint making argon2 hashes at the limit's rate forever. The budget would
    bound how many requests *succeeded*, which on this route is exactly one.

    So the assertion is the whole sequence rather than its last element: five
    attempts spend the budget with four of them failing for a different reason, and
    the sixth is the first that is refused for asking too often.
    """
    budget = POLICIES["sign_up"].subject.calls

    statuses = [sign_up(client, ALICE) for _ in range(budget + 1)]

    assert statuses == [201] + [409] * (budget - 1) + [429]


def test_one_address_spending_its_budget_leaves_anothers_alone(client):
    """A budget is per address, so an abusive caller cannot lock a door for everybody.

    The failure this rules out is the one a single shared counter would produce, and
    it is worth being precise about why it matters more here than the CPU it saves:
    an attacker who could exhaust the *route* would have a way to stop anybody
    registering an account, which is a denial of service on the whole product rather
    than on one address.
    """
    budget = POLICIES["sign_up"].subject.calls
    for _ in range(budget + 1):
        sign_up(client, ALICE)

    assert sign_up(client, BOB) == 201


def test_a_subject_that_exhausts_its_budget_does_not_spend_the_installations(
    client, db_path
):
    """The two dimensions count different things, and this is the asymmetry.

    A subject's own budget is spent by its attempts, refusals included; the ceiling
    is spent only by work the installation actually admitted. So after six attempts
    at one address the store holds **six** for the address and **five** for the
    installation - and the one-row difference is exactly the refused request.

    The ordering in ``rate_limits.enforce`` is what produces it, and the reason it is
    ordered that way is the failure a naive shared counter has: if a refused request
    charged the ceiling as well, one abusive caller could exhaust the installation's
    budget by hammering and take the feature away from everybody else. The cost of
    the ordering is on the other side - a request refused *by the ceiling* has
    already spent a subject credit - and that is harmless, because the caller could
    not have done the work either way.

    Observed through a ``flush`` because the numbers are what matters and the store is
    the only place they are legible: ``held_keys`` says how many keys there are, not
    what is in them.
    """
    budget = POLICIES["sign_up"].subject.calls
    for _ in range(budget + 1):
        sign_up(client, ALICE)

    client.app.state.rate_limiter.flush()

    assert stored_count(db_path, "sign_up", ALICE) == budget + 1
    assert stored_count(db_path, "sign_up", INSTALLATION) == budget


def test_the_installation_ceiling_trips_across_many_subjects(client):
    """Walking a list of addresses is bounded even though no budget is exhausted.

    The per-subject dimension is defeated by using a different address each time,
    which costs an attacker nothing and is why the ceiling exists. It is seeded here
    rather than driven over the wire, and that is a decision about what is being
    tested: reaching a two-hundred-an-hour ceiling with HTTP requests would be two
    hundred argon2 hashes' worth of wall clock spent proving that a counter counts.
    What the wire adds is the last step - that the ceiling is consulted on a request
    that its subject dimension was happy to admit.

    Note the seeded checks are charged under the same bucket name as the request's, so
    this is the same counter the route will consult rather than a parallel one.
    """
    policy = POLICIES["sign_up"]
    limiter = client.app.state.rate_limiter
    for _ in range(policy.ceiling.calls):
        limiter.check("sign_up", INSTALLATION, policy.ceiling)

    assert sign_up(client, ALICE) == 429


def test_the_unknown_address_arm_of_the_reset_route_is_counted_too(mail_client):
    """**The invariant that makes this limiter safe to put on that route at all.**

    ``POST /password-resets`` answers identically whether or not the address names an
    account, and it does so because the answer would otherwise be an enumeration
    oracle. A limiter is the one control that can undo that by accident: if it counted
    only the arm that found an account, then 429-versus-202 would become a statement
    about whether the address exists - asked three times and then answered, which is
    an oracle a stranger can use and the leak would have been *introduced* by the
    thing meant to protect the route.

    So both addresses are driven past their budget and the two sequences are compared
    rather than each being asserted alone, because the claim is that they are the
    same. An arm-dependent limiter produces ``[202, 202, 202, 202]`` for the address
    nobody owns - the counter for it never being touched - and that is what this
    would catch.

    The two addresses are separate subjects, so the comparison is between two
    independent budgets and not between two views of one. Note the requests are all
    on the mailing path: on an installation with no mail both arms raise before the
    address is read, and the distinction could not be observed at all. That is what
    ``mail_client`` is for.
    """
    mail_client.post("/users", json={"email": ALICE, "password": TEST_USER_PASSWORD})
    budget = POLICIES["request_password_reset"].subject.calls

    def attempts(email: str) -> list[int]:
        return [
            mail_client.post("/password-resets", json={"email": email}).status_code
            for _ in range(budget + 1)
        ]

    known = attempts(ALICE)
    unknown = attempts(NOBODY)

    assert known == [202] * budget + [429]
    assert unknown == known, "the unknown address is limited differently - an oracle"


def test_a_bad_token_on_the_email_change_route_is_a_401_and_never_a_429(client):
    """Authentication is answered before frequency, so a stranger cannot be told to wait.

    Two failures are being ruled out and they are different in kind. A 429 to a caller
    who has not proved who they are would answer a question they have no standing to
    ask - and on this route the subject the limiter counts is the *account*, which
    means a caller with no account would be shown a status about somebody else's
    budget. And a budget spent by unauthenticated requests would let anybody exhaust
    a named account's allowance without ever holding its token, which turns the
    limiter into the denial of service it is meant to prevent.

    Repeated past the route's own budget, because the ordering is the claim: if
    ``current_actor`` were consulted second this would be a 401 for three requests and
    a 429 for the rest.
    """
    headers = {"Authorization": "Bearer not-a-real-token"}
    budget = POLICIES["request_email_change"].subject.calls

    statuses = [
        client.post(
            "/users/me/email-changes",
            json={"email": ALICE, "password": TEST_USER_PASSWORD},
            headers=headers,
        ).status_code
        for _ in range(budget * 3)
    ]

    assert set(statuses) == {401}


def test_a_malformed_body_is_never_charged_to_a_budget(client):
    """A body that fails validation does not spend a credit, so a typo is not a rate.

    This is a property of *where* the limiter sits rather than of anything it does,
    and it is the reason a dependency is the right place for it. ``dependencies.py``
    declares the endpoint's own pydantic model as the limiter's parameter, and FastAPI
    validates that model while solving the dependency and skips a dependency whose
    solve produced errors - so a request with no ``password`` field never reaches the
    limiter at all, and the credit is not spent.

    Worth pinning because the opposite arrangement is easy to write and hard to
    notice: a limiter that read the raw body would charge for a request the route
    never ran, and a client with a bug in its serializer would walk itself into a 429
    while making no valid request at all. Repeated past the budget, so a limiter that
    did count would be visible as a 429 at the end.
    """
    budget = POLICIES["sign_up"].subject.calls

    statuses = [
        client.post("/users", json={"email": ALICE}).status_code
        for _ in range(budget * 2)
    ]

    assert set(statuses) == {422}


def test_a_number_is_one_budget_however_it_is_spelled(client):
    """Folding the subject is what makes the budget a budget rather than a spelling count.

    ``log_in_rate_limit`` folds the number with ``fold_phone`` before using it as a
    key, and without that the same handset is a fresh budget for every way of writing
    it - ``08012345678``, ``0801 234 5678``, ``+2348012345678`` - which is a limit an
    attacker escapes by typing. The three spellings are one account, so they have to
    be one key, and the assertion is deliberately made across two spellings rather
    than against a count: a limiter keyed on the raw string would let the second
    spelling straight through.

    It is the same fold the *lookup* applies, which is the point of it being one
    module function rather than a rule each caller restates: the spelling that is
    refused and the account that is found cannot come apart.
    """
    budget = POLICIES["sign_in"].subject.calls
    national = {"phone": "0801 234 5678", "password": TEST_USER_PASSWORD}
    international = {"phone": "+2348012345678", "password": TEST_USER_PASSWORD}

    spent = [client.post("/sessions", json=national).status_code for _ in range(budget)]

    assert set(spent) == {401}, "no such account, and the budget is what limited it"
    assert client.post("/sessions", json=international).status_code == 429


def test_a_value_that_is_not_a_number_is_still_counted(client):
    """The limiter folds rather than validates, so a strange value is a subject too.

    ``log_in_rate_limit`` uses ``fold_phone`` and deliberately not the domain's
    ``checked_phone``, which raises. A limiter that could raise would answer a
    request the domain answers with a sentence about credentials with a 500 instead -
    and, worse, would spend no credit doing it, so the spelling of a number the fold
    is unhappy about would be an unlimited spelling of it.

    The domain's own behaviour is the other half and is asserted here rather than
    assumed: ``not-a-number`` is not refused as malformed, because there is no shape
    check on this path - it names no account and is refused as one, with the same
    sentence a wrong password gets. So ten refusals spend the budget and the eleventh
    is refused for asking too often, and the difference between the last two entries
    is the whole of what this control adds.
    """
    budget = POLICIES["sign_in"].subject.calls
    body = {"phone": "not-a-number", "password": TEST_USER_PASSWORD}

    statuses = [client.post("/sessions", json=body).status_code for _ in range(budget + 1)]

    assert statuses == [401] * budget + [429]
