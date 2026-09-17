"""Exchanging a verified Google identity for a session.

The mirror of ``test_sign_up_with_google.py``, and the two files are deliberately
separate for the reason the two use cases are: one creates an account and the other
hands out a session, and a single file covering both would make "which half refused
this" a question about the file rather than about the operation.

What is worth testing narrowly here: the subject is what is looked up, an unknown
subject is refused rather than registered, the refusal is ``LogIn``'s own sentence,
and an unverified address is refused here as well - which looks redundant for a
login and is not, since the address is what the reset flow mails to.

As in the sign-up file, ``FakeGoogleIdentityVerifier`` decides validity by script,
so these tests check policy rather than cryptography. The cryptography is
``tests/infrastructure/identity/test_pyjwt_google_identity_verifier.py``'s.
"""

from datetime import datetime
from uuid import uuid4

import pytest

from app.application.identity.account_creation import record_new_google_account
from app.application.identity.log_in_with_google import LogInWithGoogle
from app.domain.identity.exception import (
    InvalidCredentialsError,
    InvalidGoogleTokenError,
    UnverifiedGoogleEmailError,
)
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)

NOW = datetime(2026, 3, 2, 12, 0)
SUBJECT = "114988223156872419036"
OTHER_SUBJECT = "109876543210987654321"
ADDRESS = "chinedu@example.com"

#: ``LogIn``'s sentence, **written out here rather than imported from that module**.
#:
#: A second copy for the reason ``test_log_in.py``'s copy is one, and here the copy
#: is doing something that file's cannot: ``LogInWithGoogle`` gets this string by
#: *importing* it, so the two use cases agree by construction and nothing inside the
#: application layer would notice if that import were dropped and the sentence
#: re-worded. This file is the only place where the claim "a Google login and a
#: password login refuse in the same words" is checkable at all.
#:
#: Note it is the same value as that file's ``_SHARED_REFUSAL`` and is not imported
#: from it, for the same reason it is not imported from ``log_in``: two copies that
#: must agree is the property, and a single import would make the check vacuous -
#: and would additionally couple this file to a private name in a sibling test
#: module.
_REFUSAL = "those details did not match an account"


@pytest.fixture
def factory(tmp_path):
    return SqliteUnitOfWorkFactory(str(tmp_path / "identity.db"))


@pytest.fixture
def google_account(factory):
    """An account that arrived through Google: a subject, and no password.

    **Written with ``record_new_google_account`` rather than driven through
    ``SignUpWithGoogle``**, on the argument ``test_log_in.py``'s ``phone_account``
    fixture makes for writing its own rows below the rule: this file is about what a
    *login* does with an account, and minting one through the signup flow would turn
    every failure here into a puzzle about two operations. The helper is tested
    where it is used.

    Note what is absent and why it is the point of this fixture: no password, so no
    ``password_credentials`` row, and therefore no hasher anywhere in this file. A
    Google login that needed one would be a login that compared a password.
    """

    def _create(
        subject: str = SUBJECT, email: str | None = ADDRESS
    ) -> User:
        user = User(
            user_id=uuid4(),
            email=email,
            phone=None,
            google_subject=subject,
            created_at=NOW,
        )
        uow = factory.start()
        try:
            record_new_google_account(uow, user=user)
            uow.commit()
        finally:
            uow.rollback()
        return user

    return _create


@pytest.fixture
def log_in_with_google(factory, build_google_verifier):
    def _build(**kwargs):
        verifier = build_google_verifier(**kwargs)
        return LogInWithGoogle(factory, verifier=verifier), verifier

    return _build


class TestASuccessfulLogin:
    def test_it_returns_the_account_the_subject_belongs_to(
        self, google_account, log_in_with_google
    ):
        account = google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        logged_in = service.execute(token, NOW)

        assert logged_in.user.user_id == account.user_id
        assert logged_in.user.email == ADDRESS

    def test_it_returns_a_session_and_the_token_that_matches_it(
        self, google_account, log_in_with_google
    ):
        """The pair, and the relation between them - the same claim ``LogIn`` makes.

        Worth asserting here rather than assuming it comes along, because the token
        is minted on this path by the same ``Session.issue`` and could stop being:
        the id_token that arrived is a *Google* proof and is not a session, so a
        use case that returned it would be handing the client a credential for a
        different system.
        """
        account = google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        logged_in = service.execute(token, NOW)

        assert logged_in.session.user_id == account.user_id
        assert logged_in.session.token_hash == hash_session_token(logged_in.token)

    def test_the_session_is_stored(self, factory, google_account, log_in_with_google):
        """Read back through the repository, not trusted from the returned value."""
        google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        logged_in = service.execute(token, NOW)

        uow = factory.start()
        try:
            stored = uow.sessions.find_by_token_hash(
                hash_session_token(logged_in.token)
            )
        finally:
            uow.rollback()

        assert stored is not None

    def test_the_google_token_is_not_what_comes_back(
        self, google_account, log_in_with_google
    ):
        """The two tokens are different things and the response carries ours.

        A client that received the id_token back would appear to be signed in - it
        would hold a credential that verifies at Google - and would fail on the very
        next call against this system, which is the worst of both: a success it
        cannot use, reported as one.
        """
        google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=SUBJECT, email=ADDRESS)

        logged_in = service.execute(token, NOW)

        assert logged_in.token != token
        assert logged_in.token != ""


class TestTheSubjectIsTheKey:
    def test_the_address_on_the_token_is_not_what_is_looked_up(
        self, factory, google_account, log_in_with_google
    ):
        """**The whole reason an account stores a subject.**

        The account's stored address is one thing and the token's address is
        another, and the login succeeds anyway - because the lookup is by subject.
        This is not an edge case: a Google account's address changes, and the
        subject does not. An account found by address would become unreachable the
        moment its owner changed their Gmail.
        """
        account = google_account(email="old-address@example.com")
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=SUBJECT, email="new-address@example.com")

        logged_in = service.execute(token, NOW)

        assert logged_in.user.user_id == account.user_id

    def test_the_stored_address_is_left_alone(
        self, factory, google_account, log_in_with_google
    ):
        """And a login does not quietly re-point the account at the new one.

        Updating the address here would be the tempting thing to do and it is out
        of scope: it is an address *change*, which this system has a flow for - a
        request, a mailed code, a confirmation - and doing it as a side effect of a
        login would be ``ResolveUserByEmail``'s find-or-create in its other
        direction: a write nobody asked for, on an unauthenticated path.
        """
        account = google_account(email="old-address@example.com")
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=SUBJECT, email="new-address@example.com")

        service.execute(token, NOW)

        uow = factory.start()
        try:
            stored = uow.users.get_by_id(account.user_id)
        finally:
            uow.rollback()

        assert stored.email == "old-address@example.com"


class TestTheRefusals:
    def test_a_subject_with_no_account_is_refused(self, log_in_with_google):
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=OTHER_SUBJECT, email=ADDRESS)

        with pytest.raises(InvalidCredentialsError):
            service.execute(token, NOW)

    def test_the_refusal_is_the_login_refusal_word_for_word(
        self, google_account, log_in_with_google
    ):
        """The sentence a client receives, asserted by value rather than by import.

        See ``_REFUSAL`` above for why this file writes the string out. The claim is
        that a person who presents a Google identity naming no account here and a
        person who types the wrong password are told the same thing - which is a
        decision about the *words*, so the words are what is asserted.
        """
        google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=OTHER_SUBJECT, email=ADDRESS)

        with pytest.raises(InvalidCredentialsError) as refusal:
            service.execute(token, NOW)

        assert str(refusal.value) == _REFUSAL

    def test_an_unknown_subject_writes_no_account(
        self, factory, log_in_with_google
    ):
        """**It refuses rather than registering**, and the row is the proof.

        This is the decision that keeps the operation from being the find-or-create
        ``SignUp``'s docstring records ``ResolveUserByEmail`` being replaced for. A
        login that created an account as a side effect of a Google identity naming
        one would make the create route's 409 meaningless - there would be nothing
        left that only the sign-up half does.
        """
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=OTHER_SUBJECT, email=ADDRESS)

        with pytest.raises(InvalidCredentialsError):
            service.execute(token, NOW)

        uow = factory.start()
        try:
            assert uow.users.find_by_google_subject(OTHER_SUBJECT) is None
            assert uow.users.find_by_email(ADDRESS) is None
            assert uow.sessions.find_by_token_hash(hash_session_token("x")) is None
        finally:
            uow.rollback()

    def test_a_token_the_verifier_rejects_is_refused(self, log_in_with_google):
        service, _ = log_in_with_google()

        with pytest.raises(InvalidGoogleTokenError):
            service.execute("a-token-nobody-minted", NOW)

    def test_the_token_is_verified_even_when_no_account_exists(
        self, log_in_with_google
    ):
        """**Verification happens before the lookup**, and the recording says so.

        An unverified pass would let a caller learn whether a subject is registered
        by presenting a token that proves nothing and reading the two refusals
        apart. Verification is also the only thing standing between a stranger and
        a session, so it is asserted to have happened on the path where its answer
        is "no account" as well as on the path where it is "here you are".
        """
        service, verifier = log_in_with_google()
        token = verifier.mint(subject=OTHER_SUBJECT, email=ADDRESS)

        with pytest.raises(InvalidCredentialsError):
            service.execute(token, NOW)

        assert verifier.attempts == [token]


class TestTheUnverifiedAddress:
    """The check that looks redundant for a login and is not.

    See ``LogInWithGoogle``'s class docstring: the account's address is what
    ``RequestPasswordReset`` mails to, so an identity whose address Google has
    merely *claimed* must not be able to reach the account that holds it.
    """

    def test_it_is_refused_even_though_the_subject_is_known(
        self, google_account, log_in_with_google
    ):
        google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(
            subject=SUBJECT, email=ADDRESS, email_verified=False
        )

        with pytest.raises(UnverifiedGoogleEmailError):
            service.execute(token, NOW)

    def test_it_writes_no_session(
        self, factory, google_account, log_in_with_google
    ):
        """**A refusal that is not a proof weighs nothing against a known subject.**

        ``UnverifiedGoogleEmailError`` is raised before the unit opens, so the
        account is never even read - and the visible consequence is that no session
        was issued. Asserted against the store rather than inferred from where the
        ``raise`` sits in the source, because "the refusal comes first" and "no row
        exists" are different claims and it is the second that matters.
        """
        account = google_account()
        service, verifier = log_in_with_google()
        token = verifier.mint(
            subject=SUBJECT, email=ADDRESS, email_verified=False
        )

        with pytest.raises(UnverifiedGoogleEmailError):
            service.execute(token, NOW)

        uow = factory.start()
        try:
            # **``delete_by_user_id`` used as a count, inside a unit that is rolled
            # back**, which deserves the sentence. The session port has no
            # "list this account's sessions" read - ``find_by_token_hash`` is the
            # only one, and a session this test is asserting *does not exist* has
            # no token to ask by. Its delete returns the number of rows it removed,
            # which is the question being asked here, and the ``rollback`` in the
            # ``finally`` below is what keeps the answer from costing a row. The
            # alternative was to leave the claim asserted in prose, which is the
            # thing this file is written to avoid.
            assert uow.sessions.delete_by_user_id(account.user_id) == 0
        finally:
            uow.rollback()
