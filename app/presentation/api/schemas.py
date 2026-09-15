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


class ConfirmationOut(BaseModel):
    """A recorded request to move money out. **Nothing has moved.**

    This is what the three money routes answer with now, and the change of shape
    is the feature: a withdrawal request no longer returns a transaction, because
    no transaction exists yet. A client holding one of these holds a question, and
    the answer is a separate call.

    **``status`` is not simply the stored column.** A request past its window
    still *stores* ``awaiting`` - nothing sweeps, and expiry is derived - so the
    server reports ``expired`` for it through ``Confirmation.status_as_of``. A
    client must not reconstruct this from ``expires_at`` against its own clock:
    the window is judged by the server's, and a response that let the two
    disagree would tell a client its request was live when the next call refuses
    it. See ``ConfirmationStatus.EXPIRED``.

    ``amount`` and ``destination`` are absent for a ``close``, which moves no
    money and names no account - the same asymmetry ``ConfirmedOperationOut``
    carries one level up, and the same one ``FundOut.maturity_date`` handles by
    sending the *reason* for the null alongside it. Here the reason is ``kind``:
    a client that sees ``close`` should expect both to be absent.
    """

    confirmation_id: UUID
    kind: str = Field(examples=["payout_from_locked"])
    status: str = Field(examples=["awaiting"])
    wallet_id: UUID
    amount: MoneyOut | None = None
    destination: DestinationOut | None = None
    fund_name: str | None = None
    internal_reference: str
    created_at: datetime
    #: When the window closes. Sent as a moment rather than a duration, like
    #: ``SessionOut.expires_at``: a client that knows when its request dies can
    #: answer it before it does, which is the difference between a prompt and an
    #: error.
    expires_at: datetime


class ConfirmedOperationOut(BaseModel):
    """What answering a confirmation produced: the request, the row, the wallet.

    Three things, and the third is sometimes absent on purpose - ``transaction``
    is ``None`` for a ``close``, which moves no money and so writes no ledger row.
    ``None`` here means *this operation writes no row*, not *the row is unknown*
    and not *the row is still being made*: a payout's row is PENDING, which is a
    different thing entirely and is why the status travels inside it.

    ``confirmation`` is the request **as it was persisted**, re-read after the
    movement committed rather than assembled from the request that was sent. So
    its ``status`` is ``confirmed``, and its window has stopped mattering.

    ``wallet`` comes back because it is what a person actually asks next, and
    this is the only place it can come from: the balance after the movement
    exists only inside the unit that made it. A client that had the transaction
    and not the wallet would have to make a second read to learn what the
    operation did to the balance it just spent.
    """

    confirmation: ConfirmationOut
    transaction: TransactionOut | None = None
    wallet: WalletOut


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


class EmailChangeOut(BaseModel):
    """What asking to move an address produced: a request, or a finished change.

    **``expires_at`` is present exactly when ``status`` is ``pending``**, and the
    coupling is not enforced here - it cannot be, since a pydantic model has no
    opinion about which of its fields belong together. It is enforced one layer
    down, and structurally rather than by a check: ``EmailChangeOutcome`` holds the
    pending ``EmailChange`` or nothing, and reads both ``applied`` and this from
    that single field, so "applied, and here is your deadline" is not a value
    anything can construct. By the time a response is built there is nothing left
    to get wrong.

    ``email`` is the address the *request is about* either way: the one the account
    now holds when ``applied``, and the one it will hold once confirmed when
    ``pending``. A client showing "we have sent a code to ..." and a client showing
    "your address is now ..." are reading the same field, which is why it is not
    two fields with two names.

    ``status`` is two words rather than three, and the third thing a client might
    expect - *expired* - is deliberately not among them. A request that has lapsed
    is not reported by the endpoint that made it; it is reported by the endpoint
    that refuses it, which is where a client can act on it.
    """

    status: Literal["pending", "applied"]
    email: str
    expires_at: datetime | None = None


class EmailChangeConfirmedOut(BaseModel):
    """A finished change, and how telling the address it left behind went.

    ``user`` is the account as it now stands, embedded rather than left to a second
    call to ``/users/me`` - the same argument ``SessionOut`` makes, and it holds
    here for the same reason: the confirm is authorised by a token and not by a
    session, so a client that wanted to learn the resulting address would otherwise
    have to log in again to ask.

    ``previous_email`` travels because nothing else can report it. The row
    deliberately holds no record of the address that was left - see
    ``ConfirmEmailChange`` - so this is the one moment the fact exists anywhere
    outside the notice that was mailed.

    **``notice_sent`` and ``notice_error`` are two fields for three states**, and
    the third is the one worth writing down because it reads like a bug:
    ``False`` with ``None`` means *this installation has no mail account*, so no
    notice was attempted. ``False`` with a reason means one was attempted and
    failed. The address moved either way - that is the whole of the best-effort
    decision - and a client that showed "your old address was not told" as an
    error would be reporting a courtesy as a failure.
    """

    user: UserOut
    previous_email: str
    notice_sent: bool
    notice_error: str | None = None


class HealthOut(BaseModel):
    """Deliberately one field, and deliberately not describing the contents.

    A health check is reachable by anything that can open a socket, including in
    deployments where the rest of the API is not. It says whether the process can
    reach its database and nothing else - no counts, no versions, no
    configuration. "How many wallets does this installation hold?" is not a
    question a probe needs answered.
    """

    status: str = Field(examples=["ok"])


class DepositIntentOut(BaseModel):
    """A deposit that has been asked for. **The wallet has not been credited.**

    The inbound mirror of ``ConfirmationOut``, and the resemblance is not
    accidental: both describe a movement that has not happened, and both exist so
    that a client cannot mistake a record of intent for a change in balance. What
    differs is who has to act - a withdrawal request waits for its owner to
    answer a prompt, and this waits for a *stranger with a card* to pay a page
    that was opened for them.

    ``authorization_url`` is the provider's page and is the only field this API
    forwards from a third party without understanding it. Nothing here parses it,
    stores it or reconstructs it, which is why it travels as an opaque string:
    see ``InitiatedDeposit``.

    ``status`` is ``pending`` and is sent rather than left implied. A client that
    read a 201 as "the money is in" would show a balance the wallet does not
    have, and this is the cheapest possible way to prevent that - the same
    argument ``TransactionOut`` makes for carrying the status of a payout.

    There is no ``expires_at``, and its absence is honest rather than an
    omission: nothing in this system knows when the provider's checkout lapses.
    The URL is single-use and the provider owns its lifetime; inventing a window
    here would be this API asserting something it cannot see.
    """

    authorization_url: str
    provider_reference: str
    amount: MoneyOut
    status: str = Field(examples=["pending"])


class WebhookAck(BaseModel):
    """What this API did about a webhook, which a provider only ever reads as a code.

    **The status code is the entire contract and this body is for humans**, which
    is worth stating so nobody later mistakes it for something Paystack consumes.
    A provider reads 200 and stops retrying; everything below that is 4xx for
    "never send this again" and 5xx for "send it again". This object is what
    appears in a log when somebody asks why a deposit did not credit, so it
    carries the two facts that answer that question - what this system concluded,
    and about which reference - and no more.

    ``outcome`` is the ``SettlementOutcome`` member's value, and it is
    deliberately the *same* vocabulary the use case uses rather than a
    webhook-specific one. A second set of names for the same facts would be a
    second thing to keep in step, and the one place they disagreed would be the
    place somebody was reading to find out what happened to their money.

    ``reference`` is ``None`` for the one outcome that has no reference to name -
    an event this code does not recognise, which is acknowledged and dropped.
    """

    outcome: str = Field(examples=["deposit_credited"])
    reference: str | None = None
    detail: str = ""


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


class EmailChangeIn(BaseModel):
    """The address to move to, and the proof that this is the account's owner asking.

    **Two credentials rather than one, and the pair is the design.** The request
    arrives carrying a session - every field of it being about the *caller* - and
    this body, which carries the one thing the session cannot say: that the person
    holding it still knows the password. The confirmation that follows is
    authorised by its mailed token alone, so without this second proof a stolen
    session would be enough to walk an account off to somebody else's mailbox.

    ``password`` is ``repr=False`` for ``SignUpIn``'s reason, unchanged: a pydantic
    model reprs its fields exactly as a dataclass does, and a request caught in an
    unexpected error must not write the secret into whatever logs the exception.

    ``email`` carries no validation here either, and it never has - the shape rule
    and the entry rule are both the domain's, and applying either one in this model
    would answer a bad address with a 422 in pydantic's vocabulary instead of a 400
    in the domain's.
    """

    email: str = Field(examples=["ada@example.com"])
    password: str = Field(
        repr=False, examples=["the password this account already has"]
    )


class ConfirmEmailChangeIn(BaseModel):
    """The code that was mailed to the new address, and nothing else.

    **It is in the body rather than in the path, and that is a decision about
    logs.** A token in a URL is a token in every access log, every proxy log and
    every ``Referer`` header that URL is ever pasted into, and this one moves an
    account. In a body it is a field of a request that is not otherwise logged at
    all.

    No ``email``, no session, no account name: the code *is* the whole of the
    authorisation, so a second field would be a second thing to check and a second
    thing to get wrong. There is no endpoint anywhere in this API that reports what
    a code is for - a client that has lost one asks for another.
    """

    token: str = Field(repr=False, examples=["the code from the confirmation email"])


class PasswordResetRequestIn(BaseModel):
    """The address a reset is being asked for, and nothing else.

    **One field, and the absence of a second is the whole design.** An address
    change asks for a session and a password because the caller has both; a reset
    exists because the caller has neither. So this is the only request body in the
    API that carries no credential at all, and the field it does carry is not
    secret - it is an address, which a stranger may already know.

    ``email`` carries no validation here, and it is worth saying so because this is
    the one place where that has a *different* consequence from everywhere else.
    ``RequestEmailChange`` refuses a malformed address with a 400; this flow
    deliberately does not, and treats ``not-an-address`` as an address that names
    no account. That is not this model's decision to make or to undo - see
    ``RequestPasswordReset``, which argues why inventing a shape rule here would be
    a second copy of a rule only ``User`` is entitled to apply.

    There is no ``repr=False`` here because there is nothing to redact. An address
    appears in every other request body this API accepts and is not a secret; the
    field that would need it does not exist yet, and does not exist at this step
    even conceptually - the new password is chosen later, by whoever reads the mail.
    """

    email: str = Field(examples=["ada@example.com"])


class PasswordResetAcceptedOut(BaseModel):
    """That the request was received, and **nothing about whether an account exists**.

    **This is the only response in the API that is deliberately the same for a hit
    and a miss**, and the reason is that the alternative is an enumeration oracle.
    An address that names an account and one that does not produce byte-identical
    bodies here, because "is ada@example.com registered?" is a question a stranger
    has no standing to ask and can otherwise ask as fast as they can type.

    The consequence for a client is that this body says nothing it can act on, and
    that is honest rather than lazy: the server genuinely has nothing to report. The
    instruction a person needs - *if that address names an account, a code has been
    sent* - is in the OpenAPI description and in the human-facing prose, not in a
    response the server would have to vary to be useful.

    ``status`` is a field rather than a bare 202, for the reason ``DepositIntentOut``
    carries one: a client that reads a status code alone will eventually show the
    wrong thing, and the cost of the extra field is one word.
    """

    status: str = Field(default="accepted", examples=["accepted"])


class ConfirmPasswordResetIn(BaseModel):
    """The mailed code and the password to replace, in one body.

    **Two fields, and the pairing is what makes this different from the change
    flow's confirm.** That one carries only a token because the thing being applied
    was decided at request time and written to the row; here the row deliberately
    holds no payload - a password is written down exactly once in this system, as an
    argon2 hash - so the value being authorised has to arrive with the answer. The
    permission is stored; the secret never is.

    ``token`` is ``repr=False`` for ``ConfirmEmailChangeIn``'s reason and ``password``
    for ``SignUpIn``'s, unchanged: both are credentials, and a request caught in an
    unexpected error must not write either into whatever logs the exception.

    **``password`` carries no length rule here**, which is exactly the decision
    ``SignUpIn`` documents and it matters more in this flow rather than less. The
    policy lives in ``PlainPassword``, and a second copy in this model would answer a
    password that is too short with a 422 in pydantic's vocabulary instead of a 400
    in the domain's. Beyond the vocabulary, it would change a behaviour that is
    argued for: a refusal from the aggregate happens *before* the claim, so the code
    is not spent and can be presented again with a longer password. A refusal here,
    in this layer, would happen before the endpoint body ever ran - which also leaves
    the code unspent, and that is the only reason this duplication is a nuisance
    rather than a bug. It is still a nuisance worth refusing: two copies of the
    policy can disagree, and the disagreement would be a password this layer accepted
    and the aggregate refused.

    No session and no address accompany these two, for ``ConfirmEmailChangeIn``'s
    reason: the code is the whole of the authorisation and names the account itself,
    so a third field would be a second thing to check and a second thing to get
    wrong.
    """

    token: str = Field(repr=False, examples=["the code from the reset email"])
    password: str = Field(
        repr=False,
        examples=["a long phrase you will remember"],
        description="At least 8 characters. Never logged, never stored in the "
        "clear - the server keeps an argon2id hash and nothing else.",
    )


class PasswordResetConfirmedOut(BaseModel):
    """A replaced password, how many devices were signed out, and how warning went.

    ``user`` is embedded rather than left to a second call, matching
    ``EmailChangeConfirmedOut`` and for a stronger version of its reason: this
    request was authorised by a code rather than a session, so the client may well
    hold no usable token at all - every one of them has just been deleted. Without
    this field the only way to learn who was affected would be to log in with the
    new password, which the person may not have done yet.

    ``sessions_revoked`` is reported rather than kept internal, and it is the one
    field here a client should show. Every device that was signed in has been signed
    out - including the one that asked, if there was one - and a person who does not
    expect to be logged out everywhere will read a closed tab as a bug. ``0`` is a
    real answer, not a failure: an account with no live sessions had none to end.

    **``notice_sent`` and ``notice_error`` are two fields for three states**, cloned
    from ``EmailChangeConfirmedOut`` and true for the same reasons: ``False`` with
    ``None`` means no mail account reached this call, so no notice was attempted;
    ``False`` with a reason means one was attempted and failed. The password changed
    either way - refusing to apply a reset because the warning about it bounced would
    leave the person locked out with the code spent - and a client that rendered a
    bounced notice as an error would be reporting a courtesy as a failure. The third
    state is unreachable for a successful confirm today, because a reset cannot be
    requested on an install with no mail; it is kept because the value it comes from
    can express it, and narrowing this model would mean the response silently
    dropping a state rather than never having it.
    """

    user: UserOut
    sessions_revoked: int
    notice_sent: bool
    notice_error: str | None = None


# --- moving money -----------------------------------------------------------


class MovementIn(BaseModel):
    """A bare amount against a wallet, and the key that makes a retry safe.

    ``amount`` is a string read in the wallet's own currency, exactly as the CLI
    reads a bare ``5000`` and for the reason ``InstructionIn.amount`` gives: the
    currency is a property of the wallet, not of the number typed.

    **``ref`` is an idempotency key, and sending one twice is safe by design.**
    Two requests carrying the same key do not move money twice: the second finds
    the first's request and returns it unchanged.

    **The key now names the request rather than the ledger row**, and that is a
    promotion rather than a change of meaning. It used to key the transaction
    directly; it now keys the record that will produce one, and the key travels
    down to that row still namespaced to the wallet, exactly as before. What it
    buys is a chain: one reference -> at most one request -> at most one
    execution attempt. A second request under a spent key is impossible, which is
    what keeps a retry from ever being handed back an old FAILED row.

    **The answer echoes the key you sent, not the one the ledger will hold.** The
    transaction underneath still stores ``"<wallet uuid>.<your key>"`` - see
    ``app.domain.money.reference`` - so a client that sent ``"abc"`` sees
    ``"abc"`` back here and would see ``"<wallet uuid>.abc"`` on the transaction
    it becomes. Sending the *transaction's* form back as a new ``ref`` would be a
    new key and, for a withdrawal, a second one. Generate the key once, per
    operation, and keep it for the retry.

    Namespacing is why a key is not a way into anybody else's ledger. The server
    prefixes what you send with a wallet id it has already resolved for you, so
    two callers choosing the same word do not collide - each gets their own row.
    """

    amount: str = Field(examples=["5000.00"])
    ref: str | None = Field(
        default=None,
        examples=["withdrawal-2026-09-12"],
        description=(
            "Idempotency key. Auto-generated when omitted. Only letters, "
            "digits and - . , = are accepted."
        ),
    )


class PayoutIn(MovementIn):
    """Send money out to an external account, from either balance.

    ``source`` is what decides which balance funds it, and it is a closed
    vocabulary in the *request shape* rather than a value handed to an aggregate
    that could refuse it - which is why this is a ``Literal`` where
    ``InstructionIn.action`` is a plain ``str``. An action travels to the domain,
    which has its own answer for a word it does not know; a source is what this
    route branches on to pick a method, so an unknown one has no owner left to
    refuse it and would otherwise fall through to nothing.

    ``fund_name`` names the pot a locked payout draws on. Leaving it out is not
    the same command with a default - it is a different act, the pooled draw that
    spends matured pots oldest first, and it exists for a plan saved before pots
    could be named. It is refused for an available-source payout, because there
    is no pot involved in spending the available balance.
    """

    destination: DestinationIn
    source: Literal["available", "locked"] = Field(
        default="available",
        description="available spends the unlocked balance; locked spends a pot.",
    )
    fund_name: str | None = Field(
        default=None,
        description="The pot a locked payout draws on. Not allowed for available.",
    )

    @model_validator(mode="after")
    def _a_pot_needs_a_locked_source(self):
        """``fund_name`` only means something with ``source="locked"``.

        The same refusal at the same kind of moment as
        ``CreatePlanIn._one_way_to_end``: the request contradicts itself before
        any wallet is loaded, so it is a 422 from the schema rather than a 400
        from the domain. Leaving it to the domain would be worse than late - the
        available-source payout has no use for a pot name at all, so nothing
        would refuse it, and the field would be silently ignored. A caller who
        named a pot and was charged from the available balance instead would have
        been told nothing.
        """
        if self.fund_name is not None and self.source != "locked":
            raise ValueError("fund_name requires source='locked'")
        return self


class ExtendFundIn(BaseModel):
    """Push a pot's maturity date later. There is no way to pull it earlier.

    No ``ref``: extending moves no money, so there is no ledger row to
    deduplicate and nothing a retry could double.

    A whole date rather than a term, because a maturity date *is* the term - it
    is what the pot stores, and it is what "has this come due" is asked against.
    Sending "three more months" would mean this layer resolving a duration into a
    date, which is the same conversion ``CreatePlanIn.term`` deliberately leaves
    to the domain.
    """

    new_date: date = Field(examples=["2027-03-02"])


# --- money arriving ---------------------------------------------------------
#
# One request, and it is the only place in this API where a client asks for money
# to be *added* rather than spent. It gets its own heading because it is the only
# request here whose outcome is not decided by this system: everything above ends
# when the domain agrees to it, and this one ends when a stranger pays a page.


class DepositIn(BaseModel):
    """An amount to collect, and the key that makes asking twice safe.

    ``amount`` is a string read in the wallet's own currency, exactly as
    ``MovementIn`` reads one and for the reason ``InstructionIn.amount`` gives:
    the currency is a property of the wallet, not of the number typed.

    **``ref`` is an idempotency key with a longer reach than the others here.**
    For a withdrawal it stops a retry recording two requests, and for a purchase
    the ledger row's ``UNIQUE`` would stop it anyway. This one does more: it is
    handed to the provider as its own idempotency key, so a retry that gets past
    this process is refused at the far end too. That is why a repeated key is a
    **409 rather than a 200 carrying the first answer** - see
    ``DepositAlreadyInitiatedError`` for why nothing can hand the original back.

    The key is namespaced to the wallet before it becomes a ledger reference, on
    the same argument ``MovementIn`` gives: two callers choosing the same word do
    not collide, because the server prefixes a wallet id it has already resolved.

    **And it is the one ``ref`` here with a rule about its characters.** The
    others never leave this system; this one is quoted into a reference that a
    payment provider has to accept, and the provider takes letters, digits and
    ``- . , =`` and nothing else. A key outside that set is refused as an
    ``InvalidIdempotencyKeyError`` - a 400 whose ``detail`` names the characters -
    before any collection is opened. That refusal is made by the domain rather
    than checked here, and it is the only place it can be made usefully: the
    provider's own answer is a bare 400 that reads as though *this* system were
    broken, which is exactly what it read as for as long as this route existed.
    """

    amount: str = Field(examples=["5000.00"])
    ref: str | None = Field(
        default=None,
        examples=["deposit-2026-09-12"],
        description=(
            "Idempotency key. Auto-generated when omitted. Only letters, "
            "digits and - . , = are accepted - this key is handed to the "
            "payment provider, which refuses anything else."
        ),
    )
