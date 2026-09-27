import httpx

from app.domain.payments.exception import (
    InvalidProviderAnswerError,
    PaymentProviderError,
    PaymentProviderUnavailableError,
)

from app.domain.payments.virtualAccountDetails import (
    VirtualAccountDetails,
)
from app.domain.payments.virtualAccountProvider import (
    VirtualAccountProvider,
)
from app.infrastructure.payments.paystack_payment_provider import (
    BASE_URL,
    DEFAULT_TIMEOUT,
)
from app.domain.payments.exception import (
    PaymentProviderError,
    PaymentProviderUnavailableError,
)


class PaystackVirtualAccountProvider(VirtualAccountProvider):
    def __init__(
        self,
        secret_key: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self._secret_key = secret_key
        self._timeout = timeout

    def create_customer(self, *, email: str, phone: str, first_name: str, last_name: str) -> str:
        body = self._request(
            "/customer",
            payload={
            "email": email,
            "phone": phone,
            "first_name": first_name,
            "last_name": last_name,
            },
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise InvalidProviderAnswerError(
                "the virtual account provider returned no customer"
            )

        customer_code = data.get("customer_code")
        if (
            not isinstance(customer_code, str)
            or customer_code.strip() == ""
        ):
            raise InvalidProviderAnswerError(
                "the virtual account provider returned a customer "
                "without a customer code"
            )

        return customer_code    

    def create_virtual_account(
        self,
        *,
        customer_code: str,
    ) -> VirtualAccountDetails:
        body = self._request(
            "/dedicated_account",
            payload={
                "customer": customer_code,
            },
        )
        data = body.get("data")
        if not isinstance(data, dict):
            raise InvalidProviderAnswerError(
                "the virtual account provider returned no bank account"
            )

        bank = data.get("bank")
        if not isinstance(bank, dict):
            raise InvalidProviderAnswerError(
                "the virtual account provider returned no bank"
            )

        account_number = data.get("account_number")
        account_name = data.get("account_name")
        bank_name = bank.get("name")

        details = {
            "account number": account_number,
            "account name": account_name,
            "bank name": bank_name,
        }

        for label, value in details.items():
            if not isinstance(value, str) or value.strip() == "":
                raise InvalidProviderAnswerError(
                    "the virtual account provider returned an account "
                    f"without a valid {label}"
                )

        return VirtualAccountDetails(
            account_number=account_number,
            account_name=account_name,
            bank_name=bank_name,
        )

        return VirtualAccountDetails(
            account_number=data["account_number"],
            account_name=data["account_name"],
            bank_name=data["bank"]["name"],
        )


    def _request(
        self,
        path: str,
        *,
        payload: dict,
    ) -> dict:
        try:
            response = httpx.request(
                "POST",
                f"{BASE_URL}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as failure:
            raise PaymentProviderUnavailableError(
                "could not reach the virtual account provider: "
                f"{type(failure).__name__}"
            ) from failure

        if response.status_code >= 500:
            raise PaymentProviderUnavailableError(
                "the virtual account provider failed this call with "
                f"{response.status_code}"
            )

        if 400 <= response.status_code < 500:
            raise PaymentProviderError(
                "the virtual account provider refused this call with "
                f"{response.status_code}"
            )

        try:
            return response.json()
        except ValueError as failure:
            raise PaymentProviderUnavailableError(
                "the virtual account provider returned a response "
                "that was not JSON"
            ) from failure
