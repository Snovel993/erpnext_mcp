# SPDX-License-Identifier: MIT
"""The nine compliance rules, and the state that makes each one ripe.

EVERY RULE IN THIS FILE READS STATE AND NONE OF THEM READS A CALENDAR. That is
the whole point of the file, so it is worth being concrete about what the
difference looks like in code:

    chronological:  if today.day == 1: warn about water testing
    kairotic:       if this block was sprayed recently AND its agricultural
                    water has not been tested in ninety days: warn

The first fires on fallow ground, on ground tested last week, and on ground
nobody has touched since 2019, twelve times a year. By March it is noise and by
June nobody reads it. The second fires on exactly the blocks where FSMA Subpart E
is engaged, on exactly the days it is engaged, and stops firing by itself the day
the test is done. One of those is a compliance system and the other is a
recurring appointment.

THE THRESHOLDS ARE LEAD TIMES, NOT PREFERENCES. Thirty days for a critical
certificate expiry is how long a renewal actually takes to come back from an
agency; ninety days for agricultural water is the Subpart E testing cadence; three
hundred and sixty-five for a habitability inspection is the annual walk a camp
gets. Every number in this file is the real interval something takes in the world,
and each one is annotated with which. A number nobody can say the provenance of
should not be here.

SEVERITY MEANS THE SAME THING IN ALL NINE. Critical blocks something today: a
person cannot lawfully work, a block cannot lawfully be entered, a certificate
that a buyer requires has lapsed. Warning means it needs doing this month.
Info is context. A rule that raises Critical for something that merely needs
attention devalues the word for the rules that meant it, so the two rules that
can raise Critical do so only when the underlying thing has actually stopped
being lawful, or is inside the window where starting late means missing it.

READ-ONLY, ALL OF THEM. A rule returns Observations and writes nothing. That is
what lets the whole rule set run in a dry run, and what lets each one be tested by
calling it with a date rather than by running a sweep and inspecting the database
afterwards.
"""

from __future__ import annotations

import frappe

from .. import compat
from .base import (
	SEVERITY_CRITICAL,
	SEVERITY_INFO,
	SEVERITY_WARNING,
	Observation,
	Rule,
	days_since,
	days_until,
	register,
)

# ── the intervals, and where each number comes from ─────────────────────────

#: Inside this many days of expiry a certificate stops being a renewal task and
#: becomes an operational risk. Thirty days is roughly the turnaround on an
#: Oregon licence renewal once the paperwork is in — start later and the lapse
#: happens whatever anybody does.
CERT_CRITICAL_DAYS = 30

#: Default lead time where a certificate does not state its own. Ninety days is
#: the GlobalGAP re-audit booking window: the auditor has to be scheduled, not
#: merely paid.
CERT_DEFAULT_WINDOW_DAYS = 90

#: FSMA Produce Safety Rule Subpart E untreated surface water testing cadence.
#: A test older than this is not a current test.
WATER_TEST_DAYS = 90

#: How recently a block must have been sprayed to count as in active rotation.
#: A hundred and twenty days is about one spray season in tree fruit: a block
#: sprayed inside it will be sprayed again, and its water matters now. Ground
#: nobody has sprayed since last year is not urgent and this rule stays quiet on
#: it — which is the kairotic gate, stated as a number.
SPRAY_SEASON_DAYS = 120

#: The annual walk a camp gets. Matches `tools.housing.INSPECTION_DAYS`, which is
#: what `get_housing_capacity` already counts overdue inspections against — one
#: number, so the calendar and the capacity report cannot disagree.
INSPECTION_DAYS = 365

#: Detectors are tested annually under OAR 437-004-1120 and the fire code the
#: camp is inspected against.
DETECTOR_DAYS = 365

#: How far ahead of an agency response deadline to say so. Thirty days is enough
#: to chase a docket before the deadline rather than after it.
FILING_RESPONSE_DAYS = 30

#: A corrective action past due by more than this has stopped being late and
#: become a finding of its own. Most schemes give 28 days for a Major.
ACTION_OVERDUE_GRACE_DAYS = 0

#: Housing unit types nobody sleeps in, which therefore need no habitability
#: inspection. Imported rather than restated so the two definitions cannot drift.
try:  # pragma: no cover - the import is available on every migrated site
	from ..erpnext_mcp.doctype.housing_unit.housing_unit import NON_RESIDENTIAL_TYPES
except Exception:  # pragma: no cover
	NON_RESIDENTIAL_TYPES = ("Toilet-Shower", "Kitchen", "Barn", "Shop")


def _company_filter(filters: dict, company: str, fieldname: str = "company") -> dict:
	if company:
		filters[fieldname] = company
	return filters


def _rows(doctype: str, filters: dict, fields, limit: int = 2000) -> list:
	"""Read rows, selecting only the columns this site actually has."""
	return [
		dict(row)
		for row in frappe.db.get_all(
			doctype,
			filters=filters,
			fields=compat.existing_fields(doctype, fields),
			limit=limit,
		)
		or []
	]


# ── 1. certification_expiring ───────────────────────────────────────────────
def _scan_certifications(context: dict) -> list:
	today = context["today"]
	out = []
	for row in _rows(
		"Certification",
		_company_filter({}, context.get("company") or ""),
		(
			"name",
			"cert_name",
			"cert_type",
			"status",
			"company",
			"holder",
			"expiration_date",
			"renewal_window_days",
			"issuing_body",
		),
	):
		status = str(row.get("status") or "Active")
		if status in ("Superseded", "Revoked"):
			# Superseded means a newer certificate covers this; revoked means it is
			# not coming back. Neither is a renewal task, and alerting on them would
			# put permanent rows on a calendar nothing can ever clear.
			continue
		expires = row.get("expiration_date")
		remaining = days_until(today, expires)
		if remaining is None:
			continue
		window = int(row.get("renewal_window_days") or 0) or CERT_DEFAULT_WINDOW_DAYS
		if remaining > window:
			# Not ripe. The renewal cannot usefully be started yet and the agency
			# would not accept it.
			continue

		what = f"{row.get('cert_type') or 'Certificate'} {row['name']}"
		holder = f" held by {row['holder']}" if row.get("holder") else ""
		if remaining < 0:
			severity = SEVERITY_CRITICAL
			message = (
				f"{what}{holder} EXPIRED {abs(remaining)} day(s) ago, on {expires}. It is not a "
				"defence and has not been one since that date"
				+ (f". Renewals go through {row['issuing_body']}." if row.get("issuing_body") else ".")
			)
		elif remaining <= CERT_CRITICAL_DAYS:
			severity = SEVERITY_CRITICAL
			message = (
				f"{what}{holder} expires in {remaining} day(s), on {expires}. Inside {CERT_CRITICAL_DAYS} "
				"days a renewal started now may not come back before the lapse"
				+ (f" — {row['issuing_body']} issues it." if row.get("issuing_body") else ".")
			)
		else:
			severity = SEVERITY_WARNING
			message = (
				f"{what}{holder} expires in {remaining} day(s), on {expires}, which is inside its "
				f"{window}-day renewal window. That window is the lead time the issuing body "
				"actually takes, not a reminder preference — starting after it means missing it."
			)

		out.append(
			Observation(
				source_doctype="Certification",
				source_docname=row["name"],
				message=message,
				severity=severity,
				due_date=str(expires),
				company=str(row.get("company") or ""),
				category=(
					"Workforce"
					if str(row.get("cert_type") or "") in ("Applicator License", "Farm Labor Contractor License")
					else "Certifications"
				),
			)
		)
	return out


register(
	Rule(
		key="certification_expiring",
		title="A certificate or licence is inside its renewal window",
		category="Certifications",
		requires=("Certification",),
		framework="GAP / GlobalGAP / PrimusGFS scheme rules; ORS 634 applicator licensing; ORS 658.405 FLC licensing",
		purpose=(
			"An operation with a lapsed certificate is not an operation with a paperwork "
			"problem — it is one whose fruit cannot be sold to the buyer who required it, "
			"or whose applicator cannot lawfully spray."
		),
		kairotic_gate=(
			"Fires when the certificate is genuinely inside the lead time its own issuing "
			"body takes, read from `renewal_window_days` on the record rather than from a "
			"fixed number of days before a date. A certificate 200 days out raises nothing "
			"however many times the sweep runs; the same certificate raises Warning the day "
			"it enters its window and Critical inside 30 days, when starting a renewal stops "
			"being enough. Superseded and revoked certificates are excluded because neither "
			"is a renewal anybody can perform. Auto-dismisses on renewal, when the expiration "
			"moves back outside the window."
		),
		scan=_scan_certifications,
	)
)


# ── 2. policy_review_overdue ────────────────────────────────────────────────
def _scan_policies(context: dict) -> list:
	today = context["today"]
	out = []
	for row in _rows(
		"Compliance Policy",
		_company_filter({}, context.get("company") or ""),
		("name", "policy_name", "category", "status", "company", "version", "review_due_date", "policy_owner"),
	):
		if str(row.get("status") or "Active") != "Active":
			# Draft was never adopted; Superseded and Retired are history. Only a
			# procedure actually in force can be overdue for review.
			continue
		overdue = days_since(today, row.get("review_due_date"))
		if overdue is None or overdue < 0:
			continue
		owner = f" {row['policy_owner']} owns it." if row.get("policy_owner") else ""
		out.append(
			Observation(
				source_doctype="Compliance Policy",
				source_docname=row["name"],
				message=(
					f"{row['name']}"
					+ (f" ({row['version']})" if row.get("version") else "")
					+ f" was due for review on {row['review_due_date']}, {overdue} day(s) ago, and is "
					"still in force. GAP and GlobalGAP both expect an annual review; a procedure "
					"nobody has read in years is a finding whether or not it is still correct."
					+ owner
				),
				severity=SEVERITY_WARNING,
				due_date=str(row["review_due_date"]),
				company=str(row.get("company") or ""),
			)
		)
	return out


register(
	Rule(
		key="policy_review_overdue",
		title="A procedure in force is overdue for review",
		category="Policies",
		requires=("Compliance Policy",),
		framework="GAP / GlobalGAP annual document review; FSMA 21 CFR 112 written procedures",
		purpose=(
			"An SOP nobody has looked at since it was written describes an operation that "
			"has since changed. Auditors ask when it was last reviewed before they ask what "
			"it says."
		),
		kairotic_gate=(
			"Reads the review date ON THE POLICY, which is the date the operation itself "
			"committed to — not an annual tick this app imposes. Only Active policies "
			"qualify: a Draft was never adopted and a Superseded one is history, and neither "
			"can meaningfully be reviewed. Auto-dismisses when the review date is moved "
			"forward, which is what reviewing it looks like in the record."
		),
		scan=_scan_policies,
	)
)


# ── 3. water_test_stale ─────────────────────────────────────────────────────
def _scan_water_tests(context: dict) -> list:
	"""Blocks in active spray rotation whose agricultural water is untested.

	THE GATE IS THE SPRAY, NOT THE DATE. FSMA Subpart E is engaged when
	agricultural water contacts the crop, and on a tree fruit block the water that
	contacts the crop is mostly what goes through the sprayer. So an untested
	block that nobody is spraying raises nothing — it is not unsafe, it is
	dormant — and the same block raises Critical the moment it re-enters rotation.
	`SPRAY_SEASON_DAYS` is what "in rotation" means, measured from the block's own
	last spray.
	"""
	today = context["today"]
	out = []
	for row in _rows(
		"Field",
		_company_filter({}, context.get("company") or "", "owning_entity"),
		(
			"name",
			"field_name",
			"owning_entity",
			"condition",
			"crop",
			"last_spray_date",
			"water_test_last_date",
		),
	):
		if str(row.get("condition") or "") == "Fallow":
			continue
		sprayed = days_since(today, row.get("last_spray_date"))
		if sprayed is None or sprayed > SPRAY_SEASON_DAYS:
			# Not in rotation. Nothing is being applied to this ground, so no
			# agricultural water is contacting a crop on it, so Subpart E is not
			# engaged and there is nothing to be alarmed about.
			continue

		tested = days_since(today, row.get("water_test_last_date"))
		if tested is not None and tested <= WATER_TEST_DAYS:
			continue

		if tested is None:
			detail = (
				"has NO agricultural water test on record at all"
			)
			due = ""
		else:
			detail = f"was last water-tested {tested} days ago, on {row['water_test_last_date']}"
			due = str(frappe.utils.add_days(frappe.utils.getdate(row["water_test_last_date"]), WATER_TEST_DAYS))

		out.append(
			Observation(
				source_doctype="Field",
				source_docname=row["name"],
				message=(
					f"{row['name']} was sprayed {sprayed} day(s) ago and {detail}. FSMA Produce "
					f"Safety Rule Subpart E wants agricultural water tested within {WATER_TEST_DAYS} "
					"days, and the water going through the sprayer onto a crop being harvested is "
					"agricultural water. This block is in active rotation, so the next application "
					"is not defensible until the test is done."
				),
				severity=SEVERITY_CRITICAL,
				due_date=due,
				company=str(row.get("owning_entity") or ""),
			)
		)
	return out


register(
	Rule(
		key="water_test_stale",
		title="A block in spray rotation has no current agricultural water test",
		category="Water and Sanitation",
		requires=("Field",),
		framework="FSMA Produce Safety Rule 21 CFR 112 Subpart E; EPA WPS 40 CFR 170",
		purpose=(
			"Water applied to a crop being harvested has to have been tested. This is the "
			"rule that stops the next application being made on an undefensible basis."
		),
		kairotic_gate=(
			"THE CLEAREST CASE IN THE WHOLE ENGINE. The gate is the spray, not the "
			f"calendar: only a block sprayed within the last {SPRAY_SEASON_DAYS} days — i.e. one "
			"genuinely in rotation — can raise this, because Subpart E is engaged by water "
			"contacting a crop and not by a date passing. Fallow ground raises nothing. "
			"Ground dormant since last season raises nothing, and starts raising the day it "
			"is sprayed again. Auto-dismisses the moment a water test is recorded."
		),
		scan=_scan_water_tests,
	)
)


# ── 4. housing_inspection_overdue ───────────────────────────────────────────
def _scan_housing_inspections(context: dict) -> list:
	today = context["today"]
	out = []
	for row in _rows(
		"Housing Unit",
		_company_filter({}, context.get("company") or "", "owning_entity"),
		("name", "unit_name", "unit_type", "parcel", "owning_entity", "condition", "last_habitability_inspection"),
	):
		if str(row.get("unit_type") or "") in NON_RESIDENTIAL_TYPES:
			continue
		if str(row.get("condition") or "") == "Uninhabitable":
			# Already off the roster: assignments into it are refused, so there is
			# nobody to protect and an inspection would be a formality. It comes
			# back the moment somebody marks it habitable again.
			continue
		inspected = days_since(today, row.get("last_habitability_inspection"))
		if inspected is not None and inspected <= INSPECTION_DAYS:
			continue

		if inspected is None:
			detail = "has never had a habitability inspection recorded"
			due = ""
		else:
			detail = f"was last inspected {inspected} days ago, on {row['last_habitability_inspection']}"
			due = str(
				frappe.utils.add_days(
					frappe.utils.getdate(row["last_habitability_inspection"]), INSPECTION_DAYS
				)
			)
		out.append(
			Observation(
				source_doctype="Housing Unit",
				source_docname=row["name"],
				message=(
					f"{row['name']}"
					+ (f" on {row['parcel']}" if row.get("parcel") else "")
					+ f" {detail}. Oregon's agricultural labor housing rules and 29 CFR 1910.142 "
					"expect an annual walk, and this unit can currently be assigned to somebody "
					"who will sleep in it tonight."
				),
				severity=SEVERITY_WARNING,
				due_date=due,
				company=str(row.get("owning_entity") or ""),
			)
		)
	return out


register(
	Rule(
		key="housing_inspection_overdue",
		title="A cabin somebody can be assigned to has no current habitability inspection",
		category="Housing",
		requires=("Housing Unit",),
		framework="OAR 437-004-1120 agricultural labor housing; 29 CFR 1910.142; FSMA Subpart L",
		purpose="Somebody sleeps there tonight, and nobody has confirmed this season that it is fit to.",
		kairotic_gate=(
			"Scoped to units that can actually be assigned. A shower block, a kitchen and a "
			"shop take no assignment and raise nothing; a unit already marked Uninhabitable "
			"raises nothing, because assignments into it are already refused and there is "
			"nobody to protect — and it starts raising again the day somebody marks it "
			"habitable. Never-inspected counts as overdue, which is the answer that gets "
			"somebody to go and look. Auto-dismisses when an inspection is recorded."
		),
		scan=_scan_housing_inspections,
	)
)


# ── 5. housing_detector_test_stale ──────────────────────────────────────────
def _scan_detectors(context: dict) -> list:
	today = context["today"]
	out = []
	for row in _rows(
		"Housing Unit",
		_company_filter({}, context.get("company") or "", "owning_entity"),
		(
			"name",
			"unit_name",
			"unit_type",
			"parcel",
			"owning_entity",
			"condition",
			"fsma_worker_facility",
			"smoke_detector_last_test",
			"co_detector_last_test",
		),
	):
		if str(row.get("unit_type") or "") in NON_RESIDENTIAL_TYPES:
			continue
		if str(row.get("condition") or "") == "Uninhabitable":
			continue
		if not compat.checked(row.get("fsma_worker_facility")):
			# The FSMA worker-facility flag is what puts a building inside Subpart L
			# and inside the camp's inspected estate. A shed on the same parcel is
			# not a bunkhouse and does not need a detector test on record.
			continue

		stale = []
		for label, fieldname in (("smoke", "smoke_detector_last_test"), ("CO", "co_detector_last_test")):
			since = days_since(today, row.get(fieldname))
			if since is None:
				stale.append(f"no {label} detector test has ever been recorded")
			elif since > DETECTOR_DAYS:
				stale.append(f"the {label} detector was last tested {since} days ago, on {row[fieldname]}")
		if not stale:
			continue

		out.append(
			Observation(
				source_doctype="Housing Unit",
				source_docname=row["name"],
				message=(
					f"{row['name']} is a FSMA worker facility and "
					+ " and ".join(stale)
					+ ". A detector nobody has tested inside a year is a detector nobody knows "
					"works, and a camp cabin with a propane heater is exactly where that matters."
				),
				severity=SEVERITY_WARNING,
				due_date="",
				company=str(row.get("owning_entity") or ""),
			)
		)
	return out


register(
	Rule(
		key="housing_detector_test_stale",
		title="A worker-facility building has no current detector test",
		category="Housing",
		requires=("Housing Unit",),
		framework="FSMA Produce Safety Rule Subpart L; OAR 437-004-1120; ORS 479 / ORS 690",
		purpose="A propane heater in a cabin with an untested CO detector is how somebody dies in their sleep.",
		kairotic_gate=(
			"Only buildings flagged `fsma_worker_facility` — the flag is what puts a "
			"building inside Subpart L and inside the camp's inspected estate, and a shed on "
			"the same parcel is not a bunkhouse. Uninhabitable units are excluded for the "
			"same reason as the inspection rule. Auto-dismisses when both detector tests are "
			"recorded inside a year."
		),
		scan=_scan_detectors,
	)
)


# ── 6. i9_expired ───────────────────────────────────────────────────────────
def _employee_field(fieldname: str) -> bool:
	return compat.has_field("Employee", fieldname)


def _scan_i9(context: dict) -> list:
	if not _employee_field("i9_status"):
		# The compliance field has not been installed on this site, so there is
		# nothing to read. Reported as an empty scan rather than as a failure:
		# `install_compliance_fields` is the fix and it says so itself.
		return []
	today = context["today"]
	filters = _company_filter({"i9_status": "Expired"}, context.get("company") or "")
	if compat.has_field("Employee", "status"):
		filters["status"] = "Active"
	out = []
	for row in _rows(
		"Employee",
		filters,
		("name", "employee_name", "company", "i9_status", "date_of_joining", "status"),
	):
		out.append(
			Observation(
				source_doctype="Employee",
				source_docname=row["name"],
				message=(
					f"{row.get('employee_name') or row['name']} has an EXPIRED I-9. Employment "
					"eligibility has to be re-verified when a work authorisation document "
					"expires, and until it is this person cannot lawfully be put on a crew "
					"tomorrow. ICE penalties under 8 USC 1324a are assessed per form."
				),
				severity=SEVERITY_CRITICAL,
				due_date=today,
				company=str(row.get("company") or ""),
			)
		)
	return out


register(
	Rule(
		key="i9_expired",
		title="An active employee's I-9 has expired",
		category="Workforce",
		requires=("Employee",),
		framework="IRCA 8 USC 1324a; Form I-9 re-verification",
		purpose="This person cannot lawfully be scheduled tomorrow. It is a rostering fact before it is a filing fact.",
		kairotic_gate=(
			"Reads the I-9 status on the employee record and fires only on Expired — not on "
			"a re-verification date this app has invented, and not on Pending, which is a "
			"person inside their lawful three-day window and not yet a problem. Scoped to "
			"ACTIVE employees where the site records employment status: a former employee's "
			"expired I-9 blocks nothing, and alerting on it would fill the calendar with "
			"people who left. Auto-dismisses when the status moves off Expired."
		),
		scan=_scan_i9,
	)
)


# ── 7. flc_license_expiring ─────────────────────────────────────────────────
def _scan_flc(context: dict) -> list:
	if not _employee_field("flc_license_expiration"):
		return []
	today = context["today"]
	filters = _company_filter({}, context.get("company") or "")
	if compat.has_field("Employee", "status"):
		filters["status"] = "Active"
	out = []
	for row in _rows(
		"Employee",
		filters,
		("name", "employee_name", "company", "status", "flc_license_status", "flc_license_expiration"),
	):
		expires = row.get("flc_license_expiration")
		remaining = days_until(today, expires)
		if remaining is None or remaining > CERT_DEFAULT_WINDOW_DAYS:
			continue
		who = row.get("employee_name") or row["name"]
		if remaining < 0:
			severity = SEVERITY_CRITICAL
			message = (
				f"{who}'s farm labor contractor licence EXPIRED {abs(remaining)} day(s) ago, on "
				f"{expires}. They cannot lawfully recruit, supervise or transport agricultural "
				"workers, and under MSPA and ORS 658.405 using an unlicensed contractor is the "
				"grower's violation as well as theirs — take them off the crew schedule."
			)
		elif remaining <= CERT_CRITICAL_DAYS:
			severity = SEVERITY_CRITICAL
			message = (
				f"{who}'s farm labor contractor licence expires in {remaining} day(s), on "
				f"{expires}. Renewal needs a bond and a background check and does not turn round "
				"in a week — a crew boss whose licence lapses mid-harvest is a crew with nobody "
				"who can lawfully supervise it."
			)
		else:
			severity = SEVERITY_WARNING
			message = (
				f"{who}'s farm labor contractor licence expires in {remaining} day(s), on {expires}. "
				"Start the renewal: the bond and background check are the long pole."
			)
		out.append(
			Observation(
				source_doctype="Employee",
				source_docname=row["name"],
				message=message,
				severity=severity,
				due_date=str(expires),
				company=str(row.get("company") or ""),
			)
		)
	return out


register(
	Rule(
		key="flc_license_expiring",
		title="A crew boss's farm labor contractor licence is expiring",
		category="Workforce",
		requires=("Employee",),
		framework="MSPA 29 USC 1801; ORS 658.405 farm labor contractor licensing",
		purpose=(
			"Using an unlicensed contractor is the grower's violation as well as the "
			"contractor's, which makes somebody else's licence the grower's problem."
		),
		kairotic_gate=(
			"Reads the expiration date on the employee record, and only for people who are "
			"still employed. Fires at 90 days, which is what an Oregon renewal actually "
			"takes once the bond and background check are counted, and escalates to Critical "
			"at 30 when starting late means lapsing. Auto-dismisses on renewal."
		),
		scan=_scan_flc,
	)
)


# ── 8. filing_response_due ──────────────────────────────────────────────────
def _scan_filings(context: dict) -> list:
	today = context["today"]
	out = []
	for row in _rows(
		"Regulatory Filing",
		_company_filter({}, context.get("company") or ""),
		(
			"name",
			"filing_name",
			"agency",
			"filing_type",
			"company",
			"status",
			"submission_date",
			"docket_number",
			"response_due_date",
			"response_received_date",
		),
	):
		if row.get("response_received_date"):
			# They answered. Nothing to chase.
			continue
		if str(row.get("status") or "") in ("Draft", "Withdrawn"):
			continue
		remaining = days_until(today, row.get("response_due_date"))
		if remaining is None or remaining > FILING_RESPONSE_DAYS:
			continue
		docket = f" under docket {row['docket_number']}" if row.get("docket_number") else ""
		when = (
			f"was due {abs(remaining)} day(s) ago" if remaining < 0 else f"is due in {remaining} day(s)"
		)
		out.append(
			Observation(
				source_doctype="Regulatory Filing",
				source_docname=row["name"],
				message=(
					f"A response to {row['name']} ({row.get('agency') or 'the agency'}, "
					f"{row.get('filing_type') or 'filing'}{docket}) {when}, on "
					f"{row['response_due_date']}, and none has been recorded. Chasing a docket "
					"before the deadline is a different conversation from chasing it after."
				),
				severity=SEVERITY_INFO if remaining >= 0 else SEVERITY_WARNING,
				due_date=str(row["response_due_date"]),
				company=str(row.get("company") or ""),
			)
		)
	return out


register(
	Rule(
		key="filing_response_due",
		title="An agency response is due on a filing and has not arrived",
		category="Filings",
		requires=("Regulatory Filing",),
		framework="Agency-specific response deadlines (USDA, ODA, DOL, EPA, IRS, OR-DOR)",
		purpose="Chasing a docket before the deadline is a different conversation from chasing it after.",
		kairotic_gate=(
			"Fires only on filings that were ACTUALLY SUBMITTED and have NO response "
			"recorded. A draft raises nothing, because nothing was sent; a filing with a "
			"response date raises nothing, because the thing being waited for has happened. "
			"That second clause is the auto-dismissal: recording the response clears the "
			"alert without anybody switching a reminder off. Escalates from Info to Warning "
			"once the deadline has actually passed — at that point it is no longer a heads-up."
		),
		scan=_scan_filings,
	)
)


# ── 9. audit_action_overdue ─────────────────────────────────────────────────
def _scan_corrective_actions(context: dict) -> list:
	"""Open corrective actions past their due date, one alert per audit.

	One alert per AUDIT rather than per action, deliberately. Five open items on
	one PrimusGFS audit are one conversation with one auditor, and five separate
	rows on the calendar would look like five problems. The message names how many
	and which is worst.
	"""
	today = context["today"]
	out = []
	audits = _rows(
		"Audit Event",
		_company_filter({}, context.get("company") or ""),
		("name", "audit_name", "audit_type", "auditor", "company", "audit_date", "corrective_actions_closed"),
	)
	for row in audits:
		if row.get("corrective_actions_closed"):
			continue
		try:
			doc = frappe.get_doc("Audit Event", row["name"])
		except Exception:
			continue
		overdue = []
		worst_due = ""
		for index, action in doc.open_actions():
			due = str(action.get("due_date") or "")
			if not due:
				continue
			late = days_since(today, due)
			if late is None or late < ACTION_OVERDUE_GRACE_DAYS:
				continue
			overdue.append((index, action, late, due))
			if not worst_due or due < worst_due:
				worst_due = due
		if not overdue:
			continue

		worst = max(overdue, key=lambda entry: entry[2])
		severity = (
			SEVERITY_CRITICAL
			if any(str(action.get("severity") or "") in ("Major", "Critical") for _i, action, _l, _d in overdue)
			else SEVERITY_WARNING
		)
		out.append(
			Observation(
				source_doctype="Audit Event",
				source_docname=row["name"],
				message=(
					f"{row['name']} ({row.get('audit_type') or 'audit'}"
					+ (f", {row['auditor']}" if row.get("auditor") else "")
					+ f") has {len(overdue)} overdue corrective action(s). The worst is "
					f"{worst[2]} day(s) past its {worst[3]} deadline: "
					f"{str(worst[1].get('finding') or '')[:160]}. An operation is not judged on "
					"having no findings — it is judged on closing them, and an open action "
					"nobody can produce evidence for is how last year's finding becomes this "
					"year's penalty."
				),
				severity=severity,
				due_date=worst_due,
				company=str(row.get("company") or ""),
			)
		)
	return out


register(
	Rule(
		key="audit_action_overdue",
		title="An audit has corrective actions past their deadline",
		category="Audits",
		requires=("Audit Event",),
		framework="GAP / GlobalGAP / PrimusGFS corrective action deadlines; agency inspection follow-up",
		purpose=(
			"The one thing an auditor is certain to ask about on the next visit, and the "
			"gate `generate_audit_packet` refuses an incomplete period on."
		),
		kairotic_gate=(
			"Reads the deadline each SCHEME set on each action, not an interval this app "
			"chose — a Critical usually gets days and a Minor gets weeks, and both are on "
			"the row. Actions with no due date raise nothing, because there is no deadline to "
			"be past. An audit whose closure date is set raises nothing, and the controller "
			"will not let that date be set while an action is open, so the two cannot "
			"disagree. Severity follows the worst open finding: a Major or Critical left late "
			"is Critical, because at that point the certificate is at risk rather than the "
			"paperwork. One alert per audit rather than per action — five open items on one "
			"audit are one conversation, not five problems."
		),
		scan=_scan_corrective_actions,
	)
)
