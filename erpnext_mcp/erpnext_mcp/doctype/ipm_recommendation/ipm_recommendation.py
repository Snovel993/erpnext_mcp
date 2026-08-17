# SPDX-License-Identifier: MIT
"""Controller for IPM Recommendation — the options, and what the mix scores.

THE SUSTAINABILITY SCALE LIVES HERE, not in the tool that reads it, because two
things need it and only one of them is a tool: this controller, which stamps the
score whenever the action table changes from any surface including the Desk, and
`tools/cropprotect.py`, which scores a proposed set of methods that has not been
saved to anything. A second copy in the tool would be a second scale, and the
first time they disagreed would be the first time somebody compared a stored
score against a freshly computed one.

────────────────────────────────────────────────────────────────────────────
WHAT THE SCORE IS, AND — MORE IMPORTANTLY — WHAT IT IS NOT
────────────────────────────────────────────────────────────────────────────

It is THIS APP'S OWN 0-100 SCALE. It is not USDA organic certification, not a
Protected Harvest or LIVE score, not IPM Institute accreditation, and a farm
that presents it as any of those is misrepresenting it. No standards body has
blessed these weights and none was asked to.

What it IS good for is a season-over-season number ON ONE FARM that moves when
the programme actually changes. A grower who says "we practise IPM" has, before
this, no evidence for the claim beyond the absence of a complaint. A grower
whose recommendation scores averaged 41 in 2025 and 63 in 2026 has a record of
having moved up the ladder, and — because every action carries the observation
and the threshold that produced it — a record of WHY each choice was made.

THE WEIGHTS ARE THE IPM PYRAMID, which is not controversial as an ordering even
though the exact numbers are a judgement:

    Cultural     1.00   prevention — the rung everything else is a fallback from
    Biological   0.90   a living control that persists after you stop paying for it
    Mechanical   0.75   real intervention, no residue, but it is intervention
    Behavioral   0.70   mating disruption, traps — low impact, high input cost
    Chemical     0.20   the last rung, and deliberately not zero: a correctly
                        timed threshold-driven spray IS integrated pest
                        management, and scoring it zero would tell a farm its
                        best chemical decision was worth the same as its worst
    No Action    1.00   the threshold said act and the beneficials said wait.
                        Full marks: this is the hardest call in the discipline
                        and the one a programme most often gets wrong by acting
    Unclassified 0.30   an action nobody categorised. Scored LOW on purpose —
                        an unclassified answer cannot be shown to have been
                        anything other than a spray, and a scale that gave it
                        the benefit of the doubt would reward not filling the
                        field in

────────────────────────────────────────────────────────────────────────────
WHAT IS SCORED CHANGES ONCE SOMEBODY DECIDES
────────────────────────────────────────────────────────────────────────────

Before any action is accepted, the score is over the PROPOSED set — it describes
the quality of the options the engine put in front of somebody. Once anything is
accepted, the score is over the ACCEPTED actions only, because from that moment
it describes what the farm chose rather than what it was offered.

That switch is the single most important line in this file. Without it, a farm
that was offered a release and a spray and chose the spray would keep the score
it earned for having been offered the release — which is a scale that rewards
generating good options and ignores taking them.

REJECTED ACTIONS ARE NEVER SCORED, at either stage. A declined biological option
is evidence about the programme and it is kept on the record for that reason,
but counting it toward the score would let a farm bank credit for every option
it turned down.
"""

import frappe
from frappe import _
from frappe.model.document import Document

CHEMICAL = "Chemical"
BIOLOGICAL = "Biological"
CULTURAL = "Cultural"
MECHANICAL = "Mechanical"
BEHAVIORAL = "Behavioral"
NO_ACTION = "No Action"
UNCLASSIFIED = "Unclassified"

CONTROL_METHODS = (CULTURAL, BIOLOGICAL, MECHANICAL, BEHAVIORAL, CHEMICAL, NO_ACTION, UNCLASSIFIED)

#: The scale. See the module docstring for what each number is and why Chemical
#: is 0.20 rather than 0, and Unclassified is below it rather than in the middle.
METHOD_WEIGHTS = {
	CULTURAL: 1.00,
	NO_ACTION: 1.00,
	BIOLOGICAL: 0.90,
	MECHANICAL: 0.75,
	BEHAVIORAL: 0.70,
	UNCLASSIFIED: 0.30,
	CHEMICAL: 0.20,
}

#: The order options are generated in — least chemical first, which is the order
#: an IPM programme is meant to consider them.
METHOD_PRIORITY = (CULTURAL, BIOLOGICAL, MECHANICAL, BEHAVIORAL, CHEMICAL, NO_ACTION, UNCLASSIFIED)

PROPOSED = "Proposed"
ACCEPTED = "Accepted"
REJECTED = "Rejected"
DONE = "Done"

#: Accepted and Done both mean "the farm chose this". Done is Accepted plus time.
CHOSEN = (ACCEPTED, DONE)

STATUSES = ("Open", "Accepted", "Declined", "Superseded", "Closed")


def _cell(row, key: str, default=""):
	"""One field off an action row, whether it is a Document or a plain dict.

	Frappe hands child rows back as Documents from `get_doc` and as dicts from
	several other paths, and this file is read from both — the controller runs on
	a loaded document, while `score_methods` is called on rows that never came
	from one. A bare `row.status` works on the first and raises `AttributeError`
	on the second, which is a crash rather than a wrong number, but it is a crash
	on the save path of a record somebody is trying to accept.
	"""
	if isinstance(row, dict):
		return row.get(key) or default
	return getattr(row, key, None) or default


def grade_for(score: float) -> str:
	"""A letter for a score, so a list view is readable at a glance.

	Deliberately coarse. A scale whose bands were five points wide would invite
	somebody to chase a grade boundary by reclassifying an action, which is the
	one way to move this number without changing anything in a block.
	"""
	if score >= 85:
		return "A"
	if score >= 70:
		return "B"
	if score >= 55:
		return "C"
	if score >= 40:
		return "D"
	return "F"


def score_methods(methods) -> dict:
	"""Score a bare list of control-method names. The whole of the arithmetic.

	Returns `{"score", "grade", "counts", "weighted", "scored_count"}`. An empty
	list scores 0 with a grade of F and a `scored_count` of 0 — a recommendation
	with no options is not a good outcome, and scoring it 100 for having nothing
	chemical in it would be the most obviously wrong answer this function could
	give.

	The mean is UNWEIGHTED BY EFFORT: three cultural actions and one spray score
	the same as one cultural action and one spray would not. That is intentional
	and it is a limitation worth stating — the scale measures the composition of
	the response, not its magnitude, because this app has no honest way to weigh
	'remove alternate hosts on the headland' against 'one cover spray' in acres
	or hours. A farm padding the table with cheap cultural rows to lift a number
	is gaming it, and the actions are on the record for anyone who looks.
	"""
	names = [str(name or UNCLASSIFIED).strip() or UNCLASSIFIED for name in methods or []]
	counts = {method: 0 for method in CONTROL_METHODS}
	weighted = 0.0
	for name in names:
		method = name if name in METHOD_WEIGHTS else UNCLASSIFIED
		counts[method] += 1
		weighted += METHOD_WEIGHTS[method]
	scored = len(names)
	score = round((weighted / scored) * 100, 1) if scored else 0.0
	return {
		"score": score,
		"grade": grade_for(score),
		"counts": counts,
		"weighted": round(weighted, 4),
		"scored_count": scored,
	}


class IPMRecommendation(Document):
	def validate(self):
		if self.status not in STATUSES:
			self.status = "Open"
		if not str(self.threat or "").strip():
			frappe.throw(_("A recommendation is about a threat. Name it."))
		self._score()
		self._stamp_decision()

	def _score(self):
		"""Stamp the score, the grade and the four counts onto the header.

		WHICH ROWS ARE SCORED is the switch described in the module docstring:
		accepted-only once anything has been accepted, proposed-set before that,
		and rejected rows never.
		"""
		rows = list(self.actions or [])
		chosen = [row for row in rows if str(_cell(row, "status", PROPOSED)) in CHOSEN]
		if chosen:
			scoring = chosen
		else:
			scoring = [row for row in rows if str(_cell(row, "status", PROPOSED)) != REJECTED]

		result = score_methods([_cell(row, "control_method", UNCLASSIFIED) for row in scoring])
		self.sustainability_score = result["score"]
		self.sustainability_grade = result["grade"]

		# The counts describe the WHOLE table rather than the scored subset. A
		# reader looking at "2 chemical, 1 biological" wants to know what is on
		# the record, and a count that silently dropped the rejected rows would
		# make a declined biological option invisible — which is exactly the
		# pattern the rows are kept in order to show.
		counts = score_methods([_cell(row, "control_method", UNCLASSIFIED) for row in rows])["counts"]
		self.chemical_actions = counts[CHEMICAL]
		self.biological_actions = counts[BIOLOGICAL]
		self.cultural_actions = counts[CULTURAL]
		self.other_actions = (
			counts[MECHANICAL] + counts[BEHAVIORAL] + counts[NO_ACTION] + counts[UNCLASSIFIED]
		)

	def _stamp_decision(self):
		if self.status in ("Accepted", "Declined") and not self.decided_on:
			self.decided_on = frappe.utils.now()
			if not self.decided_by and hasattr(frappe, "session"):
				self.decided_by = frappe.session.user
		if self.status == "Closed" and not self.closed_on:
			self.closed_on = frappe.utils.now()
