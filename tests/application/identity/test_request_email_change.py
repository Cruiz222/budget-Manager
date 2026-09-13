"""Asking to move an account's address, proved by the account's own password.

This file pins the request half of the change flow, and four of its claims are
worth naming before the tests that make them:

**The token leaves by exactly one route.** Not through the outcome, not through a
return value, not into a log - into one envelope addressed to the address being
moved to. So a test that wants to answer a request has to read the code out of the
message, which is the same constraint the person has.

**The row is durable before the mail is sent.** That ordering is what makes a
failed send survivable: the worst case is a request nobody has the code for, which
simply expires, rather than a code for a request that does not exist.

**With no mail account the change applies here and now**, on the password proof
alone - because the endpoint's whole purpose is to rescue an account stranded at an
address no provider will bill, and every install is mail-less until somebody sets
one up.

**An account already stranded at an unusable address can be moved to a real one.**
``SignUp`` refuses to create such an account now; this is the way out for one that
already exists, and it is the reason the slice was built.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.request_email_change import RequestEmailChange
from app.application.identity.sign_up import SignUp
from app.domain.identity.emailChange import EMAIL_CHANGE_LIFETIME
from app.domain.identity.emailChangeStatus import EmailChangeStatus
from app.domain.identity.exception import (
    DuplicateEmailError,
    EmailChangeExpiredError,
    EmailUnchangedError,
    InvalidCredentialsError,
    InvalidEmailChangeTokenError,
    InvalidUserEmailError,
    UnusableEmailError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)
from tests.conftest import code_in

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"
ADDRESS = "ada@example.com"
NEW_ADDRESS = "ada.new@example.com"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "identity.db")


@pytest.fixture
def factory(db_path):
    return SqliteUnitOfWorkFactory(db_path)


@pytest.fixture
def sign_up(factory, password_hasher):
    return SignUp(factory, password_hasher=password_hasher)


@pytest.fixture
def account(sign_up):
    """An ordinary account at a real address, registered the ordinary way."""
    return sign_up.execute(ADDRESS, PASSWORD, NOW)


@pytest.fixture
def change_for(factory, password_hasher):
    """``RequestEmailChange`` as a given account, with an optional mailbox.

    A factory rather than a fixture, because the actor is bound at construction -
    ``RequestEmailChange``'s own convention, following ``LogOut`` and
    ``ResolveActorFromSession`` - so a test that wants it acting as somebody has to
    say who before it can call anything.
    """

    def _build(user_id, channel=None):
        return RequestEmailChange(factory, password_hasher, user_id, channel=channel)

    return _build


def seed_account(db_path, password_hasher, email, password=PASSWORD) -> User:
    """Write an account straight into the store, bypassing ``SignUp`` entirely.

    **The only way an account this system would refuse can exist.** ``SignUp``
    refuses an address with no real domain now, so an account stranded at
    ``nobody@localhost`` can only be one written before the rule - and a test that
    went through the front door could not produce one at all. This is what a row
    that predates the rule looks like.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    uow = factory.start()
    try:
        user = User(user_id=uuid4(), email=email, google_subject=None, created_at=NOW)
        uow.users.save(user)
        uow.password_credentials.save(
            PasswordCredential(
                user_id=user.user_id,
                password_hash=password_hasher.hash(PlainPassword(password)),
                updated_at=NOW,
            )
        )
        uow.commit()
    finally:
        uow.rollback()
    return user


def requests_in(db_path) -> int:
    """How many requests the store holds, asked *around* the port rather than through it.

    ``EmailChangeRepository`` deliberately has no ``find`` - see its docstring - so
    a test that needs to know whether a row exists *at all* has to open the file.
    That is the right trade here, because the absence of a write is precisely the
    property under test, and a port method added so a test could assert it would be
    a method with no production caller.
    """
    connection = open_sqlite_connection(db_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM email_changes").fetchone()[0]
    finally:
        connection.close()


class TestThePendingOutcome:
    """What asking produces when the installation can send mail: a request, not a change."""

    def test_it_records_a_request(self, change_for, account, db_path, build_channel):
        change_for(account.user_id, channel=build_channel()).execute(
            NEW_ADDRESS, PASSWORD, NOW
        )

        assert requests_in(db_path) == 1

    def test_it_does_not_move_the_account(self, change_for, account, factory, build_channel):
        """The account still holds the address it held, and this is the whole flow.

        A request that applied the change would make the confirmation decorative:
        anybody who could present the password would have moved the address already,
        and the mailed code would be a notification rather than an authorisation.
        """
        change_for(account.user_id, channel=build_channel()).execute(
            NEW_ADDRESS, PASSWORD, NOW
        )

        uow = factory.start()
        try:
            stored = uow.users.get_by_id(account.user_id)
        finally:
            uow.rollback()

        assert stored.email == ADDRESS

    def test_the_outcome_says_pending_and_names_the_address_being_moved_to(
        self, change_for, account, build_channel
    ):
        """``applied`` is read off ``change`` rather than stored beside it.

        So the two cannot disagree, and ``email`` means the same thing either way:
        the address this request is about.
        """
        outcome = change_for(account.user_id, channel=build_channel()).execute(
            NEW_ADDRESS, PASSWORD, NOW
        )

        assert outcome.applied is False
        assert outcome.email == NEW_ADDRESS

    def test_the_outcome_carries_a_deadline(self, change_for, account, build_channel):
        """A pending change has a window; an applied one has nothing to wait for.

        The pairing is structural - ``expires_at`` is read off the request - so
        "applied, and here is your deadline" is unrepresentable rather than refused.
        """
        outcome = change_for(account.user_id, channel=build_channel()).execute(
            NEW_ADDRESS, PASSWORD, NOW
        )

        assert outcome.expires_at == NOW + EMAIL_CHANGE_LIFETIME

    def test_the_outcome_carries_no_token(self, change_for, account, build_channel):
        """**The credential is not in what the caller is handed, and the assertion is
        the mailed code rather than the word "token".**

        The use case holds the plaintext at the moment it returns. Attaching it to
        the outcome would put a value that moves an account onto the object the API
        renders for the rest of the request - and the caller does not need it,
        because the mailbox is where it is supposed to arrive.

        The code is read back out of the channel and looked for in the outcome, which
        is the only assertion that measures the claim. A substring test over ``repr``
        would not: the outcome carries the ``EmailChange``, that aggregate carries
        ``token_hash``, and *the hash is not the credential* - it is exactly what this
        system is willing to leave lying in a table. So "no token" has to mean "not
        the value that was posted", which is a fact about the plaintext.
        """
        channel = build_channel()

        outcome = change_for(account.user_id, channel=channel).execute(
            NEW_ADDRESS, PASSWORD, NOW
        )
        mailed_code = code_in(channel.sent[0])

        assert not hasattr(outcome, "token")
        assert mailed_code not in repr(outcome)

    def test_the_mail_goes_to_the_address_being_moved_to(
        self, change_for, account, build_channel
    ):
        """Not to the account's current address.

        The question the code answers is "can whoever controls the new mailbox read
        it", so sending it anywhere else would prove the wrong thing - and would
        prove it to somebody who has not been asked anything.
        """
        channel = build_channel()

        change_for(account.user_id, channel=channel).execute(NEW_ADDRESS, PASSWORD, NOW)

        assert len(channel.sent) == 1
        assert channel.sent[0].recipient == NEW_ADDRESS

    def test_the_mail_does_not_name_the_account_it_is_about(
        self, change_for, account, build_channel
    ):
        """The current address is deliberately absent from the body.

        The message goes to an address that has *not* been proven to belong to
        anybody - proving that is the point of sending it - so naming the account
        would hand the old address to whoever controls the new mailbox, whether or
        not they own the account. A stranger who receives one of these learns only
        that somebody typed their address into a form, which is the least it can
        tell them.
        """
        channel = build_channel()

        change_for(account.user_id, channel=channel).execute(NEW_ADDRESS, PASSWORD, NOW)

        assert ADDRESS not in channel.sent[0].body

    def test_the_stored_row_carries_a_hash_and_not_the_mailed_code(
        self, change_for, account, factory, build_channel
    ):
        """The property that makes a plain SHA-256 enough: the row is not a credential.

        Asserted by *spending* the row with the hash of the mailed code, which is the
        only read the port offers - and it is the read that matters, because it is
        the one the confirm performs. A row that stored the plaintext would be a copy
        of every live credential in the database, and reading it would be enough to
        move any pending account.
        """
        channel = build_channel()
        change_for(account.user_id, channel=channel).execute(NEW_ADDRESS, PASSWORD, NOW)
        code = code_in(channel.sent[0])

        uow = factory.start()
        try:
            claimed = uow.email_changes.claim_by_token_hash(hash_session_token(code), NOW)
        finally:
            uow.rollback()

        assert claimed.token_hash == hash_session_token(code)
        assert claimed.token_hash != code
        assert claimed.new_email == NEW_ADDRESS

    def test_the_request_is_written_before_the_mail_is_sent(
        self, change_for, account, db_path, build_channel
    ):
        """**Ordering, and it is the whole of the safety here.**

        A message that left before its row was durable would be a code for a request
        that does not exist: the person presents it, the claim finds nothing, and the
        request they made has vanished with no way to tell that from having typed the
        wrong code.

        Storing first means the worst case is the opposite one - a row nobody has the
        code for - which is a request that simply expires. So a send that fails
        **raises** (the caller must know the mail did not go out) and leaves the row
        behind, harmlessly, to be superseded by asking again.
        """
        channel = build_channel(failures=[OSError("connection refused")])

        with pytest.raises(OSError):
            change_for(account.user_id, channel=channel).execute(
                NEW_ADDRESS, PASSWORD, NOW
            )

        assert requests_in(db_path) == 1

    def test_a_failed_send_leaves_a_request_nobody_can_answer(
        self, change_for, account, factory, build_channel
    ):
        """The leftover row holds a hash of a code that never left the process.

        Nobody has the plaintext, so the row is not a live credential sitting in the
        database - which is worth stating, because a row that *did* still work would
        be the interesting failure, and this is what says it does not.

        Asked through the claim with a guessable value: nothing matches, and the
        refusal is the unknown-token one rather than a spent or expired one.
        """
        channel = build_channel(failures=[OSError("connection refused")])

        with pytest.raises(OSError):
            change_for(account.user_id, channel=channel).execute(
                NEW_ADDRESS, PASSWORD, NOW
            )

        uow = factory.start()
        try:
            with pytest.raises(InvalidEmailChangeTokenError):
                uow.email_changes.claim_by_token_hash(
                    hash_session_token("the-code-that-never-arrived"), NOW
                )
        finally:
            uow.rollback()

    def test_the_mailed_code_is_the_one_that_spends_the_request(
        self, change_for, account, factory, build_channel
    ):
        """The round trip: minted here, mailed, and claimable by the store.

        The claim itself is the repository's contract and is tested there; what this
        adds is that the value the use case minted is the value the store recognises -
        the two halves of the flow agreeing about which string is the credential.
        """
        channel = build_channel()
        change_for(account.user_id, channel=channel).execute(NEW_ADDRESS, PASSWORD, NOW)

        uow = factory.start()
        try:
            claimed = uow.email_changes.claim_by_token_hash(
                hash_session_token(code_in(channel.sent[0])), NOW
            )
        finally:
            uow.rollback()

        assert claimed.status is EmailChangeStatus.CONFIRMED
        assert claimed.settled_at == NOW


class TestTheUnconfiguredInstall:
    """No mail account, so the password proof is the whole of the authorisation.

    Every install is mail-less until somebody sets one up, and the endpoint exists to
    rescue an account stranded at an address no provider will bill. So refusing here
    would move the trap down a layer and tell the person to check a mailbox that no
    code in this system can post to. The outcome says ``applied`` in so many words,
    so an install without mail is never *silently* less safe than one with it.
    """

    def test_it_applies_the_change_immediately(self, change_for, account, factory):
        outcome = change_for(account.user_id).execute(NEW_ADDRESS, PASSWORD, NOW)

        uow = factory.start()
        try:
            stored = uow.users.get_by_id(account.user_id)
        finally:
            uow.rollback()

        assert outcome.applied is True
        assert outcome.email == NEW_ADDRESS
        assert stored.email == NEW_ADDRESS

    def test_it_mints_no_request_row(self, change_for, account, db_path):
        """There is nothing to answer, so there is nothing to record.

        A row here would be a pending change nobody can confirm, and the next request
        would supersede it - so it would be invisible state that only ``requests_in``
        could see, which is exactly the kind of thing that turns up later as a
        phantom request.
        """
        change_for(account.user_id).execute(NEW_ADDRESS, PASSWORD, NOW)

        assert requests_in(db_path) == 0

    def test_the_outcome_reports_applied_with_no_deadline(self, change_for, account):
        """The two reads a client uses to tell the outcomes apart.

        ``applied`` is true and ``expires_at`` is ``None``, which together are
        unreachable for a pending change - so a client never has to guess which
        installation it is talking to.
        """
        outcome = change_for(account.user_id).execute(NEW_ADDRESS, PASSWORD, NOW)

        assert outcome.applied is True
        assert outcome.expires_at is None

    def test_it_still_requires_the_password(self, change_for, account, factory):
        """**The fallback is narrower than it sounds, and this is the boundary.**

        "No mailbox, so the password proof is the whole of the authorisation" means
        the password proof is still *required* - the whole of it, not none of it.
        Without this, the unconfigured install would be one where a live session alone
        could move an address, which is the single thing requiring the password was
        for.
        """
        with pytest.raises(InvalidCredentialsError):
            change_for(account.user_id).execute(NEW_ADDRESS, "not-the-password", NOW)

        uow = factory.start()
        try:
            assert uow.users.get_by_id(account.user_id).email == ADDRESS
        finally:
            uow.rollback()


class TestTheRescue:
    """The case the whole slice exists for, end to end at the application layer."""

    def test_an_account_stranded_at_an_unusable_address_can_move_to_a_real_one(
        self, db_path, password_hasher, change_for, factory
    ):
        """**A row that predates the entry rule is not trapped by it.**

        ``SignUp`` refuses ``nobody@localhost`` now, so this account could not be
        created today - but it exists, it holds a ``UNIQUE`` address nobody else can
        ever register, and before this endpoint its owner's only remedy was a second
        account. The entry rule closes the door; this is the way out for everybody
        already inside.

        Both halves of the rescue are asserted, because either alone would be a
        half-fix: the account moves, *and* the address it was stranded on is released
        when it does.
        """
        stranded = seed_account(db_path, password_hasher, "nobody@localhost")

        outcome = change_for(stranded.user_id).execute(NEW_ADDRESS, PASSWORD, NOW)

        uow = factory.start()
        try:
            assert uow.users.get_by_id(stranded.user_id).email == NEW_ADDRESS
            assert uow.users.find_by_email("nobody@localhost") is None
        finally:
            uow.rollback()

        assert outcome.email == NEW_ADDRESS

    def test_the_rescued_account_can_be_logged_in_to_at_its_new_address(
        self, db_path, password_hasher, change_for, factory
    ):
        """What the person actually experiences, and the half a store assertion misses.

        Moving the row is only half of a rescue: the fold, the credential lookup and
        the address the account is found by all have to agree afterwards, or the
        person is left with an account that reads correctly in the database and
        cannot be signed in to. The credential is deliberately untouched here - the
        password is proved again at the request, so there is nothing to re-key.
        """
        stranded = seed_account(db_path, password_hasher, "nobody@localhost")

        change_for(stranded.user_id).execute(NEW_ADDRESS, PASSWORD, NOW)

        uow = factory.start()
        try:
            found = uow.users.find_by_email(NEW_ADDRESS)
            credential = uow.password_credentials.find_by_user_id(stranded.user_id)
        finally:
            uow.rollback()

        assert found is not None
        assert found.user_id == stranded.user_id
        assert password_hasher.verify(PlainPassword(PASSWORD), credential.password_hash)


class TestWhatIsRefused:
    """Five refusals, and the order they are reachable in is load-bearing."""

    def test_a_wrong_password_is_refused(self, change_for, account):
        with pytest.raises(InvalidCredentialsError):
            change_for(account.user_id).execute(NEW_ADDRESS, "not-the-password", NOW)

    def test_the_wrong_password_refusal_is_the_logins_own_words(self, change_for, account):
        """One error class for one mistake, wherever the password is checked.

        A password verified in two places should fail in one way, or the two places
        become distinguishable from outside - and the sentence a person sees here is
        the one they already know from the login form. The refusal also names nothing
        the caller did not already supply, which is what keeps the address out of a
        response body and out of whatever logs the error handler writes to.
        """
        with pytest.raises(InvalidCredentialsError) as raised:
            change_for(account.user_id).execute(NEW_ADDRESS, "not-the-password", NOW)

        assert NEW_ADDRESS not in str(raised.value)

    def test_a_wrong_password_writes_nothing_and_mails_nothing(
        self, change_for, account, factory, db_path, build_channel
    ):
        """**The password is checked first, and this is the observable consequence.**

        Nothing else here should be answerable by somebody who cannot prove the
        account is theirs: a caller who fails this learns nothing about which
        addresses are taken, and no code is minted for a request that was never
        authorised.
        """
        channel = build_channel()

        with pytest.raises(InvalidCredentialsError):
            change_for(account.user_id, channel=channel).execute(
                NEW_ADDRESS, "not-the-password", NOW
            )

        uow = factory.start()
        try:
            assert uow.users.get_by_id(account.user_id).email == ADDRESS
        finally:
            uow.rollback()

        assert requests_in(db_path) == 0
        assert channel.attempts == []

    def test_an_account_with_no_credential_is_refused_the_same_way(
        self, db_path, change_for
    ):
        """A Google-shaped account, reached the only way it can be: written by hand.

        Deliberately the *same* refusal rather than something more specific. Telling
        an OIDC account's owner "that account has no password" is the same disclosure
        the shared refusal exists to prevent, and ``LogIn`` already answers this case
        identically.
        """
        uow = SqliteUnitOfWorkFactory(db_path).start()
        try:
            user = User(
                user_id=uuid4(), email=ADDRESS, google_subject=None, created_at=NOW
            )
            uow.users.save(user)
            uow.commit()
        finally:
            uow.rollback()

        with pytest.raises(InvalidCredentialsError):
            change_for(user.user_id).execute(NEW_ADDRESS, PASSWORD, NOW)

    def test_an_unchanged_address_is_refused(self, change_for, account, db_path):
        """Asking to move an address to itself, which is not a change.

        Refused here rather than left to produce a pending request nobody would
        confirm, which would take the account's one pending slot and make the next
        real request supersede a no-op.
        """
        with pytest.raises(EmailUnchangedError):
            change_for(account.user_id).execute(ADDRESS, PASSWORD, NOW)

        assert requests_in(db_path) == 0

    def test_a_different_spelling_of_the_same_address_is_still_unchanged(
        self, change_for, account
    ):
        """The fold, at the point where it decides whether anything is happening.

        ``Ada@Example.com`` with whitespace is the same address as ``ada@example.com``,
        and comparing the raw string would report a change to itself as a change. That
        is why this check runs after the shape rule, which is what folds.
        """
        with pytest.raises(EmailUnchangedError):
            change_for(account.user_id).execute("  Ada@Example.COM  ", PASSWORD, NOW)

    def test_an_address_with_no_real_domain_is_refused(self, change_for, account):
        """**The entry rule, at the second of the two places an address is minted.**

        The same rule ``SignUp`` applies, read the same way - a guard, because being
        wrong costs a retry at a form. A change to ``nobody@localhost`` would leave
        the account exactly as stranded as it started, one step further from the
        person noticing.
        """
        with pytest.raises(UnusableEmailError):
            change_for(account.user_id).execute("nobody@localhost", PASSWORD, NOW)

    def test_an_address_that_is_not_one_is_refused_as_a_shape_problem(
        self, change_for, account
    ):
        """**The ordering assertion**, and it is the reason shape is asked first.

        ``not-an-address`` fails both rules - it has no ``@``, so the shape rule
        refuses it, and it has no dot after an ``@`` that is not there, so the
        usability rule would too. Which one answers is decided by the order, and the
        order is chosen so the answer names the thing the person can act on.
        """
        with pytest.raises(InvalidUserEmailError) as raised:
            change_for(account.user_id).execute("not-an-address", PASSWORD, NOW)

        assert not isinstance(raised.value, UnusableEmailError)

    def test_an_address_somebody_else_holds_is_refused(
        self, change_for, sign_up, account
    ):
        """The only one of the five that answers a question about the *store*.

        Which is why it is asked last, after the four about the value that was typed:
        a caller who has not yet proved anything learns nothing about which addresses
        are registered from asking about addresses that could not be.
        """
        sign_up.execute("grace@example.com", PASSWORD, NOW)

        with pytest.raises(DuplicateEmailError):
            change_for(account.user_id).execute("grace@example.com", PASSWORD, NOW)

    def test_a_refused_request_mints_no_code(
        self, change_for, account, sign_up, build_channel
    ):
        """Every refusal above, checked against the one observable they share.

        A refusal that had already minted and mailed a code would be worse than no
        check at all: the person would hold a working credential for a change the
        system told them it had refused.
        """
        sign_up.execute("grace@example.com", PASSWORD, NOW)
        channel = build_channel()
        service = change_for(account.user_id, channel=channel)

        for address in (
            "nobody@localhost",
            "not-an-address",
            ADDRESS,
            "grace@example.com",
        ):
            with pytest.raises(Exception):
                service.execute(address, PASSWORD, NOW)

        assert channel.attempts == []


class TestTheStrandedAccountAskingForItsOwnAddress:
    def test_the_stranded_account_is_told_it_is_unchanged(
        self, db_path, password_hasher, change_for
    ):
        """**The one case where the usability rule is not reached, and why that is right.**

        An account stranded at ``nobody@localhost`` that asks to change *to* that same
        address is refused as unchanged, before the usability rule is consulted. The
        alternative would be telling the person "that address is unusable" about the
        address they are already holding - true, and useless, and it would be the
        second time their account had been refused for something they cannot act on.

        A stranded account cannot ask to stay stranded. What it asks for is a real
        address, which is ``TestTheRescue``.
        """
        stranded = seed_account(db_path, password_hasher, "nobody@localhost")

        with pytest.raises(EmailUnchangedError):
            change_for(stranded.user_id).execute("nobody@localhost", PASSWORD, NOW)


class TestSuperseding:
    """Asking again replaces the request, so a mistyped address is correctable.

    One row per account is structural rather than a rule somebody remembers: the
    account's id *is* the primary key, so a second row cannot exist. The old code
    dies the instant the new one commits - which is what makes "ask again" the remedy
    rather than a race.
    """

    def test_asking_twice_leaves_one_request(
        self, change_for, account, db_path, build_channel
    ):
        service = change_for(account.user_id, channel=build_channel())

        service.execute(NEW_ADDRESS, PASSWORD, NOW)
        service.execute("grace@example.com", PASSWORD, NOW)

        assert requests_in(db_path) == 1

    def test_the_second_code_works_and_the_first_does_not(
        self, change_for, account, factory, build_channel
    ):
        """Both halves, because either alone would pass for a store that ignored one.

        A test asserting only that the new code works would pass for a store that kept
        both rows; one asserting only that the old code fails would pass for a store
        that wrote nothing at all.
        """
        channel = build_channel()
        service = change_for(account.user_id, channel=channel)

        service.execute(NEW_ADDRESS, PASSWORD, NOW)
        service.execute("grace@example.com", PASSWORD, NOW)

        first_code = code_in(channel.sent[0])
        second_code = code_in(channel.sent[1])
        later = NOW + timedelta(minutes=1)

        uow = factory.start()
        try:
            with pytest.raises(InvalidEmailChangeTokenError):
                uow.email_changes.claim_by_token_hash(
                    hash_session_token(first_code), later
                )
        finally:
            uow.rollback()

        uow = factory.start()
        try:
            claimed = uow.email_changes.claim_by_token_hash(
                hash_session_token(second_code), later
            )
        finally:
            uow.rollback()

        assert claimed.new_email == "grace@example.com"

    def test_the_replaced_code_is_unknown_rather_than_expired(
        self, change_for, account, factory, build_channel
    ):
        """Because the row it named is *gone*, not old.

        The distinction matters to a person: "that code means nothing" and "that code
        has expired" have different remedies, and superseding produces the first - the
        request it belonged to no longer exists. It is also, deliberately, the same
        answer a guess produces, so somebody holding a stale code cannot learn from the
        refusal that it was ever real.
        """
        channel = build_channel()
        service = change_for(account.user_id, channel=channel)

        service.execute(NEW_ADDRESS, PASSWORD, NOW)
        service.execute("grace@example.com", PASSWORD, NOW)

        uow = factory.start()
        try:
            with pytest.raises(InvalidEmailChangeTokenError) as raised:
                uow.email_changes.claim_by_token_hash(
                    hash_session_token(code_in(channel.sent[0])), NOW
                )
        finally:
            uow.rollback()

        assert not isinstance(raised.value, EmailChangeExpiredError)
