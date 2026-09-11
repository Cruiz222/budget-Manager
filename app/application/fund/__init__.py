"""Use cases for named locked funds (pots).

One package per operation family, like ``deposit``/``withdraw``/``payout``. The
three operations that move money are ``WalletOperation`` subclasses and so get
the shared flow for free - amount validation, idempotency by
``internal_reference``, the PENDING -> SUCCESSFUL/FAILED ledger row, and the
re-raise of a ``MoneyError``. Opening and extending a pot move nothing, so they
are plain service methods rather than operations; see ``WalletService``.
"""
