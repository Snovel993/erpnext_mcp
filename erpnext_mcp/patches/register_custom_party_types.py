# SPDX-License-Identifier: MIT
"""Register the `Family` and `Contact` Party Types, once per site.

Listed in `patches.txt` as well as wired to `after_migrate` for the same reason
`set_default_tool_switches` is: the patch runs once and is recorded in the Patch
Log, so an upgrade audit can see when these appeared, while the hook runs on
every migrate, so a site that skipped this version still gets them.

Both call the same idempotent seeder, so running both is a no-op the second
time. Adding a Party Type does not touch anything already recorded — every
existing rule and Journal Entry using Shareholder, Employee or Supplier keeps
working unchanged.

WHY THIS PATCH IS IN `post_model_sync`, AND WHY THAT IS LOAD-BEARING. A Party
Type's name has to be the name of a real DocType, and `Family` is a DocType this
app ships. `post_model_sync` runs AFTER `bench migrate` has synced every app's
DocType JSON, so by the time this executes the Family table exists. Moving it to
`pre_model_sync` would reintroduce exactly the failure v0.12.1 fixes.

WHY IT PRINTS RATHER THAN RAISES. v0.12.0 let a `LinkValidationError` out of
`ensure_party_types` and it aborted the whole `bench migrate` — not just this
app's, the bench's. The operator got a traceback instead of an upgrade, and
because `after_migrate` never ran, the settings defaults for that release's new
tools were never seeded either. A party type that cannot be registered is worth
saying out loud on the console; it is not worth taking a migration down over.
"""

from erpnext_mcp.tools import company


def execute() -> None:
	result = company.ensure_party_types()
	for name, why in sorted((result.get("skipped") or {}).items()):
		print(f"erpnext_mcp: Party Type {name!r} was not registered — {why}.")
