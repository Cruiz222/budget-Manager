"""Use cases that get queued messages out to the world.

The counterpart to ``app.application.planning``: that side decides *what* the
user should be told, and this side is what actually tells them. The split is the
transactional outbox, expressed as two packages - one writes the message in the
same breath as the decision to warn, the other drains the queue later, with a
network call in between that neither side has to know about.
"""
