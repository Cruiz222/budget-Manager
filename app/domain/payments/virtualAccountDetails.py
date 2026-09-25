from dataclasses import dataclass

from app.domain.payments.exception import InvalidVirtualAccountError


@dataclass(frozen=True)
class VirtualAccountDetails:
    """Provider-issued bank details for receiving external transfers."""

    account_number: str
    account_name: str
    bank_name: str

    def __post_init__(self) -> None:
        required_fields = {
            "account_number": self.account_number,
            "account_name": self.account_name,
            "bank_name": self.bank_name,
        }

        for field_name, value in required_fields.items():
            if not isinstance(value, str) or value.strip() == "":
                raise InvalidVirtualAccountError(
                    f"virtual account details require {field_name}"
                )