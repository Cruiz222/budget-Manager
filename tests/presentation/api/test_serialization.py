"""What the wire format is, and why it is worth pinning.

Two rules live in ``app/presentation/api/translate.py`` and neither of them is
enforced by a type:

* **Money is a string.** ``{"amount": "2500.00", "currency": "NGN"}``, never a
  JSON number, in both directions.
* **Enums are their ``.value``.** ``"NGN"``, ``"monthly"``, ``"available"`` -
  which are the same spellings the CLI accepts as arguments.

Both are conventions a well-meaning edit could break without breaking anything
visible: a ``float`` here would round-trip through the tests that assert on small
whole amounts, and an enum serialised as ``"PlanSource.AVAILABLE"`` would still
parse as a string. So they are asserted as rules rather than sampled as values -
the first test walks a whole response looking for *any* amount, and the enum test
constructs the real domain enums from the strings rather than comparing them to
other strings.

The last class is the one that matters most. A format is only a contract if both
ends of it agree, and this API's other end is the command line.
"""

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.fundKind import FundKind
from app.domain.money.walletStatus import WalletStatus
from app.domain.planning.cadence import Cadence
from app.domain.planning.planSource import PlanSource
from app.domain.planning.planStatus import PlanStatus
from app.domain.planning.plannedAction import PlannedAction
from app.presentation.api.schemas import LogInIn, SignUpIn
from app.presentation.cli import main
from tests.conftest import log_in_as


def money_amounts_in(body):
    """Every ``MoneyOut.amount`` anywhere in a response body, however nested.

    A walk rather than a list of paths to check, because the failure this guards
    against is a *new* field somebody adds later. A test that knew where the
    amounts were would have to be updated by the same person who forgot the rule,
    and would therefore not be updated.

    The pair of keys is the test for "this dict *is* money", rather than the key
    ``amount`` alone. That distinction is not pedantry: ``InstructionOut`` has a
    field *named* ``amount`` whose value is a nested ``MoneyOut``, so looking for
    the key alone yields whole money objects as if they were amounts - which is
    how the first version of this walker failed.
    """
    if isinstance(body, dict):
        if "amount" in body and "currency" in body:
            yield body["amount"]
        else:
            for value in body.values():
                yield from money_amounts_in(value)
    elif isinstance(body, list):
        for item in body:
            yield from money_amounts_in(item)


@pytest.fixture
def everything(client, as_user, open_wallet, open_pot, create_plan):
    """One response of every shape this API can produce.

    Read together so the rule above is checked against the whole vocabulary
    rather than against whichever single response a test happened to pick.
    """
    headers = as_user()
    wallet_id = open_wallet(headers)
    open_pot(wallet_id, headers, name="Vacation", maturity_date="2027-01-01")
    plan = create_plan(wallet_id, headers).json()

    return {
        "wallet": client.get(f"/wallets/{wallet_id}", headers=headers).json(),
        "transactions": client.get(
            f"/wallets/{wallet_id}/transactions", headers=headers
        ).json(),
        "funds": client.get(f"/wallets/{wallet_id}/funds", headers=headers).json(),
        "plans": client.get(f"/wallets/{wallet_id}/plans", headers=headers).json(),
        "plan": client.get(f"/plans/{plan['plan_id']}", headers=headers).json(),
        "runs": client.get(f"/plans/{plan['plan_id']}/runs", headers=headers).json(),
    }


class TestMoneyIsAString:
    def test_no_amount_anywhere_in_a_response_is_a_json_number(self, everything):
        """The rule, checked against every shape at once.

        The second assertion is not decoration. ``all()`` over an empty sequence
        is ``True``, so a walk that found nothing would pass this test - and a walk
        that found nothing is exactly what a rename of the ``amount`` field would
        produce, on the day somebody most needs this test to speak up.
        """
        found = [
            amount
            for body in everything.values()
            for amount in money_amounts_in(body)
        ]

        assert found, "no amounts were found - has the field been renamed?"
        assert all(isinstance(one, str) for one in found), found

    def test_an_amount_is_always_written_to_two_places(self, client, as_user, open_wallet):
        """``"0.00"`` and not ``"0"``, ``"0.0"`` or ``0``.

        ``Decimal`` keeps the exponent it was built with, so this is a real
        hazard rather than a hypothetical one: ``Money(Decimal("0"))`` and
        ``Money(Decimal("0.00"))`` are the same money and format differently. Two
        clients comparing the same balance as strings would disagree, and neither
        would be wrong about the arithmetic.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)

        wallet = client.get(f"/wallets/{wallet_id}", headers=headers).json()

        assert wallet["available_balance"]["amount"] == "0.00"

    def test_an_amount_the_api_returns_can_be_sent_straight_back(
        self, client, as_user, open_wallet, create_plan
    ):
        """The round trip is closed in both directions.

        A client that reads a total and uses it as the amount of the next plan
        must not have to reformat it - and if it had to, it would be reimplementing
        ``translate.money_out``'s format spec, which is the thing having one
        function for it exists to prevent.
        """
        headers = as_user()
        first = create_plan(open_wallet(headers), headers).json()
        quoted = first["total_to_move"]["amount"]
        assert quoted == "2500.00"

        again = create_plan(
            open_wallet(headers),
            headers,
            instructions=[
                {"action": "payout", "amount": quoted, "label": "Same as before",
                 "destination": first["instructions"][0]["destination"]}
            ],
        )

        assert again.status_code == 201
        assert again.json()["total_to_move"]["amount"] == quoted

    def test_an_amount_is_read_as_a_string_and_not_as_a_float(
        self, client, as_user, open_wallet, create_plan, payout_line
    ):
        """A third decimal place is refused rather than rounded away.

        This is the same refusal ``Money`` makes anywhere else in the codebase,
        reaching a client for the first time. What it demonstrates is that the
        value on the wire went through ``Decimal`` and not through binary floating
        point - a float would have silently accepted ``"25.001"`` and stored
        something close to it, and no later check could tell that it had.
        """
        headers = as_user()
        response = create_plan(
            open_wallet(headers),
            headers,
            instructions=[payout_line("25.001", "Three places")],
        )

        assert response.status_code == 400
        assert response.json()["error"] == "UnsupportedDecimalPlaceError"


class TestTheSameWordsForTheSameThings:
    def test_every_enum_a_response_carries_is_a_member_of_its_enum(self, everything):
        """Constructed, not compared - so the spellings cannot drift.

        ``Currency(wallet["currency"])`` is the assertion: it raises if the string
        is not a member, which is exactly the property that makes the value usable
        as a CLI argument. Comparing against a list of expected strings would pass
        on the day somebody renamed a member and updated the list.
        """
        wallet = everything["wallet"]
        pot = everything["funds"][0]
        plan = everything["plan"]

        assert Currency(wallet["currency"]) is Currency.NGN
        assert WalletStatus(wallet["status"]) is WalletStatus.ACTIVE
        assert FundKind(pot["kind"]) is FundKind.PERSONAL
        assert PlanSource(plan["source"]) is PlanSource.AVAILABLE
        assert PlanStatus(plan["status"]) is PlanStatus.ACTIVE
        assert Cadence(plan["schedule"]["cadence"]) is Cadence.MONTHLY
        assert PlannedAction(plan["instructions"][0]["action"]) is PlannedAction.PAYOUT
        assert (
            DestinationKind(plan["instructions"][0]["destination"]["kind"])
            is DestinationKind.BANK_ACCOUNT
        )


class TestTheTwoPresentationsAgree:
    """The API's wire format, checked against the thing that has to read it.

    Every test above is the API agreeing with a rule this suite wrote down. These
    are the API agreeing with the *CLI* - a different presentation, a different
    library, and the same database file. A format that only its own tests
    understand is not a format.
    """

    def test_money_deposited_at_a_terminal_is_visible_over_http(
        self, client, as_user, db_path, capsys, open_wallet
    ):
        """The one direction that could not be checked inside the API alone.

        Nothing in this phase may put money into a wallet - that is the boundary -
        so if the API were the only door in, every balance in every response would
        be ``"0.00"`` and every assertion about the money format would be an
        assertion about zero. The CLI is not bound by that boundary, so this is how
        a *non-zero* amount gets into a response at all.

        It is also the test that says the two presentations resolved the same
        account and the same wallet: the account was registered by ``as_user()``
        over HTTP, the CLI then signed into it, and the API read the deposit back
        as the same actor.

        ``log_in_as`` rather than the CLI's own ``login`` command, because the
        account was registered with the suite's fake hasher - see ``signed_in`` for
        why that substitution is made and what it costs. What is being tested here
        is the wire format, not authentication; ``test_actor.py`` is where the two
        presentations prove they agree about a *password*.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        session = log_in_as(db_path)

        assert main(
            ["--db", db_path, "--session", session, "deposit", wallet_id, "2500.50"]
        ) == 0
        capsys.readouterr()

        wallet = client.get(f"/wallets/{wallet_id}", headers=headers).json()

        assert wallet["available_balance"] == {"amount": "2500.50", "currency": "NGN"}
        assert wallet["locked_balance"] == {"amount": "0.00", "currency": "NGN"}

    def test_a_plan_created_at_a_terminal_reads_back_over_http(
        self, client, as_user, db_path, capsys, open_wallet
    ):
        """The other door, and the store is what makes them one system.

        Every value below was written by argparse and ``_lines`` and is being read
        by pydantic and ``translate`` - so the assertions are really about the
        *stored* shape being presentation-agnostic, which is the property Phase 1b
        was supposed to expose rather than create.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        session = log_in_as(db_path)

        assert main(
            [
                "--db", db_path, "--session", session,
                "plan", "create",
                "--wallet", wallet_id,
                "--name", "Rent",
                "--source", "available",
                "--every", "monthly",
                "--from", "2026-03-02T12:00",
                "--pay", "2500.00", "0123456789", "058", "Chinedu Okafor", "March rent",
            ]
        ) == 0
        capsys.readouterr()

        plans = client.get(f"/wallets/{wallet_id}/plans", headers=headers).json()

        assert len(plans) == 1
        plan = plans[0]
        assert plan["name"] == "Rent"
        assert plan["source"] == "available"
        assert plan["schedule"] == {"cadence": "monthly", "anchor": "2026-03-02T12:00:00"}
        assert plan["total_to_move"] == {"amount": "2500.00", "currency": "NGN"}
        assert plan["instructions"][0]["destination"] == {
            "kind": "bank_account",
            "identifier": "0123456789",
            "name": "Chinedu Okafor",
            "details": {"bank_code": "058"},
        }

    def test_a_plan_steered_over_http_is_steered_at_a_terminal_too(
        self, client, as_user, db_path, capsys, open_wallet, create_plan
    ):
        """A write through one door, read through the other.

        The reads above prove the two presentations share a store; this proves they
        share its *rules*. The pause went through the API's ``PlanService`` and the
        CLI is now reading a plan whose status was changed by a request it never
        saw - which is what "one domain, two presentations" means in practice.
        """
        headers = as_user()
        wallet_id = open_wallet(headers)
        plan_id = create_plan(wallet_id, headers).json()["plan_id"]
        session = log_in_as(db_path)

        assert client.post(f"/plans/{plan_id}/pause", headers=headers).status_code == 200

        assert main(
            ["--db", db_path, "--session", session, "plan", "show", plan_id]
        ) == 0
        out = capsys.readouterr().out

        assert "status: paused" in out


class TestThePasswordIsNotInTheRepr:
    """A secret in a repr is a secret in a log, and ``schemas.py`` claims otherwise.

    ``SignUpIn`` and ``LogInIn`` set ``Field(repr=False)`` on ``password``, and their
    docstring says what it buys: without it, a model caught in an unexpected error
    writes the secret into whatever records the exception - the same hazard
    ``PlainPassword.__repr__`` closes one layer down. That is a claim about a leak,
    so it gets a test rather than a comment.

    It lives in this file because this is where the request and response *shapes* are
    pinned, and because it is otherwise easy to read as covered: the API's own tests
    all assert over the wire, where the password never appears anyway. The exposure
    is in-process - a traceback, a debugger, a ``repr`` in a log line - and nothing
    that goes through ``client`` can see it.

    ``pydantic`` emits an ``UnsupportedFieldAttributeWarning`` for these two fields,
    and it is **expected rather than a symptom**. The warning is about the attribute
    being meaningless in the *standalone* context FastAPI builds -
    ``TypeAdapter(Annotated[SignUpIn, field_info])``, at ``fastapi/_compat.py:111`` -
    which is a validator and never produces a model repr. ``BaseModel.__repr_args__``
    reads ``field.repr`` directly and does honour it, which is what these tests
    assert. Silencing the warning would be the wrong fix: it is pydantic correctly
    reporting that one particular use of the attribute does nothing, and a filter
    would hide the same warning the day it meant something.
    """

    def test_the_signup_model_hides_the_password(self):
        assert "THE-SECRET" not in repr(SignUpIn(email="ada@example.com", password="THE-SECRET"))

    def test_the_login_model_hides_the_password(self):
        assert "THE-SECRET" not in repr(LogInIn(email="ada@example.com", password="THE-SECRET"))

    def test_and_the_address_is_still_there(self):
        """The control. Without it both tests above pass for a repr that says nothing.

        The reason the password is hidden is that the *other* fields are not - a
        traceback is supposed to say which request went wrong, and a model that hid
        everything would be as useless as one that hid nothing.
        """
        assert "ada@example.com" in repr(SignUpIn(email="ada@example.com", password="THE-SECRET"))
        assert "ada@example.com" in repr(LogInIn(email="ada@example.com", password="THE-SECRET"))

    def test_the_password_is_in_the_model_all_the_same(self):
        """Hidden from the repr, not from the code that has to read it.

        ``LogIn`` verifies against ``body.password``, so the field is present and
        reachable - this is a repr rule and not an access rule, and asserting the
        difference is what keeps somebody from "fixing" the leak by making the field
        private, which would break the endpoint while keeping these tests green.
        """
        assert SignUpIn(email="ada@example.com", password="THE-SECRET").password == "THE-SECRET"
        assert LogInIn(email="ada@example.com", password="THE-SECRET").password == "THE-SECRET"
