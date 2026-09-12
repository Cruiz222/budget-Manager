"""The shapes that go over the wire, in both directions.

Pydantic lives here and nowhere else, which is the arrangement the plan calls
for and the reason is worth stating: the domain refuses to import this module or
anything like it. Every rule about money - what an amount may be, which currency
a plan may spend, whether a pot can be paid early - is decided by an aggregate
that has never heard of a web framework, and that is what keeps those rules true
for the CLI, the scheduler and the tests as well as for a request.

So these models are deliberately thin. They check the *shape* of a request - this
field is a string, this one is a list, these two cannot both be present - and
nothing else. Anything that could be a rule about money is left to the object
that owns the rule, which is why ``amount`` is a ``str`` here rather than a
``Decimal`` and why ``instructions`` has no minimum length.

**Money is a string, in both directions.** ``{"amount": "8000.00", "currency":
"NGN"}`` - never a JSON number. ``Money`` refuses a float at its centre because
binary floats are not exact, and a request that accepts ``0.1`` would hand the
domain a value that has already lost the argument. Reading a string with
``Decimal`` is exact, and it makes the round trip closed: every amount the API
returns can be sent straight back to it, and every amount the CLI prints can be
sent to the API.

**Enums are their ``.value``** - ``"NGN"``, ``"locked"``, ``"monthly"``,
``"months"``. Those are the same spellings the CLI accepts as arguments, so a
value read out of a response can be pasted into a command. Where a field could
be either is exactly where the two presentations would drift apart, and this is
the one place that decides.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class MoneyOut(BaseModel):
    """An amount and the currency it is in.

    Two fields rather than one, because ``Money`` is two fields and a bare
    ``"8000.00 NGN"`` string would have to be parsed for the number back out -
    which is the kind of parsing that eventually gets a currency or a decimal
    point wrong.
    """

    #: Always two decimal places, formatted exactly as ``Money.__str__`` does.
    #: Never a number: see the module docstring.
    amount: str = Field(examples=["8000.00"])
    currency: str = Field(examples=["NGN"])


class DestinationOut(BaseModel):
    kind: str = Field(examples=["bank_account"])
    identifier: str
    name: str
    details: dict[str, str] = Field(default_factory=dict)


class InstructionOut(BaseModel):
    """One line of a plan, expanded rather than referenced.

    The whole instruction travels with the plan - including its destination -
    because a plan is read by a human deciding what it will do. A client that had
    to make a second request per line to find out where the money goes would be
    making the exact request this endpoint exists to answer.
    """

    action: str = Field(examples=["payout"])
    amount: MoneyOut
    label: str
    destination: DestinationOut | None = None


class FundOut(BaseModel):
    fund_id: UUID
    name: str
    kind: str = Field(examples=["personal"])
    balance: MoneyOut
    maturity_date: date | None = None
    first_funded_at: datetime | None = None
    created_at: datetime
    sealed_at: datetime
    #: Whether this pot has no maturity date at all - ``maturity_date is None``,
    #: which is a different question from "has the date above passed". Sent
    #: alongside the date rather than instead of it because ``null`` here means
    #: *no maturity ever*, not *unknown*, and a client that read it the other way
    #: would have the pot's behaviour exactly backwards. See ``translate.fund_out``.
    is_open: bool


class WalletOut(BaseModel):
    wallet_id: UUID
    #: The owner's id. Included because this is always the caller's own wallet -
    #: the read is scoped, so there is no case in which this names a stranger.
    user_id: UUID
    currency: str = Field(examples=["NGN"])
    status: str = Field(examples=["active"])
    available_balance: MoneyOut
    locked_balance: MoneyOut
    funds: list[FundOut]


class TransactionOut(BaseModel):
    transaction_id: UUID
    type: str = Field(examples=["deposit"])
    amount: MoneyOut
    status: str = Field(examples=["pending"])
    internal_reference: str
    provider_reference: str | None = None
    narration: str | None = None
    destination: DestinationOut | None = None
    fund_id: UUID | None = None
    created_at: datetime
    completed_at: datetime | None = None
    reversed_at: datetime | None = None


class ScheduleOut(BaseModel):
    """When a plan repeats.

    Sent alongside ``next_due_at`` rather than instead of it, because the two
    answer different questions and a client needs both. ``next_due_at`` is the
    one date that matters right now; the cadence and anchor are what a client
    renders as "every month since 2 March" and what it would have to send back to
    create the same plan again - which is what makes the round trip closed.
    """

    cadence: str = Field(examples=["monthly"])
    anchor: datetime


class PlanOut(BaseModel):
    plan_id: UUID
    wallet_id: UUID
    name: str
    source: str = Field(examples=["available"])
    status: str = Field(examples=["active"])
    schedule: ScheduleOut
    instructions: list[InstructionOut]
    #: What one run costs in total, which is the number the wallet is checked
    #: against. Sent rather than left as a sum for the client to do: it is the
    #: plan's own answer and it must not be recomputed differently elsewhere.
    total_to_move: MoneyOut
    completed_runs: int
    next_due_at: datetime
    fund_id: UUID | None = None
    ends_on: date | None = None
    created_at: datetime
    #: A plan that releases locked money cannot be cancelled or edited - the
    #: promise is the product. Sent so a client can disable those controls rather
    #: than offer a button whose only outcome is a 409.
    is_irreversible: bool


class PlanRunOut(BaseModel):
    plan_id: UUID
    due_at: datetime
    status: str = Field(examples=["succeeded"])
    recorded_at: datetime
    #: Why a blocked run did not proceed. ``None`` for a succeeded run, because
    #: there was nothing to explain.
    reason: str | None = None


class UserOut(BaseModel):
    user_id: UUID
    email: str
    created_at: datetime


class SessionOut(BaseModel):
    """A login's answer: the token, when it stops working, and who it is for.

    **This is the only response in the API that carries a secret**, and it is
    sent exactly once - there is no endpoint that returns a token for a session
    that already exists, which is what keeps a stolen session table useless and
    makes "read it from the response" the sole way a client ever comes to hold
    one. The server stores a hash; only the login response has the other half.

    ``expires_at`` is a moment rather than a duration, matching ``FundOut`` and
    ``PlanOut``, which also send the absolute instant and let a client subtract.
    A client that knows when its token dies can refresh before a request fails,
    which is the difference between a hiccup and a 401 in the middle of
    something.

    ``user`` is embedded rather than left to a second call to ``/users/me``,
    because the answer to "who am I" is already in hand at the moment of login -
    it is what just got proved - and asking the client to make another
    authenticated request to learn it would be a round trip spent re-establishing
    something the login already knew.
    """

    token: str
    expires_at: datetime
    user: UserOut


class HealthOut(BaseModel):
    """Deliberately one field, and deliberately not describing the contents.

    A health check is reachable by anything that can open a socket, including in
    deployments where the rest of the API is not. It says whether the process can
    reach its database and nothing else - no counts, no versions, no
    configuration. "How many wallets does this installation hold?" is not a
    question a probe needs answered.
    """

    status: str = Field(examples=["ok"])


# --- requests ---------------------------------------------------------------


class OpenWalletIn(BaseModel):
    currency: str = Field(examples=["NGN"], description="e.g. NGN, USD, GHS")


class OpenFundIn(BaseModel):
    name: str
    kind: str = Field(
        examples=["personal"],
        description="personal (no early payout) or business (may pay a "
        "scheduled obligation before maturity)",
    )
    maturity_date: date | None = None


class ScheduleIn(BaseModel):
    """When the plan fires, as an anchor plus a cadence.

    The anchor is a *moment* and the cadence is a period, which is the same pair
    ``Schedule`` holds. Sending "every month at 12:00 starting 2 March" is the
    whole of it: the plan derives every later occurrence from that anchor rather
    than accumulating a step, which is what stops a long-lived plan drifting off
    the day its owner chose.
    """

    cadence: str = Field(examples=["monthly"])
    anchor: datetime


class DestinationIn(BaseModel):
    kind: str = Field(default="bank_account", examples=["bank_account"])
    identifier: str
    name: str
    details: dict[str, str] = Field(default_factory=dict)


class InstructionIn(BaseModel):
    action: str = Field(examples=["payout"], description="payout or release")
    amount: str = Field(
        examples=["2500.00"],
        description="A string, read in the wallet's own currency - the same way "
        "the CLI reads a bare amount, and for the same reason: the currency is a "
        "property of the wallet, not of the number typed.",
    )
    label: str
    destination: DestinationIn | None = None


class DurationIn(BaseModel):
    """A term, as the user said it: "12 months" rather than "2027-03-02".

    The plan stores the *date* and not the term, because a date is the thing that
    stays true. This object exists only long enough to be resolved against the
    plan's anchor by ``Duration.end_from``, which is the domain's own conversion
    and not a rule either presentation is allowed to reimplement.
    """

    amount: int = Field(gt=0, examples=[12])
    unit: str = Field(examples=["months"], description="days, weeks, months or years")


class CreatePlanIn(BaseModel):
    wallet_id: UUID
    name: str
    source: str = Field(examples=["available"], description="available or locked")
    schedule: ScheduleIn
    #: No minimum length, deliberately. An empty list is refused by
    #: ``SavingsPlan`` - a plan with no lines is a plan that does nothing - and
    #: that refusal is the domain's, reported as a 400 with the domain's own
    #: words. A ``min_length`` here would answer the same request with a 422 in
    #: pydantic's vocabulary, which is a second answer to a question that already
    #: had one.
    instructions: list[InstructionIn]
    #: A day, not a moment: "until 2 July" means through 2 July.
    ends_on: date | None = None
    #: The alternative to ``ends_on``, resolved against ``schedule.anchor``.
    term: DurationIn | None = None
    #: The pot a locked-source plan draws on. Refused for an available-source
    #: plan and required for a locked one, by ``PlanService.create_plan``.
    fund_name: str | None = None

    @model_validator(mode="after")
    def _one_way_to_end(self):
        """``ends_on`` and ``term`` are mutually exclusive.

        The CLI makes ``--until`` and ``--for`` mutually exclusive at parse time,
        and this is the same refusal at the same kind of moment: the request is
        incoherent before any wallet is loaded, so it comes back as a 422 from the
        schema rather than as a 400 from the domain. Accepting both would leave
        the question "which one wins?", and whichever answer this code picked
        would be a rule the caller could not see from the request they sent.
        """
        if self.ends_on is not None and self.term is not None:
            raise ValueError("give ends_on or term, not both")
        return self


class InstructionsIn(BaseModel):
    """The body of a plan edit: a whole new set of lines, not a patch.

    Replacing rather than merging, because that is what ``edit_instructions``
    does and it is the safer of the two. A merge would need a way to name the
    line being changed, and every line is anonymous - so a patch would have to
    address them by position, and a client holding a stale list would silently
    rewrite the wrong one.
    """

    instructions: list[InstructionIn]


class SignUpIn(BaseModel):
    """A registration: an address, and the secret that will prove it.

    **``password`` carries no length rule here, and that is the same decision as
    ``instructions`` having no ``min_length``.** The policy - at least eight
    characters, at most 1024 - lives in ``PlainPassword``, and a second copy in
    this model would answer a bad password with a 422 in pydantic's vocabulary
    instead of a 400 in the domain's. The two would also be free to disagree, and
    the disagreement would present as a password this layer accepted and the
    aggregate refused.

    **``repr=False`` keeps the password out of a traceback.** A pydantic model
    reprs its fields exactly as a dataclass does, so without this a ``SignUpIn``
    caught in an unexpected error writes the secret into whatever logs the
    exception - which is the same hazard ``PlainPassword.__repr__`` exists to
    close, one layer up and one step earlier. It does not close every door: a 422
    echoes the offending input back in its own error body, so a request that sent
    a password as, say, a number would see it quoted in the response. That is
    FastAPI's error format rather than a choice made here, and it goes only to
    the caller who sent it.

    **Pydantic warns about this attribute, and the warning is expected.** It reports
    that ``repr`` has no effect in the context it was used - which is the standalone
    ``TypeAdapter(Annotated[SignUpIn, field_info])`` FastAPI builds at
    ``fastapi/_compat.py:111``, a validator that never produces a model repr. The
    model's own ``__repr__`` does honour it. Nothing is silenced: the warning is
    pydantic correctly saying one particular use is meaningless, and a filter would
    hide it on the day it meant something. The claim is pinned by
    ``TestThePasswordIsNotInTheRepr``.

    A separate class from ``LogInIn`` below despite being identical today, because
    they are different requests that happen to agree for now. Sign-up will grow
    fields - a display name, a terms flag, an invite code - and the moment it
    does, a shared ``CredentialsIn`` would have the login endpoint documenting
    parameters it ignores.
    """

    email: str = Field(examples=["ada@example.com"])
    password: str = Field(
        repr=False,
        examples=["a long phrase you will remember"],
        description="At least 8 characters. Never logged, never stored in the "
        "clear - the server keeps an argon2id hash and nothing else.",
    )


class LogInIn(BaseModel):
    """Credentials presented to become somebody, rather than to create them."""

    email: str = Field(examples=["ada@example.com"])
    password: str = Field(repr=False, examples=["a long phrase you will remember"])
