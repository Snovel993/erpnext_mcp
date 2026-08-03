# SPDX-License-Identifier: MIT
"""The regime vocabulary, and the one place that decides what a training record means.

WHY THIS MODULE EXISTS AT ALL. Four things read an Employee Training Record and
every one of them has to agree about the same two questions — *which audits does
this satisfy* and *has it lapsed*. The controller computes `status` on save; the
`training_expiring` rule computes severity every hour; `list_trainings` filters by
regime; `generate_audit_packet` pulls the subset one auditor is entitled to see.
Four copies of "does this row carry WPS" is four chances for a packet to include a
record the calendar thinks is expired, and the disagreement would surface in a
room with an inspector in it. So the vocabulary, the parser, the windows and the
retention table live here and the four callers import them.

────────────────────────────────────────────────────────────────────────────
REGIMES ARE A DELIMITED TAG LIST, NOT A CHILD TABLE
────────────────────────────────────────────────────────────────────────────

The v0.19.0 spec called for a Frappe Table MultiSelect. That would have cost
TWO extra doctypes — a `Compliance Regime` master holding eight records, and a
child table linking to it — plus a seeder to put the eight records on every site
before anybody could file a training record, which is precisely the post-deploy
configuration step the release was meant to remove. Against the house rule
("avoid table sprawl") that is a bad trade for a closed list of eight tokens that
has changed twice in fifteen years.

So `regimes` is a Small Text holding canonical tokens joined by commas, and
EVERY read of it goes through `parse` here. Two consequences worth stating:

  * MATCHING IS BY TOKEN, NEVER BY SUBSTRING. `"GlobalGAP"` contains `"GAP"`,
    so a `LIKE '%GAP%'` filter would put every GlobalGAP record into a USDA GAP
    packet — a real, quiet, wrong-evidence bug. `matches()` compares parsed
    tokens, and the query helpers below fetch rows and filter in Python rather
    than pushing a LIKE into SQL. That is the same thing `audit_packets._policies`
    already does for policy categories, at the same row counts.

  * AN UNKNOWN TOKEN IS REFUSED AT WRITE TIME, not silently dropped. A training
    record tagged `"OSHA"` when the vocabulary says `"OR-OSHA"` is a record that
    will be missing from the OR-OSHA packet and present nowhere, and the operator
    will not find out until an inspector does.

────────────────────────────────────────────────────────────────────────────
RETENTION IS THE LONGEST TAG, AND IT IS COMPUTED RATHER THAN STORED
────────────────────────────────────────────────────────────────────────────

Each regime keeps records for a different span: NOP five years (7 CFR
205.103(b)(4)), OR-OSHA three (no published universal window; three is the
defensible inspection reach-back), FSMA two (21 CFR 112.164(a)(1), verbatim: two
years past the date the record was created), WPS two (40 CFR 170.309), GAP two by
industry practice. A record tagged GAP *and* NOP is a five-year record — the
longest applicable window governs, because destroying it at two would destroy
NOP evidence.

Computed on read rather than written into a column, because the answer changes
when a regime is added to an existing record, and a stored `destroy_after` that
was right in 2026 and wrong in 2027 is worse than no column at all.
"""

from __future__ import annotations

import frappe

from . import compat

DOCTYPE = "Employee Training Record"

#: The eight tags, canonical spelling. Order is the order they are reported in,
#: chosen so the two federal food-safety regimes lead and `Other` trails.
REGIMES = (
	"FSMA",
	"GAP",
	"GlobalGAP",
	"PrimusGFS",
	"NOP",
	"WPS",
	"OR-OSHA",
	"Other",
)

#: What each tag is, in the words the rule uses. Surfaced by `list_trainings` so
#: a caller choosing a tag is choosing against the citation rather than a guess.
REGIME_NOTES = {
	"FSMA": "FDA Produce Safety Rule, 21 CFR 112 — Subpart C personnel training (§112.21–.30) and the §112.161 record form.",
	"GAP": "USDA Good Agricultural Practices / Harmonized GAP — annual worker food safety and hygiene training.",
	"GlobalGAP": "GLOBALG.A.P. Integrated Farm Assurance — the export and retailer scheme's worker training clauses.",
	"PrimusGFS": "PrimusGFS — the buyer-driven GFSI scheme's training requirements.",
	"NOP": "USDA National Organic Program, 7 CFR 205 — training for anyone handling organic inputs, via the ACA (Oregon Tilth here).",
	"WPS": "EPA Worker Protection Standard, 40 CFR 170.401 (worker) / .501 (handler) — every 12 months, records kept 2 years per §170.309. Oregon adopts it at OAR 437-002-0170 and adds OAR 437-004-6501/6502.",
	"OR-OSHA": "Oregon OSHA agriculture, OAR 437 Division 4 — heat illness (OAR 437-004-1131), field sanitation hygiene (-1110(9)), hazard communication (-9800), PPE (-1005(10)), seasonal safety orientation (-0240).",
	"Other": "A regime this app does not model. Recorded so the training is not lost, but it will not be pulled into any named audit packet.",
}

#: Years each regime keeps a training record. See the module docstring; the
#: longest tag on a record governs.
RETENTION_YEARS = {
	"FSMA": 2,
	"GAP": 2,
	"GlobalGAP": 2,
	"PrimusGFS": 2,
	"NOP": 5,
	"WPS": 2,
	"OR-OSHA": 3,
	"Other": 3,
}

#: Where each retention figure comes from. Kept beside the number because a
#: retention period nobody can cite is a retention period somebody will shorten.
RETENTION_CITATIONS = {
	"FSMA": "21 CFR 112.164(a)(1) — 2 years past the date the record was created",
	"GAP": "no published universal window; 2 years is the Harmonized GAP traceability practice",
	"GlobalGAP": "scheme rules; 2 years is the common certification-cycle minimum",
	"PrimusGFS": "scheme rules; 2 years is the common certification-cycle minimum",
	"NOP": "7 CFR 205.103(b)(4) — 5 years beyond creation",
	"WPS": "40 CFR 170.309 — 2 years at the establishment",
	"OR-OSHA": "no published universal ag window; 3 years is the defensible inspection reach-back",
	"Other": "unknown regime — 3 years applied as the general defensible floor",
}

#: Spellings a caller will genuinely type, mapped to the canon. Lower-cased keys.
#: Deliberately SHORT: this is for the spellings a regulator itself uses (OSHA
#: writes "OR-OSHA" and "Oregon OSHA"; FDA writes "21 CFR 112"), not a fuzzy
#: matcher. Anything not here is refused by name with the eight listed.
ALIASES = {
	"or osha": "OR-OSHA",
	"or-osha": "OR-OSHA",
	"oregon osha": "OR-OSHA",
	"orosha": "OR-OSHA",
	"oar 437": "OR-OSHA",
	"osha": "OR-OSHA",
	"fsma": "FSMA",
	"21 cfr 112": "FSMA",
	"produce safety rule": "FSMA",
	"psr": "FSMA",
	"gap": "GAP",
	"usda gap": "GAP",
	"harmonized gap": "GAP",
	"globalgap": "GlobalGAP",
	"global gap": "GlobalGAP",
	"global g.a.p.": "GlobalGAP",
	"primusgfs": "PrimusGFS",
	"primus": "PrimusGFS",
	"nop": "NOP",
	"organic": "NOP",
	"usda organic": "NOP",
	"7 cfr 205": "NOP",
	"tilth": "NOP",
	"wps": "WPS",
	"worker protection standard": "WPS",
	"40 cfr 170": "WPS",
	"other": "Other",
}

#: How long before expiry a training record starts asking to be renewed. Ninety
#: days is not a preference: a PSA grower course runs on a published schedule
#: with weeks of lead time, and an applicator licence renewal goes through ODA.
#: Inside thirty, booking a course is no longer reliably enough to avoid the lapse.
EXPIRING_WINDOW_DAYS = 90
CRITICAL_WINDOW_DAYS = 30

STATUS_ACTIVE = "Active"
STATUS_EXPIRING = "Expiring"
STATUS_EXPIRED = "Expired"


# ── the vocabulary ──────────────────────────────────────────────────────────
def canon(value: str) -> str:
	"""One regime token in canonical spelling, or "" if it is not one of the eight."""
	text = str(value or "").strip()
	if not text:
		return ""
	for regime in REGIMES:
		if text.lower() == regime.lower():
			return regime
	return ALIASES.get(text.lower().replace(".", "").replace("_", " "), "")


def parse(raw) -> list:
	"""Whatever a caller or a stored column holds, as canonical tokens.

	Accepts a list, a comma-separated string, or a single token, and drops
	anything that is not one of the eight. Deduplicated and returned in `REGIMES`
	order rather than the order they arrived, so two records tagged with the same
	set compare equal and a packet reads consistently.

	SILENT ON UNKNOWNS BY DESIGN — this is the READ path, and a column that
	somehow holds a stale token must not take a whole audit packet down with it.
	The WRITE path is `require`, which refuses by name.
	"""
	if raw is None:
		return []
	if isinstance(raw, (list, tuple, set)):
		pieces = list(raw)
	else:
		pieces = str(raw).replace("\n", ",").split(",")
	found = {canon(piece) for piece in pieces}
	found.discard("")
	return [regime for regime in REGIMES if regime in found]


def require(raw, label: str = "regimes") -> list:
	"""The write path: canonical tokens, or a refusal naming what was not understood."""
	if isinstance(raw, (list, tuple, set)):
		pieces = [str(piece).strip() for piece in raw]
	else:
		pieces = [piece.strip() for piece in str(raw or "").replace("\n", ",").split(",")]
	pieces = [piece for piece in pieces if piece]
	if not pieces:
		raise ValueError(
			f"{label} is required. Which audits this training counts towards is the entire "
			f"point of the record — one of: {', '.join(REGIMES)}. An untagged training record "
			"is evidence that appears in no packet."
		)
	unknown = [piece for piece in pieces if not canon(piece)]
	if unknown:
		raise ValueError(
			f"{label} does not recognise {', '.join(repr(piece) for piece in unknown)}. "
			f"The eight are: {', '.join(REGIMES)}. A tag that is nearly right — 'OSHA' for "
			"'OR-OSHA' — would file this training where no packet looks for it, so it is "
			"refused rather than corrected."
		)
	return parse(pieces)


def join(values) -> str:
	"""Canonical tokens as the column stores them."""
	return ",".join(parse(values))


def matches(stored, wanted: str) -> bool:
	"""Does a stored `regimes` column carry this regime? By TOKEN, never substring.

	See the module docstring: `"GlobalGAP"` contains `"GAP"`, and a substring
	match would put every GLOBALG.A.P. record in a USDA GAP packet.
	"""
	target = canon(wanted)
	return bool(target) and target in parse(stored)


def topics(raw) -> list:
	"""`content_topics_covered` as a list, however it was typed.

	Comma- or newline-separated, and bullet characters stripped, because somebody
	pasting from a course outline will paste bullets.
	"""
	if raw is None:
		return []
	if isinstance(raw, (list, tuple)):
		pieces = [str(piece) for piece in raw]
	else:
		pieces = str(raw).replace("\n", ",").split(",")
	out = []
	for piece in pieces:
		text = piece.strip().lstrip("-•*").strip()
		if text and text not in out:
			out.append(text)
	return out


def topics_text(raw) -> str:
	return ", ".join(topics(raw))


# ── lapse ───────────────────────────────────────────────────────────────────
def status_for(expires_date, today: str = "") -> str:
	"""The three-way status, from the expiry date and nothing else.

	No expiry means Active forever — a new-hire orientation does not lapse, and
	giving it a status that could go Expired would put a renewal on a calendar
	that nobody can clear.
	"""
	if not expires_date:
		return STATUS_ACTIVE
	today = today or frappe.utils.today()
	remaining = days_until(today, expires_date)
	if remaining is None:
		return STATUS_ACTIVE
	if remaining < 0:
		return STATUS_EXPIRED
	if remaining <= EXPIRING_WINDOW_DAYS:
		return STATUS_EXPIRING
	return STATUS_ACTIVE


def days_until(today: str, target) -> int | None:
	"""Whole days from `today` to `target`. Negative once `target` has passed."""
	if not target:
		return None
	try:
		return int(frappe.utils.date_diff(str(target), today))
	except Exception:
		return None


def retention_years(stored) -> int:
	"""The longest retention any tag on this record demands. See the docstring."""
	found = parse(stored)
	if not found:
		# An untagged record still has to be kept for something. Three years is
		# the general defensible floor and is the safe direction to be wrong in.
		return 3
	return max(RETENTION_YEARS.get(regime, 3) for regime in found)


def retention_note(stored) -> str:
	found = parse(stored)
	if not found:
		return (
			"This record carries no regime tag, so no audit packet will pull it. Three years "
			"is applied as the general floor. Tag it with record_training's `regimes`."
		)
	years = retention_years(found)
	longest = [regime for regime in found if RETENTION_YEARS.get(regime, 3) == years]
	return (
		f"Keep {years} year(s) — the longest window any tag on this record demands, which is "
		f"{', '.join(longest)} ({'; '.join(RETENTION_CITATIONS[regime] for regime in longest)}). "
		"The longest governs: destroying it earlier would destroy the evidence for that regime."
	)


# ── reads every caller shares ───────────────────────────────────────────────
#: Every column the tools, the rule and the packet builder read. One list, so a
#: field added to the doctype and not to this tuple is a field nothing surfaces.
FIELDS = (
	"name",
	"employee",
	"employee_name",
	"company",
	"farm_name_snapshot",
	"training_type",
	"training_source",
	"provider",
	"completed_date",
	"completed_time",
	"activity_datetime",
	"expires_date",
	"certificate_file",
	"regimes",
	"content_topics_covered",
	"person_performed_signature",
	"supervisor_reviewed_by",
	"supervisor_reviewed_on",
	"supervisor_signature",
	"status",
	"notes",
)


def rows(filters: dict, limit: int = 2000, order_by: str = "completed_date desc") -> list:
	"""Training records, selecting only the columns this site actually has."""
	if not compat.doctype_exists(DOCTYPE):
		return []
	return [
		dict(row)
		for row in frappe.db.get_all(
			DOCTYPE,
			filters=filters or {},
			fields=compat.existing_fields(DOCTYPE, FIELDS),
			order_by=order_by,
			limit=limit,
		)
		or []
	]


def for_regime(regime: str, company: str = "", start: str = "", end: str = "", limit: int = 2000) -> list:
	"""Every training record carrying `regime`, over a period, for one company.

	The regime filter runs in PYTHON after the fetch, not as a SQL LIKE. See the
	module docstring — a LIKE on `%GAP%` matches GlobalGAP and would hand a USDA
	GAP auditor evidence from a different scheme.

	The date filter is on `completed_date`, which is when the training happened.
	A record whose training was inside the period belongs in the packet for that
	period whether or not it has since expired — an auditor asking about last
	season is asking what the crew had been taught by then.
	"""
	filters = {}
	if company:
		filters["company"] = company
	if start and end:
		filters["completed_date"] = ("between", [start, end])
	elif start:
		filters["completed_date"] = (">=", start)
	elif end:
		filters["completed_date"] = ("<=", end)
	found = rows(filters, limit=limit)
	target = canon(regime)
	if not target:
		return found
	return [row for row in found if matches(row.get("regimes"), target)]


def describe(row: dict, today: str = "") -> dict:
	"""One record in the shape every tool and packet reports it.

	Computed columns rather than stored ones: `status_now`, `days_until_expiry`,
	`retention_years` and `supervisor_reviewed` all answer questions about TODAY,
	and a column written last March would answer them about last March.
	"""
	today = today or frappe.utils.today()
	found = parse(row.get("regimes"))
	expires = str(row.get("expires_date") or "") or None
	reviewed_by = row.get("supervisor_reviewed_by") or None
	return {
		"name": row.get("name"),
		"employee": row.get("employee"),
		"employee_name": row.get("employee_name"),
		"company": row.get("company"),
		"farm_name": row.get("farm_name_snapshot") or None,
		"training_type": row.get("training_type"),
		"training_source": row.get("training_source"),
		"provider": row.get("provider") or None,
		"completed_date": str(row.get("completed_date") or "") or None,
		"completed_time": str(row.get("completed_time") or "") or None,
		"activity_datetime": str(row.get("activity_datetime") or "") or None,
		"expires_date": expires,
		"one_time": expires is None,
		"status": row.get("status") or status_for(expires, today),
		"status_now": status_for(expires, today),
		"days_until_expiry": days_until(today, expires),
		"regimes": found,
		"content_topics_covered": topics(row.get("content_topics_covered")),
		"certificate_attached": bool(row.get("certificate_file")),
		"trainee_signed": bool(row.get("person_performed_signature")),
		"supervisor_reviewed": bool(reviewed_by),
		"supervisor_reviewed_by": reviewed_by,
		"supervisor_reviewed_on": str(row.get("supervisor_reviewed_on") or "") or None,
		"supervisor_signed": bool(row.get("supervisor_signature")),
		"retention_years": retention_years(found),
		"notes": row.get("notes") or None,
	}


def fsma_161_gaps(described: dict) -> list:
	"""Which §112.161 elements this record is missing, in the rule's own terms.

	Reported rather than refused. A training that happened and was recorded
	without a signature is still evidence that the training happened, and a system
	that refused the record would leave the operation with neither the record nor
	the signature. But the gap is stated everywhere the record is shown, because
	"missing supervisor sign-off on the §112.161(b) records" is a finding FDA
	writes up even when the underlying activity was fine.
	"""
	gaps = []
	if not described.get("activity_datetime"):
		gaps.append("§112.161(a)(1)(v) — no date and time of the activity")
	if not described.get("farm_name"):
		gaps.append("§112.161(a)(1)(i) — no farm name recorded on the record itself")
	if not described.get("trainee_signed"):
		gaps.append(
			"§112.161(a)(4) — not signed or initialled by the person who performed the activity"
		)
	if not described.get("supervisor_reviewed"):
		gaps.append(
			"§112.161(b) — not reviewed, dated and signed by a supervisor. Worker training "
			"records are on the list that requires it, and this is the element a GAP-only "
			"operation most often lacks"
		)
	if not described.get("content_topics_covered"):
		gaps.append("§112.30(b) — no topics covered recorded")
	return gaps
