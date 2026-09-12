from uuid import UUID

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

    **Exposed in this phase even though no pot can yet hold money**, which looks
    odd and is worth explaining rather than leaving to be discovered. Opening a
    pot moves nothing - ``open_fund`` writes no ledger row, only the wallet's new
    shape - so it is on the safe side of the line this phase draws. What a user
    can do with one is limited: filling it is ``fund deposit`` or ``fund lock``,
    both of which change a balance and are therefore held until Phase 2. So an
    empty pot can be created, named, listed, and drawn on by nothing.

    That is a real limitation and it is preferred to the alternative, which would
    be holding this endpoint too and having the wallet's shape be the one thing
    about it a client could not change.

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
