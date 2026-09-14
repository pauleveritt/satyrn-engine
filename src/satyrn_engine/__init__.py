"""Satyrn Engine — a bounded contract becomes a candidate change.

Phase E1 ships `check`: parse and validate a contract, lint the repository
path it names, and accept or refuse with a named cause and a stable exit
code. E2 adds the Pi adapter; E3 adds isolated candidate delivery; E3.5 adds
the Pi loop breaker; E4 adds one contract-bounded replacement; E5 runs one
real model attempt through that path.
"""

__version__ = "0.1.0"
