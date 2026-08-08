"""Deterministic core. Zero network, zero keys, pure functions.

Nothing in this package may import from slackline.sources, slackline.llm,
or perform any I/O. All data arrives as function arguments. Enforced by
tests/contract/test_purity.py.
"""
