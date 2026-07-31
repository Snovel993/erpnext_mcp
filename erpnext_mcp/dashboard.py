# SPDX-License-Identifier: MIT
"""The Compliance Command Center: what is due, what is late, and how ready you are.

WHY IT IS A FRAPPE DASHBOARD AND NOT A PAGE THIS APP DRAWS. Everything on it is
a standard `Dashboard`, `Dashboard Chart` and `Number Card`, which means it
renders in the Desk with the site's own theme, respects the reading user's
permissions, drills through to the underlying list view on a click, and keeps
working when Frappe changes its front end. A bespoke page would look better for
one release and be a maintenance liability for every one after it. The landing
page is `/app/compliance-command-center`, which is Frappe's own route for the
dashboard named below and not a route this app registers.

WHY IT IS BUILT BY AN INSTALLER RATHER THAN SHIPPED AS `fixtures`. `test_hooks.py`
forbids the `fixtures` hook by name, and this is exactly why. A fixture is
imported by `bench migrate` with no ability to look at what is already there: an
operator who reordered their cards, changed a colour or deleted a chart they did
not want would get it silently put back on the next migrate, every time, forever.
So this checks before it writes — a card that exists is left alone, including all
the ways somebody has since edited it — and only the ones that are genuinely
missing are created. Re-running is a no-op, which is asserted by a test.

THE READINESS SCORE IS THE ONE NUMBER SOMEBODY ACTS ON. Everything else on the
dashboard is a count, and a count only means something to a person who already
knows what normal looks like. "Eleven open warnings" is not actionable on a
Tuesday in July. `audit_readiness_score` — resolved alerts over all alerts raised,
as a percentage — is comparable to yesterday's, which is the property that makes
a number worth putting on a wall. It is computed in `readiness()` rather than in a
chart, because it is a ratio of two collections and a Number Card can only count
one.

WHAT THE SCORE DELIBERATELY DOES NOT DO: reward dismissing things. A human
dismissal counts as resolved — because somebody looked and decided, and that IS
the work — but the reason is mandatory, and `dismissal_quality` reports how many
of the resolutions were auto-dismissals (the condition genuinely went away)
against human ones. An operation whose score is 95% entirely through dismissals
is a different operation from one whose score is 95% because the work got done,
and the dashboard says which.

NOTHING HERE RAISES. It runs inside `after_migrate`. See `install.py`.
"""

from __future__ import annotations

import json

import frappe

from . import compat
from .alerts import ALERT_DOCTYPE, SEVERITY_CRITICAL, SEVERITY_INFO, SEVERITY_WARNING

DASHBOARD = "Dashboard"
CHART = "Dashboard Chart"
CARD = "Number Card"

#: The dashboard's name, and therefore its route: Frappe slugifies this into
#: `/app/compliance-command-center`.
DASHBOARD_NAME = "Compliance Command Center"

MODULE = "ERPNext MCP"


def _live_alert_filters(extra: dict | None = None) -> str:
	"""JSON filters selecting alerts that are actually on the calendar.

	Dismissed is off it. Snoozed is not filtered here and is deliberately left in:
	a Frappe filter cannot express "snoozed_until is in the past OR unset" in one
	clause, and a card that silently omitted snoozed items would let somebody snooze
	their way to a clean dashboard. `get_compliance_calendar` applies the snooze
	rule properly; the card counts what has been raised.
	"""
	filters = {"dismissed": 0}
	filters.update(extra or {})
	return json.dumps(filters)


#: The Number Cards, in the order they read across the top.
#:
#: Critical first because that is the order somebody works them in, and the whole
#: point of the severity split is that a list where everything is urgent is a list
#: nobody reads.
CARDS = (
	{
		"label": "Critical Compliance Alerts",
		"document_type": ALERT_DOCTYPE,
		"function": "Count",
		"filters_json": _live_alert_filters({"severity": SEVERITY_CRITICAL}),
		"color": "#e24c4c",
		"why": "Something has stopped being lawful. Somebody cannot work, or a block cannot be entered.",
	},
	{
		"label": "Warning Compliance Alerts",
		"document_type": ALERT_DOCTYPE,
		"function": "Count",
		"filters_json": _live_alert_filters({"severity": SEVERITY_WARNING}),
		"color": "#f5a623",
		"why": "Needs doing this month. Left alone, most of these become Critical on their own.",
	},
	{
		"label": "Info Compliance Alerts",
		"document_type": ALERT_DOCTYPE,
		"function": "Count",
		"filters_json": _live_alert_filters({"severity": SEVERITY_INFO}),
		"color": "#449cf0",
		"why": "Context. Worth knowing, not worth interrupting anybody for.",
	},
	{
		"label": "Overdue Corrective Actions",
		"document_type": ALERT_DOCTYPE,
		"function": "Count",
		"filters_json": _live_alert_filters({"alert_type": "audit_action_overdue"}),
		"color": "#e24c4c",
		"why": (
			"The one an auditor is certain to ask about on the next visit, and the gate "
			"generate_audit_packet refuses a period on."
		),
	},
	{
		"label": "Certificates Expiring",
		"document_type": ALERT_DOCTYPE,
		"function": "Count",
		"filters_json": _live_alert_filters({"category": "Certifications"}),
		"color": "#f5a623",
		"why": "Each one is a buyer requirement or a licence to operate that is running out.",
	},
	{
		"label": "Open Audit Events",
		"document_type": "Audit Event",
		"function": "Count",
		"filters_json": json.dumps({"corrective_actions_closed": ["is", "not set"]}),
		"color": "#7575ff",
		"why": "Audits whose findings have not all been closed. Not the same as audits that failed.",
	},
)


#: The charts. Two of the three are time series, because the question a compliance
#: dashboard actually answers is "are we getting better or worse", and a snapshot
#: cannot answer it.
CHARTS = (
	{
		"chart_name": "Compliance Alerts by Category",
		"chart_type": "Group By",
		"document_type": ALERT_DOCTYPE,
		"group_by_type": "Count",
		"group_by_based_on": "category",
		"type": "Donut",
		"filters_json": _live_alert_filters(),
		"number_of_groups": 9,
		"why": (
			"Where the work is. Categories are chosen so a whole slice can be cleared in one "
			"afternoon — every housing item is one walk round the camp."
		),
	},
	{
		"chart_name": "Compliance Alerts Raised Over Time",
		"chart_type": "Count",
		"document_type": ALERT_DOCTYPE,
		"based_on": "first_seen",
		"time_interval": "Monthly",
		"timespan": "Last Year",
		"timeseries": 1,
		"type": "Line",
		"why": (
			"Raised, not open — `first_seen` is never moved forward, so this is the shape of "
			"how often the operation drifts out of compliance rather than of how fast it "
			"catches up."
		),
	},
	{
		"chart_name": "Certificate Expirations Ahead",
		"chart_type": "Count",
		"document_type": "Certification",
		"based_on": "expiration_date",
		"time_interval": "Monthly",
		"timespan": "Last Year",
		"timeseries": 1,
		"type": "Bar",
		"why": (
			"The renewal calendar as a shape. Three certificates expiring in one month is a "
			"week of somebody's life, and it is visible a year out."
		),
	},
	{
		"chart_name": "Regulatory Filings by Agency",
		"chart_type": "Group By",
		"document_type": "Regulatory Filing",
		"group_by_type": "Count",
		"group_by_based_on": "agency",
		"type": "Bar",
		"number_of_groups": 11,
		"why": "Which agencies this operation actually deals with, which is rarely the list anybody expects.",
	},
)


def available() -> bool:
	"""Whether this site has the dashboard doctypes at all.

	Frappe has shipped all three since v13, but a site can have them disabled or
	a stripped install can lack them, and a dashboard that cannot be built is not
	a reason for a migration to fail.
	"""
	try:
		return all(compat.doctype_exists(doctype) for doctype in (DASHBOARD, CHART, CARD))
	except Exception:
		return False


def install_command_center() -> dict:
	"""Build or repair the dashboard. Idempotent, and NEVER raises.

	Anything that already exists is left exactly as it is, including every way an
	operator has since edited it. Only genuinely missing pieces are created. See
	the module docstring on why this is an installer rather than a fixture.
	"""
	report = {
		"dashboard": DASHBOARD_NAME,
		"route": "/app/compliance-command-center",
		"created_cards": [],
		"created_charts": [],
		"existing_cards": [],
		"existing_charts": [],
		"failed": [],
		"available": available(),
	}
	if not report["available"]:
		report["note"] = (
			"this site does not have the Dashboard, Dashboard Chart and Number Card doctypes, "
			"so there is nothing to build the command center out of. Every underlying number "
			"is still readable through get_compliance_calendar."
		)
		return report
	if not compat.doctype_exists(ALERT_DOCTYPE):
		report["available"] = False
		report["note"] = (
			"this site has no Compliance Alert DocType yet — it ships with erpnext_mcp and "
			"arrives with `bench migrate`. The dashboard is built on the migrate that creates it."
		)
		return report

	for spec in CARDS:
		_build(CARD, "label", spec, report, "cards")
	for spec in CHARTS:
		_build(CHART, "chart_name", spec, report, "charts")

	_build_dashboard(report)
	return report


def _build(doctype: str, key_field: str, spec: dict, report: dict, bucket: str) -> None:
	name = spec[key_field]
	kind = "cards" if bucket == "cards" else "charts"
	try:
		if frappe.db.exists(doctype, name):
			report[f"existing_{kind}"].append(name)
			return
		if not compat.doctype_exists(spec.get("document_type") or ""):
			report["failed"].append(
				{
					"name": name,
					"reason": (
						f"{spec.get('document_type')!r} is not on this site, so there is nothing "
						"for this to count. It is built on the migrate that installs it."
					),
				}
			)
			return
		doc = frappe.new_doc(doctype)
		doc.set(key_field, name)
		for field, value in spec.items():
			if field in (key_field, "why"):
				continue
			if not compat.has_field(doctype, field):
				# A Frappe version that spells this differently, or does not have it.
				# Losing a chart's colour is cosmetic; losing the chart is not.
				continue
			doc.set(field, value)
		if compat.has_field(doctype, "is_public"):
			doc.is_public = 1
		if compat.has_field(doctype, "module"):
			doc.module = MODULE
		if compat.has_field(doctype, "type") and not doc.get("type"):
			doc.type = "Document Type"
		doc.insert(ignore_permissions=True)
		report[f"created_{kind}"].append(name)
	except Exception as exc:
		report["failed"].append({"name": name, "reason": f"{type(exc).__name__}: {exc}"})


def _build_dashboard(report: dict) -> None:
	"""Create the dashboard and wire the cards and charts onto it.

	An EXISTING dashboard is not rebuilt and not appended to. Somebody who took a
	chart off it took it off on purpose, and a migrate that put it back would be
	the fixture behaviour this whole module exists to avoid.
	"""
	try:
		if frappe.db.exists(DASHBOARD, DASHBOARD_NAME):
			report["dashboard_existed"] = True
			return
		doc = frappe.new_doc(DASHBOARD)
		doc.dashboard_name = DASHBOARD_NAME
		if compat.has_field(DASHBOARD, "module"):
			doc.module = MODULE
		if compat.has_field(DASHBOARD, "is_standard"):
			doc.is_standard = 0
		for spec in CARDS:
			if frappe.db.exists(CARD, spec["label"]):
				doc.append("cards", {"card": spec["label"]})
		for spec in CHARTS:
			if frappe.db.exists(CHART, spec["chart_name"]):
				doc.append("charts", {"chart": spec["chart_name"]})
		doc.insert(ignore_permissions=True)
		report["dashboard_created"] = True
	except Exception as exc:
		report["failed"].append({"name": DASHBOARD_NAME, "reason": f"{type(exc).__name__}: {exc}"})


# ── the readiness score ─────────────────────────────────────────────────────
def readiness(company: str = "", today: str = "") -> dict:
	"""Audit readiness as one comparable number, plus what is behind it.

	resolved / raised, as a percentage, where resolved means dismissed by anybody
	— the sweep because the condition went away, or a person because they looked
	and decided. Both are the work being done; a dismissal that was not the work
	is caught by the mandatory reason on it.

	`dismissal_quality` is the honesty check on the headline. An operation at 95%
	entirely through human dismissals is a different operation from one at 95%
	because the conditions genuinely resolved, and a score that could not tell
	them apart would be a score worth gaming.

	A site with no alerts at all scores 100 and says so. That is not a lie: there
	is nothing outstanding. It is reported with `raised: 0` so nobody mistakes an
	empty site for a well-run one.
	"""
	today = today or frappe.utils.today()
	out = {
		"company": company or None,
		"as_of": today,
		"raised": 0,
		"resolved": 0,
		"open": 0,
		"snoozed": 0,
		"audit_readiness_score": 100.0,
		"by_severity": {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 0, SEVERITY_INFO: 0},
		"by_category": {},
		"dismissal_quality": {"auto_dismissed": 0, "dismissed_by_a_person": 0},
	}
	if not compat.doctype_exists(ALERT_DOCTYPE):
		out["note"] = "this site has no Compliance Alert DocType — run `bench migrate`."
		return out

	filters = {"company": company} if company else {}
	rows = frappe.db.get_all(
		ALERT_DOCTYPE,
		filters=filters,
		fields=["name", "severity", "category", "dismissed", "auto_dismissed", "snoozed_until"],
		limit=20000,
	)
	for row in rows or []:
		out["raised"] += 1
		if frappe.utils.cint(row.get("dismissed")):
			out["resolved"] += 1
			if frappe.utils.cint(row.get("auto_dismissed")):
				out["dismissal_quality"]["auto_dismissed"] += 1
			else:
				out["dismissal_quality"]["dismissed_by_a_person"] += 1
			continue
		out["open"] += 1
		severity = str(row.get("severity") or SEVERITY_WARNING)
		out["by_severity"][severity] = out["by_severity"].get(severity, 0) + 1
		category = str(row.get("category") or "Other")
		out["by_category"][category] = out["by_category"].get(category, 0) + 1
		snoozed = str(row.get("snoozed_until") or "")
		if snoozed and snoozed >= today:
			out["snoozed"] += 1

	if out["raised"]:
		out["audit_readiness_score"] = round(100.0 * out["resolved"] / out["raised"], 1)
	out["by_category"] = dict(sorted(out["by_category"].items()))
	out["note"] = (
		"No compliance alert has ever been raised on this site. The score is 100 because "
		"nothing is outstanding, which is not the same as being audit-ready — run "
		"refresh_compliance_alerts first."
		if not out["raised"]
		else (
			f"{out['resolved']} of {out['raised']} alert(s) resolved. "
			f"{out['dismissal_quality']['auto_dismissed']} resolved themselves when the "
			f"underlying condition cleared; "
			f"{out['dismissal_quality']['dismissed_by_a_person']} were dismissed by a person, "
			"each with a recorded reason."
		)
	)
	return out
