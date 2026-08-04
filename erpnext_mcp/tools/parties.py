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
FAMILY = "Family"
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
			f"tax_id_last4 must be exactly four digits. Got {tax_id_last4!r} ({len(digits)} digits). {tail}"
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
		_check_link(GOVERNANCE_DOCUMENT, governing_document, company, "company", "governing_document")

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
	entry = party_row(requested, "" if frappe.db.exists(RELATED_PARTY, requested) else requested_company)
	company = entry["company"]

	for field, why in (
		("party_name", "the docname is built from it"),
		(
			"relationship_to_company",
			"the docname is built from it, and a change of role is a new relationship",
		),
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


# ── the family register ─────────────────────────────────────────────────────
#: Why this register exists at all, in one place. ERPNext resolves a posting's
#: counterparty as a Dynamic Link THROUGH its party type: `party_type` is a Link
#: to DocType, so a party type called `Family` is only valid if there is a DocType
#: called Family, and `party` is only valid if it is a record in it. Customer,
#: Supplier, Employee and Shareholder each have one. A relative had none, which is
#: what took v0.12.0's `bench migrate` down — see `tools/company.py`.
#:
#: WHAT IT DELIBERATELY DOES NOT HOLD: a tax id. A transfer below the IRS annual
#: gift exclusion is not compensation for services, needs no W-9 and produces no
#: 1099, which is the whole reason this party type is separate from Supplier.
#: Where a relative ALSO has a tax identity worth recording — because they are a
#: member, a lessor, a trustee — `related_party` points at the register that holds
#: four digits and never more.
#:
#: WHAT `related_to` IS FOR, AND WHY IT IS NOT A LINK. "Alexander Polehn — Son"
#: does not say whose son, and a register belonging to a company with two members
#: answers that two ways. The field holds the name of the other person; which
#: register that name is in is decided on read by `_resolve_related_to`, because
#: a Frappe Link points at exactly one doctype and the answer is a Family record,
#: a Related Party record, or somebody in neither. See the Family controller.
_FAMILY_FIELDS = (
	"name",
	"family_member_name",
	"relationship",
	"related_to",
	"related_party",
	"active",
	"notes",
	"creation",
	"owner",
)

#: How far `get_family_member` will follow `related_to` before it stops. Three
#: generations is the whole of what this register is for — a son, his father, the
#: entity that father manages — and a walk with no ceiling is a walk that hangs on
#: a cycle somebody typed in by hand. The walk also refuses to visit a name twice,
#: so the ceiling is a second belt rather than the only one.
RELATED_TO_MAX_DEPTH = 6


def _require_family() -> None:
	compat.require_doctype(
		FAMILY,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _describe_family(row: dict) -> dict:
	related_to = str(row.get("related_to") or "").strip()
	doctype, resolved = _resolve_related_to(related_to)
	return {
		"name": row.get("name"),
		"family_member_name": row.get("family_member_name"),
		"relationship": row.get("relationship") or None,
		"related_to": related_to or None,
		# Which register answered, or None for a name in neither. A caller that
		# wants to follow the pointer needs the doctype as well as the value, and
		# a caller that only wants to print the row does not have to look.
		"related_to_doctype": doctype,
		"related_to_name": resolved,
		"related_party": row.get("related_party") or None,
		"has_related_party": bool(row.get("related_party")),
		"active": compat.checked(row.get("active")),
		"notes": row.get("notes") or None,
		"described_as": _described_as(row.get("family_member_name"), row.get("relationship"), related_to),
	}


def _described_as(member_name, relationship, related_to: str) -> str:
	"""The row as a human reads it: "Alexander Polehn — Son of Tim Polehn".

	One string rather than three fields a caller has to assemble, because this is
	the sentence the register exists to be able to say and every consumer of it
	would otherwise write the same join. Degrades cleanly: with no `related_to` it
	stops after the relationship, and with neither it is just the name.
	"""
	out = str(member_name or "")
	relationship = str(relationship or "").strip()
	if relationship:
		out = f"{out} — {relationship}"
		if related_to:
			out = f"{out} of {related_to}"
	elif related_to:
		out = f"{out} — related to {related_to}"
	return out


def _resolve_related_to(related_to: str) -> tuple[str | None, str | None]:
	"""Which register holds `related_to`, and under what docname.

	Family first, then Related Party. The order is the answer to the ambiguous
	case rather than an arbitrary preference: a name that is in both registers is
	one person recorded twice for two different reasons, and the Family record is
	the one this register's own reader is looking at. A name in neither register
	resolves to `(None, None)` — free text, which is the fallback the field was
	designed with and not a failure.

	Never raises. This runs on every row of `list_family_members`, and a register
	that refused to list itself because one member's parent is not on file would
	be a register nobody could fill in incrementally.
	"""
	related_to = str(related_to or "").strip()
	if not related_to:
		return None, None
	try:
		if compat.doctype_exists(FAMILY) and frappe.db.exists(FAMILY, related_to):
			return FAMILY, related_to
		if compat.doctype_exists(RELATED_PARTY):
			if frappe.db.exists(RELATED_PARTY, related_to):
				return RELATED_PARTY, related_to
			match = frappe.db.get_value(RELATED_PARTY, {"party_name": related_to}, "name")
			if match:
				return RELATED_PARTY, match
	except Exception:  # pragma: no cover - a half-migrated site mid-`bench migrate`
		return None, None
	return None, None


def family_row(member: str) -> dict:
	"""One Family record as a dict, from its docname — which is the person's name."""
	member = (member or "").strip()
	if not member:
		raise ToolError("family_name is required (the person's name, which is the docname)")
	fields = compat.existing_fields(FAMILY, _FAMILY_FIELDS)

	if frappe.db.exists(FAMILY, member):
		return dict(frappe.db.get_value(FAMILY, member, fields, as_dict=True) or {})

	matches = frappe.db.get_all(
		FAMILY, filters={"family_member_name": ("like", member)}, fields=fields, limit=25
	)
	if len(matches) == 1:
		return dict(matches[0])
	if len(matches) > 1:
		names = ", ".join(sorted(str(match.get("name")) for match in matches))
		raise ToolError(f"{member!r} matches {len(matches)} family members: {names}.")
	raise ToolError(f"nobody called {member!r} is on the family register. list_family_members has it.")


def _party_postings(member: str) -> dict:
	"""How often this person has been the party on a posting, and over what span.

	The traceability half of the register. "We moved money to Alex eleven times
	last year" is the question a family petty-cash arrangement gets asked, and
	the answer is in the ledger rather than here — so this reads it rather than
	keeping a second copy that would drift.
	"""
	if not compat.doctype_exists("GL Entry"):
		return {"posting_count": 0, "first_posting": None, "last_posting": None, "companies": []}
	fields = compat.existing_fields("GL Entry", ("name", "posting_date", "company", "debit", "credit"))
	rows = frappe.db.get_all(
		"GL Entry",
		filters={"party_type": FAMILY, "party": member, "is_cancelled": 0},
		fields=fields,
		order_by="posting_date asc",
		limit=REGISTER_CAP,
	)
	dates = [str(row.get("posting_date") or "") for row in rows or [] if row.get("posting_date")]
	total = sum(float(row.get("debit") or 0) - float(row.get("credit") or 0) for row in rows or [])
	return {
		"posting_count": len(rows or []),
		"first_posting": dates[0] if dates else None,
		"last_posting": dates[-1] if dates else None,
		"net_amount": round(total, 2),
		"companies": sorted({row.get("company") for row in rows or [] if row.get("company")}),
	}


# ── 122. create_family_member ───────────────────────────────────────────────
def create_family_member(args: dict) -> ToolResult:
	"""Put one person on the family register, so a posting can name them.

	RELATIONSHIP VOCABULARY. Spouse, Son, Daughter, Child, Parent, Sibling,
	Grandchild, Grandparent, In-Law, Other. Son and Daughter arrived in v0.13.0
	beside Child rather than instead of it — records already saying Child are
	still true, and asking somebody to re-pick a value that has not changed is
	work with no answer at the end of it.

	`related_to` ANSWERS "OF WHOM". A register that says "Alexander Polehn — Son"
	and cannot say whose son is ambiguous the moment the entity has two members.
	Pass the other person's name: a Family docname, a Related Party docname or
	party name, or — for somebody in neither register — their name as plain text.
	The result reports which register answered as `related_to_doctype`, and None
	there means free text rather than a failure.

	SIMPLE CASE, ONE POINTER. Alex is Tim's son → `related_to="Tim Polehn"`.
	COMPLEX CASE, POINTER PLUS PROSE. Alex is Tim's son AND Donella's grandson →
	`related_to="Tim Polehn"` and "also grandson of Donella Polehn" in `notes`.
	One primary pointer is deliberate: a child table of relationships would model
	a family tree properly and would turn a register whose job is to make a
	posting resolve into a genealogy database. If full genealogical modelling is
	ever genuinely needed it belongs in a Family Tree doctype of its own, and this
	field would point into it rather than grow into one.
	"""
	_require_family()
	family_name = as_str(args, "family_name", required=True)

	existing = frappe.db.get_value(FAMILY, {"family_member_name": family_name}, "name")
	if existing:
		raise ToolError(
			f"{existing} is already on the family register. One record per person: the name is "
			"the docname, and it is what every posting to them points at. Change it with "
			"update_family_member. Nothing was created."
		)

	doc = frappe.new_doc(FAMILY)
	doc.family_member_name = family_name
	doc.notes = as_str(args, "notes")

	relationship = as_str(args, "relationship")
	if relationship:
		doc.relationship = as_choice(FAMILY, "relationship", relationship, "relationship")

	related_to = as_str(args, "related_to")
	if related_to:
		doc.related_to = related_to

	related_party = as_str(args, "related_party")
	if related_party:
		compat.require_doctype(RELATED_PARTY, "It ships with erpnext_mcp — run `bench migrate`.")
		if not frappe.db.exists(RELATED_PARTY, related_party):
			raise ToolError(
				f"no Related Party called {related_party!r}. Register it with "
				"create_related_party first, or leave this blank — a family member needs no "
				"related-party entry unless they also hold a role worth disclosing. Nothing was "
				"created."
			)
		doc.related_party = related_party

	active = as_bool(args, "active")
	doc.active = 1 if (active is None or active) else 0
	doc.insert()

	described = _describe_family(dict(doc.as_dict()))
	notes = [
		"This register holds no tax id on purpose. A transfer below the IRS annual gift "
		"exclusion is not compensation for services: no W-9, no 1099. Somebody paid for work "
		"is a Contact or a Supplier, and the posting should say so."
	]
	if not described["relationship"]:
		notes.append(
			"No relationship recorded. 'Why did money go to this person' is the first question "
			"these postings get asked, and a name alone does not answer it."
		)
	if not described["related_to"]:
		notes.append(
			"No related_to set — unassigned parent. The register can say what this person is "
			"but not whose, and 'Child' means two different people in an entity with two "
			"members. Set it with update_family_member."
		)
	elif not described["related_to_doctype"]:
		notes.append(
			f"related_to {described['related_to']!r} is in neither the family register nor the "
			"related-party register, so it is being kept as free text. That is the intended "
			"fallback for somebody who receives nothing and holds no role — register them if "
			"either of those changes."
		)
	return ToolResult(
		data={
			**described,
			"party_type": FAMILY,
			"next_step": (
				f"Journal entry lines can now carry party_type='Family', party='{doc.name}'. The "
				"1099 pre-fill excludes Family-party postings and reports the count, so they are "
				"visibly excluded rather than silently missing."
			),
			"notes_on_use": notes,
		},
		summary=f"added {doc.name} to the family register",
		docstatus_delta="none → 0 (created)",
	)


# ── 123. update_family_member ───────────────────────────────────────────────
def update_family_member(args: dict) -> ToolResult:
	"""Change a family member's relationship, `related_to`, related party, active flag or notes.

	`related_to` is where an existing record acquires the answer to "of whom".
	Nothing backfilled it on upgrade and nothing will: which of two members
	somebody is the child of is a fact only the family knows, and a migration that
	guessed would produce a register that looks complete and is wrong. An empty
	string clears it.
	"""
	_require_family()
	row = family_row(as_str(args, "family_name", required=True))

	if as_str(args, "new_family_name") or as_str(args, "family_member_name"):
		raise ToolError(
			"a family member's name cannot be changed here: it IS the docname, and every "
			"journal entry that named them points at it. Renaming would orphan those postings. "
			"Nothing was changed."
		)

	doc = frappe.get_doc(FAMILY, row["name"])
	changes = {}

	if "relationship" in args:
		value = as_str(args, "relationship")
		_stage_family(
			changes,
			doc,
			"relationship",
			as_choice(FAMILY, "relationship", value, "relationship") if value else "",
		)
	if "related_to" in args:
		related_to = as_str(args, "related_to")
		if related_to and related_to.lower() == str(row.get("family_member_name") or "").lower():
			raise ToolError(
				f"{row['name']} cannot be related to themselves. related_to names the OTHER "
				"person — the parent, the grandparent, the member this one hangs off. Nothing "
				"was changed."
			)
		_stage_family(changes, doc, "related_to", related_to)
	if "notes" in args:
		_stage_family(changes, doc, "notes", as_str(args, "notes"))
	if "related_party" in args:
		related_party = as_str(args, "related_party")
		if related_party and not frappe.db.exists(RELATED_PARTY, related_party):
			raise ToolError(f"no Related Party called {related_party!r}. Nothing was changed.")
		_stage_family(changes, doc, "related_party", related_party)
	if "active" in args:
		_stage_family(changes, doc, "active", 1 if as_bool(args, "active") else 0)

	if not changes:
		raise ToolError(
			"nothing to change. Pass at least one of: relationship, related_to, related_party, active, notes."
		)

	doc.save()
	described = _describe_family(dict(doc.as_dict()))
	warnings = []
	if "active" in changes and not described["active"]:
		postings = _party_postings(row["name"])["posting_count"]
		warnings.append(
			f"Marked inactive rather than deleted, which is right: {postings} posting(s) already "
			"name this person and deleting the record would orphan them."
		)
	if "related_to" in changes and described["related_to"] and not described["related_to_doctype"]:
		warnings.append(
			f"related_to {described['related_to']!r} matches no Family and no Related Party "
			"record, so it is kept as free text. That is the intended fallback, not an error — "
			"but check the spelling if you expected it to resolve."
		)
	return ToolResult(
		data={
			**described,
			"changed": {key: [before, after] for key, (before, after) in changes.items()},
			"warnings": warnings,
		},
		summary=f"{doc.name}: {len(changes)} field(s) changed",
		docstatus_delta="0 → 0 (updated)",
	)


def _stage_family(changes: dict, doc, field: str, wanted) -> None:
	before = doc.get(field)
	before = "" if before is None else before
	if str(before) == str(wanted):
		return
	changes[field] = [before or None, wanted or None]
	doc.set(field, wanted if wanted != "" else None)


# ── 124. list_family_members ────────────────────────────────────────────────
def list_family_members(args: dict) -> ToolResult:
	"""The family register: who each person is, whose they are, and who has a party record.

	EVERY ROW CARRIES `related_to` AND THE SENTENCE IT MAKES. "Alexander Polehn —
	Son of Tim Polehn" is what somebody reading this register wants, and a list
	that returned the two halves separately would have every caller joining them
	the same way.

	A MEMBER WITH NO `related_to` IS WARNED ABOUT, NOT REFUSED. Records written
	before v0.13.0 have none, nothing backfilled them, and the answer — which of
	two members somebody is the child of — is a fact only the family has. So they
	are listed under `without_related_to` and named in a warning, which is the
	work list rather than an error.
	"""
	_require_family()
	limit = as_limit(args)

	filters = {}
	if "active" in args:
		filters["active"] = 1 if as_bool(args, "active") else 0
	relationship = as_str(args, "relationship")
	if relationship:
		filters["relationship"] = as_choice(FAMILY, "relationship", relationship, "relationship")
	related_to = as_str(args, "related_to")
	if related_to:
		filters["related_to"] = related_to

	rows = frappe.db.get_all(
		FAMILY,
		filters=filters,
		fields=compat.existing_fields(FAMILY, _FAMILY_FIELDS),
		order_by="family_member_name asc",
		limit=min(limit, REGISTER_CAP),
	)
	members = [_describe_family(dict(row)) for row in rows]

	by_relationship: dict = {}
	for member in members:
		key = member["relationship"] or "(unrecorded)"
		by_relationship[key] = by_relationship.get(key, 0) + 1

	unassigned = [member["name"] for member in members if not member["related_to"]]
	unresolved = [
		member["name"] for member in members if member["related_to"] and not member["related_to_doctype"]
	]
	data = {
		"member_count": len(members),
		"active_count": len([member for member in members if member["active"]]),
		"by_relationship": dict(sorted(by_relationship.items())),
		"with_related_party": [member["name"] for member in members if member["has_related_party"]],
		"without_related_party": [member["name"] for member in members if not member["has_related_party"]],
		"without_relationship": [member["name"] for member in members if not member["relationship"]],
		"without_related_to": unassigned,
		"related_to_free_text": unresolved,
		"party_type": FAMILY,
		"members": members,
		"note": (
			"A missing related-party entry is not a gap for most of these — a relative who "
			"only receives transfers needs no W-9 and no disclosure. It IS a gap for one who "
			"also holds a role: a member, a lessor, a trustee. That is the list to read. "
			"`related_to` is a different question: not 'do they have a tax identity' but "
			"'whose relative are they', and `related_to_doctype` says which register answered "
			"it — None there means the name is being kept as free text, which is the "
			"designed fallback for somebody in neither register."
		),
	}
	if unassigned:
		data["warning"] = (
			f"{len(unassigned)} member(s) have no related_to set — unassigned parent: "
			f"{', '.join(unassigned[:5])}. The register can say what they are and not whose, "
			"and 'Child' means two different people in an entity with two members. NOTHING "
			"BACKFILLED THESE and nothing will: which member somebody is the child of is a "
			"fact only the family has, and a guess would produce a register that looks "
			"complete and is wrong. Fill them in with update_family_member."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(members)} family member(s), "
			f"{len([member for member in members if member['active']])} active"
			+ (f", {len(unassigned)} with no related_to" if unassigned else "")
		),
	)


# ── 125. get_family_member ──────────────────────────────────────────────────
def get_family_member(args: dict) -> ToolResult:
	"""One family member, whose they are all the way up, and every posting naming them.

	THE CHAIN IS THE POINT. `related_to` on one record answers "whose son"; the
	chain answers the question somebody actually has, which is where this person
	sits in the family and what the family sits in. Alex → Son of Tim → Manager of
	Orchard Meadow, LLC crosses two registers to get there and neither one holds
	it alone. `relationship_chain` is that walk, `relationship_path` is the
	sentence it makes.

	IT STOPS RATHER THAN LOOPS. A name already visited ends the walk, and so does
	`RELATED_TO_MAX_DEPTH`. Free text is a leaf: a name in neither register has
	nothing to follow, and reporting it as the end of the chain is more honest
	than dropping it.
	"""
	_require_family()
	row = family_row(as_str(args, "family_name", required=True))
	described = _describe_family(row)
	postings = _party_postings(row["name"])
	chain = _relationship_chain(row)

	related = {}
	if row.get("related_party") and compat.doctype_exists(RELATED_PARTY):
		detail = dict(
			frappe.db.get_value(
				RELATED_PARTY,
				row["related_party"],
				compat.existing_fields(
					RELATED_PARTY,
					(
						"name",
						"party_name",
						"party_type",
						"relationship_to_company",
						"company",
						"effective_date",
						"end_date",
						"tax_id_type",
						"tax_id_last4",
					),
				),
				as_dict=True,
			)
			or {}
		)
		related = {
			"name": detail.get("name"),
			"party_name": detail.get("party_name"),
			"party_type": detail.get("party_type"),
			"relationship_to_company": detail.get("relationship_to_company"),
			"company": detail.get("company"),
			"effective_date": str(detail.get("effective_date") or "") or None,
			"end_date": str(detail.get("end_date") or "") or None,
			"tin_type": detail.get("tax_id_type") or "None",
			# Four digits, never nine — the same rule get_related_party keeps.
			"tin_last4": detail.get("tax_id_last4") or "",
		}

	notes = []
	if not described["active"] and postings["posting_count"]:
		notes.append(
			f"Marked inactive, and {postings['posting_count']} posting(s) still name them. That "
			"is correct: the record stays so the postings keep resolving."
		)
	if not related:
		notes.append(
			"No related-party entry. Fine for a relative who only receives transfers; a gap for "
			"one who is also a member, a lessor or a trustee, because that is what a "
			"related-party disclosure is built from."
		)
	if not described["related_to"]:
		notes.append(
			"No related_to set — unassigned parent. This register can say what this person is "
			"and not whose, which is ambiguous in an entity with more than one member. Set it "
			"with update_family_member; nothing guessed it on upgrade and nothing will."
		)
	elif not described["related_to_doctype"]:
		notes.append(
			f"related_to {described['related_to']!r} is free text — it matches no Family and no "
			"Related Party record, so the chain ends there. That is the designed fallback for "
			"somebody in neither register, not a broken link."
		)
	return ToolResult(
		data={
			**described,
			"party_type": FAMILY,
			"related_party_detail": related or None,
			"relationship_chain": chain,
			"relationship_path": _chain_sentence(chain),
			**postings,
			"compliance_notes": notes,
		},
		summary=(f"{_chain_sentence(chain) or row['name']}, {postings['posting_count']} posting(s)"),
	)


#: The three kinds of hop the walk makes, named because the sentence it produces
#: reads differently for each. `related_to` is a step to ANOTHER PERSON — "Son of
#: Tim". `related_party` is a step to THE SAME PERSON in the other register, which
#: is what carries their role and their company — "Manager of Orchard Meadow,
#: LLC". Rendering the second as though it were the first would produce "Tim, Son
#: of Tim", which is the bug that made this distinction worth a field.
VIA_SELF = "self"
VIA_RELATED_TO = "related_to"
VIA_RELATED_PARTY = "related_party"


def _family_hop(row: dict, via: str) -> dict:
	return {
		"doctype": FAMILY,
		"name": row.get("name"),
		"display_name": row.get("family_member_name") or row.get("name"),
		"relationship": row.get("relationship") or None,
		"relates_to": str(row.get("related_to") or "").strip() or None,
		"via": via,
		"resolved": True,
	}


def _party_hop(name: str, via: str, ends_because: str) -> dict:
	row = dict(
		frappe.db.get_value(
			RELATED_PARTY,
			name,
			compat.existing_fields(
				RELATED_PARTY, ("name", "party_name", "relationship_to_company", "company")
			),
			as_dict=True,
		)
		or {}
	)
	return {
		"doctype": RELATED_PARTY,
		"name": row.get("name") or name,
		"display_name": row.get("party_name") or name,
		"relationship": row.get("relationship_to_company") or None,
		"company": row.get("company") or None,
		"relates_to": row.get("company") or None,
		"via": via,
		"resolved": True,
		"chain_ends_because": ends_because,
	}


def _relationship_chain(row: dict) -> list[dict]:
	"""`related_to` followed upward until it runs out, then across to the company.

	TWO DIFFERENT EDGES, WHICH IS THE WHOLE DESIGN. `related_to` goes to another
	*person*, so it is followed as far as it goes. `related_party` goes to the
	*same* person's entry in the register that holds roles and entities, so it is
	followed exactly once, at the top, and it is what turns a family chain into an
	answer about the business: Alex → Son of Tim → Manager of Orchard Meadow, LLC
	crosses Family → Related Party → Company and no single record holds it.

	IT TERMINATES, AND IT SAYS WHY. A name already in the chain (a cycle somebody
	typed by hand), the depth ceiling, a name in neither register (free text,
	which is a leaf rather than a break), or simply running out of pointers. Every
	one of those writes `chain_ends_because` onto the last entry, because a chain
	that just stops leaves the reader wondering whether it stopped or broke.
	"""
	current = dict(row)
	chain: list[dict] = [_family_hop(current, VIA_SELF)]
	seen = {str(current.get("name") or "").lower()}

	while len(chain) < RELATED_TO_MAX_DEPTH:
		pointer = str(current.get("related_to") or "").strip()

		if not pointer:
			# The family runs out here. If this person also has a related-party
			# entry, that is where their role and their company live, and it is
			# the last hop worth making.
			party = str(current.get("related_party") or "").strip()
			if party and compat.doctype_exists(RELATED_PARTY) and frappe.db.exists(RELATED_PARTY, party):
				chain.append(
					_party_hop(
						party,
						VIA_RELATED_PARTY,
						"the related-party register is where the family meets the company: it "
						"names the role and the entity, and an entity has no parent to follow.",
					)
				)
			else:
				chain[-1].setdefault(
					"chain_ends_because",
					"no related_to and no related-party entry — this is the top of the chain.",
				)
			break

		if pointer.lower() in seen:
			chain[-1]["chain_ends_because"] = (
				f"{pointer!r} is already in this chain — a related_to cycle. The walk stopped "
				"rather than looping; fix it with update_family_member."
			)
			break

		doctype, resolved = _resolve_related_to(pointer)
		if doctype is None:
			chain.append(
				{
					"doctype": None,
					"name": None,
					"display_name": pointer,
					"relationship": None,
					"relates_to": None,
					"via": VIA_RELATED_TO,
					"resolved": False,
					"chain_ends_because": (
						"free text — this name is in neither register, so there is nothing "
						"further to follow. That is the designed fallback, not a broken link."
					),
				}
			)
			break

		seen.add(str(resolved or "").lower())
		if doctype == RELATED_PARTY:
			chain.append(
				_party_hop(
					resolved,
					VIA_RELATED_TO,
					"the related-party register is where the family meets the company: it names "
					"the role and the entity, and an entity has no parent to follow.",
				)
			)
			break

		current = dict(
			frappe.db.get_value(
				FAMILY, resolved, compat.existing_fields(FAMILY, _FAMILY_FIELDS), as_dict=True
			)
			or {}
		)
		chain.append(_family_hop(current, VIA_RELATED_TO))
	else:
		remaining = chain[-1].get("relates_to") or current.get("related_party")
		chain[-1]["chain_ends_because"] = (
			f"depth limit of {RELATED_TO_MAX_DEPTH} reached with {remaining!r} still to follow. "
			"A family register this deep is one to read in the Desk."
			if remaining
			else "no related_to and no related-party entry — this is the top of the chain."
		)
	return chain


def _chain_sentence(chain: list[dict]) -> str:
	"""The chain as one line: "Alex → Son of Tim → Manager of Orchard Meadow, LLC".

	A `related_to` hop reads as "<the previous entry's relationship> of <this
	one>", because that is what the previous record claims to be. A
	`related_party` hop reads as "<role> of <company>" and never names the person
	again — it is the same person, and "Tim → Parent of Tim" is nonsense.
	"""
	if not chain:  # pragma: no cover - the walk always seeds itself
		return ""
	parts = [str(chain[0]["display_name"] or "")]
	for index, entry in enumerate(chain[1:], start=1):
		if entry.get("via") == VIA_RELATED_PARTY:
			role = entry.get("relationship")
			company = entry.get("company")
			if role and company:
				parts.append(f"{role} of {company}")
			elif company:
				parts.append(f"in the related-party register for {company}")
			continue
		relationship = chain[index - 1].get("relationship")
		lead = f"{relationship} of " if relationship else "related to "
		parts.append(f"{lead}{entry['display_name']}")
		if entry.get("doctype") == RELATED_PARTY and entry.get("relationship") and entry.get("company"):
			parts.append(f"{entry['relationship']} of {entry['company']}")
	return " → ".join(parts)
