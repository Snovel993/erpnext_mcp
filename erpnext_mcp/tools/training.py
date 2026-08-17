# SPDX-License-Identifier: MIT
"""Recording what the crew was taught, and the four audits one afternoon answers.

v0.19.0. THE FIRST PULL FROM THE HR ROADMAP, AND IT EXISTS BECAUSE THE CALENDAR
COULD SEE EVERY DOCUMENT ON THE FARM AND NOTHING A PERSON KNEW.

Eleven compliance rules watched certificates, policies, cabins, water, filings and
audits. Not one of them watched TRAINING — which is what WPS asks for every twelve
months, what Oregon's heat rule asks for annually before the first hot shift, what
FSMA Subpart C asks for on hiring and periodically thereafter, and what a GAP
auditor asks for by name with the signature attached. All of it lived in a binder,
and the way an operation found out that Ana's handler card had lapsed was that
somebody looked, or that an inspector did.

These four tools plus the `training_expiring` rule put it on the same board as an
overdue cabin inspection.

────────────────────────────────────────────────────────────────────────────
ONE RECORD, MANY REGIMES — WHICH IS THE WHOLE ARCHITECTURE
────────────────────────────────────────────────────────────────────────────

A single session in a shed covering hygiene, pesticide safety and heat can satisfy
GAP, WPS and OR-OSHA at once, provided the trainer covered all three curricula.
Filing it three times produces three records that disagree by August; filing it
once with three tags produces one record three packets pull from. So `regimes` is
a tag list, `content_topics_covered` is what makes the tags defensible rather than
optimistic, and `generate_audit_packet` filters by tag.

`erpnext_mcp/training.py` holds the vocabulary and every read of it, so the
controller, the rule, these tools and the packet builder cannot disagree about
whether a row carries WPS. In particular they all match by TOKEN: `"GlobalGAP"`
contains `"GAP"`, and a substring match would hand a USDA GAP auditor evidence
from a different scheme.

────────────────────────────────────────────────────────────────────────────
THE §112.161 FIELDS ARE WRITTEN AT THE TIME, NOT BACKFILLED
────────────────────────────────────────────────────────────────────────────

`record_training` takes the trainee's signature at completion and
`sign_training_supervisor_review` is a SEPARATE tool, deliberately, because the
supervisor review is a separate act at a later moment — §112.161(b) says "within a
reasonable time after the record is made", which is a sequence rather than a form
field. A tool that took both signatures in one call would make it trivially easy
to record both at the same instant, which is the shape of a record an FDA
inspector reads as having been assembled rather than kept.

Every read reports the §112.161 elements a record is MISSING rather than refusing
the record for lacking them. An operation that trained its crew and recorded it
imperfectly is in a better position than one that trained its crew and recorded
nothing, and a tool that refused would guarantee the second.

────────────────────────────────────────────────────────────────────────────
THE GUARDS ARE `create_employee`'s, SHARED RATHER THAN RESTATED
────────────────────────────────────────────────────────────────────────────

`employee.require_hr_role` and `employee.require_company_scope` are imported, not
copied. A training record is a personnel record: it names a worker, it is read in
a wage claim, and creating one for an entity you cannot see would put a person's
qualification history on a register you cannot read. Reading is scoped the same
way — the company filter is applied against what the principal may actually reach,
so a scoped account listing "every training record" gets its own entity's.
"""

from __future__ import annotations

import frappe

from .. import compat, training
from ..args import as_bool, as_choice, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import employee as employee_tool

DOCTYPE = training.DOCTYPE

#: Most records any one read returns. A training register is read to answer a
#: question about a crew, not to be exported; a caller that wants everything
#: should narrow by company and period.
RECORD_CAP = 500

#: Status vocabulary of the compliance matrix, which is NOT `training.STATUS_*`.
#: Those three describe a RECORD that exists; these four describe a CELL, and the
#: fourth — `missing` — is the one an auditor opens the binder to find. Mapping
#: Expiring onto `due_soon` rather than reusing the word keeps the two
#: vocabularies from being confused where they meet in one response.
CELL_CURRENT = "current"
CELL_DUE_SOON = "due_soon"
CELL_EXPIRED = "expired"
CELL_MISSING = "missing"

#: Most curricula one matrix holds people against, and most people it holds. The
#: product is what gets rendered, so both are capped rather than the total: a
#: caller who narrows by regime gets more of the roster, not fewer columns.
REQUIREMENT_CAP = 40
EMPLOYEE_CAP = 400

#: Most training records one matrix reads. Generous for a register that only ever
#: needs the latest record per person per curriculum.
MATRIX_RECORD_CAP = 5000


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _readable_companies(actor: str) -> list:
	"""Companies this principal may see, or [] meaning unrestricted.

	Frappe's own rule: NO User Permission means unrestricted, which is what every
	Desk surface on the site already does. `api/guard.py` inverts that for the
	eleven mobile methods and says why; this is not that surface.
	"""
	from .. import roles

	return roles.companies_for(actor) or []


def _resolve_record(args: dict) -> dict:
	name = (as_str(args, "name") or as_str(args, "training") or as_str(args, "record", required=True)).strip()
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(
			f"no {DOCTYPE} called {name!r} on this site. list_trainings has the register; a "
			"docname looks like ETR-2026-07-00003."
		)
	return dict(
		frappe.db.get_value(DOCTYPE, name, compat.existing_fields(DOCTYPE, training.FIELDS), as_dict=True)
		or {}
	)


# ── 1. record_training ──────────────────────────────────────────────────────
def record_training(args: dict) -> ToolResult:
	"""File one training event, tagged with every audit it answers."""
	_require()
	compat.require_doctype("Employee", "It comes with the Frappe HR (hrms) app.")
	actor = employee_tool.require_hr_role()

	person = employee_tool.resolve_employee(as_str(args, "employee", required=True))
	row = frappe.db.get_value("Employee", person, ["employee_name", "company", "status"], as_dict=True) or {}
	company = resolve_company(as_str(args, "company") or str(row.get("company") or ""), required=True)
	employee_tool.require_company_scope(actor, company)
	if row.get("company") and str(row["company"]) != company:
		raise ToolError(
			f"{person} ({row.get('employee_name')}) is employed by {row['company']}, and this "
			f"call names {company}. A training record belongs to the entity that employed the "
			"person on the day — filing it against another one puts the evidence in a packet "
			"that will be handed to an auditor asking about a different company. Nothing was "
			"created."
		)

	try:
		regimes = training.require(args.get("regimes"))
	except ValueError as exc:
		raise ToolError(f"{exc} Nothing was created.") from None

	topics = training.topics(args.get("content_topics_covered"))
	if not topics:
		raise ToolError(
			"content_topics_covered is required. It is what makes a regime tag defensible "
			"rather than optimistic: Oregon's heat rule names six topics that must be covered "
			"annually, and a record claiming OR-OSHA without them is a record an inspector will "
			"disallow. 'Heat, water, shade, symptoms, reporting, emergency response' is a "
			"curriculum; 'safety meeting' is not. Nothing was created."
		)

	completed = as_date(args, "completed_date", required=True)
	expires = as_date(args, "expires_date")
	source = as_choice(
		DOCTYPE, "training_source", as_str(args, "training_source") or "Internal", "training_source"
	)

	# v0.19.2. `training_type` is a Link now, and this accepts EITHER an existing
	# Training Type or free text naming a course this site has not run before —
	# which is auto-created rather than refused. The controller would do this
	# anyway (link validation runs after `validate`, so it has to); doing it here
	# as well is what lets the result SAY a master was created, instead of a
	# curriculum quietly appearing on the site because somebody filed a training.
	#
	# THE NEW TYPE DOES NOT INHERIT THIS CALL'S `regimes`, and that is the whole
	# distinction between the two records. This call says what ONE afternoon
	# covered; the curriculum says what the course normally answers. A heat session
	# that a crew leader also used to cover hygiene is tagged GAP on the record and
	# should not make "Heat Illness Prevention" a GAP curriculum for every future
	# session of it. So the type takes what its NAME implies, and somebody who
	# disagrees corrects it once on the type rather than on thirty records.
	try:
		curriculum = training.ensure_type(as_str(args, "training_type", required=True))
	except ValueError as exc:
		raise ToolError(f"{exc} Nothing was created.") from None

	doc = frappe.new_doc(DOCTYPE)
	doc.employee = person
	doc.employee_name = row.get("employee_name") or person
	doc.company = company
	doc.training_type = curriculum["training_type"]
	doc.training_source = source
	doc.provider = as_str(args, "provider")
	doc.completed_date = completed
	doc.completed_time = as_str(args, "completed_time")
	doc.expires_date = expires
	doc.certificate_file = as_str(args, "certificate_file")
	doc.regimes = training.join(regimes)
	doc.content_topics_covered = ", ".join(topics)
	doc.person_performed_signature = as_str(args, "person_performed_signature")
	doc.notes = as_str(args, "notes")
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = training.describe(dict(doc.as_dict()))
	gaps = training.fsma_161_gaps(described)
	superseded = _superseded(doc)

	data = {
		**described,
		"actor": actor,
		"retention_note": training.retention_note(regimes),
		"fsma_112_161_gaps": gaps,
		"supersedes": superseded,
		"packets_that_will_pull_it": regimes,
		"training_type_created": bool(curriculum.get("created")),
		"training_type_regimes": curriculum.get("regimes") or [],
		"note": (
			f"Tagged for {', '.join(regimes)}, so generate_audit_packet pulls it into "
			f"{'those packets' if len(regimes) > 1 else 'that packet'} and into no others. "
			+ training.retention_note(regimes)
		),
		"next_step": (
			"sign_training_supervisor_review records the §112.161(b) review — a separate act at "
			"a later moment, which is why it is a separate tool."
			if not described["supervisor_reviewed"]
			else "Nothing outstanding on this record."
		),
	}
	if gaps:
		data["gap_note"] = (
			f"{len(gaps)} element(s) FSMA §112.161 asks for are not on this record. It is filed "
			"anyway — a training that happened and was recorded imperfectly is better evidence "
			"than no record at all — but each one is a finding FDA writes up even where the "
			"underlying activity was fine."
		)
	if curriculum.get("created"):
		data["training_type_note"] = (
			f"{curriculum['training_type']!r} was not a curriculum on this site, so it was created "
			f"as a Training Type and tagged {', '.join(curriculum['regimes']) or '(nothing)'} from "
			"its name. THAT GUESS IS ABOUT THE COURSE, NOT ABOUT THIS SESSION — this record "
			f"carries {', '.join(regimes)}, which is what was actually covered, and nothing here "
			"changed it. If the curriculum normally answers to something else, correct it once on "
			"the Training Type and every future session inherits the correction."
		)
	elif curriculum.get("regimes") and set(curriculum["regimes"]) != set(regimes):
		data["training_type_note"] = (
			f"{curriculum['training_type']!r} normally answers to "
			f"{', '.join(curriculum['regimes'])} and this session is tagged {', '.join(regimes)}. "
			"Recorded as given: the curriculum says what the course covers and the record says "
			"what this afternoon covered, and a session that ran short is entitled to say so. "
			"Worth a second look if it was not deliberate."
		)
	if expires is None:
		data["expiry_note"] = (
			"No expiry, so this is recorded as one-time training and the compliance calendar "
			"will never ask for it to be renewed. Right for a new-hire orientation or a PSA "
			"grower certificate; WRONG for WPS (12 months), Oregon heat illness (12 months) or "
			"annual GAP hygiene — set expires_date for those."
		)

	return ToolResult(
		data=data,
		summary=(
			f"recorded {doc.training_type} for {described['employee_name']} on {completed} "
			f"({', '.join(regimes)}"
			+ (f", expires {expires}" if expires else ", one-time")
			+ f") as {doc.name}"
		),
		docstatus_delta="none → 0 (created)",
	)


def _requirements(regime: str, one_type: str) -> list[dict]:
	"""The curricula this report holds every employee against.

	THE REQUIREMENT AXIS IS THE `Training Type` MASTER, and saying so is half the
	point of this function. There is no per-role training requirement anywhere on
	this site — no "every handler needs WPS, every supervisor needs OSHA 30"
	table — so a matrix cannot compute what an individual person owes. What it
	CAN do is hold the whole crew against the curricula this operation says it
	runs, which is the register a WPS or GAP auditor works from anyway: they ask
	whether the people who needed the training have it, and the honest answer
	starts with who does not have it at all.

	`active` IS THE FILTER, not a date. A curriculum somebody unticked is one this
	operation no longer runs, and reporting the whole crew non-compliant on a
	course that was retired in 2019 is how a compliance report gets ignored.
	"""
	if not compat.doctype_exists(training.TYPE_DOCTYPE):
		return []

	filters: dict = {}
	if compat.has_field(training.TYPE_DOCTYPE, "active"):
		filters["active"] = 1
	if one_type:
		filters["name"] = one_type

	rows = (
		frappe.db.get_all(
			training.TYPE_DOCTYPE,
			filters=filters,
			fields=compat.existing_fields(
				training.TYPE_DOCTYPE, ("name", "active", "retention_years", "description")
			),
			order_by="name asc",
			limit=REQUIREMENT_CAP + 1,
		)
		or []
	)
	names = [str(dict(row).get("name")) for row in rows]
	tags = training.rows_for_parents(training.TYPE_DOCTYPE, names, "regimes")

	found = []
	for row in rows:
		entry = dict(row)
		name = str(entry.get("name"))
		regimes = tags.get(name, [])
		if regime and regime not in regimes:
			continue
		found.append(
			{
				"training_type": name,
				"regimes": regimes,
				"retention_years": int(entry.get("retention_years") or 0) or None,
				"description": entry.get("description") or None,
			}
		)
	return found


def _employees_for(company: str) -> tuple[list[dict], bool]:
	"""The active roster of one company, and whether the cap bit."""
	rows = (
		frappe.db.get_all(
			"Employee",
			filters={"company": company, "status": "Active"},
			fields=compat.existing_fields(
				"Employee", ("name", "employee_name", "designation", "date_of_joining", "department")
			),
			order_by="employee_name asc",
			limit=EMPLOYEE_CAP + 1,
		)
		or []
	)
	truncated = len(rows) > EMPLOYEE_CAP
	return [dict(row) for row in rows[:EMPLOYEE_CAP]], truncated


def _cell(record: dict | None, as_of: str) -> dict:
	"""One employee's standing on one curriculum, as of a date.

	AS OF A DATE, NOT AS OF TODAY, and the whole function turns on it. A report
	run in January for last year's audit must not know about the training
	somebody did in March — a matrix that showed the crew compliant on a date
	when they were not is a document that says the opposite of what happened.
	The caller's `as_of_date` reaches both the record selection and the expiry
	arithmetic, so the two cannot disagree.
	"""
	if record is None:
		return {
			"status": CELL_MISSING,
			"record": None,
			"completed_date": None,
			"expires_date": None,
			"days_until_expiry": None,
		}
	expires = str(record.get("expires_date") or "") or None
	stored = training.status_for(expires, as_of)
	status = {
		training.STATUS_ACTIVE: CELL_CURRENT,
		training.STATUS_EXPIRING: CELL_DUE_SOON,
		training.STATUS_EXPIRED: CELL_EXPIRED,
	}[stored]
	return {
		"status": status,
		"record": record.get("name"),
		"completed_date": str(record.get("completed_date") or "") or None,
		"expires_date": expires,
		"days_until_expiry": training.days_until(as_of, expires),
		"one_time": expires is None,
		"supervisor_reviewed": bool(record.get("supervisor_reviewed_by")),
	}


# ── 5. get_training_compliance_report ───────────────────────────────────────
def get_training_compliance_report(args: dict) -> ToolResult:
	"""Every employee against every curriculum, as one matrix, as of a date.

	THE BLACK HOLE THIS CLOSES. `record_training` files what a person was taught
	and `list_trainings` reads the register back, and between them there was no
	call that answered the question an auditor actually asks: *is the crew
	trained*. A register lists what exists; a compliance matrix reports what does
	NOT, and the difference is the person with no WPS record at all — who appears
	in `list_trainings` as no row, which is to say nowhere.

	MISSING IS A STATUS, AND IT IS THE POINT. The other three cells describe a
	record; `missing` describes its absence, and it is the one an inspector
	finds first. It cannot be computed from the training register alone — it
	needs the roster on one axis and the curriculum master on the other, which
	is why this is a report rather than a filter.

	IT DOES NOT KNOW WHO NEEDED WHAT. See `_requirements`: this site has no
	per-role requirement table, so the matrix holds the whole active roster
	against every curriculum the operation says it runs. That over-reports — an
	office bookkeeper is not a pesticide handler — and the response says so in
	`requirement_basis` rather than quietly presenting the over-report as a
	finding. Narrowing by `regime` or `training_type` is how a caller asks the
	question they actually mean.
	"""
	_require()
	compat.require_doctype("Employee", "It comes with the Frappe HR (hrms) app.")
	actor = employee_tool.require_hr_role()
	company = resolve_company(as_str(args, "company", required=True), required=True)
	employee_tool.require_company_scope(actor, company)

	as_of = as_date(args, "as_of_date") or frappe.utils.today()

	regime = as_str(args, "regime")
	if regime:
		canonical = training.canon(regime)
		if not canonical:
			raise ToolError(f"regime {regime!r} is not one this app knows. {training.vocabulary_note()}")
		regime = canonical

	one_type = as_str(args, "training_type")
	if one_type and not frappe.db.exists(training.TYPE_DOCTYPE, one_type):
		raise ToolError(
			f"no {training.TYPE_DOCTYPE} called {one_type!r} on this site. A curriculum is "
			"created the first time somebody records a session of it, so a name nobody has "
			"filed against does not exist yet — and holding the crew against it would report "
			"every one of them non-compliant on a course this operation has never run."
		)

	requirements = _requirements(regime, one_type)
	roster, roster_truncated = _employees_for(company)

	records = training.rows(
		{"company": company, "completed_date": ("<=", as_of)},
		limit=MATRIX_RECORD_CAP,
		order_by="completed_date desc",
	)
	# Latest record per (person, curriculum). `rows` came back newest first, so
	# the first one seen is the one that governs — a renewal supersedes without
	# either record being edited, which is what `_superseded` exists to preserve.
	latest: dict = {}
	for row in records:
		key = (str(row.get("employee") or ""), str(row.get("training_type") or ""))
		latest.setdefault(key, row)

	matrix = []
	fully = partially = non = 0
	by_requirement: dict = {
		entry["training_type"]: {
			CELL_CURRENT: 0,
			CELL_DUE_SOON: 0,
			CELL_EXPIRED: 0,
			CELL_MISSING: 0,
		}
		for entry in requirements
	}

	for person in roster:
		employee = str(person.get("name"))
		cells = {}
		for entry in requirements:
			curriculum = entry["training_type"]
			cell = _cell(latest.get((employee, curriculum)), as_of)
			cells[curriculum] = cell
			by_requirement[curriculum][cell["status"]] += 1

		statuses = [cell["status"] for cell in cells.values()]
		held = [status for status in statuses if status in (CELL_CURRENT, CELL_DUE_SOON)]
		gaps = [status for status in statuses if status in (CELL_EXPIRED, CELL_MISSING)]
		if not statuses:
			standing = "no_requirements"
		elif not gaps:
			standing = "fully_compliant"
			fully += 1
		elif held:
			standing = "partially_compliant"
			partially += 1
		else:
			standing = "non_compliant"
			non += 1

		matrix.append(
			{
				"employee": employee,
				"employee_name": person.get("employee_name") or employee,
				"job_title": person.get("designation") or None,
				"department": person.get("department") or None,
				"date_of_joining": str(person.get("date_of_joining") or "") or None,
				"standing": standing,
				"requirements": cells,
				"missing": sorted(name for name, cell in cells.items() if cell["status"] == CELL_MISSING),
				"expired": sorted(name for name, cell in cells.items() if cell["status"] == CELL_EXPIRED),
				"due_soon": sorted(name for name, cell in cells.items() if cell["status"] == CELL_DUE_SOON),
			}
		)

	data = {
		"company": company,
		"as_of_date": as_of,
		"regime": regime or None,
		"training_type": one_type or None,
		"requirements": requirements,
		"requirement_count": len(requirements),
		"matrix": matrix,
		"by_requirement": by_requirement,
		"summary": {
			"total_employees": len(matrix),
			"fully_compliant": fully,
			"partially_compliant": partially,
			"non_compliant": non,
			"without_requirements": len(matrix) - fully - partially - non,
		},
		"expiring_window_days": training.EXPIRING_WINDOW_DAYS,
		"status_vocabulary": {
			CELL_CURRENT: "A record exists and has not lapsed as of this date.",
			CELL_DUE_SOON: (
				f"Inside the {training.EXPIRING_WINDOW_DAYS}-day renewal window. Still valid — "
				"this is the schedule, not the finding."
			),
			CELL_EXPIRED: "A record exists and its expiry date has passed as of this date.",
			CELL_MISSING: (
				"No record of this curriculum for this person at any date. The one an inspector "
				"finds first, and the one a register alone cannot report."
			),
		},
		"requirement_basis": (
			f"{len(requirements)} active {training.TYPE_DOCTYPE}(s)"
			+ (f" tagged {regime}" if regime else "")
			+ ". THIS SITE HAS NO PER-ROLE REQUIREMENT TABLE, so every active employee is held "
			"against every one of them. That over-reports — a bookkeeper is not a pesticide "
			"handler and does not need WPS — so read `non_compliant` as 'has none of these', "
			"not as a citation. Narrow with `regime` or `training_type` to ask the question you "
			"actually mean."
		),
		"actor": actor,
	}

	if not requirements:
		data["note"] = (
			f"No active {training.TYPE_DOCTYPE} matched, so the matrix has no columns and every "
			"employee is reported with no requirements. A curriculum is created the first time "
			"somebody records a session of it — if this operation has trained nobody, that is "
			"the finding."
		)
	elif not roster:
		data["note"] = (
			f"{company} has no Active employees, so the matrix has no rows. A seasonal operation "
			"between crews reads this way legitimately; one mid-season does not."
		)
	else:
		data["note"] = (
			f"{non} of {len(matrix)} employee(s) hold none of the {len(requirements)} "
			f"curriculum(s) reported, and {partially} hold some. Both counts are as of {as_of} — "
			"a record completed after that date is deliberately not counted, so this report run "
			"for a past audit says what was true then."
		)
	if roster_truncated:
		data["truncation_note"] = (
			f"{company} has more than {EMPLOYEE_CAP} Active employees and this is the first "
			f"{EMPLOYEE_CAP} by name. The summary counts describe THAT subset, not the company."
		)
		data["truncated"] = True
	else:
		data["truncated"] = False
	if len(requirements) >= REQUIREMENT_CAP:
		data["requirement_truncation_note"] = (
			f"{REQUIREMENT_CAP} curricula is the cap and it was reached. Narrow by `regime` or "
			"`training_type`, or the matrix is missing columns it does not name."
		)

	return ToolResult(
		data=data,
		summary=(
			f"{len(matrix)} employee(s) against {len(requirements)} curriculum(s) as of {as_of}: "
			f"{fully} fully compliant, {partially} partially, {non} non-compliant"
		),
	)


def _superseded(doc) -> list:
	"""Earlier records of the same training for the same person, which this replaces.

	Reported, never deleted. Last year's WPS card is the evidence that the crew was
	trained LAST year, and an auditor asking about last season wants exactly that
	row — so a renewal adds a record rather than editing one. Saying which ones it
	supersedes is what stops somebody deleting them to tidy up.
	"""
	earlier = training.rows(
		{
			"employee": doc.employee,
			"training_type": doc.training_type,
			"completed_date": ("<", str(doc.completed_date)),
		},
		limit=20,
	)
	return [
		{
			"name": row["name"],
			"completed_date": str(row.get("completed_date") or "") or None,
			"expires_date": str(row.get("expires_date") or "") or None,
		}
		for row in earlier
	]


# ── 2. list_trainings ───────────────────────────────────────────────────────
def list_trainings(args: dict) -> ToolResult:
	"""The training register, filtered the four ways an audit or a calendar asks."""
	_require()
	actor = employee_tool.require_hr_role()
	today = frappe.utils.today()
	limit = min(as_limit(args), RECORD_CAP)

	filters = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		allowed = _readable_companies(actor)
		if allowed:
			filters["company"] = ("in", allowed)

	person = as_str(args, "employee")
	if person:
		filters["employee"] = employee_tool.resolve_employee(person)

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["completed_date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["completed_date"] = (">=", from_date)
	elif to_date:
		filters["completed_date"] = ("<=", to_date)

	regime = as_str(args, "regime")
	if regime and not training.canon(regime):
		raise ToolError(f"regime {regime!r} is not one this app knows. {training.vocabulary_note()}")

	rows = training.rows(filters, limit=max(limit * 4, limit))
	described = [training.describe(row, today) for row in rows]

	if regime:
		target = training.canon(regime)
		described = [row for row in described if target in row["regimes"]]

	status = as_str(args, "status")
	if status:
		wanted = {
			option.lower(): option
			for option in (training.STATUS_ACTIVE, training.STATUS_EXPIRING, training.STATUS_EXPIRED)
		}.get(status.strip().lower())
		if not wanted:
			raise ToolError(
				f"status {status!r} is not one of Active, Expiring, Expired. Note this filters on "
				"the status AS OF TODAY, computed from the expiry date rather than read off the "
				"stored column — a record saved in March holds March's answer."
			)
		described = [row for row in described if row["status_now"] == wanted]

	within = args.get("expiring_within_days")
	if within not in (None, ""):
		try:
			days = int(within)
		except (TypeError, ValueError):
			raise ToolError(f"expiring_within_days must be a whole number of days, got {within!r}.") from None
		if days < 0:
			raise ToolError(
				"expiring_within_days cannot be negative — use status='Expired' for lapsed training."
			)
		described = [
			row
			for row in described
			if row["days_until_expiry"] is not None and row["days_until_expiry"] <= days
		]

	if as_bool(args, "unreviewed_only", False):
		described = [row for row in described if not row["supervisor_reviewed"]]

	truncated = len(described) > limit
	described = described[:limit]

	expired = [row["name"] for row in described if row["status_now"] == training.STATUS_EXPIRED]
	expiring = [row["name"] for row in described if row["status_now"] == training.STATUS_EXPIRING]
	unreviewed = [row["name"] for row in described if not row["supervisor_reviewed"]]
	unsigned = [row["name"] for row in described if not row["trainee_signed"]]

	by_regime = {}
	for row in described:
		for tag in row["regimes"] or ["(untagged)"]:
			by_regime[tag] = by_regime.get(tag, 0) + 1

	data = {
		"company": company,
		"regime": training.canon(regime) if regime else None,
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"records": described,
		"by_regime": dict(sorted(by_regime.items())),
		"expired": expired,
		"expiring": expiring,
		"without_supervisor_review": unreviewed,
		"without_trainee_signature": unsigned,
		"regime_vocabulary": training.REGIME_NOTES,
		"note": (
			f"{len(expired)} lapsed and {len(expiring)} inside the 90-day window. The "
			"training_expiring rule raises these on the compliance calendar every hour without "
			"anybody running this tool."
			if (expired or expiring)
			else "Nothing in this selection has lapsed or is inside its renewal window."
		),
	}
	if unreviewed:
		data["review_note"] = (
			f"{len(unreviewed)} record(s) have no supervisor review. FSMA §112.161(b) requires "
			"worker training records to be reviewed, dated and signed by a supervisor within a "
			"reasonable time after the record is made, and this is the element a GAP-only "
			"operation most often lacks — FDA cites it even where the training itself was fine. "
			"sign_training_supervisor_review closes it."
		)
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} record(s) matched and this is the first {limit}. Narrow by "
			"company, employee, regime or period before relying on the counts above."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} training record(s)"
			+ (f" tagged {training.canon(regime)}" if regime else "")
			+ f"; {len(expired)} expired, {len(expiring)} expiring, {len(unreviewed)} unreviewed"
		),
	)


# ── 3. get_training ─────────────────────────────────────────────────────────
def get_training(args: dict) -> ToolResult:
	"""One training record in full, with the person's history of the same training."""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_record(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	described = training.describe(row)
	gaps = training.fsma_161_gaps(described)

	history = [
		{
			"name": entry["name"],
			"completed_date": str(entry.get("completed_date") or "") or None,
			"expires_date": str(entry.get("expires_date") or "") or None,
			"regimes": training.parse(entry.get("regimes")),
			"status_now": training.status_for(entry.get("expires_date")),
		}
		for entry in training.rows({"employee": row.get("employee")}, limit=100)
	]
	same_training = [entry for entry in history if entry["name"] != row.get("name")]
	later = [
		entry["name"]
		for entry in same_training
		if str(entry["completed_date"] or "") > str(row.get("completed_date") or "")
	]

	data = {
		**described,
		"retention_years": training.retention_years(described["regimes"]),
		"retention_note": training.retention_note(described["regimes"]),
		"fsma_112_161_gaps": gaps,
		"employee_training_history": history,
		"superseded_by": later or None,
		"regime_notes": {
			regime: training.REGIME_NOTES[regime]
			for regime in described["regimes"]
			if regime in training.REGIME_NOTES
		},
	}
	if gaps:
		data["gap_note"] = (
			"This record is missing elements FSMA §112.161 asks for. They are listed rather "
			"than fixed: a signature added now would be a signature dated now, and a record "
			"assembled before an inspection is what an inspector is trained to spot."
		)
	if later:
		data["supersession_note"] = (
			f"{len(later)} later record(s) exist for this person. This one is still evidence "
			"about ITS OWN period and should not be deleted — an auditor asking about last "
			"season wants the row that was true last season."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{row.get('name')} — {described['training_type']} for {described['employee_name']} "
			f"on {described['completed_date']} ({described['status_now']}"
			+ (f", expires {described['expires_date']}" if described["expires_date"] else ", one-time")
			+ ")"
		),
	)


# ── 4. sign_training_supervisor_review ──────────────────────────────────────
def sign_training_supervisor_review(args: dict) -> ToolResult:
	"""Record the §112.161(b) supervisor review on one training record.

	A SEPARATE TOOL FROM `record_training`, DELIBERATELY. §112.161(b) asks for a
	review "within a reasonable time after the record is made" — which is a
	sequence, not a form field. A single call that took both signatures would make
	it trivial to record both at the same instant, and simultaneous signatures are
	the shape of a record an inspector reads as assembled rather than kept.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_record(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	supervisor = employee_tool.resolve_employee(as_str(args, "supervisor", required=True))
	if supervisor == str(row.get("employee") or ""):
		raise ToolError(
			f"{supervisor} is the person this record says was trained, and §112.161(b) asks for "
			"a supervisor or responsible party — a second pair of eyes, or it is nothing. "
			"Nothing was changed."
		)
	supervisor_row = (
		frappe.db.get_value("Employee", supervisor, ["employee_name", "company"], as_dict=True) or {}
	)
	if (
		supervisor_row.get("company")
		and row.get("company")
		and str(supervisor_row["company"]) != str(row["company"])
	):
		raise ToolError(
			f"{supervisor} ({supervisor_row.get('employee_name')}) is employed by "
			f"{supervisor_row['company']} and this record belongs to {row['company']}. A review "
			"by somebody with no responsibility for the entity is not the review the rule asks "
			"for. Nothing was changed."
		)

	existing = str(row.get("supervisor_reviewed_by") or "")
	if existing and existing != supervisor and not as_bool(args, "replace_reviewer", False):
		raise ToolError(
			f"{row['name']} was already reviewed by {existing} on "
			f"{row.get('supervisor_reviewed_on') or 'an unrecorded date'}. Replacing a signature "
			"that is already on a compliance record is a decision rather than a retry — pass "
			"replace_reviewer=true to say so. Nothing was changed."
		)

	reviewed_on = as_str(args, "reviewed_on") or frappe.utils.now()
	if str(reviewed_on)[:10] < str(row.get("completed_date") or ""):
		raise ToolError(
			f"the review is dated {reviewed_on} and the training was completed on "
			f"{row.get('completed_date')}. §112.161(b) is a review OF A RECORD, and a record "
			"that did not exist yet was not reviewed. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	doc.supervisor_reviewed_by = supervisor
	doc.supervisor_reviewed_on = reviewed_on
	signature = as_str(args, "supervisor_signature")
	if signature:
		doc.supervisor_signature = signature
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = training.describe(dict(doc.as_dict()))
	gaps = training.fsma_161_gaps(described)
	lag = training.days_until(str(row.get("completed_date") or ""), str(reviewed_on)[:10])

	data = {
		**described,
		"actor": actor,
		"supervisor_name": supervisor_row.get("employee_name"),
		"replaced_reviewer": existing or None,
		"days_between_training_and_review": lag,
		"fsma_112_161_gaps": gaps,
		"note": (
			"§112.161(b) is satisfied for this record: reviewed, dated and signed by a "
			"supervisor. This is the element a GAP-only operation most often lacks, and FDA "
			"cites it even where the underlying training was fine."
		),
	}
	if not signature:
		data["signature_note"] = (
			"No supervisor signature file was attached — the review is recorded by name and "
			"date only. Upload the signature through stage_file_chunk and pass its docname as "
			"`supervisor_signature` to complete it."
		)
	if lag is not None and lag > 30:
		data["timeliness_note"] = (
			f"This review is {lag} days after the training. The rule's phrase is 'within a "
			"reasonable time after the record is made', and weekly is the practice it is read "
			"against — a review a quarter later is recorded honestly here, but it is not a "
			"strong answer to the question an inspector asks about it."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{supervisor_row.get('employee_name') or supervisor} reviewed {row['name']} "
			f"({described['training_type']} for {described['employee_name']}) on {reviewed_on}"
		),
		docstatus_delta="0 → 0 (amended)",
	)
