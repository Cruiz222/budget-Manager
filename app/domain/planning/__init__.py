"""The planning side of the domain: what a user intends to happen, and when.

This package depends on ``app.domain.money`` (it uses ``Money`` and
``Destination``) but ``app.domain.money`` never depends on this one. The
direction matters: the ledger records what happened, and must stay ignorant of
what was *planned* - otherwise a plan edit could change how history reads.
"""
