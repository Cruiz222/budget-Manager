"""Serialization rules for the SQLite repositories.

Maps domain values to the TEXT columns they are stored in, and back:

  uuid.UUID       <-> str(uuid)
  enum member     <-> its name, e.g. "ACTIVE", "SUCCESSFUL"
  Money amount    <-> a fixed two-decimal string, e.g. "10000.00"
  datetime        <-> ISO-8601 string
  date            <-> ISO-8601 date string, e.g. "2026-01-31"
  optional enum   <-> its name, or NULL when absent
  metadata dict   <-> JSON
  Destination     <-> JSON (see below)
  Schedule        <-> JSON
  Instruction     <-> JSON, as a list, inside a plan's row

Money is stored as TEXT, never as REAL: binary floats cannot represent money
exactly, while a decimal string round-trips to the exact Decimal we started
from.

Two rules decide what becomes JSON rather than columns of its own, and both are
about *shape stability*:

- A Destination is JSON because its shape is deliberately open - a bank account
  carries a bank code, a future rail carries something else - so fixed columns
  could not hold it without a schema change per rail.
- A Schedule and a plan's Instructions are JSON because they are value objects
  *inside* the SavingsPlan aggregate: no identity of their own, never looked up
  alone, always loaded and written with their root. Keeping them in the plan's
  own row makes the aggregate atomic by construction - there is no bug that can
  leave a plan holding three of its five payments.
"""

import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.money import Money
from app.domain.planning.cadence import Cadence
from app.domain.planning.instruction import Instruction
from app.domain.planning.plannedAction import PlannedAction
from app.domain.planning.schedule import Schedule


def uuid_to_text(value: uuid.UUID) -> str:
    return str(value)


def text_to_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def enum_to_text(member) -> str:
    return member.name


def text_to_enum(enum_cls, value: str):
    return enum_cls[value]


def money_to_text(money: Money) -> str:
    return f"{money.amount:.2f}"


def text_to_money(value: str, currency: Currency) -> Money:
    return Money(Decimal(value), currency)


def datetime_to_text(value: datetime) -> str | None:
    return value.isoformat() if value is not None else None


def text_to_datetime(value: str) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def date_to_text(value: date | None) -> str | None:
    """A date, when absent, stays absent - unlike a datetime field that is required."""
    return value.isoformat() if value is not None else None


def text_to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def optional_enum_to_text(member) -> str | None:
    """Store an enum member by name, or NULL.

    ``enum_to_text`` would raise on None, so an optional enum needs this
    separate spelling rather than a truthiness check sprinkled at call sites.
    """
    return enum_to_text(member) if member is not None else None


def text_to_optional_enum(enum_cls, value: str | None):
    return text_to_enum(enum_cls, value) if value is not None else None


def metadata_to_text(metadata) -> str:
    return json.dumps(dict(metadata))


def text_to_metadata(value: str) -> dict:
    return json.loads(value) if value is not None else {}


def destination_to_dict(destination: Destination) -> dict:
    """The JSON shape of a destination, as a plain dict.

    Split out from ``destination_to_text`` so a destination nested inside
    another JSON document - an instruction, say - can be embedded directly
    rather than serialised to a string and immediately parsed again.
    """
    return {
        "kind": enum_to_text(destination.kind),
        "identifier": destination.identifier,
        "name": destination.name,
        "details": dict(destination.details),
    }


def dict_to_destination(payload: dict) -> Destination:
    return Destination(
        kind=text_to_enum(DestinationKind, payload["kind"]),
        identifier=payload["identifier"],
        name=payload["name"],
        details=payload["details"],
    )


def destination_to_text(destination: Destination | None) -> str | None:
    """Store a destination as JSON. Absent stays absent.

    The kind is stored under its enum *name* ("BANK_ACCOUNT"), matching how
    every other enum in the schema is written, so the same rename-migration
    rule applies to it.
    """
    if destination is None:
        return None
    return json.dumps(destination_to_dict(destination))


def text_to_destination(value: str | None) -> Destination | None:
    """Rebuild a destination from its JSON form. Absent stays absent."""
    if value is None:
        return None
    return dict_to_destination(json.loads(value))


def schedule_to_text(schedule: Schedule) -> str:
    """Store a schedule as JSON: its cadence name and its anchor date.

    The anchor is stored, not a "next due" date. That is the drift guard made
    durable - a plan reloaded from disk recomputes every occurrence from this
    one date, exactly as it did in memory.
    """
    return json.dumps(
        {
            "cadence": enum_to_text(schedule.cadence),
            "anchor": schedule.anchor.isoformat(),
        }
    )


def text_to_schedule(value: str) -> Schedule:
    payload = json.loads(value)
    return Schedule(
        cadence=text_to_enum(Cadence, payload["cadence"]),
        anchor=date.fromisoformat(payload["anchor"]),
    )


def instruction_to_dict(instruction: Instruction) -> dict:
    """The JSON shape of one instruction.

    The amount's currency travels with the amount rather than being assumed
    from the plan, because ``Instruction`` holds a ``Money`` and a Money is
    meaningless without its currency. It is redundant with the plan's
    instructions being homogeneous - deliberate redundancy, so the document is
    readable on its own.
    """
    return {
        "action": enum_to_text(instruction.action),
        "amount": money_to_text(instruction.amount),
        "currency": enum_to_text(instruction.amount.currency),
        "label": instruction.label,
        "destination": (
            destination_to_dict(instruction.destination)
            if instruction.destination is not None
            else None
        ),
    }


def dict_to_instruction(payload: dict) -> Instruction:
    currency = text_to_enum(Currency, payload["currency"])
    destination = payload["destination"]
    return Instruction(
        action=text_to_enum(PlannedAction, payload["action"]),
        amount=text_to_money(payload["amount"], currency),
        label=payload["label"],
        destination=(
            dict_to_destination(destination) if destination is not None else None
        ),
    )


def instructions_to_text(instructions) -> str:
    """Store a plan's instruction list as one JSON array.

    A list, not an object keyed by anything: instructions have no identity, so
    giving them keys would invent an identity they do not have.
    """
    return json.dumps([instruction_to_dict(instruction) for instruction in instructions])


def text_to_instructions(value: str) -> tuple[Instruction, ...]:
    """Rebuild a plan's instructions as the tuple the aggregate requires."""
    return tuple(dict_to_instruction(item) for item in json.loads(value))
