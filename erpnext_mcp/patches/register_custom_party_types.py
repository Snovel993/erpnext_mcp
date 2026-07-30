# SPDX-License-Identifier: MIT
"""Register the `Family` and `Contact` Party Types, once per site.

Listed in `patches.txt` as well as wired to `after_migrate` for the same reason
`set_default_tool_switches` is: the patch runs once and is recorded in the Patch
Log, so an upgrade audit can see when these appeared, while the hook runs on
every migrate, so a site that skipped v0.12.0 still gets them.

Both call the same idempotent seeder, so running both is a no-op the second
time. Adding a Party Type does not touch anything already recorded — every
existing rule and Journal Entry using Shareholder, Employee or Supplier keeps
working unchanged.
"""

from erpnext_mcp.tools import company


def execute() -> None:
	company.ensure_party_types()
