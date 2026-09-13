"""Answering a change: the mailed code comes back, and the account moves.

This is the only operation in the system whose whole authorisation is a value the
system posted to somebody, so most of this file is about what that does *not* mean:

- **No actor.** ``ConfirmEmailChange`` takes no caller identity at all - the same
  shape as ``LogOut``, and for a stronger version of its reason. The token exists
  only because somebody already presented the account's password, so the password
  proof is already spent on this change. Requiring a live session on top would
  refuse exactly the person who asked at a desk and opened the mail on a phone.
- **The id it acts on comes from the claimed row**, never from a request. That is
  what keeps this out of the "no privileged actor" prohibition rather than inside
  it: nothing here reaches anything without naming whose it is, and it names it by
  proving it.
- **The spend and the move are one commit.** A crash between them leaves either a
  code that still works after it has moved an account, or an account moved by a
  request that still reads ``AWAITING`` so the same code can be presented again.

The notices are the third theme, and the asymmetry with the request's mail is the
argument: the message that carries the credential must arrive or the request is
pointless, so a failure there raises. The message announcing a fait accompli must
not be able to undo it, so a failure here is reported and the change stands.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.application.identity.confirm_email_change import ConfirmEmailChange
from app.application.identity.request_email_change import RequestEmailChange
from app.application.identity.sign_up import SignUp
from app.domain.identity.emailChange import EMAIL_CHANGE_LIFETIME
from app.domain.identity.emailChangeStatus import EmailChangeStatus
from app.domain.identity.exception import (
    DuplicateEmailError,
    EmailChangeAlreadyUsedError,
    EmailChangeExpiredError,
    InvalidEmailChangeTokenError,
)
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_credential import PasswordCredential
from app.domain.identity.session import hash_session_token
from app.domain.identity.user import User
from app.infrastructure.persistence.sqlite_unit_of_work import SqliteUnitOfWorkFactory
from app.infrastructure.repositories.sqlite_user_repository import SqliteUserRepository
from tests.conftest import code_in

NOW = datetime(2026, 3, 2, 12, 0)
PASSWORD = "correct-horse-battery"
ADDRESS = "ada@example.com"
NEW_ADDRESS = "ada.new@example.com"
OTHER_ADDRESS = "grace.new@example.com"


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
def confirm(factory):
    """``ConfirmEmailChange`` with **no actor and no hasher**, which is the point.

    Built the same way a test builds one with a channel, so the absence of an
    account argument is visible at every call site rather than only in the class's
    own signature.
    """

    def _build(channel=None):
        return ConfirmEmailChange(factory, channel=channel)

    return _build


def seed_account(db_path, password_hasher, email, password=PASSWORD) -> User:
    """Write an account straight into the store, bypassing ``SignUp`` entirely.

    The only way an account this system would refuse can exist, which the rescue
    tests need: ``SignUp`` refuses an address with no real domain now.
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


def codes_in(channel) -> list:
    """Every *verification* code the channel carried, in the order it carried them.

    A channel records both messages, and the notice has no code in it - so indexing
    the send list directly would break the moment a test confirmed one change before
    asking for another. Selecting on the label is what keeps each test about its own
    claim rather than about how many messages have gone past.
    """
    return [
        code_in(message)
        for message in channel.sent
        if "present this code to confirm it" in message.body
    ]


def requested(
    db_path, password_hasher, build_channel, address=ADDRESS, new_address=NEW_ADDRESS
) -> tuple:
    """An account with a pending request, and the channel the code is waiting in.

    The setup most tests below start from, in one place, so each test's body is the
    claim it is making.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    user = seed_account(db_path, password_hasher, address)
    channel = build_channel()
    RequestEmailChange(factory, password_hasher, user.user_id, channel=channel).execute(
        new_address, PASSWORD, NOW
    )
    return user, channel


class TestTheTokenIsTheWholeAuthorisation:
    """What the confirm does not ask for, and why that is not a hole."""

    def test_it_applies_the_change(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        user, channel = requested(db_path, password_hasher, build_channel)

        confirm().execute(code_in(channel.sent[0]), NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(user.user_id).email == NEW_ADDRESS
        finally:
            uow.rollback()

    def test_the_stored_hash_is_not_a_usable_credential(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        """**What makes a plain SHA-256 sufficient, and it is a property of *this* layer.**

        The table holds ``hash_session_token`` of the code that was mailed and never the
        code itself - pinned next door by
        ``test_request_email_change.py::test_the_stored_row_carries_a_hash_and_not_the_mailed_code``,
        which is why this test may derive rather than read it. So a person who could
        read ``email_changes`` - a leaked database, a backup, an operator with a shell -
        still cannot move anybody's account.

        That works because the hash is always *derived from a value that was presented*,
        never taken from a request: this use case hashes whatever arrives and looks
        *that* up, so handing it the stored value hashes the hash and matches no row.

        Pinned here rather than on the repository, and the placement is the assertion.
        The store's ``claim_by_token_hash`` takes a hash and hashes nothing - that is its
        contract, and the test beside it says so. The step that makes a stolen row
        useless is the ``hash_session_token`` call *above* it, so this is the only layer
        at which the claim can be made about the running system.
        """
        user, channel = requested(db_path, password_hasher, build_channel)
        stored = hash_session_token(code_in(channel.sent[0]))

        with pytest.raises(InvalidEmailChangeTokenError):
            confirm().execute(stored, NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(user.user_id).email == ADDRESS
        finally:
            uow.rollback()

    def test_it_needs_no_actor_and_no_password(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """**The signature is the assertion.**

        ``ConfirmEmailChange`` is constructed with a unit of work and nothing else -
        no ``actor``, no ``password_hasher`` - and this test builds one that way and
        uses it. A future change that grew an actor parameter would have to come here
        and delete this, which is the conversation worth having: the token exists only
        because somebody already proved the password, so a second proof adds a way to
        fail rather than a check.
        """
        _, channel = requested(db_path, password_hasher, build_channel)

        result = confirm().execute(code_in(channel.sent[0]), NOW + timedelta(minutes=1))

        assert result.user.email == NEW_ADDRESS

    def test_the_account_it_moves_is_the_one_the_token_names(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        """**The id comes from the claimed row, never from a request.**

        This is the distinction between "authorised by a mailed token" and a
        privileged actor: nothing here can be pointed at an arbitrary account, because
        there is no parameter to point. Two accounts, one request, and only the
        requested one moves.
        """
        first, channel = requested(db_path, password_hasher, build_channel)
        second = seed_account(db_path, password_hasher, "grace@example.com")

        confirm().execute(code_in(channel.sent[0]), NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(first.user_id).email == NEW_ADDRESS
            assert uow.users.get_by_id(second.user_id).email == "grace@example.com"
        finally:
            uow.rollback()

    def test_it_reports_the_address_the_account_left(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """Carried in the result rather than read off anything.

        By the time this exists the account already holds the new address, which is
        exactly why the notice to the old one cannot be composed from the account
        afterwards - and why the row deliberately has no ``previous_email`` column.
        """
        _, channel = requested(db_path, password_hasher, build_channel)

        result = confirm().execute(code_in(channel.sent[0]), NOW + timedelta(minutes=1))

        assert result.previous_email == ADDRESS
        assert result.user.email == NEW_ADDRESS

    def test_the_moment_it_records_is_the_one_it_was_given(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """``now`` is the single instant this is decided against - ``Session.issue``'s
        contract.

        The claim's window test, the moment written on the row and the moment printed
        in the notice all come from it, so a change cannot be recorded as answered
        before it was allowed to be.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        answered_at = NOW + timedelta(minutes=3)

        result = confirm().execute(code_in(channel.sent[0]), answered_at)

        assert result.change.settled_at == answered_at


class TestTheThreeRefusalsAreDistinguishable:
    """Because they have three different remedies.

    "Ask again" is a different instruction from "you already did this", and "that code
    means nothing" is different from both. The store distinguishes them by a follow-up
    read inside the same transaction as the claim - and the last one is only reachable
    because the row *survives* being spent, which is the class below.
    """

    def test_an_unknown_code_is_refused(self, confirm):
        with pytest.raises(InvalidEmailChangeTokenError):
            confirm().execute("not-a-code-that-was-ever-minted", NOW)

    def test_a_code_past_its_window_is_refused(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """At the boundary instant, not a moment after it.

        The store's claim tests ``expires_at > ?``, so the window is closed *at* the
        moment it closes - the same side ``EmailChange.is_expired`` reads with ``>=``.
        Asking at exactly ``expires_at`` is the case that would differ if the two ever
        disagreed, and a disagreement would be a credential working for one instant
        longer than it was mailed for.
        """
        _, channel = requested(db_path, password_hasher, build_channel)

        with pytest.raises(EmailChangeExpiredError):
            confirm().execute(code_in(channel.sent[0]), NOW + EMAIL_CHANGE_LIFETIME)

    def test_a_code_that_was_already_answered_is_refused(
        self, db_path, password_hasher, build_channel, confirm
    ):
        _, channel = requested(db_path, password_hasher, build_channel)
        code = code_in(channel.sent[0])
        later = NOW + timedelta(minutes=1)

        confirm().execute(code, later)

        with pytest.raises(EmailChangeAlreadyUsedError):
            confirm().execute(code, later + timedelta(seconds=1))

    def test_the_three_refusals_are_three_different_classes(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """The property, rather than three separate tests that happen to pass.

        Each test above would pass for a store that answered everything with the same
        error - which would collapse three remedies into one. This is the assertion
        that makes them three.
        """
        _, used_channel = requested(db_path, password_hasher, build_channel)
        used_code = code_in(used_channel.sent[0])
        later = NOW + timedelta(minutes=1)
        confirm().execute(used_code, later)

        _, stale_channel = requested(
            db_path,
            password_hasher,
            build_channel,
            address="grace@example.com",
            new_address=OTHER_ADDRESS,
        )
        stale_code = code_in(stale_channel.sent[0])

        raised = []
        for token, moment in (
            ("never-minted", later),
            (used_code, later + timedelta(seconds=1)),
            (stale_code, later + EMAIL_CHANGE_LIFETIME),
        ):
            with pytest.raises(Exception) as refusal:
                confirm().execute(token, moment)
            raised.append(type(refusal.value))

        assert len(set(raised)) == 3


class TestTheSpentRequestSurvives:
    """The row is not deleted on use, and that is what makes "already used" sayable.

    Deleting it would collapse "you already did this" into "that code means nothing",
    which are different things to be told. The cost is one row per account, kept - and
    the last change is still readable, which is most of an audit trail for free.
    """

    def test_the_row_is_still_there_after_it_is_spent(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        _, channel = requested(db_path, password_hasher, build_channel)
        code = code_in(channel.sent[0])
        later = NOW + timedelta(minutes=1)
        confirm().execute(code, later)

        uow = factory.start()
        try:
            with pytest.raises(EmailChangeAlreadyUsedError):
                uow.email_changes.claim_by_token_hash(hash_session_token(code), later)
        finally:
            uow.rollback()

    def test_it_records_that_it_was_answered(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """``CONFIRMED`` and a moment, together, from the claim's single ``UPDATE``.

        The aggregate refuses a settled row with no moment and an awaiting row that has
        one, so this pair is a check on that statement rather than on a caller's
        discipline.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        answered_at = NOW + timedelta(minutes=1)

        result = confirm().execute(code_in(channel.sent[0]), answered_at)

        assert result.change.status is EmailChangeStatus.CONFIRMED
        assert result.change.settled_at == answered_at


class TestTheAddressTakenInTheMeantime:
    """The one thing that genuinely can change inside the fifteen minutes."""

    def test_it_is_refused_when_somebody_else_registers_it_first(
        self, db_path, password_hasher, build_channel, sign_up, confirm
    ):
        """**``DuplicateEmailError``, and it is deliberately not a 500.**

        The address is re-checked at apply time because it is the only fact here that
        can move between asking and answering - the usability rule cannot change inside
        one run of one version of this code, so it is checked once, at the request, and
        a second check would only be a second place for the two to disagree.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        sign_up.execute(NEW_ADDRESS, PASSWORD, NOW + timedelta(seconds=30))

        with pytest.raises(DuplicateEmailError):
            confirm().execute(code_in(channel.sent[0]), NOW + timedelta(minutes=1))

    def test_the_request_is_spent_by_the_attempt_not_by_the_success(
        self, db_path, password_hasher, build_channel, sign_up, confirm
    ):
        """**The refusal commits the claim before it raises.**

        A token that survived a failed attempt would be a live credential whose whole
        purpose is to be usable once - and the remedy is the same either way, which is
        to ask again with a different address. This is why ``ConfirmEmailChange``
        commits ahead of the ``raise``: without that commit the ``finally`` below it
        would roll the spend back, and "spent by the attempt" would be a sentence in a
        docstring rather than something the store does.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        code = code_in(channel.sent[0])
        later = NOW + timedelta(minutes=1)
        sign_up.execute(NEW_ADDRESS, PASSWORD, NOW + timedelta(seconds=30))

        with pytest.raises(DuplicateEmailError):
            confirm().execute(code, later)

        with pytest.raises(EmailChangeAlreadyUsedError):
            confirm().execute(code, later + timedelta(seconds=1))

    def test_the_account_is_left_where_it_was(
        self, db_path, password_hasher, build_channel, sign_up, factory, confirm
    ):
        """A refused change moves nothing - the refusal comes before the write."""
        user, channel = requested(db_path, password_hasher, build_channel)
        sign_up.execute(NEW_ADDRESS, PASSWORD, NOW + timedelta(seconds=30))

        with pytest.raises(DuplicateEmailError):
            confirm().execute(code_in(channel.sent[0]), NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(user.user_id).email == ADDRESS
        finally:
            uow.rollback()

    def test_an_account_moving_back_to_an_address_it_held_is_not_a_duplicate(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """The check is against *another* account, not against any row at all.

        ``find_by_email`` finds this account itself the moment an earlier change
        committed, so a comparison that did not exclude the acting user would refuse
        the move back to an address the account had held and left - which is a
        legitimate thing to do and the obvious correction for a change made in error.
        That is the reason the condition reads ``holder.user_id != user.user_id``.
        """
        user, channel = requested(db_path, password_hasher, build_channel)
        confirm().execute(codes_in(channel)[0], NOW + timedelta(minutes=1))

        RequestEmailChange(
            SqliteUnitOfWorkFactory(db_path),
            password_hasher,
            user.user_id,
            channel=channel,
        ).execute(ADDRESS, PASSWORD, NOW + timedelta(minutes=2))

        result = confirm().execute(codes_in(channel)[1], NOW + timedelta(minutes=3))

        assert result.user.email == ADDRESS


class TestTheNoticeToTheAddressLeftBehind:
    """``notice_sent`` and ``notice_error`` are three states in two fields."""

    def test_it_goes_to_the_old_address_and_not_the_new_one(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """The direction is the point.

        The person who needs to know their address was moved is the one who can still
        read mail at the old one - if the change was not theirs, this is the only
        warning they will get. The new mailbox, by definition, belongs to whoever asked
        for the change.
        """
        _, channel = requested(db_path, password_hasher, build_channel)

        confirm(channel=channel).execute(codes_in(channel)[0], NOW + timedelta(minutes=1))

        assert len(channel.sent) == 2
        notice = channel.sent[1]
        assert notice.recipient == ADDRESS
        assert NEW_ADDRESS in notice.body

    def test_it_is_reported_as_sent(
        self, db_path, password_hasher, build_channel, confirm
    ):
        _, channel = requested(db_path, password_hasher, build_channel)

        result = confirm(channel=channel).execute(
            codes_in(channel)[0], NOW + timedelta(minutes=1)
        )

        assert result.notice_sent is True
        assert result.notice_error is None

    def test_a_failed_notice_does_not_refuse_the_change(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        """**The asymmetry with the request's mail, and the whole argument for it.**

        The message that carries the credential must arrive or the request is
        pointless, so a failure there raises. This one announces something already done
        and that nothing here can undo - so refusing the change because a farewell
        bounced would strand the very account the endpoint exists to rescue. A notice
        to a stranded address is *expected* to bounce; that is what stranded means.
        """
        user, channel = requested(db_path, password_hasher, build_channel)
        code = codes_in(channel)[0]

        result = confirm(
            channel=build_channel(failures=[OSError("mailbox unavailable")])
        ).execute(code, NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(user.user_id).email == NEW_ADDRESS
        finally:
            uow.rollback()

        assert result.notice_sent is False

    def test_a_failed_notice_says_what_went_wrong(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """Reported rather than swallowed, so an operator can see why.

        The third state of the pair - ``False`` with ``None`` - means there is no mail
        account on this installation and nothing was attempted. That is different from
        "we tried and it failed", and a client that could not tell them apart would
        report a mail configuration problem as a delivery problem.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        code = codes_in(channel)[0]

        result = confirm(
            channel=build_channel(failures=[OSError("mailbox unavailable")])
        ).execute(code, NOW + timedelta(minutes=1))

        assert result.notice_sent is False
        assert "mailbox unavailable" in result.notice_error

    def test_with_no_channel_the_notice_is_reported_as_not_attempted(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """``False`` with no reason, which is the state a reader will misread.

        Reachable when an installation's mail account is removed between a request and
        its answer, which is the only way a pending request can meet a confirm that has
        no channel: a request made *without* one applies immediately and mints no code
        at all, so there would be nothing to answer.

        The change still applies here. What this pins is the honest report - nothing was
        attempted, which is not the same as something failing.
        """
        user, channel = requested(db_path, password_hasher, build_channel)
        code = codes_in(channel)[0]

        result = confirm().execute(code, NOW + timedelta(minutes=1))

        assert result.user.email == NEW_ADDRESS
        assert result.notice_sent is False
        assert result.notice_error is None

    def test_a_channel_that_raises_something_undeclared_is_still_caught(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """Broad on purpose, and this is the shape of the reason.

        A channel is third-party code talking to a network, and every failure mode it
        has means the same thing here: the notice did not arrive. Narrowing the catch to
        the adapter's declared exception would let an undeclared one become a 500
        *after* the address had already moved - reporting a change that happened as a
        failure.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        code = codes_in(channel)[0]

        result = confirm(
            channel=build_channel(failures=[RuntimeError("something nobody declared")])
        ).execute(code, NOW + timedelta(minutes=1))

        assert result.notice_sent is False
        assert result.notice_error == "something nobody declared"

    def test_it_names_the_address_the_account_left_and_not_the_one_it_moved_to(
        self, db_path, password_hasher, build_channel, confirm
    ):
        """The only message in the system that goes to an address the account no
        longer has - which is the whole point of it.

        Composed from the ``settled_at`` the claim wrote, so the moment in the text is
        the moment on the row rather than a second reading of the clock.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        answered_at = NOW + timedelta(minutes=1)

        confirm(channel=channel).execute(codes_in(channel)[0], answered_at)

        notice = channel.sent[1]
        assert ADDRESS in notice.body
        assert answered_at.isoformat(timespec="minutes") in notice.body


class TestTheChangeAndTheSpendLandTogether:
    """One commit, one fact - and the failure that would otherwise split them."""

    def test_a_failure_while_moving_the_account_leaves_the_code_unspent(
        self, db_path, password_hasher, build_channel, factory, confirm, monkeypatch
    ):
        """**Atomicity, provoked rather than assumed.**

        If the claim and the address change were two commits, a crash between them
        would leave a token already spent while the account still holds its old address
        - and the person would be told their code is used up, with nothing to show for
        it and no way forward except asking again from scratch.

        Provoked by making the address write fail, which is the second of the two
        writes. The rollback has to take the claim back with it; if it did not, the
        claim at the end of this test would raise ``EmailChangeAlreadyUsedError``
        instead of succeeding.
        """
        _, channel = requested(db_path, password_hasher, build_channel)
        code = codes_in(channel)[0]
        later = NOW + timedelta(minutes=1)

        def refuse_to_write(user):
            raise RuntimeError("the disk is full")

        monkeypatch.setattr(SqliteUserRepository, "save", staticmethod(refuse_to_write))

        with pytest.raises(RuntimeError):
            confirm().execute(code, later)

        uow = factory.start()
        try:
            claimed = uow.email_changes.claim_by_token_hash(
                hash_session_token(code), later
            )
        finally:
            uow.rollback()

        assert claimed.status is EmailChangeStatus.CONFIRMED

    def test_the_address_that_moved_is_the_one_the_row_named(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        """The claim re-reads the row it just wrote rather than building one in Python.

        Which matters here more than anywhere: the use case takes the new address from
        what the claim returns, so a locally-assembled object that differed from the
        stored one would move the account to an address the row does not name - and the
        next confirm would then disagree with the account about what happened.
        """
        user, channel = requested(db_path, password_hasher, build_channel)

        result = confirm().execute(codes_in(channel)[0], NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            stored = uow.users.get_by_id(user.user_id)
        finally:
            uow.rollback()

        assert result.change.new_email == NEW_ADDRESS
        assert stored.email == result.change.new_email


class TestTheStrandedAccountIsRescuedEndToEnd:
    """The whole slice, both halves, with a code in the middle."""

    def test_a_row_that_predates_the_entry_rule_can_be_answered(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        """``SignUp`` refuses ``nobody@localhost`` now, so this account could not be
        created today.

        Before this flow its owner's only remedy was a second account, because the
        stranded address was taken forever by the ``UNIQUE`` on ``users.email``. The
        entry rule closes the door; this is the way out for everybody already inside.
        """
        user, channel = requested(
            db_path, password_hasher, build_channel, address="nobody@localhost"
        )

        result = confirm().execute(codes_in(channel)[0], NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(user.user_id).email == NEW_ADDRESS
            assert uow.users.find_by_email("nobody@localhost") is None
        finally:
            uow.rollback()

        assert result.previous_email == "nobody@localhost"

    def test_the_notice_to_a_stranded_address_may_bounce_without_undoing_anything(
        self, db_path, password_hasher, build_channel, factory, confirm
    ):
        """**The case the "best-effort" rule was written for, made concrete.**

        A stranded address is one that bounces - that is what stranded means - so this
        is not an edge case but the expected path. The change stands, the account is
        rescued, and the failure is reported rather than raised.
        """
        user, channel = requested(
            db_path, password_hasher, build_channel, address="nobody@localhost"
        )
        code = codes_in(channel)[0]

        result = confirm(
            channel=build_channel(failures=[OSError("no such domain")])
        ).execute(code, NOW + timedelta(minutes=1))

        uow = factory.start()
        try:
            assert uow.users.get_by_id(user.user_id).email == NEW_ADDRESS
        finally:
            uow.rollback()

        assert result.notice_sent is False
        assert result.notice_error is not None
