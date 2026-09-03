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
    CurrencyMismatchError,
    InvalidAmountError,
    WalletAlreadyFrozenError,
    WalletAlreadyClosedError,
    WalletAlreadyActiveError,
    InvalidWalletCurrencyError


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
    
    @property
    def locked_balance(self) -> Money:
        return self._locked_balance

    def apply_deposit(self, amount: Money):
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError
        
        if amount.currency != self.currency:
            raise CurrencyMismatchError  
       
        if amount.amount <= 0:
            raise InvalidAmountError  

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
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError
        
        if amount.amount <= 0:
            raise InvalidAmountError
        
        if self.currency != amount.currency:
            raise CurrencyMismatchError 
        
        if self._available_balance < amount:
            raise InsufficientFundsError   
        
        self._locked_balance = self._locked_balance + amount

        self._available_balance = self._available_balance - amount



    def release_funds(self, amount: Money):
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError

        if amount.amount <= 0:
            raise InvalidAmountError  

        if self.currency != amount.currency:
            raise CurrencyMismatchError  
        
        if self._locked_balance < amount:
            raise InsufficientFundsError
        
        self._locked_balance = self._locked_balance - amount

        self._available_balance = self._available_balance + amount    


    def freeze(self):
        if self.status == WalletStatus.FROZEN:
            raise WalletAlreadyFrozenError
        
        if self.status == WalletStatus.CLOSED:
            raise WalletAlreadyClosedError
        
        self.status = WalletStatus.FROZEN



    def unfreeze(self):
        if self.status == WalletStatus.ACTIVE:
            raise WalletAlreadyActiveError

        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError    
        
        self.status = WalletStatus.ACTIVE    



    def __post_init__(self):
        if not isinstance(self.currency, Currency):
           raise InvalidWalletCurrencyError("invalid wallet currency")
              
    