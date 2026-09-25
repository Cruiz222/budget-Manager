from abc import ABC, abstractmethod

from app.domain.payments.virtualAccountDetails import VirtualAccountDetails


class VirtualAccountProvider(ABC):
    """A provider capable of creating external-transfer receiving accounts.

    Customer creation and bank-account creation are separate operations so the
    application can persist the provider customer code between network calls.
    That saved code becomes the retry checkpoint if account creation fails.
    """

    @abstractmethod
    def create_customer(
        self,
        *,
        email: str,
        phone: str,
        first_name: str,
        last_name: str,
    ) -> str:
        """Create the provider customer and return its stable customer code."""
        pass

    @abstractmethod
    def create_virtual_account(
        self,
        *,
        customer_code: str,
    ) -> VirtualAccountDetails:
        """Create a receiving account for an existing provider customer."""
        pass