# SPDX-License-Identifier: MIT
"""Controller for Trade Document Template — the shape of one kind of paper.

THE DOCNAME IS THE TEMPLATE NAME, for the same reason a wizard's docname is its
key: a template an operator edits in the Desk and a template a Destination
Document Requirement points at have to be the same row, and an autoname off
`template_name` is what makes that true rather than hoped for.

WHAT IS CHECKED IS WHAT WOULD FAIL LATE. A `required_fields` blob that will not
parse renders an EMPTY form at a sales desk with a truck waiting, and a
`applicable_tiers` value nobody recognises makes a document silently required of
no shipment at all — which looks exactly like a document nobody needed. Both are
cheap to find on save and expensive to find at a border, so both are found here.

THE TIER VOCABULARY LIVES HERE because this is the module every other one
already has to import to talk about templates, and a second copy of three
strings is how Local, Domestic and International come to mean different things
in the seeder and in the lookup.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document

#: The three tiers a shipment is documented at. Ordered by how much paper each
#: needs, which is also the order a farm grows into them: fruit on a truck to
#: the packing house down the road, fruit across a state line, fruit on a vessel.
LOCAL = "Local"
DOMESTIC = "Domestic"
INTERNATIONAL = "International"
TIERS = (LOCAL, DOMESTIC, INTERNATIONAL)

#: Recognised on input for each tier, so a caller saying "international" or
#: "intl" is understood rather than refused over capitalisation. NOT a licence
#: to store anything: `normalise_tier` returns one of `TIERS` or raises.
TIER_ALIASES = {
	"local": LOCAL,
	"domestic": DOMESTIC,
	"interstate": DOMESTIC,
	"us": DOMESTIC,
	"usa": DOMESTIC,
	"international": INTERNATIONAL,
	"intl": INTERNATIONAL,
	"export": INTERNATIONAL,
}


def normalise_tier(value, required: bool = True) -> str:
	"""One of `TIERS`, or a refusal naming all three.

	Raises `frappe.ValidationError` rather than this app's `ToolError` because
	it is called from a document controller as well as from the tools, and a
	controller that raised a ToolError would produce a traceback in the Desk
	instead of a message somebody can read.
	"""
	raw = str(value or "").strip()
	if not raw:
		if required:
			frappe.throw(_("A destination tier is required — one of {0}.").format(", ".join(TIERS)))
		return ""
	resolved = TIER_ALIASES.get(raw.casefold())
	if resolved:
		return resolved
	for tier in TIERS:
		if tier.casefold() == raw.casefold():
			return tier
	frappe.throw(
		_(
			"{0} is not a destination tier. It is one of {1} — the three amounts of paper a "
			"shipment can need, and a closed list because a tier nobody recognises makes a "
			"document required of no shipment at all, which looks exactly like a document "
			"nobody needed."
		).format(frappe.utils.cstr(value), ", ".join(TIERS))
	)


def parse_tiers(value) -> tuple:
	"""A comma-separated `applicable_tiers` as a tuple, empty meaning every tier.

	EMPTY IS EVERY TIER RATHER THAN NO TIER, which is the reading that fails
	safe: a template whose scope nobody filled in should be offerable everywhere
	and declined where it does not apply, not silently invisible.
	"""
	if not value:
		return ()
	if isinstance(value, (list, tuple)):
		chunks = [str(entry) for entry in value]
	else:
		chunks = str(value).replace("\n", ",").split(",")
	out = []
	for chunk in chunks:
		entry = chunk.strip()
		if not entry:
			continue
		resolved = TIER_ALIASES.get(entry.casefold())
		if not resolved:
			for tier in TIERS:
				if tier.casefold() == entry.casefold():
					resolved = tier
					break
		if resolved and resolved not in out:
			out.append(resolved)
	return tuple(out)


def applies_to_tier(applicable_tiers, tier: str) -> bool:
	"""Whether a template scoped like this belongs on a shipment at `tier`."""
	scope = parse_tiers(applicable_tiers)
	return True if not scope else tier in scope


class TradeDocumentTemplate(Document):
	def validate(self):
		self.template_name = str(self.template_name or "").strip()
		if not self.template_name:
			frappe.throw(_("Template Name is required — it is the docname a requirement points at."))
		if not str(self.document_type or "").strip():
			frappe.throw(_("Document Type is required; it is what a destination requirement matches on."))

		# Normalised on the way in rather than on every read. The column is small
		# and read constantly; parsing 'intl, Local' on each lookup would be a
		# cost paid forever to avoid one paid once.
		tiers = parse_tiers(self.applicable_tiers)
		if self.applicable_tiers and not tiers:
			frappe.throw(
				_(
					"Applicable Tiers is set to {0}, and none of it names a tier. Use any of "
					"{1}, comma-separated — or leave it empty, which means every tier."
				).format(self.applicable_tiers, ", ".join(TIERS))
			)
		self.applicable_tiers = ", ".join(tiers)

		if not str(self.label_en or "").strip():
			# Falls back rather than refusing: the docname is a perfectly good
			# label, and refusing to save a template over a display string would
			# be a rule that costs more than it protects.
			self.label_en = self.template_name

		self._check_json(self.required_fields, "Required Fields")
		self._check_json(self.auto_populate_map, "Auto-populate Map")

		# `cint` RATHER THAN A BARE TRUTHINESS CHECK. A Check field arrives as the
		# STRING "0" often enough — from a Single, from a fixture, from an import
		# — and `bool("0")` is True, so a plain `if self.requires_external_filing`
		# refuses to save every template that has the box UNticked. That is the
		# same trap `settings.as_bool` exists for, one layer down.
		if frappe.utils.cint(self.requires_external_filing) and not str(self.external_system or "").strip():
			frappe.throw(
				_(
					"{0} requires an external filing but does not say to which system. "
					"'Requires External Filing' with no system named is a document that can "
					"never be completed — nobody can record a reference from a system nobody "
					"named. Set External System (PCIT, AES, DCSA …), or untick the box."
				).format(self.template_name)
			)

	def _check_json(self, raw, where: str) -> None:
		"""Refuse a blob that will not parse, at the cheap moment.

		A `required_fields` that will not parse renders an EMPTY document at a
		sales desk with a truck waiting. Saving the template is where that
		should be found.
		"""
		if not raw:
			return
		try:
			parsed = json.loads(raw) if isinstance(raw, str) else raw
		except (json.JSONDecodeError, ValueError, TypeError):
			frappe.throw(_("{0} is not valid JSON.").format(where))
			return
		if where == "Required Fields" and not isinstance(parsed, list):
			frappe.throw(
				_("Required Fields must be a JSON list of fields, got {0}.").format(type(parsed).__name__)
			)
		if where == "Auto-populate Map" and not isinstance(parsed, dict):
			frappe.throw(
				_(
					"Auto-populate Map must be a JSON object of {{\"this document's field\": "
					"\"the source record's field\"}}, got {0}."
				).format(type(parsed).__name__)
			)
