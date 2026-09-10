from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    InvalidDestinationDetailsError,
    InvalidDestinationIdentifierError,
    InvalidDestinationKindError,
    InvalidDestinationNameError,
    MissingDestinationDetailError,
)

#: Which ``details`` keys each rail requires. A bank account is not identifiable
#: without knowing which bank, so the bank code is mandatory. These are a floor,
#: not a ceiling: extra details are permitted, so a later rail can add keys
#: without invalidating anything already stored.
_REQUIRED_DETAILS: dict[DestinationKind, tuple[str, ...]] = {
    DestinationKind.BANK_ACCOUNT: ("bank_code",),
}


@dataclass(frozen=True)
class Destination:
    """Where money is going, outside the wallet.

    A value object, immutable like Money. That immutability is the whole point:
    a settled payment records the destination it was actually sent to, and that
    record must never change. Editing a saved destination later must not rewrite
    history, so a transaction holds a *snapshot* of this value rather than a
    pointer to a live row.

    ``kind`` selects the rail and decides which ``details`` are required;
    ``identifier`` plus ``details`` are what the rail needs to route the money.

        Destination(BANK_ACCOUNT, "0123456789", "Chinedu Okafor",
                    {"bank_code": "058"})

    Like Money, this is frozen - but a frozen dataclass containing a dict is
    only *shallowly* frozen, so ``__post_init__`` seals ``details`` behind a
    read-only mapping and ``__hash__`` is defined by hand (the generated one
    would try to hash that mapping and fail). That defence is what makes the
    value safe to hand around.
    """

    kind: DestinationKind
    identifier: str
    name: str
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.kind, DestinationKind):
            raise InvalidDestinationKindError(
                f"destination kind must be a DestinationKind, not "
                f"{type(self.kind).__name__}"
            )

        if not isinstance(self.identifier, str) or self.identifier.strip() == "":
            raise InvalidDestinationIdentifierError(
                "destination identifier must be a non-empty string"
            )

        if not isinstance(self.name, str) or self.name.strip() == "":
            raise InvalidDestinationNameError(
                "destination name must be a non-empty string"
            )

        if not isinstance(self.details, Mapping):
            raise InvalidDestinationDetailsError(
                f"destination details must be a mapping, not "
                f"{type(self.details).__name__}"
            )

        for key, value in self.details.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidDestinationDetailsError(
                    "destination details must map strings to strings"
                )

        for required in _REQUIRED_DETAILS[self.kind]:
            if required not in self.details:
                raise MissingDestinationDetailError(
                    f"a {self.kind.value} destination requires '{required}'"
                )

        # Seal the mapping: callers keep their own dict and cannot reach in and
        # mutate ours afterwards.
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def detail(self, key: str) -> str | None:
        """Read a rail-specific field. Absent keys are a normal outcome."""
        return self.details.get(key)

    def __hash__(self) -> int:
        # Written by hand because the dataclass-generated __hash__ hashes every
        # field, and MappingProxyType is not hashable. Sorting the items keeps
        # the hash stable regardless of the order the details were supplied in.
        return hash(
            (self.kind, self.identifier, self.name, tuple(sorted(self.details.items())))
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.kind.value}:{self.identifier})"

    def __repr__(self) -> str:
        return (
            f"Destination(kind={self.kind.name}, identifier={self.identifier!r}, "
            f"name={self.name!r}, details={dict(self.details)!r})"
        )
