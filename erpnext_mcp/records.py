# SPDX-License-Identifier: MIT
"""The three compliance records a task completion produces, and what they share.

A Housing Inspection, a Detector Test and a Water Test are three different pieces
of evidence about three different regimes, and they share exactly one thing: the
shape of what happens when somebody finishes one. That shape is here, once, so
the three controllers cannot drift.

THE WORKFLOW BRANCHES ON WHAT WAS FOUND, NOT ON WHO PRESSED WHAT. This is the
hybrid pattern the 2026-07-31 design conversation landed on, and the branch is
the whole of it:

    findings blank      →  Recorded
    findings present    →  Corrective Action Required

A clean inspection is not a thing anybody should have to route, approve or
transition. It happened, it was clean, the unit's inspection date moves forward,
and the alert that asked for it dismisses itself on the next sweep. The ONLY
records that need a human afterwards are the ones that found something — and
those get their own state, their own Critical alert, and stay there until a later
clean record for the same unit supersedes them or somebody closes them by hand.

Deriving the state from the findings rather than from a transition somebody
chooses is what makes it honest. A worker who has typed "water stain, north wall,
spreading" cannot then mark the inspection as passed, because nothing offers them
the option: the state is a function of the text, computed on every save.

`workflow_state` IS THE FRAMEWORK'S OWN FIELD NAME, deliberately. A site that
wants Frappe's native Workflow doctype layered on top — approvals, roles,
docstatus transitions — attaches one to this field and it works, and
`advance_workflow` (v0.9.0) drives it. What ships is the branch, because a branch
that needs a Workflow record installed before it works is a branch that is off on
every site nobody configured.

THE WRITE-BACK ONLY EVER MOVES A DATE FORWARD. Recording an inspection sets the
unit's `last_habitability_inspection`, which is what dismisses the alert. But a
back-dated record — somebody entering March's walk in July — must not move the
date backwards, because that would re-raise an alert about work that has since
been done. `advance_date` is the whole rule, and it is asserted by a test.

NONE OF THE WRITE-BACK IS OPTIONAL OR DEFERRED. It happens in the controller, on
save, so a record entered in the Desk by somebody who has never heard of MCP
updates the register exactly as a record written by a tool does. A compliance
system where the evidence and the register agree only when the right door was
used is a compliance system that disagrees with itself by August.
"""

from __future__ import annotations

import re

import frappe

#: The three states every one of these records moves through. Not submittable:
#: `docstatus` is a framework concept about immutability, and these records are
#: corrected — a laboratory result that arrives three days later belongs on the
#: sample it describes, not on a second document beside it.
DRAFT = "Draft"
RECORDED = "Recorded"
CORRECTIVE_ACTION_REQUIRED = "Corrective Action Required"

RECORD_STATES = (DRAFT, RECORDED, CORRECTIVE_ACTION_REQUIRED)

#: The states in which the record counts as done — the evidence exists, the
#: register is updated, the alert that asked for it can dismiss. Both of them:
#: an inspection that found something IS an inspection that happened.
COMPLETE_STATES = (RECORDED, CORRECTIVE_ACTION_REQUIRED)

#: FSMA Produce Safety Rule 112.44(b): the generic E. coli criterion for
#: untreated surface water used during growing, as a geometric mean, in
#: CFU (or MPN) per 100 mL. A single sample above it is not by itself a
#: violation of the geometric-mean criterion — it IS the number above which a
#: single result stops being routine and starts being something somebody has to
#: look at, which is what this app is deciding.
ECOLI_ACTION_LEVEL_CFU = 126

#: Words a laboratory uses to mean "we found it".
_PRESENT_WORDS = ("present", "positive", "detect", "fail", "unsatisfactory", "exceed")

#: Words a laboratory uses to mean "we did not".
_ABSENT_WORDS = ("absent", "negative", "none", "nd", "not detected", "pass", "satisfactory", "<1")


def branch_state(findings, draft: bool = False) -> str:
	"""Which state a record with these findings belongs in.

	The whole hybrid workflow, in three lines. `draft` is the one thing a caller
	gets to decide — a record started in the field and finished in the evening is
	legitimate — and it is deliberately the only thing, because everything else
	is a function of what was written down.
	"""
	if draft:
		return DRAFT
	return CORRECTIVE_ACTION_REQUIRED if str(findings or "").strip() else RECORDED


def advance_date(doctype: str, name: str, fieldname: str, value) -> dict:
	"""Move a compliance date forward on a register record. Never backwards.

	Returns what happened, so the tool that triggered it can say so rather than
	leaving a caller to diff the register afterwards. A blank `value` is a no-op:
	a record with no date is not evidence that anything happened on one.
	"""
	out = {
		"doctype": doctype,
		"name": name,
		"fieldname": fieldname,
		"was": None,
		"now": None,
		"advanced": False,
	}
	value = str(value or "").strip()
	if not (doctype and name and fieldname and value):
		return out
	try:
		if not frappe.get_meta(doctype).has_field(fieldname):
			out["skipped"] = f"{doctype} has no field {fieldname!r} on this site"
			return out
		existing = frappe.db.get_value(doctype, name, fieldname)
	except Exception as exc:  # pragma: no cover - a register row that vanished mid-save
		out["skipped"] = f"{type(exc).__name__}: {exc}"
		return out

	out["was"] = str(existing or "") or None
	if existing and str(existing) >= value:
		# A back-dated record. The register already knows about something later,
		# and moving it backwards would re-raise an alert about work that has
		# since been done. The evidence is still filed; the register is not moved.
		out["now"] = out["was"]
		out["skipped"] = (
			f"{doctype} {name} already records {existing} for {fieldname}, which is on or after "
			f"{value}. A back-dated record is filed as evidence but never moves the register "
			"backwards — that would re-raise an alert about work that has since been done."
		)
		return out

	frappe.db.set_value(doctype, name, fieldname, value)
	out["now"] = value
	out["advanced"] = True
	return out


def result_is_detection(result) -> bool | None:
	"""Did this laboratory result find something? None when it cannot be read.

	    Laboratories say the same thing eight ways — "Absent", "<1 MPN/100mL", "0",
	    "Present", "12", "Positive" — and a compliance decision cannot depend on which
	    one a technician typed. So both dialects are read:

	  * WORDS first, because "Present" and "Absent" are unambiguous;
	  * NUMBERS second, where any count above zero is a detection.

	Anything that reads as neither returns None, which callers report as
	"unreadable" rather than silently treating as clean. A result nobody can
	interpret is not a clean result.
	"""
	text = str(result or "").strip().lower()
	if not text:
		return None
	for word in _ABSENT_WORDS:
		if word in text:
			return False
	for word in _PRESENT_WORDS:
		if word in text:
			return True
	number = _first_number(text)
	if number is None:
		return None
	return number > 0


def ecoli_over_action_level(result) -> bool | None:
	"""Is this generic E. coli result above the FSMA action level?

	None where the result carries no number — a presence/absence E. coli result
	is a detection question, which `result_is_detection` answers, and pretending
	an absent count is a count of zero would report a positive presence result as
	comfortably under the limit.
	"""
	number = _first_number(str(result or "").strip().lower())
	if number is None:
		return None
	return number > ECOLI_ACTION_LEVEL_CFU


def _first_number(text: str):
	match = re.search(r"-?\d+(?:\.\d+)?", text or "")
	if not match:
		return None
	try:
		return float(match.group(0))
	except ValueError:  # pragma: no cover - the regex cannot produce this
		return None
