# SPDX-License-Identifier: MIT
"""Controller for Compliance Rule — a rule definition, as a record.

WHAT IT REFUSES, AND WHY EACH REFUSAL EARNED ITS PLACE:

  * A RULE THAT IS ENABLED WITH NOBODY'S NAME ON IT. `enabled` requires
    `human_approved_by` AND `human_approved_on`. A rule that could fire without
    an approver would make the compliance calendar an assertion nobody signed,
    which is the exact failure the Configurable Compliance Framework exists to
    prevent — and it is the failure that matters most once an AI can propose
    rules, because "the model wrote it and it went live" is the sentence this
    field set exists to make impossible to say.
  * A SECOND LIVE ROW FOR ONE `rule_id`. Enforced here rather than by a unique
    index, because the constraint is not on the column: v1 and v2 share a
    rule_id by construction, and superseding is how history stays readable.
    What may not exist twice is a row that is enabled AND unsuperseded, because
    those are two definitions of one rule and the one the sweep ran would be
    whichever sorted first.
  * A `rule_id` THAT IS NOT A KEY. It is the first segment of every alert
    docname, so spaces and colons are refused — a colon would split the key and
    put the rest of the docname in the wrong field.
  * A MALFORMED SCOPE FILTER, EVIDENCE CONTRACT OR PACKET LIST. Each of those
    is a blob that asks for nothing when it is wrong and looks like it asks for
    something. Refused while the person who typed it is present.
  * `custom_python` THAT THE SANDBOX WOULD NOT RUN. Vetted on save by the same
    checker the sweep uses, so `import os` is refused at authoring time rather
    than at two in the morning in a report nobody reads.
  * A `builtin_scanner` NAMING A SCANNER THIS APP DOES NOT SHIP. A rule
    delegating to a scanner that is not there raises nothing, for ever, quietly.

WHAT IT DOES NOT REFUSE. A rule with no message template — a built-in scanner
writes its own. A rule with no regimes, which is a real answer for something
nobody outside is coming to inspect (though `Internal` is the better one, and
the tools say so). A rule with no producer task: most rules describe a state,
and what to do about it is a judgement.

`active_row_flag` IS COMPUTED HERE AND NEVER TYPED. It is `enabled and not
superseded`, which is the one question the sweep asks, materialised as a column
so it can be indexed and so the constraint above has something to check.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import compliance_rules, shifts
from erpnext_mcp.alerts import sandbox


def _select_options(doctype: str, fieldname: str) -> list:
	"""The values a Select field will accept on THIS site, or an empty list.

	Empty means "could not read the meta", and the caller treats that as "do not
	refuse" — a rule that could not be saved because a doctype's meta would not
	build would be a worse failure than a task type nobody checked.
	"""
	try:
		field = frappe.get_meta(doctype).get_field(fieldname)
		return [line.strip() for line in str(getattr(field, "options", "") or "").split("\n") if line.strip()]
	except Exception:  # pragma: no cover - a site whose meta cannot be built
		return []


class ComplianceRule(Document):
	def autoname(self):
		"""CRULE-YYYY-0001, where YYYY is the year the rule was authored."""
		year = str(frappe.utils.today())[:4]
		self.name = shifts.next_in_series(compliance_rules.DOCTYPE, "CRULE", year)

	def validate(self):
		self._tidy()
		self._check_the_key()
		self._check_the_control_point()
		self._check_the_blobs()
		self._check_the_program()
		self._check_the_producer()
		self._check_the_approval()
		self._refuse_a_second_live_row()

	# ── tidying ─────────────────────────────────────────────────────────────
	def _tidy(self) -> None:
		for fieldname in (
			"rule_id",
			"title",
			"builtin_scanner",
			"date_field",
			"window_field",
			"regimes_from_field",
			"producer_farm_task_type",
			"producer_skill_required",
			"producer_assigned_to_expression",
		):
			self.set(fieldname, str(self.get(fieldname) or "").strip())
		if not self.category:
			self.category = "Records"
		if not self.missing_date_behaviour:
			self.missing_date_behaviour = compliance_rules.ON_MISSING_SKIP
		if not self.due_date_mode:
			self.due_date_mode = compliance_rules.DUE_FROM_ANCHOR
		for fieldname, fallback in (
			("severity_critical", "Critical"),
			("severity_warning", "Warning"),
			("severity_expired", "Critical"),
			("default_severity", compliance_rules.SEVERITY_DEFAULT),
		):
			if not self.get(fieldname):
				self.set(fieldname, fallback)
		if not self.authored_by:
			self.authored_by = compliance_rules.AUTHOR_OPERATOR
		if int(self.version or 0) < 1:
			self.version = 1
		if not str(self.requires_doctypes or "").strip() and self.target_doctype:
			self.requires_doctypes = str(self.target_doctype)
		# The one computed column. See the module docstring.
		self.active_row_flag = (
			1 if (int(self.enabled or 0) and not str(self.superseded_by or "").strip()) else 0
		)

	# ── the key ─────────────────────────────────────────────────────────────
	def _check_the_key(self) -> None:
		if not self.rule_id:
			frappe.throw(_("Rule ID is required — it is the key every alert of this rule is filed under."))
		if len(self.rule_id) > compliance_rules.MAX_RULE_ID:
			frappe.throw(
				_(
					"Rule ID is {0} characters, past the {1} cap. It is the first segment of every "
					"alert docname and the whole docname has to fit in 140."
				).format(len(self.rule_id), compliance_rules.MAX_RULE_ID)
			)
		bad = [character for character in ":\n\t " if character in self.rule_id]
		if bad:
			frappe.throw(
				_(
					"Rule ID {0!r} contains {1}. An alert's docname is "
					"`<rule_id>:<source_doctype>:<source_name>`, so a colon would split the key and "
					"put the rest of the name in the wrong field, and a space makes a key nobody "
					"can type. Use lower_snake_case — `certification_expiring`."
				).format(self.rule_id, ", ".join(repr(character) for character in bad)),
				title=_("Rule ID is not a key"),
			)
		if not str(self.title or "").strip():
			frappe.throw(_("Title is required — a rule nobody can read on a calendar."))
		if not str(self.kairotic_gate_description or "").strip():
			frappe.throw(
				_(
					"The kairotic gate is required. Say what STATE makes this rule ripe — and what "
					"keeps it quiet — or it is a calendar reminder, which belongs in somebody's "
					"phone rather than in a compliance system. It is also the paragraph an auditor "
					"is read when they ask why an alert fired."
				),
				title=_("No kairotic gate"),
			)

	# ── the control point ───────────────────────────────────────────────────
	def _check_the_control_point(self) -> None:
		"""v0.80.0. A gate rule must name a gate this app actually consults.

		THE FAILURE THIS PREVENTS IS THE WORST ONE THE REGISTER CAN PRODUCE. An
		operator reads a row saying the site enforces segregation of duties,
		believes it, and tells an auditor so — while nothing anywhere asks that row
		anything. A swept rule that matches nothing is visibly quiet; a gate that
		nothing calls is indistinguishable from a gate that never had to fire.

		THE MODE IS NORMALISED RATHER THAN REFUSED WHEN BLANK, and it normalises to
		Advisory. A gate whose strictness was somehow unset must not fall to the
		strict reading: the cost of quietly enforcing something an operator did not
		ask to enforce is a locked ledger, and the cost of quietly advising is a
		calendar entry.
		"""
		from erpnext_mcp import enforcement

		control_point = str(self.control_point or "").strip()
		self.control_point = control_point
		if not control_point:
			# Not a gate. `enforcement_mode` is meaningless on a swept rule and is
			# blanked rather than left at a default, so a register sorted by it does
			# not show every cadence rule as "Advisory" — which would read as a
			# claim that they are gates somebody softened.
			self.enforcement_mode = None
			return

		if control_point not in enforcement.CONTROL_POINTS:
			frappe.throw(
				_(
					"{0} is not a control point this app implements, so nothing would ever consult "
					"this rule. The control points are: {1}. A rule naming a gate that does not "
					"exist is worse than no rule — it tells an operator their site enforces "
					"something it does not."
				).format(control_point, ", ".join(sorted(enforcement.CONTROL_POINTS))),
				title=_("No such control point"),
			)

		if str(self.enforcement_mode or "").strip() not in enforcement.MODES:
			self.enforcement_mode = enforcement.ADVISORY

		if str(self.builtin_scanner or "").strip() or str(self.custom_python or "").strip():
			frappe.throw(
				_(
					"A rule with a control point is a GATE — it is consulted at the moment of the "
					"transaction and is never scanned. Giving it a scanner as well would create a "
					"second, nightly opinion about the same condition, filed under the same alert "
					"key, which would fight the gate's own findings. Clear the scanner, or clear "
					"the control point."
				),
				title=_("A gate is not swept"),
			)

	# ── the blobs ───────────────────────────────────────────────────────────
	def _check_the_blobs(self) -> None:
		for fieldname, parser, label in (
			("scope_filters_json", compliance_rules.parse_filters, "scope_filters"),
			("evidence_contract_json", compliance_rules.parse_contract, "evidence_contract"),
			("audit_packet_types", compliance_rules.parse_packet_types, "audit_packet_types"),
			# v0.22.5. Refused at the same door as the other three, and by the same
			# parser the seeder and the MCP tools use, so a malformed blob cannot be
			# accepted through one and refused at another.
			(
				"latest_child_field_threshold_json",
				compliance_rules.parse_latest_child_threshold,
				"latest_child_field_threshold",
			),
		):
			try:
				parsed = parser(self.get(fieldname), label)
			except ValueError as exc:
				frappe.throw(str(exc), title=_("Malformed rule definition"))
			self.set(fieldname, json.dumps(parsed))

		scanner = str(self.builtin_scanner or "").strip()
		if scanner:
			from erpnext_mcp.alerts import rules as shipped

			if scanner not in shipped.SCANNERS:
				frappe.throw(
					_(
						"{0} is not a scanner this app ships. A rule delegating to a scanner that "
						"is not there raises nothing, for ever, quietly. The scanners are: {1}."
					).format(scanner, ", ".join(sorted(shipped.SCANNERS))),
					title=_("No such scanner"),
				)
		if scanner and str(self.custom_python or "").strip():
			frappe.throw(
				_(
					"This rule names both a built-in scanner ({0}) and a custom_python program, and "
					"only one of them can be the rule. Clear whichever is not."
				).format(scanner),
				title=_("Two definitions"),
			)

	def _check_the_program(self) -> None:
		program = str(self.custom_python or "").strip()
		if not program:
			return
		try:
			sandbox.check(program)
		except sandbox.SandboxError as exc:
			frappe.throw(
				_(
					"custom_python was refused by the sandbox: {0}\n\nIt is refused HERE, while you "
					"are looking at it, rather than at two in the morning in a sweep report nobody "
					"reads. If the rule genuinely needs what was refused, it wants a declarative "
					"primitive or a shipped scanner rather than this field."
				).format(exc),
				title=_("The sandbox refused this program"),
			)

	# ── the producer task ───────────────────────────────────────────────────
	def _check_the_producer(self) -> None:
		"""v0.22.5. What the rule asks somebody to do, and WHICH somebody.

		THE TWO ROUTINGS ARE EXCLUSIVE, and that is a statement about dispatch
		rather than a tidiness rule. A skill is a POOL — anybody holding it may
		pick the task up — and an assignee is a PERSON. A task carrying both would
		be a task whose holder depends on which of the two the dispatcher happened
		to read first, and the failure is silent in the worst direction: the
		foreman who was standing in the heat never sees the task, and somebody with
		the skill closes it from a desk.
		"""
		expression = str(self.producer_assigned_to_expression or "").strip()
		if expression:
			if str(self.producer_skill_required or "").strip():
				frappe.throw(
					_(
						"This rule names both a producer skill ({0}) and an assignee expression "
						"({1}). A skill is a POOL and an assignee is a PERSON, and a task that is "
						"both is a task whose holder depends on which one the dispatcher read "
						"first. Clear whichever is not the routing you meant."
					).format(self.producer_skill_required, expression),
					title=_("Two routings"),
				)
			if str(self.producer_task_template or "").strip():
				frappe.throw(
					_(
						"This rule names both a Farm Task Template ({0}) and an assignee "
						"expression, and the template already states how the work is routed — its "
						"own dispatch_mode and skill. A task with a named holder is not in a pool, "
						"so the two are answers to one question and the holder would depend on "
						"which the dispatcher read first."
					).format(self.producer_task_template),
					title=_("Two routings"),
				)
			try:
				sandbox.check(expression)
			except sandbox.SandboxError as exc:
				frappe.throw(
					_(
						"producer_assigned_to_expression was refused by the sandbox: {0}\n\nIt is "
						"one expression over the alert's source row — `row.foreman` — evaluated "
						"under exactly the rules custom_python runs under. Refused HERE, rather "
						"than the first hot afternoon somebody needed the task."
					).format(exc),
					title=_("The sandbox refused this expression"),
				)

		# The rule's task type has to be one the Farm Task doctype will accept. A
		# phrase nobody's Select holds is a producer task that fails at the moment
		# an alert becomes work, which is the moment the whole framework exists for.
		task_type = str(self.producer_farm_task_type or "").strip()
		if task_type and frappe.db.exists("DocType", "Farm Task"):
			options = _select_options("Farm Task", "task_type")
			if options and task_type not in options:
				frappe.throw(
					_(
						"producer_farm_task_type is {0!r}, which is not one of the Farm Task types: "
						"{1}. This field is the SELECT VALUE the producer task is filed under, not "
						"the sentence describing the errand — that goes in the message template and "
						"in extra_parameters.producer_task_what."
					).format(task_type, ", ".join(options)),
					title=_("No such Farm Task type"),
				)

	# ── the approval ────────────────────────────────────────────────────────
	def _check_the_approval(self) -> None:
		if not int(self.enabled or 0):
			return
		if not (str(self.human_approved_by or "").strip() and str(self.human_approved_on or "").strip()):
			frappe.throw(
				_(
					"A rule cannot be enabled without an approver AND the date they approved it. "
					"Both, because a review date with nobody attached is what an auditor is trained "
					"to disbelieve. approve_compliance_rule fills them in and turns the rule on in "
					"one call.{0}"
				).format(
					_(
						"\n\nThis one is AI-proposed, which is exactly the case the gate is for: a "
						"model drafted it, and until somebody has read the citation against the "
						"regulation it stays off."
					)
					if self.authored_by == compliance_rules.AUTHOR_AI
					else ""
				),
				title=_("Not approved"),
			)

	def _refuse_a_second_live_row(self) -> None:
		"""One LIVE row per rule_id. See the module docstring for why here."""
		if not int(self.active_row_flag or 0):
			return
		clash = frappe.db.get_all(
			compliance_rules.DOCTYPE,
			filters={
				"rule_id": self.rule_id,
				"active_row_flag": 1,
				"name": ("!=", self.name or "new"),
			},
			pluck="name",
			limit=1,
		)
		if clash:
			frappe.throw(
				_(
					"{0} is already live as {1}. Two enabled, unsuperseded rows for one rule_id are "
					"two definitions of one rule, and the one tonight's sweep ran would be whichever "
					"sorted first. Edit that one with update_compliance_rule — it supersedes rather "
					"than overwrites, so the definition an alert was raised under stays readable — "
					"or deactivate it first."
				).format(self.rule_id, clash[0]),
				title=_("That rule is already live"),
			)
