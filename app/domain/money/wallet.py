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
    InvalidWalletCurrencyError,
    InvalidWalletStatusError,
    InvalidWalletAvailableBalanceError,
    InvalidWalletLockedBalanceError,
    InvalidWalletIDError,
    InvalidWalletUserIDError


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


    def payout_from_locked(self, amount: Money):
        """Send money out of the wallet, spending the locked balance.

        This is the counterpart of withdraw() for reserved funds. The
        distinction that matters: lock_funds and release_funds only *move*
        money between the two balances, so the wallet still holds it all.
        payout_from_locked reduces what the wallet holds - value actually
        leaves - which is why it refuses a frozen wallet exactly as withdraw()
        does. Freezing stops value from leaving; it does not stop internal
        reshuffling between available and locked.
        """
        if self.status == WalletStatus.CLOSED:
            raise WalletClosedError("this wallet is closed")

        if self.status == WalletStatus.FROZEN:
            raise WalletFrozenError("this wallet is frozen")

        if amount.amount <= 0:
            raise InvalidAmountError("amount must be greater than zero")

        if self.currency != amount.currency:
            raise CurrencyMismatchError("currency must be the same")

        if self._locked_balance < amount:
            raise InsufficientFundsError("insufficient locked balance")

        self._locked_balance = self._locked_balance - amount


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
        if not isinstance(self.wallet_id, uuid.UUID):
            raise InvalidWalletIDError("invalid wallet id")

        if not isinstance(self.user_id, uuid.UUID):
            raise InvalidWalletUserIDError("invalid user id")

        if not isinstance(self.currency, Currency):
           raise InvalidWalletCurrencyError("invalid wallet currency")
        
        if not isinstance(self.status, WalletStatus):
            raise InvalidWalletStatusError("invalid wallet status")
        
        if not isinstance(self._available_balance, Money):
            raise InvalidWalletAvailableBalanceError("invalid wallet balance")
        
        if not isinstance(self._locked_balance, Money):
            raise InvalidWalletLockedBalanceError("invalid locked balance")
        
        if self._available_balance.currency != self.currency:
            raise CurrencyMismatchError("available balance currency must match wallet currency")
        
        if self._locked_balance.currency != self.currency:
            raise CurrencyMismatchError("locked balance currency must match wallet currency")
              
    