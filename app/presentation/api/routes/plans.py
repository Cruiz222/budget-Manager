from uuid import UUID

from fastapi import APIRouter, Depends

from app.application.plan_service import PlanService
from app.application.wallet_service import WalletService
from app.presentation.api import dependencies, schemas, translate

router = APIRouter(tags=["plans"])


@router.post("/plans", response_model=schemas.PlanOut, status_code=201)
def create_plan(
    payload: schemas.CreatePlanIn,
    plans: PlanService = Depends(dependencies.plan_service),
    wallets: WalletService = Depends(dependencies.wallet_service),
) -> schemas.PlanOut:
    """Create a plan on one of the caller's wallets.

    **The wallet is loaded for its currency**, and for a second reason that is
    worth naming: loading it *is* the ownership check. A plan can only be created
    on a wallet the caller can read, so an id belonging to somebody else fails
    here as a 404 before any plan exists - rather than creating a plan that
    nobody owns.

    The pot travels as the *name* the caller typed, not an id, exactly as it does
    on the command line. ``create_plan`` turns it into an id, because that method
    also holds the rule that a locked plan must name a pot and an available one
    must not - and resolving earlier would mean checking that rule after the
    lookup, reporting "no such pot" for a pot the plan was never going to use.

    The wallet's *balance* is deliberately not consulted. A plan may exist before
    it is affordable - that is what saving towards one means - and the shortfall
    is reported when a run actually fires. That rule is what lets a locked-source
    plan be created here against a pot that is still empty, which it can be.

    **What the boundary means in practice: no money can enter the system through
    this API.** Every operation that would put value into a wallet - deposit,
    deposit into a pot, lock into a pot - changes a balance and is held until
    Phase 2. So every wallet opened here stays at zero, and every plan created
    here will block for insufficient funds the first time the scheduler ticks.
    That is a client being able to build a *shape* and not a balance, which is
    exactly the intended trade: the shape is worth having, and the alternative -
    exposing a deposit so the shape looks complete - would be exposing the one
    thing this phase exists to withhold.
    """
    wallet = wallets.get_wallet(payload.wallet_id)
    plan = plans.create_plan(
        wallet_id=payload.wallet_id,
        name=payload.name,
        source=translate.source_in(payload.source),
        schedule=translate.schedule_in(payload.schedule),
        instructions=translate.instructions_in(
            payload.instructions, wallet.currency
        ),
        ends_on=translate.end_date_in(payload),
        fund_name=payload.fund_name,
    )
    return translate.plan_out(plan)


@router.get("/wallets/{wallet_id}/plans", response_model=list[schemas.PlanOut])
def list_plans(
    wallet_id: UUID, plans: PlanService = Depends(dependencies.plan_service)
) -> list[schemas.PlanOut]:
    """Every plan drawn on a wallet, oldest first.

    The wallet is validated first, so "no such wallet" is a 404 and "a wallet
    with no plans yet" is an empty list. The two are different answers and a
    client that could not tell them apart would show an empty schedule to
    somebody who had mistyped a UUID.
    """
    return [translate.plan_out(one) for one in plans.plans_for_wallet(wallet_id)]


@router.get("/plans/{plan_id}", response_model=schemas.PlanOut)
def get_plan(
    plan_id: UUID, plans: PlanService = Depends(dependencies.plan_service)
) -> schemas.PlanOut:
    """One plan. Another user's plan is a 404, like a wallet's."""
    return translate.plan_out(plans.get_plan(plan_id))


@router.get("/plans/{plan_id}/runs", response_model=list[schemas.PlanRunOut])
def list_runs(
    plan_id: UUID, plans: PlanService = Depends(dependencies.plan_service)
) -> list[schemas.PlanRunOut]:
    """A plan's run history, earliest occurrence first.

    This is the record that answers "why did my plan stop?". A blocked run names
    its reason and this is the only place that answer exists - the alternative to
    reading it here is reading the database by hand.
    """
    return [translate.plan_run_out(one) for one in plans.runs_for_plan(plan_id)]


@router.post("/plans/{plan_id}/pause", response_model=schemas.PlanOut)
def pause_plan(
    plan_id: UUID, plans: PlanService = Depends(dependencies.plan_service)
) -> schemas.PlanOut:
    """Stop a plan being due, keeping its place in the schedule.

    A plan that is not currently active is refused by the aggregate with a 409.
    That refusal is not softened here: the use case has nothing to add to the
    rule, and inventing friendlier words at this layer would mean two places knew
    what the rule was.

    Pausing is not cancelling. The run counter does not advance while paused, so
    resuming picks the missed payment back up rather than skipping it - which is
    what makes this safe to offer without a confirmation step.
    """
    return translate.plan_out(plans.pause_plan(plan_id))


@router.post("/plans/{plan_id}/resume", response_model=schemas.PlanOut)
def resume_plan(
    plan_id: UUID, plans: PlanService = Depends(dependencies.plan_service)
) -> schemas.PlanOut:
    """Return a paused plan to service, at the occurrence it left off on."""
    return translate.plan_out(plans.resume_plan(plan_id))


@router.post("/plans/{plan_id}/cancel", response_model=schemas.PlanOut)
def cancel_plan(
    plan_id: UUID, plans: PlanService = Depends(dependencies.plan_service)
) -> schemas.PlanOut:
    """End a plan for good.

    **A plan that releases locked money refuses this**, with a 409, and the
    refusal is the product rather than a limitation: locking is a commitment
    device, and a commitment that can be revoked with one request is not one. The
    aggregate raises ``IrreversibleReleasePlanError`` and this layer does not
    translate it - so the words a client shows are the domain's own.
    """
    return translate.plan_out(plans.cancel_plan(plan_id))


@router.put("/plans/{plan_id}/instructions", response_model=schemas.PlanOut)
def edit_instructions(
    plan_id: UUID,
    payload: schemas.InstructionsIn,
    plans: PlanService = Depends(dependencies.plan_service),
) -> schemas.PlanOut:
    """Replace a plan's lines with a new set.

    **The currency comes from the plan, not from a wallet id in the request.**
    A plan's currency cannot change - the aggregate allows only one across its
    lines and the use case holds it equal to the wallet's - so the plan is the
    authority on how to read these numbers. Asking for a wallet here would invite
    a caller to name a *different* wallet, and the answer to that is a rule
    nobody should have to write: the plan already knows.

    ``PUT`` rather than ``PATCH``, because the body is the whole new set of lines
    rather than a change to the existing ones. A plan that releases locked money
    refuses this too, for the same reason it refuses cancelling.
    """
    plan = plans.get_plan(plan_id)
    updated = plans.edit_instructions(
        plan_id,
        translate.instructions_in(
            payload.instructions, plan.total_to_move.currency
        ),
    )
    return translate.plan_out(updated)
