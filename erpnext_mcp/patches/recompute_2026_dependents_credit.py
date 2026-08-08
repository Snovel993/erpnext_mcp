# SPDX-License-Identifier: MIT
"""v0.48.1. Restate every 2026 W-4's dependents credit at the 2026 amount.

WHAT WAS WRONG. `W4Form._compute_dependents` multiplied the qualifying-children
count by a flat $2,000 — the 2020-2025 figure. The 2026 Form W-4 says $2,200.
Every W-4 filed for tax year 2026 before this release therefore holds a Step 3
total that is $200 a child short, and two things read that stored number rather
than recomputing it: `withholding.py`, which subtracts `total_dependents_credit`
from the tentative tax, and `w4_pdf.py`, which prints it into box 3 of the
government form. So the employee was over-withheld AND their filed form was
wrong.

WHY A PATCH IS NEEDED AT ALL. The credit is a stored column computed in
`validate`. Fixing the controller fixes the next save and nothing else — an
Active W-4 nobody touches keeps its stale total forever, and nobody touches an
Active W-4, because the whole design is that a change supersedes rather than
edits. Left alone, the bug would outlive the fix on every existing row.

IT TOUCHES ONLY TAX YEAR 2026 AND LATER. A 2025 form claiming $2,000 a child was
correct for its edition and is left exactly as it is; see `UNDER_17_CREDIT` in
the controller for why the amount is keyed to the row's own year. Rows already
holding the right total are counted and skipped, so re-running is a no-op.

IT RECOMPUTES RATHER THAN ADDING $200 A CHILD. A row whose stored total was
edited by hand, or written before some earlier field existed, is not reliably
"the old formula's output" — recomputing from the counts and the year makes the
column agree with the controller whatever it held before.

NEVER RAISES. It runs inside `bench migrate`, where an exception aborts the
migration for the whole bench.
"""

import frappe

from .. import compat
from ..erpnext_mcp.doctype.w_4_form.w_4_form import (
	OTHER_DEPENDENT_CREDIT,
	UNDER_17_CREDIT,
	credit_for,
)

W4 = "W-4 Form"
FIRST_YEAR = 2026
FIELDS = ("dependents_under_17_amount", "other_dependents_amount", "total_dependents_credit")


def execute() -> None:
	for line in report_lines(recompute_2026_dependents_credit()):
		print(line)


def recompute_2026_dependents_credit() -> dict:
	"""Restate the three Step 3 columns on every W-4 for 2026 or later."""
	report = {"restated": [], "already_right": 0, "skipped": ""}
	if not compat.doctype_exists(W4) or not compat.has_field(W4, "dependents_under_17_amount"):
		report["skipped"] = f"this site has no {W4} with a dependents credit column yet."
		return report

	try:
		rows = frappe.db.get_all(
			W4,
			filters={"tax_year": (">=", FIRST_YEAR)},
			fields=[
				"name",
				"employee",
				"tax_year",
				"status",
				"dependents_under_17_count",
				"other_dependents_count",
				*FIELDS,
			],
			limit=100000,
		)
	except Exception as exc:  # pragma: no cover - reported, never raised
		report["skipped"] = f"the W-4s could not be read — {type(exc).__name__}: {exc}"
		return report

	for row in rows or []:
		under_17 = int(row.get("dependents_under_17_count") or 0)
		other = int(row.get("other_dependents_count") or 0)
		year = row.get("tax_year")
		wanted = {
			"dependents_under_17_amount": under_17 * credit_for(UNDER_17_CREDIT, year),
			"other_dependents_amount": other * credit_for(OTHER_DEPENDENT_CREDIT, year),
		}
		wanted["total_dependents_credit"] = (
			wanted["dependents_under_17_amount"] + wanted["other_dependents_amount"]
		)
		if all(float(row.get(field) or 0) == float(wanted[field]) for field in FIELDS):
			report["already_right"] += 1
			continue
		was = float(row.get("total_dependents_credit") or 0)
		try:
			for field, value in wanted.items():
				# update_modified=False: the credit did not change because anyone
				# touched the form, and stamping it would make an untouched W-4
				# look freshly edited in every list sorted by `modified`.
				frappe.db.set_value(W4, str(row["name"]), field, value, update_modified=False)
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["skipped"] = f"{row['name']} could not be restated — {type(exc).__name__}: {exc}"
			continue
		report["restated"].append(
			{
				"w4": str(row["name"]),
				"employee": str(row.get("employee") or ""),
				"tax_year": int(year or 0),
				"status": str(row.get("status") or ""),
				"children": under_17,
				"was": was,
				"now": float(wanted["total_dependents_credit"]),
			}
		)
	return report


def report_lines(report: dict) -> list:
	"""What the migrate prints. Every restated W-4 by name — silence would be worse.

	A payroll run that has already used the old credit is NOT rewritten by this
	patch and cannot be: the slips are posted. The operator is told, because the
	difference is real money and only they can decide whether it is worth a
	correction or whether the next run absorbing it is enough.
	"""
	lines = []
	if report.get("skipped"):
		lines.append(f"erpnext_mcp: the 2026 dependents credit was not recomputed — {report['skipped']}")
	for entry in report.get("restated") or ():
		lines.append(
			f"erpnext_mcp: W-4 {entry['w4']} ({entry['employee']}, tax year {entry['tax_year']}, "
			f"{entry['status']}) claimed {entry['children']} qualifying children at the old $2,000 "
			f"— its Step 3 total was {entry['was']:,.2f} and is now {entry['now']:,.2f}, the 2026 "
			"form's $2,200 a child. Withholding from the next payroll run uses the corrected "
			"figure. ANY SLIP ALREADY POSTED FROM THIS W-4 USED THE OLD CREDIT and over-withheld; "
			"this patch does not touch posted slips. Reprint the form with render_w4_pdf — the "
			"copy on file has the old number in box 3."
		)
	if report.get("already_right"):
		lines.append(
			f"erpnext_mcp: {report['already_right']} W-4(s) for 2026 or later already held the "
			"correct dependents credit and were left alone."
		)
	return lines
