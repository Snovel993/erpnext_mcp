# SPDX-License-Identifier: MIT
"""Seed any settings field that has no stored value with its declared default.

Listed in `patches.txt` as well as wired to `after_migrate` because the two fire
in different situations: the patch runs once per site and is recorded in the
Patch Log (so it shows up in an upgrade audit), while the hook runs on every
migrate (so a field added by a version an operator skipped still gets seeded).
Both call the same idempotent function, so running both is a no-op the second
time.
"""

from erpnext_mcp import settings


def execute() -> None:
	settings.seed_defaults()
