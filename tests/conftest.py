"""Shared pytest fixtures.

The application-layer and repository tests each used to hand-roll their own
`build_wallet` helper (11 copies, each with slightly different hidden
defaults). A fixture factory is pytest's answer: one definition, requested by
name in whichever test wants it.

This factory exposes every dial a test could turn - status, both balances and
currency - with explicit defaults:

    build_wallet()                          # ACTIVE, 10000.00 NGN, nothing locked
    build_wallet(status=WalletStatus.CLOSED)
    build_wallet(available="500", locked="2500")
"""

import hashlib
import hmac
import secrets
from datetime import date, datetime
from decimal import Decimal
from string import ascii_letters, digits
from uuid import UUID, uuid4

import pytest

from app.composition_root import build_log_in, build_sign_up
from app.domain.identity.password import PlainPassword
from app.domain.identity.password_hasher import PasswordHasher
from app.domain.identity.user import User
from app.infrastructure.payments.paystack_payment_provider import (
    PaystackPaymentProvider,
)
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from app.presentation.cli import _write_token
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.notifications.notificationChannel import NotificationChannel
from app.domain.payments.exception import PaymentProviderError
from app.domain.payments.paymentIntent import PaymentIntent
from app.domain.payments.providerAnswer import ProviderAnswer
from app.domain.payments.providerAnswerStatus import ProviderAnswerStatus
from app.domain.planning.cadence import Cadence
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.savingsPlan import SavingsPlan
from app.domain.planning.schedule import Schedule
from app.domain.repositories.transaction_repository import TransactionRepository

#: A well-formed bank destination, reused by the plan fixture below.
BANK_DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)

#: The moment the ``build_wallet`` fixture opens and funds its pot at.
#:
#: Fixed rather than ``datetime.now()``, and the reason is now a domain one: a
#: pot records ``sealed_at`` and ``first_funded_at``, and a fixture that read the
#: clock would give every test a pot whose timeline moved with it. A test that
#: wanted to place a commitment relative to the pot's funding would then have to
#: know *when the suite ran* to write its expectation - which is the kind of
#: hidden dependency the rest of this suite avoids by passing moments in.
#:
#: One moment for the pot's opening, its sealing and its funding, which is
#: exactly the shape the pre-pots migration produces: a pot born already holding
#: money (see ``_migrate_locked_balance_into_funds``).
POT_MOMENT = datetime(2026, 1, 1)

#: The owner every fixture-built wallet and plan belongs to.
#:
#: **Fixed, not ``uuid4()``, and the difference is the whole of this constant.**
#: ``build_wallet`` used to mint a random ``user_id`` per call, which cost
#: nothing while nothing read it. It costs a great deal now: a wallet is only
#: reachable by the actor that owns it, so a random owner per wallet would make
#: every service call in the suite raise ``WalletNotFoundError`` - and the
#: failure would be about the fixture rather than about the code under test.
#: Roughly fifty tests take a ``build_wallet`` wallet and hand it to a service
#: built for the default actor; one shared owner is what keeps those tests
#: testing what they were testing.
#:
#: It is a constant rather than a fixture for the same reason ``POT_MOMENT`` is:
#: a fixture would have to be requested by every module that needs it, and the
#: natural mistake - a module that builds a service without requesting it - would
#: produce exactly the confusing failure above. A name in ``conftest``'s
#: namespace is importable and visible from anywhere, and reading it at a call
#: site says plainly which owner is meant.
#:
#: Tests about ownership are the ones that pass a *different* id deliberately,
#: through the ``user_id=`` dial below. That is the point of the default: a test
#: that wants a second user has to say so.
TEST_USER_ID = UUID("00000000-0000-4000-8000-000000000001")

#: The address :data:`TEST_USER_ID` is registered at.
#:
#: **``example.com`` rather than ``localhost``, and the change is not cosmetic: an
#: account registered at ``localhost`` cannot take a deposit.** A payment provider
#: refuses an address with no real domain - Paystack answers
#: ``invalid_email_address``, which a live run found the hard way - so a suite
#: whose standard account lived at ``localhost`` was asserting deposits that could
#: never have happened outside the suite. That is the same shape of blind spot as
#: the reference string this project fixed in the same week: the fixtures agreed
#: with the code, and neither agreed with the provider.
#:
#: ``example.com`` keeps the property the old address had - RFC 2606 reserves it
#: precisely so that it can never be a real account, and nobody can mistake one of
#: these for a person - and adds the one it lacked. It is not a guess either:
#: ``live@example.com`` was accepted by Paystack in the very live run that refused
#: ``live@localhost``, so the value is one there is evidence for.
#:
#: **And what was a convention here is now a rule.** When this constant moved, the
#: domain was a choice the suite made to keep its deposits honest - nothing stopped
#: a test registering at ``localhost`` anyway, and several did. ``SignUp`` refuses
#: an address with no real domain now, so a fixture that moved back would not be
#: making a poor choice; it would be refused. The paragraph above is history and the
#: rule is what makes it stick.
TEST_USER_EMAIL = "test@example.com"

#: The password :data:`TEST_USER_EMAIL` is registered with, where a test needs one.
#:
#: A shared constant rather than a literal in each test, and for once the reason
#: is not tidiness: a password is checked by ``LogIn`` against what was *stored*,
#: so a test that signed an account up with one literal and logged in with
#: another would fail at the login rather than at the assertion it was about.
#: One name means the two halves cannot disagree.
#:
#: A well-known passphrase rather than a random string, deliberately. It is
#: unmistakably not a secret anybody chose - so a copy of it in a log, an error
#: message or a stack trace reads as the test value it is, rather than as a
#: credential that leaked out of the suite. Note it is long enough for
#: ``PlainPassword``'s floor by construction: a test constant that the policy
#: would refuse is a constant that fails in whichever test happens to use it
#: first, for a reason that has nothing to do with that test.
TEST_USER_PASSWORD = "correct-horse-battery-staple"


def session_path_for(db_path: str) -> str:
    """Where a CLI run against ``db_path`` keeps its token.

    **The session is named after the database, and that is the whole function.**
    ``settings.session_path()`` defaults to ``~/.config/budget/session``, which is
    exactly right for a person and exactly wrong for a test suite: every test in
    the suite would share one file, they would run in whatever order pytest chose,
    and a developer who happened to be logged in on the machine running them would
    have their own token read by a test. Deriving the path from the database makes
    the two things a test run is made of - its storage and its identity - come from
    one ``tmp_path``, so a test's session cannot outlive it, cannot collide with a
    sibling's, and cannot be somebody's real one.

    A function rather than a fixture, because it is needed in two places that are
    not both fixtures: the ``signed_in`` fixture builds the session, and each CLI
    test module's ``run`` helper has to pass the same path as ``--session``. A
    fixture could only serve the first, and the second would have to reimplement
    the naming rule - which is the one thing that must not be duplicated, since a
    session written to one path and read from another is a test that fails with
    "not signed in" for no visible reason.

    ``db_path + ".session"`` rather than replacing the extension: a test is free
    to use two databases in one ``tmp_path``, and ``cli.db`` and ``cli.db2`` would
    both reduce to ``cli.session`` under ``with_suffix``.
    """
    return f"{db_path}.session"


def log_in_as(
    db_path: str,
    email: str = TEST_USER_EMAIL,
    password: str = TEST_USER_PASSWORD,
) -> str:
    """Sign in as an account that already exists, and store the token.

    Returns the session path, so a caller that wants to name it explicitly can.
    Raises whatever ``LogIn`` raises if the account or the password is wrong -
    ``InvalidCredentialsError`` - which is a fixture failing for a reason worth
    reading rather than a silent no.

    The account must exist; ``signed_in`` below is the version that creates it.
    The split matters for the tests that cross the two presentations: an account
    registered *through the API* has to be signed into *through the CLI* without
    being registered a second time, and a single helper that always registered
    would make that a ``DuplicateEmailError``.
    """
    logged_in = build_log_in(
        unit_of_work_factory=SqliteUnitOfWorkFactory(db_path),
        password_hasher=FakePasswordHasher(),
    ).execute(email, password, datetime.now())

    # ``_write_token`` rather than ``open(...).write(...)``, private name and all.
    # The token file's *format* is what the CLI reads back, and a test fixture that
    # wrote its own version of it would be a second implementation free to disagree
    # - about the trailing newline, the mode, the encoding. One writer means the
    # only thing this helper can get wrong is the path.
    path = session_path_for(db_path)
    _write_token(path, logged_in.token)
    return path


def signed_in(
    db_path: str,
    email: str = TEST_USER_EMAIL,
    password: str = TEST_USER_PASSWORD,
) -> str:
    """Register an account, sign in, and leave the token where the CLI will look.

    Returns the session path. This is ``log_in_as`` with a registration in front of
    it, and it is what every CLI test now needs and none of them used to: every
    wallet, fund and plan command resolves an actor before it does anything, so a
    test whose database has no account and whose session file does not exist fails
    with "not signed in" rather than with anything about wallets. Rather than add
    three lines to each of a hundred and eleven tests, they call this.

    It goes through the *use cases* rather than through the CLI's own ``signup``
    and ``login`` commands, and the difference matters. The commands are the real
    end-to-end path and they are tested as such in ``test_cli_identity.py``; what
    every other CLI test needs is the *precondition* - an account that exists and a
    valid token on disk - and building that through two command invocations would
    make a hundred tests depend on the login command's output format as well as on
    its behaviour.

    **The hasher here is the fake one, and that is a deliberate trade with a sharp
    edge worth naming.** Argon2 costs tens of milliseconds by design; paying it
    twice per test, a hundred and eleven times, is most of a minute spent on
    arithmetic that ``tests/infrastructure/security/`` tests directly. So the
    account this creates holds a hash only ``FakePasswordHasher`` can verify -
    which is invisible to every caller, because nothing in the CLI *verifies a
    password* except the ``login`` command. A test that wants to log in through the
    CLI must therefore register through the CLI too, and that is exactly what
    ``test_cli_identity.py`` does.
    """
    factory = SqliteUnitOfWorkFactory(db_path)
    hasher = FakePasswordHasher()
    now = datetime.now()

    build_sign_up(unit_of_work_factory=factory, password_hasher=hasher).execute(
        email, password, now
    )
    return log_in_as(db_path, email, password)


#: A second owner, for the tests that need two of them.
#:
#: A fixed second id rather than a ``uuid4()``: the test that matters most here
#: (two users, both plans due, one tick) reads more clearly with two named
#: constants than with one constant and one anonymous value, and a fixed value
#: makes a failure message say ``...0002`` instead of a random string nobody can
#: compare against anything.
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture
def actor():
    """The user the suite's services act as: ``TEST_USER_ID``, by fixture.

    A fixture rather than an import from this module, and that is the house
    pattern rather than a preference - ``POT_MOMENT`` is a plain constant only
    because nothing outside this file ever names it, while ``build_wallet`` and
    ``build_plan`` are fixtures precisely so a test module can ask for them
    without importing anything. Ownership is something plenty of modules need to
    name, so it comes through the same door as everything else they share.

    It resolves to the same value ``build_wallet`` and ``build_plan`` default
    their owner to, which is what makes ``service.get_wallet(wallet.wallet_id)``
    work in a test that built its wallet with the fixture: the wallet belongs to
    whoever the service acts as.
    """
    return TEST_USER_ID


@pytest.fixture
def stranger():
    """A second user, who owns nothing the test built unless it says so.

    Named for what it is used *for* rather than as ``other_actor``: every test
    that requests it is asking "and what does somebody else see?", and the
    answer should be nothing. A test that wants this user to own something
    builds it deliberately with ``user_id=stranger``.
    """
    return OTHER_USER_ID

#: Every variable ``settings`` reads. Cleared for every test in the suite.
#:
#: ``os.environ`` is a global that exactly one module reads, and leaving it
#: alone would make the tests depend on whoever is running them: a developer
#: with SMTP configured would have ``plan tick`` tests that queue nothing and
#: then reach out over the network, and the same suite would pass on a clean
#: machine and fail on a working one. Clearing them here is what makes "no test
#: opens a socket" a property of the suite rather than a property of the shell
#: it happens to run in. A test that wants a configured install sets the
#: variables it needs with ``monkeypatch.setenv``.
#:
#: ``BUDGET_DB`` is in the list for the second half of that argument. It is not a
#: mail setting, but it is read by the same module, and a developer with it
#: exported would have the API tests write to - and read from - a real database
#: file instead of the one their ``tmp_path`` fixture made. The failure would be
#: confusing in a way a missing variable never is: the suite would pass, against
#: the wrong storage.
#:
#: ``BUDGET_SESSION`` is the same argument again, and it is worth spelling out
#: because its wrong-storage failure is worse. A developer with it exported has a
#: token in that file, and a CLI test that read it would be *authenticated as
#: them* while acting against a ``tmp_path`` database - so the run would present a
#: perfectly valid token for a user that database has never heard of, and every
#: wallet command would refuse with "that token is not a valid session". Nothing
#: about that message points at the environment, and the test that failed would be
#: whichever one ran first.
SETTINGS_VARIABLES = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_STARTTLS",
    "BUDGET_NOTIFY_TO",
    "BUDGET_NOTIFY_FROM",
    "BUDGET_DB",
    "BUDGET_SESSION",
    "PAYSTACK_SECRET_KEY",
)


@pytest.fixture(autouse=True)
def no_settings_environment(monkeypatch):
    for name in SETTINGS_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class FakeChannel(NotificationChannel):
    """A delivery channel that records instead of sending.

    This is the seam that means no test in this suite opens a socket. It is not
    a mock: it implements the real port, so anything that passes with this passes
    with the SMTP adapter as far as the calling code is concerned.

    ``failures`` is a queue of exceptions to raise, consumed one per attempt.
    Once it is empty, sends succeed - which is what makes "the second tick gets
    through" expressible without any further arrangement.
    """

    def __init__(self, failures=()):
        self.attempts: list = []
        self.sent: list = []
        self._failures = list(failures)

    def send(self, message) -> None:
        self.attempts.append(message)
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(message)


def code_in(message) -> str:
    """The code out of a verification message, the way a person would read it.

    **Here rather than in one of the three test modules that need it**, and the
    placement is the argument: the *text* belongs to
    ``emailChangeMessage.verification_message``, the envelope belongs to
    ``FakeChannel`` just above, and what is left - four lines that find the line
    under a label - is neither module's to own. Three copies of it would be three
    things to change when the message changes, and the two that nobody thought
    about would fail with an ``IndexError`` in a test about something else.

    Recovered from the text rather than from a returned value, and that is the
    point rather than an inconvenience: ``RequestEmailChange`` deliberately returns
    no token - it is not in the outcome, not in the response, not in a log - so the
    mailbox is the only place one exists. A test that wants to answer a request is
    under the same constraint the person is: it has to be holding the envelope.

    Found by its *label* rather than by matching the token alphabet, which would be
    ambiguous. ``budget-manager`` appears two lines below the code and is spelled
    entirely in characters a urlsafe token may contain, so "the longest
    token-looking run in the body" would sometimes return the wrong line.
    """
    lines = message.body.splitlines()
    label = next(
        index
        for index, line in enumerate(lines)
        if "present this code to confirm it" in line
    )
    return lines[label + 2].strip()


@pytest.fixture
def build_wallet():
    """Return a fresh Wallet in the requested state.

    ``locked`` builds one open pot named "Locked" rather than a locked balance,
    because a wallet no longer *has* a locked balance to set - it is the sum of
    its pots. The same name and the same openness are what the migration gives
    every pre-existing locked balance (see ``_migrate_locked_balance_into_funds``),
    so a test written against ``locked="4000"`` describes exactly the wallet it
    always did: 4000 reserved, and nothing preventing its release.

    That equivalence is what keeps this factory worth having. Roughly fifty tests
    reach the locked balance only through ``locked=``, and none of them should
    have to learn what a pot is to keep testing what they were testing.
    """

    def _build(
        available: str = "10000",
        locked: str = "0",
        status: WalletStatus = WalletStatus.ACTIVE,
        currency: Currency = Currency.NGN,
        user_id: UUID = TEST_USER_ID,
    ) -> Wallet:
        # Built ACTIVE and given its pot first, with the requested status applied
        # at the end. Not a detail: ``open_fund`` refuses a closed wallet, which
        # is correct domain behaviour, so a fixture that set the status up front
        # could not build the very states its callers ask for. Constructing a
        # state is also not the same as transitioning to it - ``freeze`` and
        # ``unfreeze`` are tested on their own, and this factory is entitled to
        # start where it likes.
        #
        # ``user_id`` is a dial rather than a constant baked in, so a test about
        # ownership can build a wallet belonging to somebody else - which is the
        # only way to write "and the other user cannot see it".
        wallet = Wallet(
            wallet_id=uuid4(),
            user_id=user_id,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(Decimal(available), currency),
            currency=currency,
        )
        if Decimal(locked) != 0:
            # Only when there is something to put in it: a wallet with no locked
            # money should have no pots, not an empty one. An empty pot would make
            # ``locked=0`` build a wallet differing in *shape* from
            # ``build_wallet()``, and shape is exactly what the payout draw order
            # depends on.
            fund = wallet.open_fund(
                "Locked", FundKind.PERSONAL, as_of=POT_MOMENT
            )
            wallet.deposit_into_fund(
                fund.fund_id, Money(Decimal(locked), currency), POT_MOMENT
            )
        wallet.status = status
        return wallet

    return _build


@pytest.fixture
def build_plan():
    """Return a fresh SavingsPlan in the requested state.

    Defaults describe the product's headline case: a locked-source monthly plan
    paying 2000 NGN to one named bank account, starting 1 January 2026.

    The anchor default is **midnight**, and that is a deliberate choice about
    what the suite can see. ``date(...) != datetime(...)``, so a midnight anchor
    still catches a value that came back as a plain date - the type regression.
    It does *not* catch a moment that lost its time of day on the way through,
    because midnight is the one answer that losing the time cannot change; the
    noon-based tests in ``test_schedule.py`` and ``test_savings_plan.py`` are
    what cover that. Keeping midnight here buys readable expectations everywhere
    else in exchange for those few tests carrying the weight.
    """

    def _build(
        source: PlanSource = PlanSource.LOCKED,
        cadence: Cadence = Cadence.MONTHLY,
        anchor: datetime = datetime(2026, 1, 1),
        instructions: tuple[Instruction, ...] | None = None,
        status: PlanStatus = PlanStatus.ACTIVE,
        completed_runs: int = 0,
        ends_on: date | None = None,
        wallet_id: UUID | None = None,
        name: str = "salary",
        fund_id: UUID | None = None,
        created_at: datetime = datetime(2026, 1, 1),
        user_id: UUID = TEST_USER_ID,
    ) -> SavingsPlan:
        """Build a plan, defaulting to one that names no pot.

        ``fund_id`` defaults to ``None``, and that default is the honest one
        rather than a convenience: ``None`` is what a plan saved before pots
        could be named looks like, which is the shape most of this suite is
        about. A test that wants the commitment rule exercised names a pot - and
        it has to want that deliberately, which is the point.

        ``created_at`` is fixed for the mirror-image reason ``POT_MOMENT`` is:
        it is one half of the ``sealed_at <= committed_at <= first_funded_at``
        comparison, so a plan built with ``datetime.now()`` would make that
        ordering depend on when the suite ran.

        ``user_id`` is the plan's owner, and note it defaults to the same
        constant ``build_wallet`` does. That is not a coincidence to be
        maintained by hand: a plan whose owner is not its wallet's owner is a
        state the scheduler cannot act on, so tests that build a plan *and* a
        wallet together should let both default. The dial is here for the tests
        about that state, and for the two-user scheduler test where the point is
        that each plan's owner differs.
        """
        if instructions is None:
            instructions = (
                Instruction(
                    action=PlannedAction.PAYOUT,
                    amount=Money(Decimal("2000"), Currency.NGN),
                    label="salary",
                    destination=BANK_DESTINATION,
                ),
            )
        return SavingsPlan(
            wallet_id=wallet_id if wallet_id is not None else uuid4(),
            user_id=user_id,
            name=name,
            source=source,
            schedule=Schedule(cadence=cadence, anchor=anchor),
            _instructions=instructions,
            status=status,
            completed_runs=completed_runs,
            ends_on=ends_on,
            fund_id=fund_id,
            created_at=created_at,
        )

    return _build


@pytest.fixture
def build_user():
    """Return a fresh User, defaulting to the suite's standard owner.

    Deliberately *not* a module-level object like ``TEST_USER_ID`` is a constant.
    A ``User`` is mutable in the sense that matters here - it is a row - so a
    single shared instance would be saved by one test and read by the next, and
    a test that changed it would change it for everyone. The id is shared; the
    objects are not.

    Defaults to the same address ``TEST_USER_ID`` is registered at, so
    ``build_user()`` and ``TEST_USER_ID`` describe one account rather than two
    that look alike. Tests about lookup pass a different address through
    ``email=``.
    """

    def _build(
        email: str = TEST_USER_EMAIL,
        user_id: UUID = TEST_USER_ID,
        google_subject: str | None = None,
        created_at: datetime = POT_MOMENT,
    ) -> User:
        return User(
            user_id=user_id,
            email=email,
            google_subject=google_subject,
            # ``POT_MOMENT`` rather than ``datetime.now()``, for the reason every
            # other moment in this module is fixed: an assertion about a stored
            # value should not depend on when the suite ran. Note the field is
            # *not* validated for being in the past - an account created a moment
            # ago is ordinary, and a clock that disagrees with the store's is not
            # something this aggregate should have an opinion about.
            created_at=created_at,
        )

    return _build


@pytest.fixture
def build_channel():
    """Return a fresh FakeChannel, optionally told to fail the first N sends.

        build_channel()                                  # every send succeeds
        build_channel(failures=[OSError("refused")])      # the first send raises
        build_channel(failures=[OSError("a"), OSError("b")])   # the first two
    """

    def _build(failures=()) -> FakeChannel:
        return FakeChannel(failures=failures)

    return _build


class FakePasswordHasher(PasswordHasher):
    """A hasher that is deliberately weak and deliberately instant.

    **Not a mock.** It implements the real port and is wrong in exactly one way
    that matters: it is fast. That is the point of it. The real adapter is argon2id
    with default parameters, which is *designed* to take tens of milliseconds and
    megabytes of memory per call - a cost a login rightly pays once and a test
    suite would pay on every sign-up in every file. Two hundred tests at 80ms is
    sixteen seconds of wall clock spent proving that arithmetic works.

    What it keeps is every *observable* property the calling code depends on, and
    each one is here for a reason a test would otherwise lose:

    - The hash is not the password, so a test can assert that nothing stored
      contains what the user typed.
    - Two hashes of one password differ, because the salt is random - so
      ``LogIn`` cannot accidentally be written to compare hashes rather than call
      ``verify``, which is the mistake the port's docstring warns about.
    - ``verify`` is the only way to check, and it answers ``False`` rather than
      raising for a wrong password or a value that is not one of its own hashes.

    What it does *not* keep is the cost, which is the entire reason it exists, and
    the algorithmic properties that make argon2 worth its cost - preimage
    resistance, memory-hardness, a versioned encoding. Those are the adapter's, and
    they are tested against the adapter in
    ``tests/infrastructure/security/test_argon2_password_hasher.py``. A test that
    wants to know whether *this* codebase stores a hash it can check does not need
    argon2 to answer; a test that wants to know argon2 works must use argon2.

    The ``$``-separated encoding mirrors the real one's shape rather than its
    content, so that a value produced here is recognisably a hash rather than a
    password - and ``verify`` refuses anything tagged with another scheme, which is
    what makes it safe to hand a test that deliberately corrupts a column.
    """

    #: The tag on every hash this produces. Named so that a stored value says
    #: which hasher made it, exactly as ``$argon2id$`` does.
    SCHEME = "fake"

    def hash(self, password: PlainPassword) -> str:
        salt = secrets.token_hex(8)
        return f"{self.SCHEME}${salt}${self._digest(salt, password)}"

    def verify(self, password: PlainPassword, encoded: str) -> bool:
        parts = encoded.split("$")
        if len(parts) != 3 or parts[0] != self.SCHEME:
            return False
        scheme, salt, digest = parts
        # ``compare_digest`` rather than ``==``, so that the double is not the one
        # place in the codebase comparing a secret with a short-circuiting
        # operator. It is not protecting anything here - the values are test
        # values - but a reader who learned the shape from this file would learn
        # the wrong one.
        return hmac.compare_digest(digest, self._digest(salt, password))

    @staticmethod
    def _digest(salt: str, password: PlainPassword) -> str:
        return hashlib.sha256(f"{salt}{password.secret}".encode("utf-8")).hexdigest()


@pytest.fixture
def typed_password(monkeypatch):
    """Answer the CLI's password prompt with :data:`TEST_USER_PASSWORD`.

    A fixture returning a function, so a test can choose the password it types:

        typed_password()                 # the shared constant
        typed_password("something-else")  # a password no account was made with

    **``getpass`` is patched, and there is no other way to test this.** The
    password is read from the terminal rather than from ``argv`` - which is the
    design, and it is not negotiable - so a test that wanted to drive the real path
    would have to attach a pty and write to it. Patching ``getpass.getpass``
    itself, rather than anything in ``app.presentation.cli``, keeps the test
    honest about what is being replaced: the *terminal*, not the CLI's handling of
    what the terminal returned. ``_prompt_password`` still runs, so the
    confirmation check and the refusal on a mismatch are still exercised.

    The patch is applied when the returned function is *called* rather than when
    the fixture is built, so a test that wants to type one thing for ``signup``
    and another for ``login`` can ask twice.
    """

    def _typed(password: str = TEST_USER_PASSWORD):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": password)

    return _typed


@pytest.fixture
def password_hasher():
    """A fresh :class:`FakePasswordHasher` for each test.

    Fresh rather than shared, though it holds no state: the point is that a test
    which wants to subclass or spy on one can do so without reaching into a
    module-level object other tests are using.
    """
    return FakePasswordHasher()


class RecordingTransactionRepository(TransactionRepository):
    """A ledger that keeps every save, in order, and nothing else.

    ``InMemoryTransactionRepository`` keeps only the latest row per transaction,
    which is what most tests want - they ask what the *final* row says. This one
    exists for the tests that ask what the *sequence* said, which is how the
    PENDING-then-SUCCESSFUL shape of ``WalletOperation.execute`` is observable at
    all: by the time an operation returns, PENDING has been overwritten and the
    in-memory repository has forgotten it ever existed.

    Five test modules used to define their own copy of this class. The three fund
    modules share the fixture instead; consolidating the older five onto it is a
    cleanup that belongs with whatever touches them next, not with this change.

    **The statuses are snapshots, taken at save time, and that is load-bearing.**
    ``save`` is called twice for one transaction, and the second call is after
    the *same object* has been marked FAILED or SUCCESSFUL - so a property
    reading ``[transaction.status for transaction in self.saved]`` would report
    the final status twice and never show PENDING at all. The only way to observe
    the PENDING-then-SUCCESSFUL sequence is to record what the status was when
    the save happened, which is what the original per-module copies did and what
    this one has to keep doing.
    """

    def __init__(self):
        self.saved = []
        self.saved_statuses = []

    def save(self, transaction):
        self.saved.append(transaction)
        self.saved_statuses.append(transaction.status)
        return transaction

    def get_by_id(self, transaction_id):
        raise NotImplementedError

    def get_by_internal_reference(self, internal_reference):
        return None

    def get_by_wallet_id(self, wallet_id):
        raise NotImplementedError

    def get_by_provider_reference(self, provider_reference):
        raise NotImplementedError

    def list_awaiting_provider(self):
        raise NotImplementedError


@pytest.fixture
def recording_transactions():
    """A ``RecordingTransactionRepository``, fresh for each test."""
    return RecordingTransactionRepository()


#: The secret a fake provider signs and verifies with.
#:
#: Shaped like Paystack's own keys - ``sk_test_`` and thirty-odd characters of
#: hex - so that a test which accidentally printed one would be recognisable as a
#: test value rather than mistaken for a live credential. It is not a credential:
#: it is a string two functions in one process agree about, and ``sk_test_``
#: rather than ``sk_live_`` is the honest prefix for exactly that.
TEST_PAYSTACK_SECRET = "sk_test_" + "0" * 32

#: The characters Paystack accepts in a transaction reference.
#:
#: Quoted from its own rejection of a reference this codebase used to send, which
#: is the sentence that found the bug: *"Ensure that you aren't using any
#: characters that aren't alphanumeric or contained in '-,., =' in your
#: reference"*. The space inside those quotes is read as punctuation rather than a
#: member of the set - no reference this system mints contains one, and refusing
#: it is the safe side of an ambiguous sentence.
#:
#: **A second copy of the rule, deliberately.** It could be imported from
#: ``app.domain.money.reference``, and importing it would make this constant
#: agree with the code by construction rather than by being right - see
#: ``FakePaymentProvider``'s docstring for what that cost the last time.
_PAYSTACK_REFERENCE_ALPHABET = frozenset(ascii_letters + digits + "-.,=")


class FakePaymentProvider(PaystackPaymentProvider):
    """A payment provider that answers instead of calling out.

    **It subclasses the real adapter and replaces the two methods that call
    out**, which is a deliberate departure from ``FakeChannel`` and
    ``FakePasswordHasher`` above, and the difference is worth the paragraph.
    Those two reimplement their ports from scratch because the real
    implementations cost something a test cannot pay - argon2 is slow by design,
    and SMTP needs a server - so there is nothing to inherit. Here the real
    adapter has three halves and only one of them can hurt: the two that make a
    request open a socket, and ``verify_signature`` is pure HMAC over bytes with
    no I/O anywhere in it.

    So the choice is between a fake that reimplements HMAC-SHA512 and one that
    *is* the adapter's verification with the network calls stubbed out. The first
    would pass whether or not the adapter's verification worked - the classic
    way a security test ends up testing itself - and the second is what lets
    ``test_webhooks.py`` assert that a body altered after signing is refused,
    with no provider account and no second copy of the signing rule anywhere in
    this suite.

    That argument used to cover one method, and reconciliation is what made it
    cover two: ``outcome_for`` also opens a socket, so leaving it un-overridden
    would have sent the whole API suite to the network the day it was written.
    The rule that keeps this honest is the one this class has always followed -
    **every method that leaves the process is replaced here, and every method
    that is pure is inherited** - which is now two and one rather than one and
    one.

    What the replacement keeps is the contract: a reference in, a ``PaymentIntent``
    out, and ``PaymentProviderError`` raised rather than a sentinel returned when
    it cannot do its job. ``requests`` records every call so a test can ask what
    was sent - which is how "the provider was told the payer's email and nothing
    else about them" is assertable.

    ``failures`` is a queue of exceptions to raise from ``initialize_deposit``,
    consumed one per attempt. Once it is empty, calls succeed - the same shape
    ``FakeChannel`` uses, and for the same reason: it makes "the retry gets
    through" expressible without any further arrangement. It belongs to the
    deposit path alone; a reconciler test that wants a lookup to fail scripts
    that into ``answers`` instead, where it can name the row.

    **And it refuses what Paystack refuses**, which is the one thing that changed
    after a bug this double could not see. The alphabet below is written out here
    rather than imported from ``app.domain.money.reference``, because a double
    that borrows the rule it is checking cannot check it: the reference that broke
    every deposit against the real provider was perfectly well formed as far as
    this file was concerned, so the whole API suite passed while the far end was
    rejecting all of it. Quoting Paystack's own rejection keeps the two copies
    independent, so a separator changed on one side fails on the other.

    ``provider_reference`` is the name this fake reports the collection under.
    It defaults to the reference it was handed, which is what Paystack does when
    it is given one - and it is a dial because the honest thing to test is that
    the two columns are separate even when the values agree.

    ``answers`` is what ``outcome_for`` will say, keyed by the reference asked
    about, and ``lookups`` is every reference it was asked about in order. Two
    things about the mapping are worth stating:

      - **An unscripted reference answers ``NOT_SETTLED``**, because that is what
        a real run mostly hears and because it is the answer that changes
        nothing. A test therefore scripts only the rows it is about, and a test
        that forgot to script one gets a quiet run rather than a confusing
        error - which is why the *other* assertion, that nothing was asked at
        all, is made against ``lookups`` rather than against the answers.
      - **A value may be an exception**, which is how "this one row raises and
        the rest still settle" is expressible without depending on call order.
        A queue consumed per call would work too, but it would say "the second
        lookup fails" when the test means "dep-2 fails", and those stop being the
        same sentence the moment the ordering changes.
    """

    def __init__(
        self,
        secret_key: str = TEST_PAYSTACK_SECRET,
        failures=(),
        provider_reference: str | None = None,
        answers=None,
    ):
        super().__init__(secret_key=secret_key)
        self.requests: list = []
        self.lookups: list = []
        self._failures = list(failures)
        self._provider_reference = provider_reference
        self._answers = dict(answers or {})

    def initialize_deposit(
        self, *, reference: str, amount: Money, email: str
    ) -> PaymentIntent:
        self.requests.append(
            {"reference": reference, "amount": amount, "email": email}
        )
        if not set(reference) <= _PAYSTACK_REFERENCE_ALPHABET:
            # Paystack's answer to this is a 400 whose body reads as though
            # *this* system is broken - ``invalid_character_in_reference``, with
            # the allowed set in a ``meta.nextStep`` string. Raised as the same
            # failure the adapter would raise, so a caller has one thing to catch
            # and a test can say which reference it was about.
            #
            # The sentence carries the provider's own words, because the real
            # adapter's does now: it reports the two error-contract fields the
            # provider sent, which is what a live run proved was missing. A double
            # that refuses for the right reason but says less than the thing it
            # stands in for is the same class of gap as one that refuses nothing.
            raise PaymentProviderError(
                "the payment provider refused the call with 400: "
                "Invalid character in transaction reference "
                "(invalid_character_in_reference)"
            )
        if self._failures:
            raise self._failures.pop(0)
        return PaymentIntent(
            authorization_url=f"https://checkout.paystack.test/{reference}",
            provider_reference=(
                reference
                if self._provider_reference is None
                else self._provider_reference
            ),
        )

    def outcome_for(self, reference: str) -> ProviderAnswer:
        self.lookups.append(reference)
        answer = self._answers.get(reference)
        if answer is None:
            return ProviderAnswer(ProviderAnswerStatus.NOT_SETTLED)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def build_payment_provider():
    """Return a fresh :class:`FakePaymentProvider`.

        build_payment_provider()                              # every call works
        build_payment_provider(failures=[PaymentProviderError("down")])
        build_payment_provider(secret_key="sk_test_other")     # a key nothing signs with
        build_payment_provider(answers={"dep-1": an_answer})   # what a lookup says
    """

    def _build(**kwargs) -> FakePaymentProvider:
        return FakePaymentProvider(**kwargs)

    return _build


def sign_paystack_body(
    body: bytes, secret_key: str = TEST_PAYSTACK_SECRET
) -> str:
    """The signature Paystack would put on ``body``, for a test to send.

    **This is the one place the suite writes the signing rule, and it writes it
    on the *test's* side of the wire.** It exists so that a request can be built
    that a real verifier will accept, and deliberately not so that anything can
    be checked with it - the verification under test is the adapter's, and a test
    that asked this function whether a signature was good would be asking the
    thing it is trying to test. The distinction is the same one ``FakeChannel``
    makes by recording rather than asserting.

    Hex, lowercase, SHA-512, over the bytes as sent. Everything about that
    sentence is Paystack's rather than ours, which is why a wrong guess here
    shows up as a failing test rather than as a passing one that proved nothing.
    """
    return hmac.new(
        secret_key.encode("utf-8"), body, hashlib.sha512
    ).hexdigest()


@pytest.fixture
def sign_webhook():
    """``sign_paystack_body`` bound to a secret a caller can override.

    A fixture rather than a bare import so that a test module reads on its own -
    the same reason ``build_wallet`` and ``build_channel`` are fixtures. The
    argument is the *secret*, not the body, because the body is the thing under
    the test's control in every case and the secret is the one dial: signing with
    the right key and signing with a wrong one are the two halves of every
    signature test there is.
    """

    def _sign(body: bytes, secret_key: str = TEST_PAYSTACK_SECRET) -> str:
        return sign_paystack_body(body, secret_key)

    return _sign
