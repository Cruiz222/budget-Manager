from dataclasses import dataclass
from .currency import Currency
import uuid
from .money import Money
from .walletStatus import WalletStatus
from .exception import (
    InsufficientFundsError,
    WalletFrozenError,
    WalletClosedError,
    ZeroAmountWithdrawalError,
    NegativeAmountWithdrawalError,
    CurrencyMismatchError

)
@dataclass
class Wallet:
    wallet_id: uuid.UUID
    user_id: uuid.UUID
    currency: Currency
    status: WalletStatus
    _available_balance: Money
    _locked_balance: Money

    @property
    def available_balance(self) -> Money:
        return self._available_balance

    def apply_deposit(self, amount: Money):
       
        if amount.amount <= 0:
            raise ValueError("Amount must be greater than zero")

        if amount.currency != self.currency:
            raise ValueError("Unsuppported currency")    
        
        if self.status == WalletStatus.CLOSED:
            raise ValueError("this account is Closed")

        self._available_balance = self._available_balance + amount


    def withdraw(self, amount: Money):
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")
        
        if self.status == WalletStatus.FROZEN:
            raise WalletFrozenError("this wallet is frozen")
        
        if amount.amount == 0:
            raise ZeroAmountWithdrawalError("amount must be greater than zero")
        
        if amount.amount < 0:
            raise NegativeAmountWithdrawalError("amount can not be negative")
        
        if self.currency != amount.currency:
            raise CurrencyMismatchError("currency must be thes ame")
        
        if self._available_balance < amount:
            raise InsufficientFundsError
        
        self._available_balance = self._available_balance - amount


    def lock_funds(self, amount: Money):
        self._locked_balance = self._locked_balance + amount
        self._available_balance = self._available_balance - amount