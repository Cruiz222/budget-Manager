"""The HTTP presentation, sitting beside the CLI rather than above it.

Nothing here decides anything about money. Every route is a few lines that read
a request, call one use case, and hand the result to ``translate`` - which is the
intended shape rather than a stage of work, because the moment a rule about money
lives in a route it stops being true for the command line and the scheduler.

    app/presentation/
        cli.py          the terminal
        api/            this package

``create_app`` is the composition root for this presentation, in the same sense
``app.composition_root`` is for the use cases: it is the one place concrete
storage is chosen, and it takes that choice as an optional argument so a test can
point it somewhere else. Two ways to configure storage would be one too many.
"""
