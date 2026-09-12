from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Response

from app.application.payments.initiate_deposit import InitiateDeposit
from app.application.wallet_service import WalletService
from app.domain.money.confirmationKind import ConfirmationKind
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import deposit_service, wallet_service

router = APIRouter(tags=["wallets"])


@router.post("/wallets", response_model=schemas.WalletOut, status_code=201)
def open_wallet(
    payload: schemas.OpenWalletIn,
    service: WalletService = Depends(wallet_service),
) -> schemas.WalletOut:
    """Open a wallet for the caller.

    **The owner is not in the request, and cannot be.** The service was built for
    one user before this function ran, so there is no field through which a
    caller could name somebody else - which is why the request body is a currency
    and nothing more. The CLI has the same shape for the same reason; see
    ``_open``.

    201 rather than 200, because this creates a resource and says where it is.
    """
    wallet = service.open_wallet(translate.currency_in(payload.currency))
    return translate.wallet_out(wallet)


@router.get("/wallets/{wallet_id}", response_model=schemas.WalletOut)
def get_wallet(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> schemas.WalletOut:
    """One wallet, with its pots.

    **Someone else's wallet is a 404 with the same body as one that does not
    exist**, and that is the whole of Phase 1a reaching the client. There is no
    branch here that could tell the two apart: the service's scoped read raises
    the same ``WalletNotFoundError`` for both, and ``errors`` renders it without
    inventing a distinction. See ``test_isolation.py``.

    The pots travel with the wallet rather than behind a second request because a
    wallet's balance is not readable without them: the locked balance is a sum
    over pots, so a client that had the wallet and not the pots would hold two
    numbers it could not reconcile.
    """
    return translate.wallet_out(service.get_wallet(wallet_id))


@router.get(
    "/wallets/{wallet_id}/transactions",
    response_model=list[schemas.TransactionOut],
)
def list_transactions(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> list[schemas.TransactionOut]:
    """A wallet's ledger, oldest first.

    The wallet is validated before the ledger is read, so "no such wallet" and
    "a wallet with no movements yet" are different answers - a 404 and an empty
    list. The distinction is not cosmetic: one means the caller has the wrong id,
    the other means the account is new, and a client that could not tell them
    apart would show "no transactions" to somebody who had mistyped a UUID.
    """
    return [
        translate.transaction_out(one)
        for one in service.transactions_for_wallet(wallet_id)
    ]


# --- moving money out, and the status transitions ---------------------------
#
# What these five have in common is that they are the wallet's own operations:
# no plan is involved, and every one of them acts on a wallet the caller has
# already been proved to own. What separates them is settlement, and it is the
# reason the three money routes look so different from the three status ones.
#
# **The three money routes no longer move money.** They record a request to, and
# answer with that request. A second call - ``POST /confirmations/{id}/confirm``
# - is what executes it. That is the whole feature, and it is why their responses
# changed shape rather than gaining a field: a request is not a transaction, and
# a body that looked like one would be read as one.
#
# What has *not* changed is the settlement story underneath. Once confirmed, a
# withdrawal or a payout is still the only operation here that crosses the
# system's edge - money leaves towards a bank account nothing here has contacted
# - so the ledger row it produces is still **PENDING**, not SUCCESSFUL, with the
# amount already debited from the wallet. The money is held, and the receipt is
# deliberately not sent; see ``WalletOperation.settles_immediately`` and
# ``WalletService._announce``. A client must not read the confirm's 201 as "the
# money arrived", which is why the response carries the row's status rather than
# leaving it implied.


def _status_for(response: Response, created: bool) -> None:
    """Say whether this call recorded a request or found one already there.

    201 when the request is new, 200 when it is the one already on file - which
    is the answer a client that retried a request whose response it never saw
    needs, and cannot get any other way. The body is identical in both cases, on
    purpose: it *is* the same request, and a client that only looked at the body
    would have no reason to treat the second answer as a failure.

    Set here rather than by returning a ``JSONResponse``, because the route's job
    is to describe the resource and not to build an envelope around it; the
    declared ``status_code=201`` remains the answer for the ordinary case, and
    this only ever lowers it.

    Nothing about this is a claim that money did not move. A request is inert
    either way - see ``WalletService.request_confirmation`` - so a 200 and a 201
    differ in what was recorded, not in what was spent.
    """
    if not created:
        response.status_code = 200


@router.post(
    "/wallets/{wallet_id}/withdrawals",
    response_model=schemas.ConfirmationOut,
    status_code=201,
)
def withdraw(
    wallet_id: UUID,
    payload: schemas.MovementIn,
    response: Response,
    service: WalletService = Depends(wallet_service),
) -> schemas.ConfirmationOut:
    """Record a request to move money out of the available balance. **It moves nothing.**

    The wallet is read first, and only to learn its currency - the amount arrives
    as a bare string and has to be read in something. That is one extra read per
    money request, and it is the same shape the CLI uses, because the alternative
    is a currency in the request body that a caller could disagree with the
    wallet about.

    **The balance is not checked, here or in the service.** A request is not an
    attempt: the balance that decides is the one at confirm time, so a check now
    would be a second and weaker copy of the wallet's rule, free to disagree with
    it. It also means a request made against an empty wallet still works once the
    wallet is topped up before it is answered, which is what somebody about to be
    paid would want.

    Posting the same ``ref`` twice returns the *same* request - a 200 carrying the
    id the first call returned. See ``_status_for``.
    """
    wallet = service.get_wallet(wallet_id)
    amount = translate.money_in(payload.amount, wallet.currency)
    now = datetime.now()
    requested = service.request_confirmation(
        wallet_id,
        ConfirmationKind.WITHDRAWAL,
        now,
        internal_reference=payload.ref,
        amount=amount,
    )
    _status_for(response, requested.created)
    return translate.confirmation_out(requested.confirmation, now)


@router.post(
    "/wallets/{wallet_id}/payouts",
    response_model=schemas.ConfirmationOut,
    status_code=201,
)
def payout(
    wallet_id: UUID,
    payload: schemas.PayoutIn,
    response: Response,
    service: WalletService = Depends(wallet_service),
) -> schemas.ConfirmationOut:
    """Record a request to send money to an external account. **It moves nothing.**

    Two kinds behind one route, because the source is what picks the balance and
    both record the same ``PAYOUT``; the difference is which money funds it. The
    dispatch is here rather than in the schema because it is not a rule about the
    request - both branches are legitimate requests - it is a choice between two
    operations, which is exactly what a route does. The choice is recorded on the
    request as its ``kind``, so the confirm call does not have to be told it
    again: a payout request cannot be answered as anything but a payout.

    ``as_of`` is read here, at the adapter, and is deliberately *not* used for the
    request - a request may name a pot that has not come due, and naming one is
    not the same as spending it. The moment that decides whether a pot has matured
    is read again at confirm time, for the reason ``cli._payout`` gives: a locked
    payout may only spend pots that have come due, so the use case has to be told
    when "now" is rather than reading it itself.

    PENDING once confirmed, like a withdrawal, and for the same reason.
    """
    wallet = service.get_wallet(wallet_id)
    amount = translate.money_in(payload.amount, wallet.currency)
    destination = translate.destination_in(payload.destination)

    now = datetime.now()
    requested = service.request_confirmation(
        wallet_id,
        (
            ConfirmationKind.PAYOUT_FROM_LOCKED
            if payload.source == "locked"
            else ConfirmationKind.PAYOUT_FROM_AVAILABLE
        ),
        now,
        internal_reference=payload.ref,
        amount=amount,
        destination=destination,
        fund_name=payload.fund_name,
    )
    _status_for(response, requested.created)
    return translate.confirmation_out(requested.confirmation, now)


@router.post("/wallets/{wallet_id}/freeze", response_model=schemas.WalletOut)
def freeze_wallet(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> schemas.WalletOut:
    """Stop value leaving this wallet until it is unfrozen.

    Returns the wallet rather than 204, because the caller's next question is
    always what the balance is now - and freezing does not change it. A status
    code would say the command landed; the body says what the wallet became.

    No request body. A freeze takes no parameters, and a schema with no fields
    would invite a client to send one and wonder why it was ignored.
    """
    return translate.wallet_out(service.freeze_wallet(wallet_id))


@router.post("/wallets/{wallet_id}/unfreeze", response_model=schemas.WalletOut)
def unfreeze_wallet(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> schemas.WalletOut:
    """Return a frozen wallet to its active state. Refused on a closed one."""
    return translate.wallet_out(service.unfreeze_wallet(wallet_id))


@router.post(
    "/wallets/{wallet_id}/close",
    response_model=schemas.ConfirmationOut,
    status_code=201,
)
def close_wallet(
    wallet_id: UUID,
    response: Response,
    service: WalletService = Depends(wallet_service),
) -> schemas.ConfirmationOut:
    """Record a request to close a wallet for good. **It closes nothing.**

    There is no reopen, which is why this one is confirmed like the money
    operations even though it moves nothing: it is the one transition in the API
    with no way back.

    **The two 409s moved to the confirm call, and that is a change in when a
    caller learns, not in what they learn.** ``WalletNotEmptyError`` and
    ``WalletHasActivePlansError`` are still the refusals, still 409, still naming
    the different remedies they always named - but they now arrive when the
    request is answered, because that is when the wallet and its plans are read
    for the decision. Recording the request cannot refuse them: a request is not
    an attempt, and the wallet it names may well be empty by the time somebody
    answers it.

    This is also the one operation whose request *survives a refusal*. A refused
    close writes no ledger row, so there is nothing for a retry to be handed back
    and nothing to spend the request against - the request stays answerable, and
    emptying the wallet and confirming again is exactly what the refusal told the
    caller to do. See ``WalletService._close_wallet``.

    **This route has no CLI counterpart, today or after this.** ``close`` is
    reachable only over HTTP, so its confirmation is API-only; adding a CLI verb
    is separate work rather than something smuggled in beside this.
    """
    now = datetime.now()
    requested = service.request_confirmation(
        wallet_id, ConfirmationKind.CLOSE, now
    )
    _status_for(response, requested.created)
    return translate.confirmation_out(requested.confirmation, now)


# --- money arriving ---------------------------------------------------------
#
# One route, and it is the only one on this router that does not move money
# *between* the wallet's own balances or out of it. It is here rather than in a
# module of its own because its path says where it belongs - a deposit is
# something you do to a wallet - and the contrast that matters is with the
# withdrawal and payout routes directly above.
#
# **It is not confirmed, and that is the line decision 113 drew rather than an
# exception to it.** That decision put a second look before operations where
# money *leaves*, because those are the ones where a mistake is irreversible and
# a prompt can still catch it. A prompt before receiving money would be a prompt
# whose only possible answer is yes.
#
# **A deposit is the one wallet operation the provider pays for, and the one
# whose settlement is not ours to decide.** The withdrawal above answers with a
# request because a person has to answer it; this answers with a collection
# because a person has to pay it, and neither has moved anything yet. What
# settles them is different in kind: the withdrawal is settled by its owner at
# ``/confirmations/{id}/confirm``, and the deposit is settled by Paystack at
# ``/webhooks/paystack``, which no client of this API can call.


@router.post(
    "/wallets/{wallet_id}/deposits",
    response_model=schemas.DepositIntentOut,
    status_code=201,
)
def deposit(
    wallet_id: UUID,
    payload: schemas.DepositIn,
    wallets: WalletService = Depends(wallet_service),
    deposits: InitiateDeposit = Depends(deposit_service),
) -> schemas.DepositIntentOut:
    """Open a collection for this wallet. **The wallet is not credited.**

    The wallet is read first, and only to learn its currency - the amount arrives
    as a bare string and has to be read in something. That is one extra read per
    deposit, and it is exactly the read ``withdraw`` above makes for exactly the
    same reason, so the two money-in and money-out routes agree about how a bare
    amount becomes a ``Money``: the wallet decides the currency, never the
    request.

    **The order of the two dependencies is the feature, not the wiring.** An
    installation with no payment key refuses this request at
    ``dependency.payment_provider`` - a 503 - and it does so *before* the wallet
    is read, before the amount is parsed and before any row is written. There is
    no partial state to clean up because nothing has happened yet, which is the
    property to want from a request whose whole purpose is to reach outside this
    system.

    Three refusals, and they are graded by ``errors`` like every other domain
    refusal:

    - ``WalletNotFoundError`` -> **404**, the same answer a wallet that never
      existed gets. A caller cannot open a collection against somebody else's
      wallet, and cannot tell that one exists by trying.
    - ``WalletClosedError`` -> **409**. A closed wallet is refused *here*, before
      the payer is ever sent anywhere, because ``Wallet.apply_deposit`` will not
      credit one - so a collection opened against it would take money that
      nothing could ever put anywhere.
    - ``DepositAlreadyInitiatedError`` -> **409**. A deposit is already open
      under this key. **This is deliberate rather than the friendlier-sounding
      alternative**, and it is the one refusal here worth reading twice: this
      route does not return the first collection's ``authorization_url`` for a
      repeated key, because nothing stores it and a checkout URL is single-use.
      Handing back a page that has already been paid would be telling the client
      a deposit is live when the provider would refuse to take the money. See
      ``DepositAlreadyInitiatedError``.

    A **frozen** wallet is allowed, and the asymmetry with closed is deliberate:
    freezing stops value *leaving*, and this is value arriving. The balance it
    lands in is one the freeze already protects.
    """
    wallet = wallets.get_wallet(wallet_id)
    amount = translate.money_in(payload.amount, wallet.currency)
    initiated = deposits.execute(
        wallet_id,
        amount,
        payload.ref if payload.ref is not None else str(uuid4()),
    )
    return translate.initiated_deposit_out(initiated)
