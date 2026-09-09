"""Serialization rules for the SQLite repositories.

Maps domain values to the TEXT columns they are stored in, and back:

  uuid.UUID       <-> str(uuid)
  enum member     <-> its name, e.g. "ACTIVE", "SUCCESSFUL"
  Money amount    <-> a fixed two-decimal string, e.g. "10000.00"
  datetime        <-> ISO-8601 string
  metadata dict   <-> JSON

Money is stored as TEXT, never as REAL: binary floats cannot represent money
exactly, while a decimal string round-trips to the exact Decimal we started
from.
"""

import json
import uuid
from datetime import datetime
from decimal import Decimal

from app.domain.money.currency import Currency
from app.domain.money.money import Money


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


def metadata_to_text(metadata) -> str:
    return json.dumps(dict(metadata))


def text_to_metadata(value: str) -> dict:
    return json.loads(value) if value is not None else {}
