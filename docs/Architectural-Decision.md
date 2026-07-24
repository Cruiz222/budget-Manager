## Decision

The domain will use enums instead of raw strings for fixed business concepts.

## Reason

Enums prevent invalid values, improve readability, provide IDE auto-completion, and make the domain model self-documenting.

**Examples**
CurrencyCode
SavingsPlanStatus
TransactionType
ReleaseStrategy
UserRole


## BuBusiness Rules

Money can be created.
Money stores amount.
Money stores currency.
Money is immutable.

Alread*y four tests.

**Addition**

Rules

Same currency succeeds.
Different currency fails.
Original objects unchanged.
Returns new object.

Another four tests.

**Subtraction**

Rules

Same currency works.
Different currency fails.
Negative result allowed.

**Multiplication**

Rules

Integer works.
Decimal works.
Float rejected.
Money × Money rejected.

**Division**

Rules

Integer works.
Decimal works.
Divide by zero raises.
Money ÷ Money rejected.
Equality

Rules

₦10

==

₦10.00

True.

₦10

!=

$10

True.

**Comparison**

Rules

Same currency.

Allowed.

Different currency.

Rejected.