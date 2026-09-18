"""How a domain refusal becomes an HTTP response.

The CLI collapses this whole tree to one line - ``error: ...`` and exit 1 - and
that is the right answer for a terminal with one user watching. HTTP has to be
more precise, because the status code is what a client *acts* on: a retry, a
re-authentication, a message to show somebody. So the grades below are part of
the contract rather than a rendering detail.

They are graded by **what kind of question was refused**, not by listing classes
as they come up:

    401  we do not know who is asking, or the proof offered did not hold
    404  the resource is not there *for this actor*
    409  the resource is there, and its current state refuses this
    400  something in the request is not acceptable
    422  the request did not have the shape the endpoint declares (FastAPI's own)
    429  the same caller is asking too often, and the answer is to come back later
    503  this installation cannot serve this request, whoever asks
    500  a bug

Five of those eight are defaults rather than lists. ``UNAUTHORIZED``,
``NOT_FOUND``, ``CONFLICT`` and ``UNAVAILABLE`` are the four *domain* exceptions
worth naming, and everything else that is a ``MoneyError`` is a 400 - which is the
safe direction to be wrong in. A refusal this module has never heard of is far more
likely to be about a value the caller sent than about a resource's state, and a
400 tells the caller to look at their request rather than to retry.

**429 is the fifth default and the first that no ``MoneyError`` can ever reach**,
which is why it arrives as a class this layer made rather than as a grade in
``_grade``. The domain has no opinion about how often a request arrives - the same
line ``PaymentsUnconfiguredError`` sits on - so there is nothing for ``_grade`` to
match and no tuple to add it to. It is a different kind of answer from the seven
above it: those are all statements about *this request*, and this one is a statement
about the requests before it. A client that receives it should wait rather than
change anything, which is what ``Retry-After`` says and what no other grade here
means.

**``UNAVAILABLE`` was added late and is the only list that is not about the
caller at all**, which is worth marking because it changes what this module is
for: three of the four grades answer a question about the request, and the fourth
answers a question about the deployment. It exists because a forgotten-password
request cannot be honoured without a mailbox, and a domain refusal for that fact
would otherwise have fallen through to the 400 - telling somebody their address
was malformed when the address was fine and the mail server was not configured.

**401 sits above 404 because it is a different kind of question.** The three
below it are all answers about a resource - there, not there, refusing - and they
are only askable once the caller is known. A 401 says the question never got that
far, which is why no endpoint in this API returns both for the same request: an
unauthenticated caller learns nothing about what exists, and the isolation
guarantee that makes a foreign wallet a 404 is not reachable at all until a
session has been resolved.

**``WalletNotFoundError`` is matched before the generic case**, and that
ordering is the whole reason this module is not three lines. It is a
``MoneyError``, so a handler registered only for the root would turn it into a
400 and tell a client that a wallet it cannot see is a badly-formed request.

**The 404 is where Phase 1a's guarantee reaches the wire.** A wallet belonging to
somebody else raises the *same* exception as one that does not exist - there is
no branch between them anywhere in the domain - and the handler below must not
reintroduce a distinction that a phase was spent removing. It does not, because
it has nothing to distinguish: it prints the class name and the message, and both
are identical for the two cases. That is asserted in
``tests/presentation/api/test_isolation.py``, which is the test that would fail
if somebody later added a friendlier message for the foreign case.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.identity.exception import (
    DuplicateEmailError,
    DuplicateGoogleSubjectError,
    DuplicatePhoneError,
    EmailChangeAlreadyUsedError,
    EmailChangeExpiredError,
    EmailUnchangedError,
    GoogleProviderError,
    InvalidCredentialsError,
    InvalidEmailChangeTokenError,
    InvalidGoogleTokenError,
    InvalidPasswordResetTokenError,
    InvalidPhoneVerificationTokenError,
    InvalidSessionError,
    NoGoogleAccountError,
    NoMailAccountError,
    NoSmsAccountError,
    PasswordResetAlreadyUsedError,
    PasswordResetExpiredError,
    PhoneVerificationAlreadyUsedError,
    PhoneVerificationExpiredError,
    UserNotFoundError,
)
from app.domain.money.exception import (
    ConfirmationAlreadyUsedError,
    ConfirmationExpiredError,
    ConfirmationNotFoundError,
    DuplicateFundNameError,
    FundNotFoundError,
    FundNotMaturedError,
    InsufficientFundsError,
    MaturityNotExtendedError,
    MoneyError,
    TransactionNotFoundError,
    WalletAlreadyActiveError,
    WalletAlreadyClosedError,
    WalletAlreadyFrozenError,
    WalletClosedError,
    WalletFrozenError,
    WalletHasActivePlansError,
    WalletNotEmptyError,
    WalletNotFoundError,
)
from app.domain.planning.exception import (
    CommittedPayoutRemovalError,
    IrreversibleReleasePlanError,
    PlanAlreadyFinishedError,
    PlanNotActiveError,
    PlanNotPausedError,
    SavingsPlanNotFoundError,
)
from app.domain.payments.exception import DepositAlreadyInitiatedError

#: We do not know who is asking, or the proof offered did not hold.
#:
#: Two classes, and they are the same answer to a client: present a token, or
#: present a better one. ``InvalidSessionError`` is a token that is unknown,
#: expired or orphaned - the domain keeps those one refusal deliberately, so this
#: list cannot split them even if it wanted to. ``InvalidCredentialsError`` is a
#: login whose address and password did not match, which arrives at the same
#: status for a different reason: the request asked to *become* somebody and the
#: answer is that it did not.
#:
#: **The 401 that goes out carries no ``WWW-Authenticate`` header.** RFC 9110
#: asks for one on a 401, and its job is to tell a client which scheme to satisfy
#: so it can retry without being told. This API has exactly one scheme, it is
#: documented in the OpenAPI description, and no client of it negotiates - so the
#: header would be a formality that the error body's ``error`` field already
#: answers more precisely. Worth naming rather than leaving for somebody to
#: notice from a spec-compliance tool.
#:
#: ``InvalidEmailChangeTokenError`` is the newest member and the first that is
#: neither a session nor a login. It fits the grade rather than stretching it:
#: the request presented a credential, the credential was looked up, and it was
#: not good enough. Which of the three ways it failed - no such code, a code that
#: has been used, a code whose account is gone - is deliberately one class on the
#: wire, and only the last of those is *not* in this list: an expired code is its
#: own 409, because "ask again" is a different instruction from "present a better
#: code" and a person who waited too long needs to be told which happened.
#:
#: The absent ``WWW-Authenticate`` argument above holds here too, and it is worth
#: noticing that this is where it is most obviously right: a client cannot
#: *satisfy* a challenge for a code it was mailed, so a header naming a scheme
#: would be pointing at something that does not exist.
#:
#: ``InvalidPasswordResetTokenError`` is the newest member and the exact counterpart
#: of the one above, one flow over. It is the same three-way collapse for the same
#: reason - no such code, a code naming a deleted account - and it is a 401 for the
#: same reason: the request presented a credential, the credential was looked up,
#: and it was not good enough. The three refusals a reset can make map onto the
#: grades identically to a change's, which is the point worth checking rather than
#: assuming: **the pairing is the test of whether the two flows really do share a
#: lifecycle**, and `tests/presentation/api/test_password_resets.py` asserts it.
#:
#: ``InvalidPhoneVerificationTokenError`` is the fourth and the third of the same
#: shape, and it is worth noticing that the three-way collapse has now been arrived
#: at three times independently rather than generalised. Each flow refuses "no such
#: code", "a code whose account is gone" and - for the signup, where there is no
#: account yet - nothing else, with one class, because a caller who could tell them
#: apart has an oracle and the remedy is identical. The 401 is the same 401 for the
#: same reason: a credential was presented, looked up, and was not good enough.
#:
#: ``InvalidGoogleTokenError`` is the fifth, and it is the first member of this list
#: whose credential neither this system minted nor the caller chose - it is a
#: signature Google made over an assertion about somebody. It fits the grade in the
#: same words rather than by analogy: the request presented a credential, the
#: credential was checked, and it was not good enough. **Its collapse is the widest
#: of the five and the reason is the same one**: unknown, malformed, expired, signed
#: by the wrong key, issued to *another application* and issued by another issuer
#: are one class on the wire, because the remedy is one thing - get a fresh token -
#: and because telling a forger which check their forgery failed is telling them how
#: to fix it. The ``aud`` case is worth naming inside that list rather than leaving
#: it as one of five, since it is the one a caller can reach *by accident*: a
#: perfectly good Google token, for somebody else's product, is not a credential
#: here and must not be treated as one.
UNAUTHORIZED = (
    InvalidSessionError,
    InvalidCredentialsError,
    InvalidEmailChangeTokenError,
    InvalidPasswordResetTokenError,
    InvalidPhoneVerificationTokenError,
    InvalidGoogleTokenError,
)

#: The resource is not there for the actor asking. One status, one body, whatever
#: the reason - see the module docstring.
#:
#: ``ConfirmationNotFoundError`` is the newest member and it carries two meanings
#: at once, on purpose: no such request, *or* one belonging to another account.
#: The domain keeps those one refusal for the reason ``WalletRepository.get_owned``
#: gives - telling a stranger that a request exists and is not theirs answers a
#: question they have no standing to ask - so this list could not split them even
#: if it wanted to.
NOT_FOUND = (
    WalletNotFoundError,
    TransactionNotFoundError,
    FundNotFoundError,
    UserNotFoundError,
    SavingsPlanNotFoundError,
    ConfirmationNotFoundError,
)

#: The resource is there and its current state refuses this. Note every one of
#: these is a statement about a row that already exists: a pot whose name is
#: taken, a plan that is not paused, a promise that cannot be revoked. None of
#: them is about the request being malformed - the request is fine, and the
#: answer is "not from where you are standing".
#:
#: ``DuplicateEmailError`` is the newest and the one that fits least obviously.
#: The row that exists is a *user*, and the request was to create one - so this
#: is a 409 about a resource the caller never named, arrived at by an address.
#: It belongs here anyway: the request is well formed, the address is a real
#: address, and the state of the world is what refuses it. 400 would blame the
#: caller for a value that is fine; the alternative of reporting success would
#: leave them unable to log in and unsure why.
#:
#: ``WalletNotEmptyError`` and ``WalletHasActivePlansError`` are close's two
#: refusals, and they are the plainest members of this list: the wallet is
#: exactly what the path names, it is there, and what it still holds is the whole
#: reason. They stay two classes at one status because they have two remedies -
#: move the money out, cancel the plan - and a caller that received one name for
#: both would have to guess which.
#:
#: ``ConfirmationExpiredError`` and ``ConfirmationAlreadyUsedError`` are the two
#: refusals a confirmation can make, and they are the same shape as the pair
#: above: the request is exactly what the path names, it is there, and it is its
#: own state that refuses. They are two classes rather than one because the
#: remedies differ and the difference is real - a spent request means *ask
#: again*, a lapsed one means *ask again, and answer it sooner* - and because
#: collapsing them would make "already answered" indistinguishable from "never
#: answered in time" in a log, which are the two facts worth telling apart when
#: somebody reports that money did not move.
#:
#: ``DepositAlreadyInitiatedError`` is the newest member and the first that is
#: about a *provider's* resource rather than one of ours. The row that exists is
#: a PENDING deposit, but what makes the request refusable is that a collection
#: has already been opened under this key - and a checkout URL is single-use, so
#: there is nothing to hand back. A duplicate key is the plainest possible
#: statement about a resource's state, so it is a 409 like the rest.
#:
#: ``EmailChangeExpiredError`` and ``EmailChangeAlreadyUsedError`` are the change
#: flow's pair, and they are deliberately *not* folded into
#: ``InvalidEmailChangeTokenError`` even though all three refuse the same request.
#: The two here are the same shape as the confirmation pair above - the request is
#: exactly what was presented, the row is there, and its own state is what refuses
#: it - and the split is the same split for the same reason: *ask again*, against
#: *ask again, and answer it sooner*. The third is a 401 because it is about the
#: credential rather than about the row, which is the line this whole module is
#: drawn along.
#:
#: ``EmailUnchangedError`` is the member that fits least obviously, and it earns
#: its place the same way ``DuplicateEmailError`` did. Nothing is malformed: the
#: address is real, usable, and the caller's own. What refuses the request is that
#: the account is already in the state it is asking for - so the honest answer is
#: "not from where you are standing" rather than a 400 blaming a value that is
#: fine, and definitely not a 200 reporting a change that did not happen.
#:
#: ``PasswordResetExpiredError`` and ``PasswordResetAlreadyUsedError`` are the
#: reset flow's pair, and they are the change flow's pair restated: the same two
#: classes, the same two remedies, the same refusal to collapse them into the 401
#: above. The reason the resemblance is worth stating rather than assuming is that
#: the *consequence* differs even though the grading does not - a spent or lapsed
#: code here means a password that was not changed, so "ask again" is the remedy for
#: somebody who is still locked out rather than for somebody whose address is still
#: the old one.
#:
#: ``PhoneVerificationExpiredError`` and ``PhoneVerificationAlreadyUsedError`` are
#: the signup flow's pair, and they are the two pairs above restated a third time -
#: which makes the resemblance a pattern rather than a coincidence between two
#: flows, and is why it is worth one sentence rather than a paragraph. What differs
#: is only what a *spent* code means here, and it is the difference that makes this
#: pair the most useful of the three to a caller: a spent or lapsed verification
#: means no account was created, so "ask again" is the remedy for somebody who does
#: not have a login at all. Contrast a reset, where the same state means a password
#: that was not changed for an account that exists.
#:
#: ``DuplicatePhoneError`` is ``DuplicateEmailError`` one identifier over, and it is
#: the second 409 about a resource the caller never named. It arrives a step later
#: than its sibling - at the confirm rather than the request - because asking at the
#: request would turn an unauthenticated endpoint into an oracle over a space as
#: small as a phone number's. The grade is unchanged by that: the request is well
#: formed, the number is a real number, and it is the state of the world that
#: refuses it.
#:
#: ``DuplicateGoogleSubjectError`` is the third of that family and the first that is
#: about a *link* rather than a value. An address and a number are things a person
#: types and could have typed differently; a Google subject is an opaque identifier
#: issued to one account by a third party, and there is exactly one of it per
#: account. So what refuses this request is "that Google identity already has an
#: account here" - the plainest possible statement about a row that exists. It is
#: deliberately not folded into ``DuplicateEmailError`` beside it even though both
#: can be returned by the same route, because the two remedies differ and a caller
#: that got one name for both would have to guess: *use another address*, against
#: *log in*.
CONFLICT = (
    InsufficientFundsError,
    WalletFrozenError,
    WalletClosedError,
    WalletAlreadyFrozenError,
    WalletAlreadyClosedError,
    WalletAlreadyActiveError,
    WalletNotEmptyError,
    WalletHasActivePlansError,
    FundNotMaturedError,
    MaturityNotExtendedError,
    DuplicateFundNameError,
    DuplicateEmailError,
    DuplicatePhoneError,
    DuplicateGoogleSubjectError,
    PlanNotActiveError,
    PlanNotPausedError,
    PlanAlreadyFinishedError,
    IrreversibleReleasePlanError,
    CommittedPayoutRemovalError,
    ConfirmationExpiredError,
    ConfirmationAlreadyUsedError,
    DepositAlreadyInitiatedError,
    EmailUnchangedError,
    EmailChangeExpiredError,
    EmailChangeAlreadyUsedError,
    PasswordResetExpiredError,
    PasswordResetAlreadyUsedError,
    PhoneVerificationExpiredError,
    PhoneVerificationAlreadyUsedError,
)


class ApiError(Exception):
    """A refusal this layer makes, with a status code attached.

    Deliberately *not* a ``MoneyError``. Everything under that root is a
    statement the domain makes about money, and the identity of a request - which
    header carried it, whether it was there at all - is not something the domain
    has an opinion about. Deriving from it to reuse the handler would put a
    transport concern inside the domain's exception tree, which is the one place
    this codebase has been careful to keep it out of.
    """

    status_code = 500

    def __init__(self, detail: str = "", headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.detail = detail
        #: Response headers this refusal needs to carry. Empty for every subclass but
        #: one, which is why it has a default rather than being threaded through each
        #: ``__init__``: ``RateLimitedError`` needs ``Retry-After``, and nothing else
        #: in this tree has ever needed a header at all. It is on the base class
        #: rather than special-cased in the handler so that a future refusal which
        #: needs one does not have to reach into this module to get it.
        self.headers = dict(headers) if headers else {}


class MissingCredentialsError(ApiError):
    """No ``Authorization`` header on a request that needs to know who is asking.

    A 401, which is what its predecessor's docstring promised it would become:
    ``MissingActorHeaderError`` was a 400 because there was no authentication
    scheme to satisfy, and a 401 would have pointed a client at a challenge that
    did not exist. There is one now, so the honest status is the one that means
    "prove who you are and come back".

    Only the *presence* of the header is this layer's business. A header that is
    there but malformed, or that carries a token nothing recognises, goes to
    ``ResolveActorFromSession`` and comes back as ``InvalidSessionError`` - a 401
    through the domain's own handler, with the domain's own words. So the two
    ways to fail produce one status and two ``error`` names, and the difference
    is exactly the one a client acts on: "you sent nothing" against "what you
    sent was not good enough".
    """

    status_code = 401


class InvalidWebhookSignatureError(ApiError):
    """A webhook arrived that this installation's secret key did not sign.

    A 401, and the module docstring's own grading is what puts it there: *"we do
    not know who is asking, or the proof offered did not hold"*. A webhook
    presents no session, so the first half is not askable - but the second is
    exactly what happened. The caller is unidentified and the credential it
    offered was checked and rejected, which is a 401 in every sense except that
    the credential is a signature rather than a token.

    **It carries no body detail about *why*.** An absent header, a wrong header
    and a body altered after signing are one answer, because they are one fact -
    this request cannot be shown to have come from Paystack - and telling a
    stranger which of the three it was is telling them how their forgery failed.
    The distinction exists in the server's log, not in the response; see
    ``routes/webhooks.py``.

    **Not a ``MoneyError``, and this is the class that most needs the argument.**
    The domain has never heard of a header, a signature or a byte string. A
    forged webhook is not a statement about money that the aggregates could have
    an opinion about - it is a statement about a *request*, which is this layer's
    whole subject.
    """

    status_code = 401


class MalformedWebhookError(ApiError):
    """A signed body that is not an event this installation can read.

    A 400 rather than the 200 the *unrecognised* case gets, and the difference
    between the two words is the whole of this class. An event this code has
    never heard of is a provider doing something new, which is not a problem and
    is acknowledged; a body that has no ``event`` field, or whose ``data``
    carries no reference or no usable amount, is one that cannot be read at all.
    There is nothing to acknowledge, no row to name, and nothing a retry would do
    differently - so the honest answer is "this is not a request I can act on".

    400 rather than 500 is also what stops the retry: a provider retries a 5xx
    and gives up on a 4xx, and giving up is correct here. A 500 would have
    Paystack redeliver a body that will fail identically every time, which is a
    retry storm over a bug rather than a recovery from an outage.
    """

    status_code = 400


class PaymentsUnconfiguredError(ApiError):
    """This installation has no payment key, so it can neither collect nor judge.

    A 503, and the choice of 503 over 401 is the point of writing this class
    down. It would be easy to call a webhook on an unconfigured install
    *unauthenticated* - there is no key, so no signature can verify - but that
    would blame the caller for the installation's state. Paystack's signature may
    be perfectly good; there is simply nothing here to check it against, and a
    503 says so: the server cannot serve this request *right now*, try later.
    Which is what we want a provider to do, because the operator who forgot to
    set the key will fix it and the events will then land.

    A 401 would tell Paystack to stop, and the money taken during that window
    would be settled by nothing.

    It covers both routes on this subject, and they reach it the same way: a
    deposit refused because no collection can be opened, and a webhook refused
    because no signature can be verified. See
    ``dependencies.payment_provider`` - one dependency, so that "is this
    installation configured?" has one answer rather than one per route.
    """

    status_code = 503


class RateLimitedError(ApiError):
    """This caller has asked this question too often, and should come back later.

    A 429, and the first grade in this API whose remedy is *wait* rather than *change
    something*. Every other refusal here can be fixed by the caller - present a token,
    fix the address, choose another name - and this one cannot: the request is well
    formed, the caller may be perfectly entitled to make it, and the only thing wrong
    with it is how recently they made the last one. That is why this response carries
    ``Retry-After``, and it is the only response in this API that does.

    **What it deliberately does not say is which limit was hit, or how much of it is
    left.** A body naming the policy would describe the shape of the defences to
    whoever is probing them, and on ``POST /password-resets`` it would be an oracle in
    its own right: that route answers identically whether or not the address exists,
    and a body distinguishing "too many requests about this address" from "too many
    requests from you" would hand back the distinction the endpoint was built to
    withhold. So the detail is a sentence about waiting, and the numbers stay in the
    server's records.

    **It is not a 503, and the two are easy to confuse.** A 503 says this installation
    cannot serve the request *whoever asks*; a 429 says it can, and will, and not yet
    from this caller. That difference decides what a well-behaved client does with it,
    and getting it backwards is not a cosmetic error: a client that treated a 429 as a
    503 would take the whole installation offline on the strength of one abusive
    caller, which is the failure the two grades exist to keep apart.

    ``retry_after`` is seconds and is the caller's own remaining window rather than a
    constant - see ``Verdict.retry_after_seconds``, which rounds up so that a client
    which obeys the header is not refused a second time for having obeyed it.
    """

    status_code = 429

    def __init__(self, retry_after: int, detail: str = ""):
        super().__init__(
            detail or "too many requests; try again shortly",
            headers={"Retry-After": str(retry_after)},
        )
        self.retry_after = retry_after


#: The installation cannot serve this request, whatever the caller does. These are
#: statements about *this deployment* rather than about the request or the caller,
#: and the 503 is what tells a well-behaved client to come back later instead of
#: changing what it sent.
#:
#: **``NoMailAccountError`` is the newest member, and it joins the group the
#: module's own header did not have a name for.** ``PaymentsUnconfiguredError``
#: above is a 503 this layer makes; this is a 503 the *domain* makes, which is why
#: it needs a grade rather than a class - and the arrival of a second member is
#: what turns "the payment key is missing" from a one-off into a category.
#:
#: It is a 503 and not a 400 for exactly the reason the payment one is: the request
#: is well formed, the address is a real address, and there is no way to prove a
#: forgotten password on a machine that cannot send mail. Telling the caller their
#: request was wrong would send them to look for a typo in an address that is
#: correct.
#:
#: The body names the missing variable, because the use case's sentence is composed
#: from ``describe_configuration``. That is a deliberate disclosure on an
#: unauthenticated endpoint - it tells a stranger that this install has no SMTP -
#: and it is the right trade: an operator who cannot see which variable is missing
#: cannot fix it, the fact is already inferable from the 503, and nothing here is
#: secret. The alternative, a 503 saying only "unavailable", is the failure mode
#: ``describe_configuration`` exists to prevent.
#:
#: **``NoSmsAccountError`` is the second member and the one that settles the
#: category**, because it is the first to reach this grade from a *different
#: channel* rather than from a different feature. The two refusals now say the same
#: thing about two installations: this deployment cannot reach you the way the flow
#: requires, and the body names which variable would fix it - ``describe_configuration``
#: for one, ``describe_termii_configuration`` for the other. The disclosure argument
#: above transfers word for word, and so does the reason it is not a 400, with one
#: addition worth stating because it is new here: a phone signup has no fallback at
#: all. A mail-less install can still apply an address change on the password proof;
#: a number has no second proof, because the reason to believe somebody holds a
#: handset *is* a message arriving on it. So the 503 is the only true answer rather
#: than the kinder one.
#:
#: **``NoGoogleAccountError`` is the third and the first that is not about a channel
#: this installation runs.** The two above say "this deployment cannot reach you the
#: way the flow requires"; this one says "this deployment cannot check what you
#: showed me" - the same shape one step further back, and the disclosure argument
#: transfers with it, since the body names ``GOOGLE_CLIENT_ID`` for the same reason
#: the others name theirs. What is new is that the missing variable is one **no
#: caller could supply**: a person who wanted to reset a password on a mail-less
#: install could not fix it either, but at least the setting is about reaching
#: *them*. This one is purely the operator's, which is why the 503 is not merely the
#: kinder answer but the only true one - "change what you sent" is not available
#: here in any form.
#:
#: It is a distinct class from ``GoogleProviderError``, which is also a 503 and
#: describes the opposite situation: Google configured and *unreachable*. The two
#: are worth keeping apart for the reason the code above gives about 401s and 404s -
#: one is a fact about the deployment that the deployment can fix, the other is a
#: fact about the world that nobody can. Both are 503s because both mean "come back
#: later" rather than "change your request", which is the property this grade is
#: defined by rather than a coincidence of two classes landing together.
#:
#: **``GoogleProviderError`` is the fourth member, and the first that is not about a
#: missing setting.** The three above are all "this installation has nothing to work
#: with"; this one is "it has everything and could not reach the far end" - and it
#: belongs in this tuple rather than in the 400 fall-through for the reason
#: ``GoogleProviderError``'s own docstring gives at length: an SMS provider failure
#: is about *the message*, and a key set that could not be fetched is a fact about
#: the server, so no change to the request would alter it. Leaving it out would be
#: the exact failure this module's header warns about - a refusal falling through to
#: a 400 and telling a caller their token was wrong when it was never read.
#:
#: It is the one member here that is reachable while the installation is *correctly
#: configured*, which is what makes it worth asserting separately: the three above
#: are testable by clearing a variable, and this one needs Google to be unreachable
#: while ``GOOGLE_CLIENT_ID`` is set.
UNAVAILABLE = (
    NoMailAccountError,
    NoSmsAccountError,
    NoGoogleAccountError,
    GoogleProviderError,
)


def _grade(exc: MoneyError) -> int:
    """The status for a domain refusal, by kind rather than by name.

    The fall-through is the 400, and ``UnusableEmailError`` is the member that
    most needs saying so rather than being left to be discovered: it is refused
    because of the *value that was typed* - an address with no domain - and not
    because of anything the store holds. That is the definition of this module's
    400, and it is the same grade ``InvalidUserEmailError`` gets beside it, which
    is the pair that has to agree: a person told "that is not an address" and a
    person told "that address cannot work" are being told the same kind of thing.

    ``UNAVAILABLE`` is tested **first**, and the order is load-bearing rather than
    stylistic. It is the only grade that is about the server rather than about the
    caller, so it is the only one whose answer does not depend on anything the
    request carried - and testing it first makes that visible: a request that is
    simultaneously unauthenticated and unservable is answered with the state of the
    installation, which is the fact that will still be true once the caller fixes
    their half.
    """
    if isinstance(exc, UNAVAILABLE):
        return 503
    if isinstance(exc, UNAUTHORIZED):
        return 401
    if isinstance(exc, NOT_FOUND):
        return 404
    if isinstance(exc, CONFLICT):
        return 409
    return 400


def _detail(exc: BaseException) -> str:
    """The message, or the class name when the domain raised without one.

    The fallback is not decoration. ``WalletNotFoundError`` is raised bare - see
    ``SqliteWalletRepository`` - so without this every 404 would come back with an
    empty ``detail``, and a client would have a status code and nothing else to
    show. ``app.presentation.cli`` does the same thing for the same reason, in
    ``_describe``; the two presentations render the same refusal the same way,
    which is what makes it recognisable across them.
    """
    return str(exc) if str(exc) else type(exc).__name__


def _body(name: str, detail: str) -> dict:
    """The one response shape every failure uses.

    ``error`` is the exception's class name, and it is the field a client
    branches on; ``detail`` is prose for a human. Both are present on every
    failure, including the ones this layer raises itself, so a client never has
    to parse two shapes.
    """
    return {"error": name, "detail": detail}


async def money_error_handler(request: Request, exc: MoneyError) -> JSONResponse:
    """Render a domain refusal.

    ``async`` while the endpoints are not, and the difference is deliberate
    rather than an oversight. An endpoint does blocking SQLite work and belongs
    in FastAPI's threadpool, which is what a plain ``def`` gets it. A handler
    does no I/O at all - it formats a string - so there is nothing to hand off,
    and making it async keeps it on the event loop where it costs nothing.
    """
    return JSONResponse(
        status_code=_grade(exc), content=_body(type(exc).__name__, _detail(exc))
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """Render a refusal this layer made.

    ``headers`` is forwarded rather than hard-coded, which is what lets
    ``RateLimitedError`` carry its ``Retry-After`` without this handler knowing that
    class exists. Every other ``ApiError`` has an empty mapping and takes the same
    path it always did, so this is a seam rather than a special case.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(type(exc).__name__, _detail(exc)),
        headers=exc.headers or None,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """The 500, with nothing of the exception in it.

    **The message is deliberately not echoed**, which is the opposite of what
    every handler above does. A domain refusal is a sentence written for a user:
    it names a pot, or an amount, or a plan, and it is safe to show. An
    unexpected exception is a sentence written for whoever is debugging it, and
    it can carry a file path, a SQL fragment, or a value out of a row that
    belongs to somebody else. So the response says only that something broke, and
    the exception goes to the server's log where it belongs.
    """
    return JSONResponse(
        status_code=500, content=_body("InternalServerError", "internal server error")
    )


def install(app: FastAPI) -> None:
    """Register every handler, in the order that matters.

    Registration order does not decide matching - Starlette walks the exception's
    MRO and picks the most specific registered class - so listing ``MoneyError``
    before ``Exception`` here is documentation rather than mechanism. It is worth
    having anyway, because "the specific ones first" is the property a reader
    needs to check and the code should be readable as the rule it obeys.
    """
    app.add_exception_handler(MoneyError, money_error_handler)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
