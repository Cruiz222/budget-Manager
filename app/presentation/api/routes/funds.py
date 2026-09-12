from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends

from app.application.wallet_service import WalletService
from app.presentation.api import schemas, translate
from app.presentation.api.dependencies import wallet_service

router = APIRouter(tags=["funds"])


@router.get("/wallets/{wallet_id}/funds", response_model=list[schemas.FundOut])
def list_funds(
    wallet_id: UUID, service: WalletService = Depends(wallet_service)
) -> list[schemas.FundOut]:
    """A wallet's pots, oldest first.

    Also reachable inside ``GET /wallets/{wallet_id}``, and the overlap is
    deliberate: a client showing a wallet wants the pots with it, and a client
    polling one pot's balance should not have to fetch the whole wallet to get it.
    """
    return [translate.fund_out(fund) for fund in service.funds_for_wallet(wallet_id)]


@router.post(
    "/wallets/{wallet_id}/funds",
    response_model=schemas.FundOut,
    status_code=201,
)
def open_fund(
    wallet_id: UUID,
    payload: schemas.OpenFundIn,
    service: WalletService = Depends(wallet_service),
) -> schemas.FundOut:
    """Open a named pot on a wallet.

    Exposed in Phase 1b even though no pot could then hold money, which looked odd
    and was worth explaining rather than leaving to be discovered. Opening a pot
    moves nothing - ``open_fund`` writes no ledger row, only the wallet's new
    shape - so it was always on the safe side of the line. What a user could do
    with one was limited: filling it changes a balance and was held.

    **That limitation is now gone.** ``lock`` and ``release`` below move money in
    and out of a pot, and both are routed in this phase, so a pot opened here can
    be filled, matured and emptied. The paragraph above is kept rather than
    deleted because it records why this endpoint shipped early - a decision that
    looked wrong until the phase that justified it arrived.

    ``as_of`` is not taken from the request. It defaults to now inside the
    service, and it is the moment the pot was sealed - a fact about this server's
    clock rather than a value a caller should be able to backdate.
    """
    fund = service.open_fund(
        wallet_id,
        payload.name,
        translate.fund_kind_in(payload.kind),
        payload.maturity_date,
    )
    return translate.fund_out(fund)


# --- moving money in and out of a pot ---------------------------------------
#
# Both of these settle *here*, unlike the wallet's withdrawals and payouts, and
# the difference is the whole reason they are in a different file with a
# different response contract. A pot is inside the wallet: locking moves money
# from the available balance into it, releasing moves it back, and neither has a
# far end outside this system. So both come back SUCCESSFUL, immediately, with a
# receipt queued - there is nothing left to confirm.


@router.post(
    "/wallets/{wallet_id}/funds/{fund_name}/lock",
    response_model=schemas.TransactionOut,
    status_code=201,
)
def lock_into_fund(
    wallet_id: UUID,
    fund_name: str,
    payload: schemas.MovementIn,
    service: WalletService = Depends(wallet_service),
) -> schemas.TransactionOut:
    """Move money from the available balance into a named pot.

    **The pot is named in the path, not in the body**, and that is the same
    choice the CLI makes by taking it as a positional argument: a pot is a
    resource this operation acts *on*, not a parameter of the movement. It also
    means the pot's name is where a client would look for it, next to the wallet
    id, rather than buried in a payload beside the amount.

    An unknown name is a 404, and it is raised before any ledger row exists - the
    operation looks the pot up in its constructor. So a request naming a pot that
    does not exist leaves no trace, which is the right answer: nothing happened.

    Unlike a withdrawal this settles here, so the response says SUCCESSFUL and a
    receipt is queued. Locking is a promise made, and the wallet can keep it
    without anybody else's help.
    """
    wallet = service.get_wallet(wallet_id)
    amount = translate.money_in(payload.amount, wallet.currency)
    transaction = service.lock_into_fund(
        wallet_id,
        fund_name,
        amount,
        payload.ref if payload.ref is not None else str(uuid4()),
        # Read at the adapter, and required rather than defaulted: money arriving
        # in a pot stamps its funding moment, and the moment a plan's commitment
        # is judged against is this one.
        datetime.now(),
    )
    return translate.transaction_out(transaction)


@router.post(
    "/wallets/{wallet_id}/funds/{fund_name}/release",
    response_model=schemas.TransactionOut,
    status_code=201,
)
def release_from_fund(
    wallet_id: UUID,
    fund_name: str,
    payload: schemas.MovementIn,
    service: WalletService = Depends(wallet_service),
) -> schemas.TransactionOut:
    """Move money out of a named pot, back into the available balance.

    **Refused before the pot matures, and that refusal is a 409 with a ledger
    row.** ``FundNotMaturedError`` is the domain's, graded in ``errors``, and the
    failed attempt is recorded before it is re-raised - so the ledger remembers
    that somebody tried early, which is the kind of thing a wallet should keep.

    Note the two different refusals this can produce, because only one of them
    leaves a trace. An unknown pot name is refused before the operation is built
    and leaves nothing; a pot that exists but has not come due is refused inside
    the operation and leaves a FAILED row. ``ReleaseFromFund`` explains why.

    Settles here, like lock, and for the same reason: the money never left the
    wallet, it changed pockets.
    """
    wallet = service.get_wallet(wallet_id)
    amount = translate.money_in(payload.amount, wallet.currency)
    transaction = service.release_from_fund(
        wallet_id,
        fund_name,
        amount,
        payload.ref if payload.ref is not None else str(uuid4()),
        datetime.now(),
    )
    return translate.transaction_out(transaction)


@router.post(
    "/wallets/{wallet_id}/funds/{fund_name}/extend",
    response_model=schemas.FundOut,
)
def extend_fund(
    wallet_id: UUID,
    fund_name: str,
    payload: schemas.ExtendFundIn,
    service: WalletService = Depends(wallet_service),
) -> schemas.FundOut:
    """Push a pot's maturity date later. There is no way to pull it earlier.

    Returns the pot, not a transaction: extending moves no money, so there is no
    ledger row to return. What the caller wants to see is the new date, and that
    is in the body.

    A date that is not later than the current one, or not in the future, is
    refused by the aggregate as ``MaturityNotExtendedError`` - a 409, because the
    pot is exactly what the request names and its current date is the reason.
    """
    return translate.fund_out(
        service.extend_fund(wallet_id, fund_name, payload.new_date, datetime.now())
    )
