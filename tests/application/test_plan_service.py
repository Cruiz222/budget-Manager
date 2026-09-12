from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.plan_service import PlanService
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    CurrencyMismatchError,
    FundNotFoundError,
    WalletNotFoundError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.planning.cadence import Cadence
from app.domain.planning.exception import (
    EmptyPlanInstructionsError,
    IrreversibleReleasePlanError,
    MissingPlanFundError,
    PlanAlreadyFinishedError,
    PlanNotActiveError,
    PlanNotPausedError,
    SavingsPlanNotFoundError,
    UnexpectedPlanFundError,
)
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.schedule import Schedule
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from tests.conftest import TEST_USER_ID

NGN = Currency.NGN
USD = Currency.USD
ANCHOR = datetime(2026, 1, 1)

#: The user every service in this file acts as - the same one ``build_wallet``
#: and ``build_plan`` give their objects, so a wallet or plan built by a fixture
#: is reachable from here. See ``test_wallet_service.ACTOR``.
ACTOR = TEST_USER_ID

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


def build_service(tmp_path, name="plan_service.db"):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / name))
    return PlanService(factory, actor=ACTOR), factory


def save_wallet(factory, wallet):
    uow = factory.start()
    try:
        uow.wallets.save(wallet)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise
    return wallet


def stored_wallet(factory, wallet_id):
    """Read a wallet back from the store, as the service would.

    Needed because a service call is its own unit of work: it loads its own copy
    of the aggregate, changes it, and commits. The object a test was handed is a
    snapshot from before that, so a change the service made - opening a pot, most
    of all - is invisible on it. Asking the database is the only way to see what
    actually landed.
    """
    uow = factory.start()
    try:
        return uow.wallets.get_owned(wallet_id, ACTOR)
    finally:
        uow.rollback()


def payout(amount: str, currency: Currency = NGN, label: str = "salary") -> Instruction:
    return Instruction(
        action=PlannedAction.PAYOUT,
        amount=Money(Decimal(amount), currency),
        label=label,
        destination=DESTINATION,
    )


def release(amount: str, currency: Currency = NGN, label: str = "unlock") -> Instruction:
    return Instruction(
        action=PlannedAction.RELEASE,
        amount=Money(Decimal(amount), currency),
        label=label,
    )


def open_pot(factory, wallet_id, name="Savings"):
    """Give a wallet an empty pot, the way ``fund open`` does.

    Needed because a locked-source plan must now name the pot it draws from, and
    most of this file is not about pots at all - it is about creating, reading and
    steering plans. Opening one here keeps those tests describing what they always
    described: a plan in the ordinary shape, which now includes a pot.

    Idempotent on purpose. ``test_a_wallets_plans_all_come_back`` creates two
    plans against one wallet, and a second ``open`` of the same name is a
    duplicate the wallet would rightly refuse.
    """
    uow = factory.start()
    try:
        wallet = uow.wallets.get_owned(wallet_id, ACTOR)
        if not any(fund.name == name for fund in wallet.funds):
            wallet.open_fund(name, FundKind.PERSONAL, as_of=ANCHOR)
        uow.wallets.save(wallet)
        uow.commit()
    except BaseException:
        uow.rollback()
        raise


def create(service, factory, wallet_id, instructions=None, **overrides):
    """Create a plan through the service, with a pot opened for it if it needs one.

    ``fund_name`` is not an ``override`` the way ``source`` is: when the source is
    locked the pot is opened first and named on the way through, because that is
    what the CLI does and what the rule now requires. A test that wants the two
    bad pairings - a locked plan with no pot, an available plan naming one -
    says so explicitly and bypasses this helper, which is the point of it being a
    convenience rather than the only door.

    Note the ``is not None`` on the ``open_pot`` call, which is not defensive
    padding. A test that passes ``fund_name=None`` is asking for the *refusal*,
    and opening a pot called ``None`` first would raise ``InvalidFundNameError``
    from the pot before the rule under test was ever reached - the test would
    still go red, but for a reason that has nothing to do with what it claims.
    """
    source = overrides.pop("source", PlanSource.LOCKED)
    fund_name = overrides.pop(
        "fund_name", "Savings" if source is PlanSource.LOCKED else None
    )
    if source is PlanSource.LOCKED and fund_name is not None:
        open_pot(factory, wallet_id, fund_name)
    return service.create_plan(
        wallet_id=wallet_id,
        name=overrides.pop("name", "Salary 2026"),
        source=source,
        schedule=overrides.pop("schedule", Schedule(Cadence.MONTHLY, ANCHOR)),
        instructions=instructions if instructions is not None else (payout("2000"),),
        fund_name=fund_name,
        **overrides,
    )


class TestCreatingAPlan:
    def test_creates_and_persists(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        plan = create(service, factory, wallet.wallet_id, name="Salary 2026")

        assert service.get_plan(plan.plan_id).name == "Salary 2026"

    def test_a_plan_may_exist_before_it_is_affordable(self, tmp_path, build_wallet):
        """An empty wallet does not stop a plan being created.

        This is the product rule written as an executable claim. Saving towards
        something means the plan comes first and the money arrives later; a
        creator that checked the balance would make the feature impossible to
        use the way it is meant to be used. The shortfall is discovered at run
        time, and recorded as a blocked run.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(available="0", locked="0"))

        plan = create(service, factory, wallet.wallet_id)

        assert plan.total_to_move == Money(Decimal("2000"), NGN)
        assert service.get_plan(plan.plan_id).status is PlanStatus.ACTIVE

    def test_a_release_plan_records_its_end_date(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        plan = create(
            service,
            factory,
            wallet.wallet_id,
            instructions=(release("1000"),),
            ends_on=date(2027, 1, 1),
        )

        assert service.get_plan(plan.plan_id).ends_on == date(2027, 1, 1)


class TestTheCurrencyRule:
    """The one invariant neither aggregate can hold, because it spans both.

    It lives here, in the use case, for the reason SavingsPlan's docstring gives:
    the plan cannot see its wallet and the wallet has never heard of a plan, so
    the only place both are loaded is the place that can check.
    """

    def test_a_plan_in_another_currency_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(currency=USD))

        with pytest.raises(CurrencyMismatchError):
            create(service, factory, wallet.wallet_id, instructions=(payout("2000", NGN),))

    def test_a_refused_plan_leaves_nothing_behind(self, tmp_path, build_wallet):
        """The check runs *before* the save, so a refusal writes nothing.

        Worth pinning down: the difference between "refused" and "saved and then
        refused" is invisible until something reads the store, and by then the
        plan has already been ticked over.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(currency=USD))

        with pytest.raises(CurrencyMismatchError):
            create(service, factory, wallet.wallet_id, instructions=(payout("2000", NGN),))

        assert service.plans_for_wallet(wallet.wallet_id) == []

    def test_a_plan_in_the_wallets_currency_is_accepted(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet(currency=USD))

        plan = create(service, factory, wallet.wallet_id, instructions=(payout("2000", USD),))

        assert plan.total_to_move.currency is USD


class TestThePotRule:
    """Which source may name a pot, and what happens if you get it the wrong way round.

    Four rows, and they are a *pairing* rather than two independent choices:

        source      fund_name   outcome
        locked      given       accepted - the plan draws on that pot
        locked      omitted     refused  - a locked plan must name its pot
        available   omitted     accepted
        available   given       refused  - an available plan spends no pot

    The two refusals are deliberately not symmetric. "A locked plan must name a
    pot" is a rule the *use case* holds, not the aggregate, because a plan saved
    before pots could be named has no pot and must keep working; "an available
    plan may not name one" is held by both, at different doors.
    """

    def test_a_locked_plan_naming_a_pot_is_accepted(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        plan = create(
            service,
            factory,
            wallet.wallet_id,
            fund_name="Vacation",
        )

        assert plan.fund_id is not None
        # Resolved from the name to the wallet's own pot, not invented: the plan
        # has to point at a real row, or the first run fails looking it up.
        #
        # Read back rather than asked of the local ``wallet``, because ``create``
        # opens the pot in its own unit of work - the object above is a snapshot
        # from before the pot existed.
        assert plan.fund_id == stored_wallet(
            factory, wallet.wallet_id
        ).fund_by_name("Vacation").fund_id

    def test_a_locked_plan_naming_no_pot_is_refused(self, tmp_path, build_wallet):
        """The rule that makes the whole commitment meaningful.

        A plan that draws on the locked balance without saying which pot has no
        commitment for ``authorises_early_payout`` to judge - it would be a plan
        that spends whatever is there, which is the pooled behaviour this phase
        removed.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        with pytest.raises(MissingPlanFundError):
            create(service, factory, wallet.wallet_id, fund_name=None)

    def test_an_available_plan_naming_a_pot_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        with pytest.raises(UnexpectedPlanFundError):
            create(
                service,
                factory,
                wallet.wallet_id,
                source=PlanSource.AVAILABLE,
                fund_name="Vacation",
            )

    def test_the_pairing_is_judged_before_the_pot_is_looked_up(
        self, tmp_path, build_wallet
    ):
        """A nonsense name on the wrong source is refused for the pairing.

        The order of the two checks is observable, and this test exists to make
        it so. Refusing ``--source available --from-fund Nonsense`` for an
        unknown pot would send the user off to check a pot that was never going
        to be used; the pairing is the thing they got wrong.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        with pytest.raises(UnexpectedPlanFundError):
            create(
                service,
                factory,
                wallet.wallet_id,
                source=PlanSource.AVAILABLE,
                fund_name="No Such Pot",
            )

    def test_an_available_plan_naming_no_pot_is_accepted(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        plan = create(
            service,
            factory,
            wallet.wallet_id,
            source=PlanSource.AVAILABLE,
        )

        assert plan.fund_id is None

    def test_an_unknown_pot_is_refused_at_creation(self, tmp_path, build_wallet):
        """Refused when a human typed the name, not weeks later at the first run.

        The pot is *loaded* rather than merely recorded, which is what turns a
        typo from a scheduler surprise into a refusal at the terminal - and it
        costs nothing, because the wallet is already open for the currency rule.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        with pytest.raises(FundNotFoundError):
            service.create_plan(
                wallet_id=wallet.wallet_id,
                name="Salary 2026",
                source=PlanSource.LOCKED,
                schedule=Schedule(Cadence.MONTHLY, ANCHOR),
                instructions=(payout("2000"),),
                fund_name="No Such Pot",
            )


class TestReading:
    def test_an_unknown_plan_is_an_error(self, tmp_path):
        service, _ = build_service(tmp_path)

        with pytest.raises(SavingsPlanNotFoundError):
            service.get_plan(uuid4())

    def test_a_wallet_with_no_plans_lists_none(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())

        assert service.plans_for_wallet(wallet.wallet_id) == []

    def test_an_unknown_wallet_is_an_error(self, tmp_path):
        """Distinguishable from "a wallet with no plans" - the same split the
        transaction queries make, and for the same reason: one of these means
        the plan is not there, the other means the wallet is not."""
        service, _ = build_service(tmp_path)

        with pytest.raises(WalletNotFoundError):
            service.plans_for_wallet(uuid4())

    def test_a_wallets_plans_all_come_back(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        first = create(service, factory, wallet.wallet_id, name="rent")
        second = create(service, factory, wallet.wallet_id, name="salary")

        plans = service.plans_for_wallet(wallet.wallet_id)

        # Compared as a set, not a list. The contract says oldest first, but two
        # plans created microseconds apart have created_at values that are not
        # reliably ordered by the clock - asserting the order here would make
        # this test a coin toss. Ordering belongs to the repository's own test.
        assert {plan.plan_id for plan in plans} == {first.plan_id, second.plan_id}

    def test_a_plan_with_no_runs_has_empty_history(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)

        assert service.runs_for_plan(plan.plan_id) == []

    def test_runs_of_an_unknown_plan_is_an_error(self, tmp_path):
        service, _ = build_service(tmp_path)

        with pytest.raises(SavingsPlanNotFoundError):
            service.runs_for_plan(uuid4())


class TestSteering:
    def test_pause_then_resume_round_trip(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)

        assert service.pause_plan(plan.plan_id).status is PlanStatus.PAUSED
        assert service.resume_plan(plan.plan_id).status is PlanStatus.ACTIVE

    def test_pausing_twice_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)
        service.pause_plan(plan.plan_id)

        with pytest.raises(PlanNotActiveError):
            service.pause_plan(plan.plan_id)

    def test_resuming_an_active_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)

        with pytest.raises(PlanNotPausedError):
            service.resume_plan(plan.plan_id)

    def test_cancel_ends_the_plan(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)

        assert service.cancel_plan(plan.plan_id).status is PlanStatus.CANCELLED

    def test_cancelling_a_cancelled_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)
        service.cancel_plan(plan.plan_id)

        with pytest.raises(PlanAlreadyFinishedError):
            service.cancel_plan(plan.plan_id)

    def test_a_release_plan_cannot_be_cancelled(self, tmp_path, build_wallet):
        """The commitment device, reached through the service.

        The rejection is deliberately not translated here. The use case adds
        nothing to the rule, and inventing a friendlier message at this layer
        would put a second description of the rule in a second place.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(
            service,
            factory,
            wallet.wallet_id,
            instructions=(release("1000"),),
            ends_on=date(2027, 1, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            service.cancel_plan(plan.plan_id)

        assert service.get_plan(plan.plan_id).status is PlanStatus.ACTIVE


class TestEditing:
    def test_editing_replaces_the_lines(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id, name="salary")

        updated = service.edit_instructions(plan.plan_id, (payout("3000", label="raise"),))

        assert updated.total_to_move == Money(Decimal("3000"), NGN)
        assert service.get_plan(plan.plan_id).total_to_move == Money(Decimal("3000"), NGN)

    def test_editing_a_release_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(
            service,
            factory,
            wallet.wallet_id,
            instructions=(release("1000"),),
            ends_on=date(2027, 1, 1),
        )

        with pytest.raises(IrreversibleReleasePlanError):
            service.edit_instructions(plan.plan_id, (payout("1000"),))

    def test_a_rejected_edit_leaves_the_original_lines_in_place(
        self, tmp_path, build_wallet
    ):
        """Validate-then-assign, checked from the outside.

        The aggregate checks the candidate before it assigns, so a refused edit
        is not a half-applied one. This is the observable consequence of that
        ordering, which is why it is worth a test rather than a comment.

        The empty list is refused here by ``EmptyPlanInstructionsError`` because
        this plan names no pot. On a pot-named plan the committed-payout rule
        refuses it first - a different refusal, with its own test
        (``test_a_committed_plan_may_not_drop_its_payout``). This test is about
        the ordering of *validation against assignment*, so it takes the plain
        plan and leaves the pot rule to where it belongs.
        """
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id, source=PlanSource.AVAILABLE)

        with pytest.raises(EmptyPlanInstructionsError):
            service.edit_instructions(plan.plan_id, ())

        assert service.get_plan(plan.plan_id).total_to_move == Money(
            Decimal("2000"), NGN
        )

    def test_editing_a_cancelled_plan_is_refused(self, tmp_path, build_wallet):
        service, factory = build_service(tmp_path)
        wallet = save_wallet(factory, build_wallet())
        plan = create(service, factory, wallet.wallet_id)
        service.cancel_plan(plan.plan_id)

        with pytest.raises(PlanNotActiveError):
            service.edit_instructions(plan.plan_id, (payout("500"),))


def test_the_services_own_reads_see_committed_writes(tmp_path, build_wallet):
    """A smoke test for the transaction boundaries, stated as a claim.

    Every PlanService method opens and closes its own unit. If a write were left
    uncommitted, the very next read would not see it - which is exactly the bug
    this shape of test catches, without counting connections.
    """
    service, factory = build_service(tmp_path)
    wallet = save_wallet(factory, build_wallet())

    plan = create(service, factory, wallet.wallet_id, name="salary")

    assert [one.name for one in service.plans_for_wallet(wallet.wallet_id)] == ["salary"]
    assert service.get_plan(plan.plan_id).wallet_id == wallet.wallet_id
