class Wallet:
    def apply_deposit(self, amount: Money):
        if self.is_closed:
            raise ValueError("This account is closed")
       
        if amount.amount <= 0:
            raise ValueError("Amount must be greater than zero")

        if amount.currency != self.currency:
            raise ValueError("Unsuppported currency")    

        self._available_balance = self._available_balance.add(amount)
        