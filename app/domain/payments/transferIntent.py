from dataclasses import dataclass

from app.domain.payments.exception import InvalidTransferIntentError


@dataclass(frozen=True)
class TransferIntent:
    provider_reference: str

    def __post_init__(self):
        if not isinstance(self.provider_reference, str):
            raise InvalidTransferIntentError(
                "provider reference must be a string"
        )

        if self.provider_reference.strip() == "":
            raise InvalidTransferIntentError(
                "provider reference must not be empty"
        )