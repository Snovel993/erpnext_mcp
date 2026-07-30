# SPDX-License-Identifier: MIT
"""The related-party register: who is related to this company, how, and since when.

WHAT THIS IS NOT. It is not the Party field on a Journal Entry. ERPNext already
answers "who was this transaction with" through Supplier, Customer, Employee and
Shareholder links, those work, and nothing here replaces or shadows them. A
transaction is an event; a relationship is a state, and no amount of tagging
events reconstructs the state. "Was the person we paid $24,000 last year a
manager of this company at the time" is a question the ledger cannot answer and
the IRS asks anyway.

WHY IT SITS BESIDE THE CAP TABLE RATHER THAN INSIDE IT. `Cap Table Entry` maps an
anonymous member id to a legal entity and a percentage — an *ownership* fact,
deliberately the only place on the site where the mapping exists. Related Party
holds every other kind of relationship: the trustee who owns nothing, the estate
attorney, the son who is a beneficiary but not yet a member, the sister who
retired out. Folding those into the cap table would mean rows with no ownership
percentage in a register whose whole purpose is that the percentages add to 100.
The two are linked, so a member appears in both without either being copied.

FOUR DIGITS, NEVER NINE. The tax id field holds the last four digits of an SSN or
EIN and refuses anything longer, loudly, naming what it thinks it was sent. The
full number belongs on a signed W-9 in a locked drawer. See the controller.

WHY IT SHIPPED WITH THE 1099 TOOL. `generate_1099_prefill` reads Suppliers and
their payments, and a Supplier row cannot say "this vendor is the manager's own
LLC". This register can, through the `supplier` link, which is what turns a
payment in the ledger into a related-party disclosure on the return. That link is
the reason these two features are one release.
"""

import frappe

from .. import compat
from ..args import (
	as_bool,
	as_choice,
	as_date,
	as_limit,
	as_str,
	resolve_company,
	select_options,
)
from ..errors import ToolError
from ..result import ToolResult

RELATED_PARTY = "Related Party"
CAP_TABLE = "Cap Table Entry"
GOVERNANCE_DOCUMENT = "Governance Document"
PARCEL = "Parcel"
LEASE = "Lease"

_PARTY_FIELDS = (
	"name",
	"party_name",
	"company",
	"party_type",
	"relationship_to_company",
	"effective_date",
	"end_date",
	"tax_id_type",
	"tax_id_last4",
	"address",
	"cap_table_entry",
	"supplier",
	"governing_document",
	"notes",
	"creation",
	"owner",
)

#: Party types whose payments are reportable on a 1099-NEC when they cross the
#: threshold, and those whose are not. Read by `tax.py` rather than duplicated
#: there. "LLC" is in neither list on purpose: a single-member LLC is reportable
#: and an LLC taxed as a corporation is not, and only the W-9 says which.
REPORTABLE_PARTY_TYPES = ("Individual", "Partnership", "Family Member")
EXEMPT_PARTY_TYPES = ("Corporation",)

#: Relationships that make a vendor a related party for disclosure purposes —
#: which is every one of them except a plain arm's-length Vendor.
DISCLOSABLE_RELATIONSHIPS = (
	"Member",
	"Manager",
	"Trustee",
	"Beneficiary",
	"Family",
	"Officer",
	"Director",
)

REGISTER_CAP = 500


def _require() -> None:
	compat.require_doctype(
		RELATED_PARTY,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _describe(row: dict, today: str = "") -> dict:
	end_date = str(row.get("end_date") or "") or None
	return {
		"name": row.get("name"),
		"party_name": row.get("party_name"),
		"company": row.get("company"),
		"party_type": row.get("party_type"),
		"relationship_to_company": row.get("relationship_to_company"),
		"effective_date": str(row.get("effective_date") or "") or None,
		"end_date": end_date,
		"current": not (end_date and today and end_date < today),
		"tax_id_type": row.get("tax_id_type") or "None",
		"tax_id_last4": row.get("tax_id_last4") or None,
		"address": row.get("address") or None,
		"cap_table_entry": row.get("cap_table_entry") or None,
		"supplier": row.get("supplier") or None,
		"governing_document": row.get("governing_document") or None,
		"notes": row.get("notes") or None,
	}


def party_row(party: str, company: str = "") -> dict:
	"""One Related Party as a dict, from its docname or its party name.

	A bare party name is ambiguous the moment somebody holds two roles, which is
	the case this register was shaped around — so a name matching more than one
	entry is refused with the docnames listed rather than resolved to the first.
	"""
	party = (party or "").strip()
	if not party:
		raise ToolError("party is required (a Related Party docname, or the party's name)")
	fields = compat.existing_fields(RELATED_PARTY, _PARTY_FIELDS)

	if frappe.db.exists(RELATED_PARTY, party):
		row = frappe.db.get_value(RELATED_PARTY, party, fields, as_dict=True)
		if company and row and row.get("company") != company:
			raise ToolError(
				f"Related Party {party!r} belongs to company {row.get('company')!r}, not {company!r}"
			)
		return dict(row)

	filters = {"party_name": party}
	if company:
		filters["company"] = company
	matches = frappe.db.get_all(RELATED_PARTY, filters=filters, fields=fields, limit=25)
	if len(matches) > 1:
		names = ", ".join(sorted(str(match.get("name")) for match in matches))
		raise ToolError(
			f"{party!r} is registered in {len(matches)} capacities: {names}. Somebody who is both "
			"a Manager and a Member is two entries by design — pass the docname of the one you "
			"mean."
		)
	if len(matches) == 1:
		return dict(matches[0])
	scope = f" for {company}" if company else ""
	raise ToolError(f"no Related Party called {party!r}{scope}. list_related_parties has the register.")


def _check_link(doctype: str, value: str, company: str, company_field: str, label: str) -> str:
	if not frappe.db.exists(doctype, value):
		raise ToolError(f"no {doctype} named {value!r}. Nothing was created.")
	owner = frappe.db.get_value(doctype, value, company_field) if company_field else None
	if owner and company and owner != company:
		raise ToolError(f"{label} {value!r} belongs to {owner!r}, not {company!r}. Nothing was created.")
	return value


def _validate_tax_id(tax_id_type: str, tax_id_last4: str, tail: str) -> tuple[str, str]:
	"""The four-digits-never-nine rule, refused here before the controller sees it.

	The controller enforces the same thing — it has to, because the Desk form is
	another door into the same field — but a tool that let a full SSN reach
	`doc.insert()` would report it as a framework validation error rather than as
	a sentence about why this site does not want the number.
	"""
	tax_id_type = (tax_id_type or "").strip() or "None"
	tax_id_last4 = (tax_id_last4 or "").strip()

	if tax_id_type not in ("None", "SSN", "EIN"):
		raise ToolError(f"tax_id_type must be None, SSN or EIN. Got {tax_id_type!r}. {tail}")
	if not tax_id_last4:
		if tax_id_type in ("SSN", "EIN"):
			raise ToolError(
				f"tax_id_type is {tax_id_type} but tax_id_last4 is empty. Either give the last "
				f"four digits, or leave tax_id_type as None. {tail}"
			)
		return tax_id_type, ""
	if tax_id_type == "None":
		raise ToolError(
			f"tax_id_last4 was given but tax_id_type is None. Say which kind of number it is. {tail}"
		)
	digits = tax_id_last4.replace("-", "").replace(" ", "")
	if not digits.isdigit():
		raise ToolError(f"tax_id_last4 must be four digits. Got {tax_id_last4!r}. {tail}")
	if len(digits) == 9:
		raise ToolError(
			f"that is nine digits — a whole {tax_id_type}, not the last four of one. This field "
			f"stores four and only four: send {digits[-4:]!r} instead. The full number belongs on "
			f"the signed W-9, not on this site. {tail}"
		)
	if len(digits) != 4:
		raise ToolError(
			f"tax_id_last4 must be exactly four digits. Got {tax_id_last4!r} ({len(digits)} "
			f"digits). {tail}"
		)
	return tax_id_type, digits


# ── 87. create_related_party ────────────────────────────────────────────────
def create_related_party(args: dict) -> ToolResult:
	"""Register one relationship: who, in what capacity, from when, under what paper."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	party_name = as_str(args, "party_name", required=True)
	party_type = as_choice(
		RELATED_PARTY, "party_type", as_str(args, "party_type", required=True), "party_type"
	)
	relationship = as_choice(
		RELATED_PARTY,
		"relationship_to_company",
		as_str(args, "relationship_to_company", required=True),
		"relationship_to_company",
	)
	effective_date = as_date(args, "effective_date", required=True)
	end_date = as_date(args, "end_date")
	if end_date and end_date < effective_date:
		raise ToolError(
			f"end_date {end_date} is before effective_date {effective_date}. Nothing was created."
		)

	duplicate = frappe.db.get_value(
		RELATED_PARTY,
		{"party_name": party_name, "relationship_to_company": relationship, "company": company},
		"name",
	)
	if duplicate:
		raise ToolError(
			f"{party_name} is already registered as {relationship} of {company} ({duplicate}). A "
			"second role for the same person is a second entry with a different "
			"relationship_to_company; the same role twice is a duplicate. Nothing was created."
		)

	tax_id_type, tax_id_last4 = _validate_tax_id(
		as_str(args, "tax_id_type"), as_str(args, "tax_id_last4"), "Nothing was created."
	)

	cap_table_entry = as_str(args, "cap_table_entry")
	if cap_table_entry:
		_check_link(CAP_TABLE, cap_table_entry, company, "company", "cap_table_entry")
	supplier = as_str(args, "supplier")
	if supplier:
		_check_link("Supplier", supplier, "", "", "supplier")
	governing_document = as_str(args, "governing_document")
	if governing_document:
		_check_link(
			GOVERNANCE_DOCUMENT, governing_document, company, "company", "governing_document"
		)

	doc = frappe.new_doc(RELATED_PARTY)
	doc.party_name = party_name
	doc.company = company
	doc.party_type = party_type
	doc.relationship_to_company = relationship
	doc.effective_date = effective_date
	doc.tax_id_type = tax_id_type
	for field, value in (
		("end_date", end_date),
		("tax_id_last4", tax_id_last4),
		("address", as_str(args, "address")),
		("cap_table_entry", cap_table_entry),
		("supplier", supplier),
		("governing_document", governing_document),
		("notes", as_str(args, "notes")),
	):
		if value:
			doc.set(field, value)
	doc.insert()

	row = frappe.db.get_value(
		RELATED_PARTY, doc.name, compat.existing_fields(RELATED_PARTY, _PARTY_FIELDS), as_dict=True
	)
	data = _describe(dict(row), frappe.utils.today())
	data["disclosable"] = relationship in DISCLOSABLE_RELATIONSHIPS
	data["note"] = (
		"This is governance, not accounting. Nothing about how a transaction with this party "
		"posts has changed: the Party field on a Journal Entry is still Supplier / Customer / "
		"Employee, and this register sits beside it."
	)
	next_steps = []
	if not governing_document:
		next_steps.append(
			"File the instrument that establishes this relationship with "
			"attach_governance_document and point governing_document at it — a relationship with "
			"no paper behind it is a claim."
		)
	if relationship in ("Member", "Manager") and not cap_table_entry:
		next_steps.append(
			"If this party holds an interest, link their Cap Table Entry so the register and the "
			"member register cannot drift apart."
		)
	if not supplier and relationship in DISCLOSABLE_RELATIONSHIPS:
		next_steps.append(
			"If this party is ever paid, link their Supplier record: that is what makes "
			"generate_1099_prefill flag the payment as a related-party transaction."
		)
	if next_steps:
		data["next_step"] = " ".join(next_steps)
	if tax_id_type == "None":
		data["warning"] = (
			"No tax id recorded. A 1099 cannot be issued to this party until at least the type "
			"and last four digits are known — which means getting a signed W-9."
		)

	return ToolResult(
		data,
		f"registered {party_name} ({party_type}) as {relationship} of {company} from "
		f"{effective_date} as {doc.name}",
		docstatus_delta="none → 0 (created)",
	)


# ── 88. update_related_party ────────────────────────────────────────────────
def update_related_party(args: dict) -> ToolResult:
	"""Change a registered relationship. Cannot re-key it: name, role and company are the key."""
	_require()
	requested = as_str(args, "party", required=True)
	requested_company = as_str(args, "company")
	# A docname is unambiguous, so a `company` argument beside one is a GUARD
	# rather than a filter — and the guard's refusal should be the one that
	# explains why the key cannot be edited. A bare name still gets narrowed.
	entry = party_row(
		requested, "" if frappe.db.exists(RELATED_PARTY, requested) else requested_company
	)
	company = entry["company"]

	for field, why in (
		("party_name", "the docname is built from it"),
		("relationship_to_company", "the docname is built from it, and a change of role is a new relationship"),
		("company", "a party related to a different company is a different entry"),
	):
		if field in args and as_str(args, field) not in ("", str(entry.get(field) or "")):
			raise ToolError(
				f"{field} cannot be changed: {why}. Register the new fact with "
				"create_related_party and set an end_date on this one. Nothing was changed."
			)

	changes: dict = {}
	updates: dict = {}

	if "party_type" in args:
		requested = as_str(args, "party_type")
		if requested:
			party_type = as_choice(RELATED_PARTY, "party_type", requested, "party_type")
			if party_type != entry.get("party_type"):
				updates["party_type"] = party_type
				changes["party_type"] = [entry.get("party_type"), party_type]

	effective_date = as_date(args, "effective_date")
	if effective_date and effective_date != str(entry.get("effective_date") or ""):
		updates["effective_date"] = effective_date
		changes["effective_date"] = [str(entry.get("effective_date") or ""), effective_date]

	if "end_date" in args:
		end_date = as_date(args, "end_date") or ""
		if end_date != str(entry.get("end_date") or ""):
			updates["end_date"] = end_date or None
			changes["end_date"] = [str(entry.get("end_date") or ""), end_date]

	final_effective = updates.get("effective_date", str(entry.get("effective_date") or ""))
	final_end = updates.get("end_date") or ""
	if "end_date" not in updates:
		final_end = str(entry.get("end_date") or "")
	if final_end and final_effective and final_end < final_effective:
		raise ToolError(
			f"end_date {final_end} is before effective_date {final_effective}. Nothing was changed."
		)

	if "tax_id_type" in args or "tax_id_last4" in args:
		tax_id_type = (
			as_str(args, "tax_id_type") if "tax_id_type" in args else str(entry.get("tax_id_type") or "None")
		)
		tax_id_last4 = (
			as_str(args, "tax_id_last4") if "tax_id_last4" in args else str(entry.get("tax_id_last4") or "")
		)
		tax_id_type, tax_id_last4 = _validate_tax_id(tax_id_type, tax_id_last4, "Nothing was changed.")
		if tax_id_type != str(entry.get("tax_id_type") or "None"):
			updates["tax_id_type"] = tax_id_type
			changes["tax_id_type"] = [entry.get("tax_id_type") or "None", tax_id_type]
		if tax_id_last4 != str(entry.get("tax_id_last4") or ""):
			updates["tax_id_last4"] = tax_id_last4 or None
			changes["tax_id_last4"] = [entry.get("tax_id_last4") or "", tax_id_last4]

	for field in ("address", "notes"):
		if field in args:
			value = as_str(args, field)
			if value != str(entry.get(field) or ""):
				updates[field] = value or None
				changes[field] = [entry.get(field) or "", value]

	for field, doctype, company_field in (
		("cap_table_entry", CAP_TABLE, "company"),
		("supplier", "Supplier", ""),
		("governing_document", GOVERNANCE_DOCUMENT, "company"),
	):
		if field in args:
			requested = as_str(args, field)
			if requested:
				_check_link(doctype, requested, company if company_field else "", company_field, field)
			if requested != str(entry.get(field) or ""):
				updates[field] = requested or None
				changes[field] = [entry.get(field) or "", requested]

	if not changes:
		raise ToolError(
			f"nothing to change on {entry['name']}. Pass at least one of party_type, "
			"effective_date, end_date, tax_id_type, tax_id_last4, address, cap_table_entry, "
			"supplier, governing_document or notes, with a value that differs from the current "
			"one."
		)

	doc = frappe.get_doc(RELATED_PARTY, entry["name"])
	for field, value in updates.items():
		doc.set(field, value)
	doc.save()

	after = frappe.db.get_value(
		RELATED_PARTY, entry["name"], compat.existing_fields(RELATED_PARTY, _PARTY_FIELDS), as_dict=True
	)
	data = _describe(dict(after), frappe.utils.today())
	data["changes"] = changes
	data["note"] = (
		"An entry is never deleted when a relationship ends — set end_date. The transactions it "
		"explains are still in the ledger, and a disclosure schedule for a prior year still needs "
		"to know who was who at the time."
	)
	summary = ", ".join(f"{field} {before!r} → {value!r}" for field, (before, value) in changes.items())
	return ToolResult(data, f"updated Related Party {entry['name']}: {summary}", docstatus_delta="")


# ── 89. list_related_parties ────────────────────────────────────────────────
def list_related_parties(args: dict) -> ToolResult:
	"""One company's related-party register, current relationships and ended ones."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	limit = min(as_limit(args), REGISTER_CAP)
	today = frappe.utils.today()

	filters = {"company": company}
	party_type = as_str(args, "party_type")
	if party_type:
		filters["party_type"] = as_choice(RELATED_PARTY, "party_type", party_type, "party_type")
	relationship = as_str(args, "relationship_to_company")
	if relationship:
		filters["relationship_to_company"] = as_choice(
			RELATED_PARTY, "relationship_to_company", relationship, "relationship_to_company"
		)
	supplier = as_str(args, "supplier")
	if supplier:
		filters["supplier"] = supplier

	fields = compat.existing_fields(RELATED_PARTY, _PARTY_FIELDS)
	rows = frappe.db.get_all(
		RELATED_PARTY, filters=filters, fields=fields, order_by="party_name asc", limit=limit
	)
	parties = [_describe(dict(row), today) for row in rows]

	current_only = as_bool(args, "current_only", False)
	if current_only:
		parties = [party for party in parties if party["current"]]

	by_relationship: dict = {}
	by_party_type: dict = {}
	for party in parties:
		by_relationship[party["relationship_to_company"]] = (
			by_relationship.get(party["relationship_to_company"], 0) + 1
		)
		by_party_type[party["party_type"]] = by_party_type.get(party["party_type"], 0) + 1

	people = sorted({party["party_name"] for party in parties})
	without_document = [party["name"] for party in parties if not party["governing_document"]]
	without_tax_id = [
		party["name"]
		for party in parties
		if party["tax_id_type"] == "None" and party["relationship_to_company"] != "Other"
	]

	total_count = frappe.db.count(RELATED_PARTY, {"company": company})
	data = {
		"company": company,
		"parties": parties,
		"count": len(parties),
		"total_in_register": total_count,
		"as_of": today,
		"distinct_people": len(people),
		"current_count": len([party for party in parties if party["current"]]),
		"ended_count": len([party for party in parties if not party["current"]]),
		"by_relationship": by_relationship,
		"by_party_type": by_party_type,
		"linked_to_supplier": len([party for party in parties if party["supplier"]]),
		"linked_to_cap_table": len([party for party in parties if party["cap_table_entry"]]),
		"without_governing_document": without_document,
		"without_tax_id": without_tax_id,
		"current_only": bool(current_only),
		"limit": limit,
		"note": (
			"One person may appear more than once: a Manager who is also a Member is two "
			"entries, under two instruments, from two dates. `distinct_people` counts names, "
			"`count` counts relationships. Ended relationships are listed by default — the "
			"transactions they explain are still in the ledger."
		),
	}
	if len(parties) < total_count and not (party_type or relationship or supplier or current_only):
		data["warning"] = (
			f"{total_count} relationships are registered for {company} but the limit returned "
			f"{len(parties)}. Raise `limit` before treating this as the whole register."
		)
	elif without_document:
		data["warning"] = (
			f"{len(without_document)} relationship(s) have no governing document linked: "
			f"{', '.join(without_document[:5])}. A relationship nothing on paper establishes is "
			"a claim, and it is the first thing an examiner asks for."
		)
	return ToolResult(
		data,
		f"{len(parties)} relationship(s) across {len(people)} party/parties for {company} "
		f"({data['current_count']} current, {data['ended_count']} ended)",
	)


# ── 90. get_related_party ───────────────────────────────────────────────────
def get_related_party(args: dict) -> ToolResult:
	"""One relationship in full, with everything on this site that points at it."""
	_require()
	company = as_str(args, "company")
	entry = party_row(as_str(args, "party", required=True), company)
	today = frappe.utils.today()
	data = _describe(entry, today)
	data["disclosable"] = entry.get("relationship_to_company") in DISCLOSABLE_RELATIONSHIPS

	data["other_roles"] = sorted(
		row
		for row in (
			frappe.db.get_all(
				RELATED_PARTY,
				filters={
					"party_name": entry.get("party_name"),
					"company": entry.get("company"),
					"name": ("!=", entry["name"]),
				},
				pluck="name",
				limit=25,
			)
			or []
		)
	)

	data["cap_table_detail"] = None
	if entry.get("cap_table_entry") and frappe.db.exists(CAP_TABLE, entry["cap_table_entry"]):
		fields = compat.existing_fields(
			CAP_TABLE,
			("name", "member_id", "legal_entity_name", "entity_type", "ownership_percentage", "retired"),
		)
		data["cap_table_detail"] = dict(
			frappe.db.get_value(CAP_TABLE, entry["cap_table_entry"], fields, as_dict=True) or {}
		)

	data["supplier_detail"] = None
	if entry.get("supplier") and frappe.db.exists("Supplier", entry["supplier"]):
		fields = compat.existing_fields(
			"Supplier", ("name", "supplier_name", "supplier_type", "supplier_group", "tax_id", "disabled")
		)
		supplier = dict(frappe.db.get_value("Supplier", entry["supplier"], fields, as_dict=True) or {})
		# Never echo a full tax id back through an MCP result, whatever the site
		# has stored on the Supplier. The register's own four digits are the
		# number this app is willing to hand over.
		if "tax_id" in supplier:
			supplier["tax_id"] = "on file" if supplier.get("tax_id") else None
		data["supplier_detail"] = supplier

	data["parcels_titled"] = (
		frappe.db.get_all(PARCEL, filters={"title_holder": entry["name"]}, pluck="name", limit=100)
		if compat.doctype_exists(PARCEL)
		else []
	)
	data["leases_as_counterparty"] = (
		frappe.db.get_all(LEASE, filters={"counterparty": entry["name"]}, pluck="name", limit=100)
		if compat.doctype_exists(LEASE)
		else []
	)

	data["note"] = (
		"`tax_id_last4` is four digits and this app never returns more, including from a linked "
		"Supplier — `supplier_detail.tax_id` says only whether one is on file. `other_roles` "
		"lists this person's other capacities at the same company, which is the normal state for "
		"a member-manager."
	)
	return ToolResult(
		data,
		f"related party {entry['name']}: {data['party_type']}, "
		f"{data['relationship_to_company']} of {data['company']} since {data['effective_date']}"
		+ ("" if data["current"] else f", ended {data['end_date']}"),
	)


def relationship_options() -> list[str]:
	"""The relationships this site declares, read off the meta. Used by `tax.py`."""
	return select_options(RELATED_PARTY, "relationship_to_company")
