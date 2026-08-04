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
ONE VOCABULARY, TWO STORAGE SHAPES — AND WHY v0.19.2 REVERSED HALF A DECISION
────────────────────────────────────────────────────────────────────────────

v0.19.0 refused a Frappe Table MultiSelect here and said so at length: it would
cost two extra doctypes plus a seeder, and against the house rule ("avoid table
sprawl") that looked like a bad trade for a closed list of tokens that has
changed twice in fifteen years. That argument was right about `Employee Training
Record.regimes` and it still holds — THAT COLUMN IS STILL A DELIMITED TAG LIST.

It was wrong about everything else, and v0.19.2 is where that showed. Two new
callers needed a regime tag on records this module does not own — `Compliance
Alert.regime`, so a calendar can be read one audit at a time, and `Training
Type.regimes`, so a curriculum carries its own regimes instead of every record
of it restating them. A Small Text on those would have meant a third and fourth
hand-parsed column, and the Desk cannot offer a picker over one: an operator
tagging an alert would be typing `OR-OSHA` from memory into a text box, which is
precisely the near-miss `require` exists to refuse. So those two are Table
MultiSelects over a `Compliance Regime` master.

WHAT DID NOT CHANGE IS THE VOCABULARY. `REGIMES` below is still the only list,
the master is SEEDED FROM IT by `seed_regimes` on every migrate, and `canon`,
`parse`, `require` and `matches` are still the only readers. A child table and a
comma-separated column that disagreed about whether a row carries WPS is exactly
the failure this module was written to prevent, so the child rows are converted
to tokens at the boundary (`from_rows`) and back (`to_rows`), and nothing
downstream knows which shape a given field used.

Two consequences worth stating, and they are unchanged:

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
TRAINING TYPE IS A RECORD NOW, AND REGIMES ARE ITS PROPERTY
────────────────────────────────────────────────────────────────────────────

v0.19.0 made `training_type` free text and defended it: a Select would have to be
edited every time a regulator renames a curriculum. Still true — so v0.19.2 does
not make it a Select. It makes it a LINK to `Training Type`, a master anybody can
add a row to, and `ensure_type` creates one from free text on first use so no
caller has to configure anything before filing a record.

What that buys is the thing free text could not: "WPS Handler Training satisfies
WPS" is a fact about the curriculum, not about one afternoon, and stating it once
on the type is how thirty records of it stop being thirty chances to mistype the
tag. `TYPE_REGIME_HEURISTICS` is what a name implies when nobody has said —
applied when a type is auto-created, never applied again afterwards, because an
operator who corrected the regimes on a type meant it.


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

#: The `Compliance Regime` master, seeded from `REGIMES` by `seed_regimes`.
REGIME_DOCTYPE = "Compliance Regime"

#: The Table MultiSelect child that links to it. One shape of row, two parents:
#: `Compliance Alert.regime` and `Training Type.regimes`.
REGIME_LINK_DOCTYPE = "Compliance Regime Link"

#: The curriculum master `Employee Training Record.training_type` links to.
TYPE_DOCTYPE = "Training Type"

#: The ten tags, canonical spelling. Order is the order they are reported in,
#: chosen so the two federal food-safety regimes lead and the two catch-alls
#: trail.
#:
#: v0.19.2 ADDED `OTCO` AND `Internal`, and both came from the alert engine
#: rather than from the training register. `Internal` is what a rule is tagged
#: with when the obligation is the operation's own — a detector test nobody
#: outside the farm audits, a filing deadline, a corrective action — and without
#: it every such alert would have had to claim a regime it does not answer to,
#: which is worse than saying "ours". `OTCO` is Oregon Tilth Certified Organic,
#: the certifier that actually holds the file here; NOP is the federal rule it
#: certifies against, and a Tilth inspection asks for OSP-practice evidence a
#: bare NOP tag does not distinguish.
REGIMES = (
	"FSMA",
	"GAP",
	"GlobalGAP",
	"PrimusGFS",
	"NOP",
	"OTCO",
	"WPS",
	"OR-OSHA",
	"Internal",
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
	"OTCO": "Oregon Tilth Certified Organic — the accredited certifying agent that holds the organic file. NOP is the rule; this is the inspection, and it asks for evidence against the Organic System Plan as written rather than against 7 CFR 205 in the abstract.",
	"WPS": "EPA Worker Protection Standard, 40 CFR 170.401 (worker) / .501 (handler) — every 12 months, records kept 2 years per §170.309. Oregon adopts it at OAR 437-002-0170 and adds OAR 437-004-6501/6502.",
	"OR-OSHA": "Oregon OSHA agriculture, OAR 437 Division 4 — heat illness (OAR 437-004-1131), field sanitation hygiene (-1110(9)), hazard communication (-9800), PPE (-1005(10)), seasonal safety orientation (-0240).",
	"Internal": "The operation's own standard, answering to nobody outside it. Real work with a real due date and no external auditor — which is why it is a tag rather than an absence: an item nobody is coming to inspect still has to be done, and filing it under a regime it does not answer to would put it in a packet where it invites a question.",
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
	"OTCO": 5,
	"WPS": 2,
	"OR-OSHA": 3,
	"Internal": 3,
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
	"OTCO": "the certifier's file is kept against 7 CFR 205.103(b)(4) — 5 years, same as the rule it certifies",
	"WPS": "40 CFR 170.309 — 2 years at the establishment",
	"OR-OSHA": "no published universal ag window; 3 years is the defensible inspection reach-back",
	"Internal": "no external requirement; 3 years is the general defensible floor this app applies where nobody else sets one",
	"Other": "unknown regime — 3 years applied as the general defensible floor",
}

#: Spellings a caller will genuinely type, mapped to the canon. Lower-cased keys.
#: Deliberately SHORT: this is for the spellings a regulator itself uses (OSHA
#: writes "OR-OSHA" and "Oregon OSHA"; FDA writes "21 CFR 112"), not a fuzzy
#: matcher. Anything not here is refused by name with the whole list shown.
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
	# DELIBERATELY LEFT POINTING AT NOP even though v0.19.2 added OTCO, which is
	# what Oregon Tilth actually is. Records written through this alias since
	# v0.19.0 are stored as NOP, and repointing it would make the same word mean
	# a different set of rows on the read path than it wrote on the write path —
	# a filter that quietly stops matching its own history. `otco` below is the
	# unambiguous spelling, and it is the one the seeder and the heuristics use.
	"tilth": "NOP",
	"otco": "OTCO",
	"oregon tilth certified organic": "OTCO",
	"wps": "WPS",
	"worker protection standard": "WPS",
	"40 cfr 170": "WPS",
	"internal": "Internal",
	"in-house": "Internal",
	"in house": "Internal",
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
def vocabulary_note(label: str = "The vocabulary is") -> str:
	"""The whole list, in one sentence, for a refusal or a schema description.

	Written once here rather than typed into each caller, because v0.19.2 found
	four places saying "the eight are" against a list that had become ten. A
	count in prose is a second copy of a fact the tuple already holds.
	"""
	return f"{label}: {', '.join(REGIMES)}."


def canon(value: str) -> str:
	"""One regime token in canonical spelling, or "" if it is not in `REGIMES`."""
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
	anything not in `REGIMES`. Deduplicated and returned in `REGIMES`
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
			f"{vocabulary_note()} A tag that is nearly right — 'OSHA' for "
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


# ── the child table shape ───────────────────────────────────────────────────
#
# Two fields store regimes as Table MultiSelect rows rather than as a delimited
# column — `Compliance Alert.regime` and `Training Type.regimes` — and these four
# functions are the ONLY place that shape is known. Everything downstream gets
# canonical tokens and cannot tell which storage a given field used, which is the
# property that stops a child table and a comma-separated column from disagreeing
# about whether a row carries WPS.
def to_rows(values) -> list:
	"""Canonical tokens as Table MultiSelect child rows, in `REGIMES` order.

	Goes through `parse`, so an unknown token is dropped here rather than becoming
	a child row that fails link validation on insert with a message about a
	missing `Compliance Regime` — which is a true statement about the wrong thing.
	The WRITE path callers use `require` first and get the refusal by name.
	"""
	return [{"regime": regime} for regime in parse(values)]


def from_rows(rows) -> list:
	"""Table MultiSelect child rows as canonical tokens.

	Accepts what `doc.get(fieldname)` returns (dicts or documents) and what
	`frappe.db.get_all` returns (dicts). Anything else parses to nothing rather
	than raising: this is a READ path, and one malformed row must not take a whole
	audit packet down.
	"""
	if not rows:
		return []
	found = []
	for row in rows:
		if isinstance(row, dict):
			found.append(row.get("regime") or "")
		else:
			found.append(getattr(row, "regime", "") or "")
	return parse(found)


def rows_for_parents(parenttype: str, names, parentfield: str) -> dict:
	"""`{parent docname: [tokens]}` for a batch of parents, in one query.

	One query rather than one per parent. The calendar reads five hundred alerts
	at a time and a per-row child fetch would be five hundred round trips to
	answer a question that is one join.

	A parent with no rows is ABSENT from the result rather than present with an
	empty list, and callers rely on that: `{}.get(name, [])` and "untagged" are
	the same answer here, whereas a site whose `Compliance Regime Link` DocType is
	missing entirely returns `{}` too and must not be read as "nothing is tagged".
	Callers that care check `compat.doctype_exists` themselves.
	"""
	wanted = [str(name) for name in (names or []) if name]
	if not wanted or not compat.doctype_exists(REGIME_LINK_DOCTYPE):
		return {}
	found: dict = {}
	for row in (
		frappe.db.get_all(
			REGIME_LINK_DOCTYPE,
			filters={"parenttype": parenttype, "parentfield": parentfield, "parent": ("in", wanted)},
			fields=["parent", "regime"],
			limit=len(wanted) * (len(REGIMES) + 1),
		)
		or []
	):
		found.setdefault(str(row.get("parent") or ""), []).append(row.get("regime"))
	return {parent: parse(values) for parent, values in found.items() if parent}


def set_rows(doc, fieldname: str, values) -> list:
	"""Replace a Table MultiSelect's rows with these regimes. Returns the tokens.

	Cleared and rebuilt rather than diffed. The set is small, the order is
	`REGIMES` order so two documents with the same tags compare equal, and a diff
	that got it wrong would leave a stale tag on a record an auditor reads.
	"""
	found = parse(values)
	doc.set(fieldname, [])
	for regime in found:
		doc.append(fieldname, {"regime": regime})
	return found


def seed_regimes() -> dict:
	"""One `Compliance Regime` row per token in `REGIMES`. Idempotent.

	SEEDED FROM THE TUPLE, NEVER TYPED. The master exists so the Desk can offer a
	picker; `REGIMES` is still what decides what a regime IS. A row somebody added
	by hand is left exactly as it is — including one this app does not know, which
	is how an operation with a scheme this app has never heard of records it —
	and a row whose description has been edited is not rewritten, because an
	operator who reworded it meant to.
	"""
	report = {"created": [], "present": [], "failed": []}
	if not compat.doctype_exists(REGIME_DOCTYPE):
		return report
	for regime in REGIMES:
		try:
			if frappe.db.exists(REGIME_DOCTYPE, regime):
				report["present"].append(regime)
				continue
			doc = frappe.new_doc(REGIME_DOCTYPE)
			doc.regime_name = regime
			doc.description = REGIME_NOTES.get(regime) or ""
			doc.retention_years = RETENTION_YEARS.get(regime, 3)
			doc.retention_citation = RETENTION_CITATIONS.get(regime) or ""
			doc.active = 1
			doc.insert(ignore_permissions=True)
			report["created"].append(regime)
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["failed"].append({"name": regime, "reason": f"{type(exc).__name__}: {exc}"})
	return report


# ── training types ──────────────────────────────────────────────────────────
#: What a curriculum's NAME implies about the regimes it answers, applied when a
#: `Training Type` is created from free text and NEVER re-applied afterwards —
#: an operator who corrected the regimes on a type meant it, and a heuristic that
#: kept overwriting them would be a heuristic nobody could disagree with.
#:
#: EVALUATED IN ORDER, first match wins, so the guards come before the traps:
#: `"GlobalGAP Refresher"` contains `"GAP"` and would otherwise be tagged for a
#: USDA GAP audit, which is the same substring bug `matches()` exists to prevent,
#: one layer up. `Heat` precedes `OSHA` because "Heat Illness Prevention (OSHA)"
#: is the heat rule specifically.
TYPE_REGIME_HEURISTICS = (
	(("wps", "worker protection"), ("WPS",)),
	(("psa", "produce safety", "fsma"), ("FSMA",)),
	(("heat",), ("OR-OSHA",)),
	(("osha",), ("OR-OSHA",)),
	(("otco", "oregon tilth"), ("OTCO",)),
	(("nop", "organic"), ("NOP",)),
	(("globalgap", "global gap"), ("GlobalGAP",)),
	(("primus",), ("PrimusGFS",)),
	(("gap",), ("GAP",)),
	(("applicator", "pesticide"), ("WPS", "OR-OSHA")),
)

#: The curricula nearly every Oregon tree-fruit operation runs, seeded on install
#: so the first `record_training` call has something to link to rather than
#: inventing a type from whatever the caller typed. Regimes are stated here
#: rather than derived, because these are the ones worth being sure about; the
#: RETENTION is derived (`retention_years`), so the seed cannot contradict the
#: doctrine that the longest tag on a record governs.
SEED_TRAINING_TYPES = (
	(
		"PSA Grower Training",
		("FSMA",),
		"The Produce Safety Alliance grower course. FSMA §112.22(c) wants at least one supervisor to have completed it; the certificate does not expire.",
	),
	(
		"WPS Handler Training",
		("WPS",),
		"40 CFR 170.501 handler training, every twelve months. A handler whose card has lapsed cannot lawfully perform an application.",
	),
	(
		"WPS Worker Training",
		("WPS",),
		"40 CFR 170.401 worker training, every twelve months, for anybody entering a treated area.",
	),
	(
		"Heat Illness Prevention",
		("OR-OSHA",),
		"OAR 437-004-1131, annually and BEFORE work at any site where the heat index will reach 80 °F. Six named topics must be covered.",
	),
	("OSHA 10", ("OR-OSHA",), "The ten-hour general safety course."),
	("OSHA 30", ("OR-OSHA",), "The thirty-hour supervisor safety course."),
	(
		"Hazard Communication",
		("OR-OSHA", "WPS"),
		"OAR 437-004-9800 — chemical hazards, labels and safety data sheets. Overlaps the WPS pesticide-safety curriculum, so one session tagged for both is one session.",
	),
	(
		"Applicator License Renewal",
		("WPS", "OR-OSHA"),
		"ODA pesticide applicator licence recertification credits under ORS 634.",
	),
	(
		"NOP Handler Training",
		("NOP",),
		"7 CFR 205.201 — handling of approved organic inputs, as written into the Organic System Plan the certifier inspects against.",
	),
	(
		"Food Safety Refresher",
		("GAP", "FSMA"),
		"Annual worker health and hygiene. USDA GAP asks for it by name and FSMA Subpart C asks for it periodically, so one session answers both.",
	),
)


def guess_type_regimes(name: str) -> list:
	"""What a training type's name implies, or `["Internal"]` where it implies nothing.

	`Internal` rather than `[]`, because an untagged type produces untagged
	records and an untagged record appears in no packet — which is a silent way
	to lose evidence. "Ours" is a claim somebody can correct; nothing is not.
	"""
	text = str(name or "").lower()
	for needles, regimes in TYPE_REGIME_HEURISTICS:
		if any(needle in text for needle in needles):
			return parse(regimes)
	return ["Internal"]


def find_type(name: str) -> str:
	"""The existing `Training Type` docname matching this text, or "".

	Case- and whitespace-insensitive, because 'wps handler training' typed into a
	tool argument and 'WPS Handler Training' on the seeded record are the same
	curriculum, and creating a second type for the casing would split one
	curriculum's history across two masters.
	"""
	wanted = " ".join(str(name or "").split()).strip()
	if not wanted or not compat.doctype_exists(TYPE_DOCTYPE):
		return ""
	if frappe.db.exists(TYPE_DOCTYPE, wanted):
		return wanted
	folded = wanted.casefold()
	for existing in frappe.db.get_all(TYPE_DOCTYPE, pluck="name", limit=5000) or []:
		if str(existing).casefold() == folded:
			return str(existing)
	return ""


def ensure_type(name: str, regimes=None, description: str = "") -> dict:
	"""The `Training Type` for this text, creating it from free text if it is new.

	THE FREE-TEXT ERGONOMICS OF v0.19.0, WITH THE MASTER OF v0.19.2. Making
	`training_type` a Link and stopping there would mean the first person to
	record a curriculum this site has not run before gets a link validation error
	instead of a record — which is how a compliance system trains people to file
	the training under whatever name already exists, and that is worse than free
	text ever was.

	AUTO-CREATING A TYPE IS NOT THE SAME DECISION AS AUTO-CREATING A REGIME, and
	the difference is worth stating because this module refuses the second three
	functions up. Regimes are a closed legal vocabulary: `OSHA` for `OR-OSHA` is
	a typo that files evidence where nothing looks, so it is refused. Curricula
	are open — a regulator renames one every few years and an operation invents
	its own — so a new name is a new curriculum until somebody says otherwise.

	Returns what happened, so a tool can say so in its result rather than creating
	a master silently.
	"""
	wanted = " ".join(str(name or "").split()).strip()
	if not wanted:
		raise ValueError(
			"training_type is required — it is what the auditor reads first, and 'training' is "
			"not an answer to 'trained in what'."
		)
	if not compat.doctype_exists(TYPE_DOCTYPE):
		# A site mid-upgrade. The caller's text is returned unchanged so the
		# record can still be filed; the column is still a Data field there.
		return {"training_type": wanted, "created": False, "regimes": [], "available": False}

	existing = find_type(wanted)
	if existing:
		return {
			"training_type": existing,
			"created": False,
			"regimes": type_regimes(existing),
			"available": True,
		}

	tags = parse(regimes) or guess_type_regimes(wanted)
	doc = frappe.new_doc(TYPE_DOCTYPE)
	doc.training_type_name = wanted
	doc.description = description or ""
	doc.retention_years = retention_years(tags)
	doc.active = 1
	set_rows(doc, "regimes", tags)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return {"training_type": doc.name, "created": True, "regimes": tags, "available": True}


def type_regimes(training_type: str) -> list:
	"""The regimes on one `Training Type`, as canonical tokens."""
	name = str(training_type or "").strip()
	if not name or not compat.doctype_exists(TYPE_DOCTYPE):
		return []
	return rows_for_parents(TYPE_DOCTYPE, [name], "regimes").get(name, [])


def seed_training_types() -> dict:
	"""The ten common curricula, on install and every migrate. Idempotent.

	A type somebody has since edited is left alone — including one whose regimes
	were corrected, and including one that was deactivated, because deactivating
	a curriculum the operation does not run is a decision and re-enabling it every
	migrate would be this app overruling it monthly.
	"""
	report = {"created": [], "present": [], "failed": []}
	if not compat.doctype_exists(TYPE_DOCTYPE):
		return report
	for name, regimes, description in SEED_TRAINING_TYPES:
		try:
			if find_type(name):
				report["present"].append(name)
				continue
			outcome = ensure_type(name, regimes, description)
			report["created" if outcome["created"] else "present"].append(outcome["training_type"])
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["failed"].append({"name": name, "reason": f"{type(exc).__name__}: {exc}"})
	return report


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
		gaps.append("§112.161(a)(4) — not signed or initialled by the person who performed the activity")
	if not described.get("supervisor_reviewed"):
		gaps.append(
			"§112.161(b) — not reviewed, dated and signed by a supervisor. Worker training "
			"records are on the list that requires it, and this is the element a GAP-only "
			"operation most often lacks"
		)
	if not described.get("content_topics_covered"):
		gaps.append("§112.30(b) — no topics covered recorded")
	return gaps
