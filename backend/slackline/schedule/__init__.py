"""Schedule package.

``build_gtfs.py`` is the OFFLINE build step (network allowed, build time only).
``index.py`` is the pure read-only loader used at runtime; it does no network
and is covered by the purity contract test.
"""
