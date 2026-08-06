# SPDX-License-Identifier: MIT
"""The rails an AI-authored proposal arrives on. Pure functions, no database.

WHAT THIS MODULE IS FOR. v0.22.0 reserved two surfaces — `propose_compliance_rule`
and `propose_inspection_template_from_regulation` — and left them refusing, on the
grounds that the shape should be fixed before anything filled it. v0.37.0 fills
them, and this is where the shape lives: the rails are written once, in one file,
so a rule proposal and a template proposal cannot drift into being safe in
different amounts.

THE ARCHITECTURE THIS SERVES, STATED PLAINLY BECAUSE IT IS EASY TO GET WRONG.
There is no model call anywhere in this app, and these tools do not add one. THE
AI DOING THE PROPOSING IS THE MCP CLIENT — Claude, reading a regulation, drafting
a record, and handing it over as structured arguments. What the tools do is what
a tool can do and a model cannot do for itself: refuse the draft if it is the
wrong shape, stamp the provenance the client cannot choose, land it DISABLED, and
put what needs a second pair of eyes on the record where the approver will see it.

So the honest description of a proposal tool is: A VALIDATOR AND A GATE. Not an
author. The sentence "an AI wrote this rule" is true of the draft and stays true
after approval; what changes at approval is that a person's name is on it.

FOUR RAILS, AND EACH ONE IS SOMETHING THE CLIENT CANNOT OVERRIDE:

  * IT LANDS OFF. `enabled`/`active` is forced to 0 whatever was passed. The
    DocType refuses `enabled` without an approver anyway, so this is a belt to
    that brace — but it is the rail that makes the whole surface safe to expose
    at all, and a rail that only exists in one place is a rail one refactor away
    from not existing.
  * IT SAYS WHO WROTE IT. `authored_by` is forced to `AI-proposed`. A caller
    passing `Operator` is refused rather than quietly overridden: that argument
    is an attempt to launder provenance, and a tool that silently corrected it
    would teach the caller nothing.
  * IT SAYS WHERE IT WAS READ. `ai_source_citation` is REQUIRED. A proposal with
    no source is a claim with no source, and the whole point of the human
    approval is that somebody can check the draft against the text — which they
    cannot do if the draft does not say which text.
  * IT CANNOT SIGN ITSELF. `human_approved_by`, `human_approved_on` and the
    approver's employee and signature are refused as arguments. There is exactly
    one door those fields go through and it is the approval tool.

AND ONE THAT IS NOT A REFUSAL BUT A LABEL. `ai_review_flags` names what about
this particular draft needs more than a skim — a program rather than a set of
fields, a routing to one named person, a section that asks the worker for nothing
checkable, or a proposal whose approval will stand a running rule down. The
code-shaped flags have teeth: the approval tool refuses them until the approver
acknowledges them by name. The rest are read.

WHAT IS DELIBERATELY NOT HERE. There is no propose-a-deletion and no
propose-a-disable, in either direction and by construction: these functions build
records, and the only tools that stand something down — `deactivate_compliance_rule`,
`deactivate_inspection_template` — take a written reason from a person. A model
that has decided a rule is obsolete can say so; it cannot act on it.
"""

from __future__ import annotations

import json

#: The three words the `authored_by` Select on both doctypes holds. Restated here
#: rather than imported from `compliance_rules` so this module stays importable
#: with nothing behind it — it is pure, and the tests treat it that way.
AUTHOR_SYSTEM = "System"
AUTHOR_OPERATOR = "Operator"
AUTHOR_AI = "AI-proposed"

#: How much of the regulation's own text a proposal may quote back onto the
#: record. Enough to find the passage again; not a copy of the regulation.
EXCERPT_CAP = 240

#: Fields an approval fills in, refused as proposal arguments. See rail four.
APPROVAL_FIELDS = (
	"human_approved_by",
	"human_approved_on",
	"approver_employee",
	"approver_signature",
	"approver_signature_file_token",
)

# ── the flags ───────────────────────────────────────────────────────────────
FLAG_CUSTOM_PYTHON = "custom_python"
FLAG_PRODUCER_EXPRESSION = "producer_assigned_to_expression"
FLAG_SUPERSEDES_LIVE_RULE = "supersedes_live_rule"
FLAG_SUPERSEDES_LIVE_TEMPLATE = "supersedes_live_template"
FLAG_SECTIONS_WITHOUT_CONTRACT = "sections_without_evidence_contract"
FLAG_NO_REGIMES = "no_regime_named"

#: The flags an approver must acknowledge BY NAME rather than by clicking past.
#: Both are a program: the sandbox has already refused what it refuses — no
#: imports, no filesystem, no network, bounded in steps — and what it cannot
#: refuse is a program that runs perfectly and asks the wrong question. That is
#: a reading job, and this is the field that says a reading job is outstanding.
CODE_FLAGS = (FLAG_CUSTOM_PYTHON, FLAG_PRODUCER_EXPRESSION)

#: What each flag means, in the sentence the approver is shown. Kept beside the
#: names so a flag cannot be added without one.
FLAG_NOTES = {
	FLAG_CUSTOM_PYTHON: (
		"This draft is a PROGRAM, not a set of fields. The sandbox has already refused what it "
		"refuses; what it cannot tell you is whether the program asks the right question. Read it "
		"line by line, and if you can say what SHAPE of question it asks, that shape probably wants "
		"to be a declarative field instead."
	),
	FLAG_PRODUCER_EXPRESSION: (
		"This draft routes its producer task to one NAMED person by evaluating an expression over "
		"the alert's source row. An expression that resolves to nobody is a task that lands on "
		"nobody, discovered on the afternoon somebody needed it."
	),
	FLAG_SUPERSEDES_LIVE_RULE: (
		"There is already a live rule with this rule_id. Approving this draft stands that one down "
		"and points it here — so read the diff, not just the draft. Nothing is superseded until you "
		"approve."
	),
	FLAG_SUPERSEDES_LIVE_TEMPLATE: (
		"There is already a live template with this name. Approving this draft deactivates that one "
		"and points it here. Sessions already worked from it stay readable, exactly as they do for "
		"any other supersession — but the next worker gets this form."
	),
	FLAG_SECTIONS_WITHOUT_CONTRACT: (
		"One or more sections ask for no evidence at all. A section with an empty contract can be "
		"filed empty and still looks complete, which is the failure mode an inspection template "
		"exists to prevent. Say what each section must come back with."
	),
	FLAG_NO_REGIMES: (
		"Nothing says which audit this answers, so it will not appear in a regime-filtered sweep or "
		"in an audit packet built for one scheme. `Internal` is a real answer and a better one than "
		"silence."
	),
}


# ── provenance ──────────────────────────────────────────────────────────────
def citation(url: str = "", section: str = "", explicit: str = "", text: str = "", read_on: str = "") -> str:
	"""The `ai_source_citation` line, from whatever the proposer actually has.

	A URL and a section number where the regulation is online; a section alone
	where it is not; the whole line written out where the proposer would rather
	say it themselves. All three are things a real proposal genuinely has, and
	the one thing that is not accepted is none of them — which is rail three.

	Raises ValueError if there is nothing to cite. The caller turns that into the
	refusal, so the sentence a client reads names the tool it called.
	"""
	explicit = str(explicit or "").strip()
	url = str(url or "").strip()
	section = str(section or "").strip()
	text = str(text or "").strip()

	head = explicit
	if not head:
		head = " — ".join(part for part in (section, url) if part)
	if not head:
		raise ValueError(
			"a proposal must say where it was read from. Pass regulation_url and/or "
			"regulation_section — or ai_source_citation, written out — because the whole of what "
			"the human approval checks is the draft AGAINST THE TEXT, and a draft that does not "
			"name the text cannot be checked at all"
		)
	if read_on:
		head = f"{head} (read {read_on})"
	if text:
		head = f'{head}\nQuoted: "{excerpt(text)}"'
	return head


def excerpt(text: str, cap: int = EXCERPT_CAP) -> str:
	"""Enough of the regulation's own words to find the passage again."""
	flat = " ".join(str(text or "").split())
	return flat if len(flat) <= cap else flat[: cap - 1].rstrip() + "…"


def offered_approval_fields(args: dict) -> list:
	"""Which approval fields a proposal tried to fill in. Rail four's evidence."""
	return [field for field in APPROVAL_FIELDS if str((args or {}).get(field) or "").strip()]


# ── the flags ───────────────────────────────────────────────────────────────
def rule_flags(spec: dict, supersedes_live: bool = False) -> list:
	"""What about this rule draft needs a second pair of eyes, in a fixed order."""
	spec = spec or {}
	flags = []
	if str(spec.get("custom_python") or "").strip():
		flags.append(FLAG_CUSTOM_PYTHON)
	if str(spec.get("producer_assigned_to_expression") or "").strip():
		flags.append(FLAG_PRODUCER_EXPRESSION)
	if supersedes_live:
		flags.append(FLAG_SUPERSEDES_LIVE_RULE)
	if not (spec.get("regimes") or []):
		flags.append(FLAG_NO_REGIMES)
	return flags


def template_flags(sections: list, supersedes_live: bool = False) -> list:
	"""The same, for a template draft."""
	flags = []
	bare = [
		str(section.get("section_name") or "")
		for section in sections or []
		if not (section.get("evidence_contract") or {})
	]
	if bare:
		flags.append(FLAG_SECTIONS_WITHOUT_CONTRACT)
	if supersedes_live:
		flags.append(FLAG_SUPERSEDES_LIVE_TEMPLATE)
	return flags


def code_flags(flags) -> list:
	"""The subset an approver has to acknowledge by name. See CODE_FLAGS."""
	return [flag for flag in read_flags(flags) if flag in CODE_FLAGS]


def notes_for(flags) -> list:
	"""Each flag as {flag, note}, in the order they were raised."""
	return [{"flag": flag, "note": FLAG_NOTES.get(flag, "")} for flag in read_flags(flags)]


def dump_flags(flags) -> str:
	"""The JSON list the `ai_review_flags` column holds. `[]` rather than NULL."""
	return json.dumps(read_flags(flags))


def read_flags(raw) -> list:
	"""The flags off a stored row, or off a list. Never raises: this is a READ.

	The column is read-only and written only by the proposal tools, so a blob
	that will not parse as JSON is a bug here rather than somebody's typo — but a
	comma-separated line is read anyway, because the alternative is an approval
	that silently sees no flags on a row that has them.
	"""
	if raw is None or raw == "":
		return []
	if isinstance(raw, (list, tuple)):
		return [str(entry).strip() for entry in raw if str(entry).strip()]
	if isinstance(raw, str):
		try:
			parsed = json.loads(raw)
		except (TypeError, ValueError):
			return [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
		return read_flags(parsed)
	return []
