"""The notifications side of the domain: what the system owes the outside world.

This package depends on nothing else in the domain, and nothing else in the
domain depends on it. That direction is worth holding: a warning mentions a
``plan_id``, but a plan knows nothing about warnings, and the scheduler that
pays a plan never reads a message. Keeping the arrow pointing this way is what
stops delivery state from ever becoming something a payout can be conditional
on.
"""
