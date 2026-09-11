"""Errors raised by the notifications side of the domain.

Deriving from ``MoneyError`` rather than from ``Exception`` continues the rule
``app.domain.planning.exception`` states: **a new exception belongs under the
existing root, or every catch site in the codebase has to be revisited.** The
CLI catches ``MoneyError`` once, at the top of ``main``, and turns it into
``error: ...`` with exit code 1. A notifications root outside that tree would
mean every message-construction bug escaped as a traceback instead.

The name ``MoneyError`` no longer describes what it holds, and this is the
package that makes that plain - there is no money anywhere in a warning about a
payout. It is left alone deliberately: renaming the root touches every domain
package and README decision 6, which is a change worth making on a day when
nothing else is in flight rather than in the middle of a feature.
"""

from app.domain.money.exception import MoneyError


class NotificationError(MoneyError):
    """Base for every rejection the notifications domain makes."""


# --- OutboundMessage --------------------------------------------------------


class InvalidOutboundMessagePlanIDError(NotificationError):
    pass


class InvalidOutboundMessageDueAtError(NotificationError):
    pass


class InvalidOutboundMessageRecipientError(NotificationError):
    pass


class InvalidOutboundMessageSubjectError(NotificationError):
    pass


class InvalidOutboundMessageBodyError(NotificationError):
    pass


class InvalidOutboundMessageCreatedAtError(NotificationError):
    pass


class InvalidOutboundMessageStatusError(NotificationError):
    pass


class InvalidOutboundMessageAttemptsError(NotificationError):
    pass


class InvalidOutboundMessageLastErrorError(NotificationError):
    pass


class InvalidOutboundMessageSettledAtError(NotificationError):
    pass


class MessageAlreadySettledError(NotificationError):
    pass


# --- Notification -----------------------------------------------------------
# The receipt half, with its own classes rather than reusing the ones above.
# The two aggregates reject different things - an ``OutboundMessage`` without a
# plan id is a bug, and so is a ``Notification`` *with* one - so a shared error
# would have to describe neither. ``MessageAlreadySettledError`` is the one
# exception both raise, because "this message is already finished" means exactly
# the same thing on either side.


class InvalidNotificationEventKeyError(NotificationError):
    pass


class InvalidNotificationKindError(NotificationError):
    pass


class InvalidNotificationSubjectIDError(NotificationError):
    pass


class InvalidNotificationRecipientError(NotificationError):
    pass


class InvalidNotificationSubjectError(NotificationError):
    pass


class InvalidNotificationBodyError(NotificationError):
    pass


class InvalidNotificationCreatedAtError(NotificationError):
    pass


class InvalidNotificationStatusError(NotificationError):
    pass


class InvalidNotificationAttemptsError(NotificationError):
    pass


class InvalidNotificationLastErrorError(NotificationError):
    pass


class InvalidNotificationSettledAtError(NotificationError):
    pass
