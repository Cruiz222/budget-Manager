"""The derived keys that make a notification exactly once.

A notification has no identity of its own. It is about an *event*, and the event
already has a name - a plan and the outcome it reached, or a ledger row - so
handing out a generated id would invent a second identity, free to disagree with
the first. This is decision 7 applied to a second kind of record: idempotency
keys are derived, never generated.

Deriving it is what makes the claim work. ``NotificationRepository.enqueue``
inserts on this key and reports whether it was the one that inserted, so a
duplicate is not rejected by a check somewhere - it is *unrepresentable*, because
the same event derives the same key and the primary key accepts it only once.

**Every key has the same three parts: ``<kind>:<scope>:<identity>``.**

The *kind* is first, and it is doing real work rather than decorating. Two
different events can be about the same occurrence: a run that is blocked at noon
and later retried successfully reaches both outcomes for one ``due_at``, and the
store updates a single ``plan_runs`` row for it (see ``PlanRun``). Those are two
things that happened to the user and warrant two messages, so they must derive
two keys - which they do, because the kind differs. Meanwhile the *same* outcome
reached twice, such as a plan blocked, resumed unfunded, and blocked again,
collapses to one key and is announced once. That is also intended: the user has
already been told, and telling them again on every resume teaches them to ignore
it. Getting this wrong is not loud - it is a message that silently never sends.

The *scope* separates plan events from wallet events. Both kinds of subject are
``UUID``s, so without it a plan event and a wallet event could in principle derive
the same string.

**The text of a key is a persisted contract.** It is written to the database, so
changing a format tomorrow does not rewrite the keys written today - and two
different strings for one event do not collide, which would let that event be
announced twice. ``ExecutePlanRun._reference`` documents the same trap for the
ledger and draws the same conclusion: the safe time to change a key format is
when no row can exist that uses the old one.
"""

import uuid
from datetime import datetime

from .notificationKind import NotificationKind


def for_plan_event(
    kind: NotificationKind, plan_id: uuid.UUID, due_at: datetime
) -> str:
    """The key for one outcome, of one occurrence, of one plan.

    The occurrence's *moment* is part of the key for the same reason it is in
    ``ExecutePlanRun._reference``: so that the same plan firing next month is a
    different event and not a duplicate.

    Note this is **one key per run, not one per instruction**. A plan paying
    three accounts is one thing that happened to the user, and it must be one
    email - not three that arrive together and have to be reassembled by hand.
    """
    return f"{kind.value}:plan:{plan_id}:{due_at.isoformat()}"


def for_wallet_event(
    kind: NotificationKind, wallet_id: uuid.UUID, internal_reference: str
) -> str:
    """The key for one ledger row.

    ``internal_reference`` is already ``UNIQUE`` on ``transactions``, so this key
    inherits exactly that uniqueness rather than inventing a second guarantee
    that could drift away from it.

    That matters on the idempotent path: repeating an operation with the same
    ``--ref`` returns the *existing* transaction rather than moving money twice,
    and because the returned row derives the same kind and the same reference as
    the first attempt, the duplicate enqueue inserts nothing. One deposit, one
    receipt.
    """
    return f"{kind.value}:wallet:{wallet_id}:{internal_reference}"
