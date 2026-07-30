# SPDX-License-Identifier: MIT
"""`generate_1099_prefill` — who has to get a 1099-NEC, and what goes in Box 1.

IT IS CALLED A PRE-FILL AND IT MEANS IT. Three things this deliberately does not
finish, each of which is somebody's judgement rather than a query result:

  * **The recipient's taxpayer identification number.** This site holds four
    digits, on purpose (see `Related Party`), so every form comes out reading
    `XXX-XX-1234`. The full number is on the signed W-9, on paper, and a human
    copies it across. A tool that stored nine digits so it could print nine
    digits would be trading a real breach risk for a saved minute.
  * **The classification of anything ambiguous.** An LLC is reportable when it is
    a disregarded entity and exempt when it has elected corporate treatment, and
    nothing in a ledger says which. Those land in `borderline` with the reason
    spelled out — never silently in or silently out, because both silences are
    wrong and only one of them is visible.
  * **Copy A.** The IRS copy has to be the official scannable red-ink form or an
    electronic filing. What comes out here is a legible facsimile stamped as an
    information copy. Copies B and C print on plain paper and are the ones that
    go in the envelope.

WHERE THE MONEY COMES FROM, AND WHY IT IS GL ENTRY. Reading `Journal Entry
Account` would see only journal entries; GL Entry sees every voucher type that
reaches the ledger — journals, payment entries, purchase invoices — through one
uniform shape, carries the cost center the spreadsheet needs, and exists only for
*submitted* documents. Drafts and cancellations are therefore excluded because
they were never there, not because a filter remembered to exclude them.

THE ARITHMETIC, WHICH IS THE PART WORTH ARGUING WITH. Payments to a party are
summed as:

  * on a **Payable** account: **debits only**. A debit to accounts payable is a
    bill being paid; a credit is a bill being raised, and a 1099 reports cash
    paid, not amounts owed.
  * on **every other account**: **debits minus credits**. A site that books a
    supplier straight from expense to bank — which is what a bank feed produces,
    and what this ledger is full of — puts the party on the expense line, so the
    debit is the payment and a credit is a refund or a reversal that genuinely
    reduces it.

That rule is right in both bookkeeping styles, which is why it is a rule and not
a switch. `by_account` in the output shows the debits and credits behind every
total so the reasoning can be checked rather than believed.

WHAT IS EXCLUDED, AND SAID SO OUT LOUD. Employees, because that is W-2 territory
— and the count and total of employee-party postings is reported anyway, so
"nobody looked" and "somebody looked and excluded them" are distinguishable.
Opening entries, because an opening balance is not a payment. Cancelled vouchers,
which GL Entry does not carry. Anything under the threshold, listed with its
total so a borderline case near $600 is visible rather than absent.
"""

from __future__ import annotations

import os
import re

import frappe

from .. import __version__, compat
from ..args import as_bool, as_float, as_int, as_str, resolve_company
from ..errors import ToolError
from ..render.pdf import PdfDocument
from ..render.xlsx import STYLE_MONEY, STYLE_TEXT, Sheet, XlsxWorkbook
from ..result import ToolResult
from . import artifacts
from .parties import DISCLOSABLE_RELATIONSHIPS, RELATED_PARTY

GOVERNANCE_DOCUMENT = "Governance Document"

#: The statutory 1099-NEC reporting floor. An argument rather than a constant
#: because it has changed before and will change again, and a site preparing a
#: prior year needs that year's number.
DEFAULT_THRESHOLD = 600.0

#: Rows of ledger detail read before the tool refuses to go on. A year of one
#: company's supplier postings is hundreds; ten thousand means the filters are
#: wrong and a silent truncation would produce a wrong 1099.
LEDGER_CAP = 20000

#: Attachments a single run will produce. Thirty recipients is a large family
#: office; three hundred is a payroll company that should not be using this.
RECIPIENT_CAP = 100

#: Final words that mean a corporation, whose payments are generally exempt.
#: Matched against the LAST token of the name, not anywhere in it: "Lawson
#: Supply Co" is a company and "Corporate Cleaning LLC" is not, and a substring
#: test gets both backwards.
_CORPORATION_ENDINGS = frozenset(
	{"inc", "incorporated", "corp", "corporation", "co", "plc", "ltd", "limited"}
)

#: Tokens that mean a vendor provides legal services. Attorneys are reportable
#: EVEN WHEN INCORPORATED, which is the exception that makes "it ends in PC, skip
#: it" the wrong rule — and `Friend & Reagan PC` exactly the case it gets wrong.
#:
#: Tokens rather than substrings, and this is the reason: "law" inside "Lawson"
#: is not a law firm, and a vendor wrongly ruled an attorney gets a 1099 it
#: should not have had.
_ATTORNEY_TOKENS = frozenset({"law", "attorney", "attorneys", "legal", "counsel", "llp", "pc", "esq"})

#: Tokens and phrases that suggest a government body. Used ONLY to move something
#: into `borderline` — never to exclude it silently. "Northern Wasco County PUD"
#: is a utility district, and a name match is a hint rather than a determination.
_GOVERNMENT_TOKENS = frozenset({"irs", "usda", "dmv", "treasurer", "municipal"})
_GOVERNMENT_PHRASES = (
	"internal revenue",
	"department of revenue",
	"state of ",
	"city of ",
	"county of ",
)

#: Supplier types this site may declare, mapped to whether payments are
#: reportable. Read off the site's own Select where possible; this is the
#: fallback reading of the words themselves.
_REPORTABLE_SUPPLIER_TYPES = ("individual", "proprietorship", "sole proprietorship", "partnership")
_EXEMPT_SUPPLIER_TYPES = ("company", "corporation")

CLASSIFICATION_REPORTABLE = "reportable"
CLASSIFICATION_BORDERLINE = "borderline"
CLASSIFICATION_EXEMPT = "exempt"


def _money(value) -> float:
	return round(float(value or 0), 2)


def _year_bounds(tax_year: int) -> tuple[str, str]:
	return f"{tax_year}-01-01", f"{tax_year}-12-31"


# ── 91. generate_1099_prefill ───────────────────────────────────────────────
def generate_1099_prefill(args: dict) -> ToolResult:
	"""Aggregate a year of supplier payments into a 1099-NEC worksheet and forms."""
	company = resolve_company(as_str(args, "company"), required=True)
	tax_year = as_int(args, "tax_year")
	if tax_year is None:
		raise ToolError("tax_year is required, as a four-digit year such as 2025")
	if tax_year < 1980 or tax_year > 2200:
		raise ToolError(f"tax_year must be a four-digit year, got {tax_year}")

	start, end = _year_bounds(tax_year)
	today = frappe.utils.today()
	if end >= today:
		raise ToolError(
			f"tax year {tax_year} has not ended — it runs to {end} and today is {today}. A 1099 "
			"reports a whole calendar year of payments, so a pre-fill built now would be missing "
			f"the rest of it. The earliest this can be run for {tax_year} is {tax_year + 1}-01-01."
		)

	# Not `as_float(args.get("threshold", DEFAULT))`: `as_float` maps None to 0.0,
	# so a caller sending `threshold: null` would silently get a nothing-excluded
	# run rather than the statutory floor.
	raw_threshold = args.get("threshold")
	threshold = (
		DEFAULT_THRESHOLD if raw_threshold in (None, "") else as_float(raw_threshold, "threshold")
	)
	if threshold < 0:
		raise ToolError(f"threshold must be zero or more, got {threshold}")

	dry_run = as_bool(args, "dry_run", False)
	overwrite = as_bool(args, "overwrite", False)
	include_forms = as_bool(args, "include_forms", True)

	ledger = _read_ledger(company, start, end)
	totals = _aggregate(ledger["rows"])
	if not totals:
		seen = (
			f" {ledger['employee_count']} Employee-party posting(s) were found and excluded as "
			"W-2 territory."
			if ledger["employee_count"]
			else ""
		)
		raise ToolError(
			f"no ledger postings for {company} in {tax_year} carry a Supplier party, so there is "
			f"nothing to report.{seen} Either no supplier was paid that year, or the party field "
			"was never filled in on the entries that paid them — check a known vendor's postings "
			"with get_journal_entries before concluding the first."
		)

	recipients = [_classify(company, party, figures) for party, figures in sorted(totals.items())]
	above = [row for row in recipients if row["total_payments"] >= threshold]
	below = [row for row in recipients if row["total_payments"] < threshold]
	forms = [row for row in above if row["classification"] != CLASSIFICATION_EXEMPT]
	exempt_above = [row for row in above if row["classification"] == CLASSIFICATION_EXEMPT]

	if len(forms) > RECIPIENT_CAP:
		raise ToolError(
			f"{len(forms)} recipients meet the {threshold:,.2f} threshold, which is past this "
			f"tool's ceiling of {RECIPIENT_CAP}. That many 1099s is a payroll service's job, not "
			"a pre-fill's. Raise `threshold` to narrow it, or run the filing through software "
			"built for volume."
		)

	payer = _payer(company, args)
	workbook = _workbook(company, tax_year, threshold, payer, forms, below, exempt_above, ledger)
	workbook_bytes = workbook.render()
	form_documents = (
		[(row, _form_pdf(payer, row, tax_year)) for row in forms] if include_forms else []
	)

	summary_rows = [_public_row(row) for row in forms]
	data = {
		"company": company,
		"tax_year": tax_year,
		"threshold": _money(threshold),
		"period": {"from": start, "to": end},
		"payer": payer["public"],
		"recipients": summary_rows,
		"recipient_count": len(forms),
		"reportable_count": len([row for row in forms if row["classification"] == CLASSIFICATION_REPORTABLE]),
		"borderline_count": len([row for row in forms if row["classification"] == CLASSIFICATION_BORDERLINE]),
		"exempt_above_threshold": [_public_row(row) for row in exempt_above],
		"below_threshold": [
			{
				"recipient": row["recipient"],
				"total_payments": row["total_payments"],
				"classification": row["classification"],
			}
			for row in below
		],
		"total_box_1": _money(sum(row["total_payments"] for row in forms)),
		"related_party_recipients": [row["recipient"] for row in forms if row["related_party"]],
		"excluded": {
			"employee_party_postings": ledger["employee_count"],
			"employee_party_total": _money(ledger["employee_total"]),
			"opening_entries": ledger["opening_count"],
			"note": (
				"Employee-party postings are W-2 territory and are not on any 1099 produced "
				"here. They are counted rather than merely skipped so that 'nobody looked' and "
				"'somebody looked and excluded them' are different-looking answers."
			),
		},
		"basis": _basis_note(),
		"mcp_action_log_id": None,
	}

	if dry_run:
		data["dry_run"] = True
		data["would_produce"] = {
			"workbook": f"1099-NEC-{tax_year}-{_slug(company)}.xlsx",
			"forms": [f"1099-NEC-{tax_year}-{_slug(row['recipient'])}.pdf" for row, _pdf in form_documents],
			"governance_document": _archive_title(company, tax_year),
		}
		data["note"] = (
			"Nothing was written. The figures above are the ones the workbook would carry — "
			"check the borderline classifications before running it live."
		)
		return ToolResult(
			data,
			f"dry run: {len(forms)} 1099-NEC recipient(s) for {company} {tax_year}, "
			f"{data['total_box_1']:,.2f} total",
		)

	# Resolved BEFORE anything is created: a path outside the site's file storage
	# should refuse the whole run, not leave an archive entry behind and then
	# refuse. Fail safe means failing before the first write, not during.
	output_dir = artifacts.resolve_output_dir(as_str(args, "output_path"))

	archive = _archive(company, tax_year, end, len(forms), data["total_box_1"], args)
	workbook_name = f"1099-NEC-{tax_year}-{_slug(company)}.xlsx"
	attachment = artifacts.attach_bytes(GOVERNANCE_DOCUMENT, archive.name, workbook_name, workbook_bytes)
	written = []
	if output_dir:
		written.append(
			artifacts.write_output(os.path.join(output_dir, workbook_name), workbook_bytes, overwrite)
		)

	form_files = []
	for row, payload in form_documents:
		file_name = f"1099-NEC-{tax_year}-{_slug(row['recipient'])}.pdf"
		form_attachment = artifacts.attach_bytes(GOVERNANCE_DOCUMENT, archive.name, file_name, payload)
		form_files.append(
			{
				"recipient": row["recipient"],
				**artifacts.describe_attachment(form_attachment, payload),
			}
		)
		if output_dir:
			written.append(
				artifacts.write_output(os.path.join(output_dir, file_name), payload, overwrite)
			)

	data["governance_document"] = archive.name
	data["workbook"] = artifacts.describe_attachment(attachment, workbook_bytes)
	data["forms"] = form_files
	data["written_to_disk"] = written
	data["note"] = (
		"THIS IS A PRE-FILL. Every form reads XXX-XX-nnnn because this site holds four digits "
		"of a taxpayer id and never more — complete the number from the signed W-9 before "
		"filing. Copy A must be the official scannable form or an electronic filing; the Copy A "
		"page here is stamped as an information copy and cannot be submitted. Copies B and C "
		"print on plain paper and are the ones that go out."
	)
	data["next_step"] = (
		f"Read {len(forms)} form(s) and the workbook back with get_attachment_content, or find "
		f"them on Governance Document {archive.name}. Resolve every `borderline` classification "
		"against a W-9 before anything is mailed."
	)
	if data["borderline_count"]:
		data["warning"] = (
			f"{data['borderline_count']} recipient(s) are classified borderline and a form was "
			"produced for each. A borderline case is one the ledger cannot settle — an LLC that "
			"may or may not have elected corporate treatment, an incorporated law firm that is "
			"reportable anyway. Do not mail those without checking the W-9."
		)
	elif data["related_party_recipients"]:
		data["warning"] = (
			f"{len(data['related_party_recipients'])} recipient(s) are on the related-party "
			"register: "
			f"{', '.join(data['related_party_recipients'][:5])}. That is not an error — it is a "
			"disclosure. Related-party payments belong on the return's related-party schedule as "
			"well as on a 1099."
		)

	return ToolResult(
		data,
		f"1099-NEC pre-fill for {company} {tax_year}: {len(forms)} recipient(s), "
		f"{data['total_box_1']:,.2f} in Box 1, filed as {archive.name}",
		docstatus_delta="none → 0 (created)",
	)


# ── the ledger ──────────────────────────────────────────────────────────────
def _read_ledger(company: str, start: str, end: str) -> dict:
	"""Every GL Entry in the year that carries a party, plus what was excluded."""
	fields = compat.existing_fields(
		"GL Entry",
		(
			"name",
			"account",
			"posting_date",
			"debit",
			"credit",
			"party",
			"party_type",
			"cost_center",
			"voucher_type",
			"voucher_no",
			"is_opening",
			"is_cancelled",
		),
	)
	rows = frappe.db.get_all(
		"GL Entry",
		filters={
			"company": company,
			"posting_date": ("between", (start, end)),
			"is_cancelled": 0,
			"party": ("is", "set"),
		},
		fields=fields,
		order_by="posting_date asc",
		limit=LEDGER_CAP + 1,
	)
	if len(rows) > LEDGER_CAP:
		raise ToolError(
			f"more than {LEDGER_CAP} party postings for {company} in this year. A pre-fill that "
			f"silently used the first {LEDGER_CAP} would produce a wrong 1099, so it refuses "
			"instead. This is a volume this tool is not built for."
		)

	supplier_rows = []
	employee_count = 0
	employee_total = 0.0
	opening_count = 0
	for row in rows:
		if str(row.get("is_opening") or "").strip().lower() == "yes":
			opening_count += 1
			continue
		party_type = row.get("party_type")
		if party_type == "Employee":
			employee_count += 1
			employee_total += float(row.get("debit") or 0) - float(row.get("credit") or 0)
			continue
		if party_type != "Supplier":
			continue
		supplier_rows.append(dict(row))

	return {
		"rows": supplier_rows,
		"employee_count": employee_count,
		"employee_total": employee_total,
		"opening_count": opening_count,
	}


def _payable_accounts(accounts) -> set:
	"""Which of these accounts are Payable-typed, read off the site's own chart."""
	if not accounts:
		return set()
	rows = frappe.db.get_all(
		"Account",
		filters={"name": ("in", sorted(accounts))},
		fields=compat.existing_fields("Account", ("name", "account_type", "root_type")),
		limit=len(accounts) + 1,
	)
	return {row.get("name") for row in rows if row.get("account_type") == "Payable"}


def _aggregate(rows) -> dict:
	"""Sum payments per supplier under the debits/credits rule in the module docstring."""
	payable = _payable_accounts({row.get("account") for row in rows if row.get("account")})
	totals: dict = {}
	for row in rows:
		party = row.get("party")
		if not party:  # pragma: no cover - filtered upstream
			continue
		debit = float(row.get("debit") or 0)
		credit = float(row.get("credit") or 0)
		amount = debit if row.get("account") in payable else debit - credit

		figures = totals.setdefault(
			party,
			{
				"total": 0.0,
				"by_cost_center": {},
				"by_account": {},
				"by_voucher_type": {},
				"vouchers": set(),
				"first": None,
				"last": None,
			},
		)
		figures["total"] += amount
		cost_center = row.get("cost_center") or "unassigned"
		figures["by_cost_center"][cost_center] = round(
			figures["by_cost_center"].get(cost_center, 0.0) + amount, 2
		)
		account = figures["by_account"].setdefault(
			row.get("account"), {"debit": 0.0, "credit": 0.0, "counted": 0.0, "payable": row.get("account") in payable}
		)
		account["debit"] = round(account["debit"] + debit, 2)
		account["credit"] = round(account["credit"] + credit, 2)
		account["counted"] = round(account["counted"] + amount, 2)
		voucher_type = row.get("voucher_type") or "unknown"
		figures["by_voucher_type"][voucher_type] = round(
			figures["by_voucher_type"].get(voucher_type, 0.0) + amount, 2
		)
		if row.get("voucher_no"):
			figures["vouchers"].add(row["voucher_no"])
		posting_date = str(row.get("posting_date") or "")
		if posting_date:
			figures["first"] = min(figures["first"] or posting_date, posting_date)
			figures["last"] = max(figures["last"] or posting_date, posting_date)
	for figures in totals.values():
		figures["total"] = round(figures["total"], 2)
		figures["vouchers"] = sorted(figures["vouchers"])
	return totals


# ── classification ──────────────────────────────────────────────────────────
def _classify(company: str, party: str, figures: dict) -> dict:
	"""Decide reportable / borderline / exempt, and say why in one sentence."""
	supplier = _supplier(party)
	related = _related_party(company, party)
	name = str(supplier.get("supplier_name") or party)
	verdict, reason = _verdict(name, supplier, related)

	return {
		"recipient": name,
		"supplier": party,
		"supplier_type": supplier.get("supplier_type") or None,
		"supplier_group": supplier.get("supplier_group") or None,
		"supplier_has_tax_id": bool(supplier.get("tax_id")),
		"related_party": related.get("name") if related else None,
		"relationship": related.get("relationship_to_company") if related else None,
		"party_type": related.get("party_type") if related else None,
		"disclosable": bool(
			related and related.get("relationship_to_company") in DISCLOSABLE_RELATIONSHIPS
		),
		"tin_type": (related.get("tax_id_type") if related else None) or "None",
		"tin_last4": (related.get("tax_id_last4") if related else None) or "",
		"address": (related.get("address") if related else None) or "",
		"classification": verdict,
		"reason": reason,
		"total_payments": _money(figures["total"]),
		"by_cost_center": figures["by_cost_center"],
		"by_account": figures["by_account"],
		"by_voucher_type": figures["by_voucher_type"],
		"voucher_count": len(figures["vouchers"]),
		"first_payment": figures["first"],
		"last_payment": figures["last"],
		"possible_employee": _looks_like_an_employee(name),
	}


def _tokens(name: str) -> list[str]:
	"""Lowercase word tokens of a business name, punctuation discarded.

	`P.C.` becomes `pc` and `Friend & Reagan` becomes `friend`, `reagan`, which
	is what makes the attorney and corporation rules readable — and what stops
	them firing on a substring inside an unrelated word.
	"""
	return [token for token in re.split(r"[^a-z0-9]+", str(name).lower()) if token]


#: The attorney sentence, which is the same wherever it is reached.
_ATTORNEY_REASON = (
	"Payments to an attorney for services are reportable in Box 1 EVEN IF the firm is "
	"incorporated — the corporate exemption does not apply to legal services. Gross proceeds "
	"paid TO an attorney (a settlement) go on 1099-MISC Box 10 instead, so check which this was."
)


def _verdict(name: str, supplier: dict, related: dict) -> tuple[str, str]:
	"""The classification rules, most specific first. Never silently exempt.

	ORDER MATTERS, AND IT IS THIS. What the related-party register says about an
	entity beats what its name looks like — a registered Individual is reportable
	whether or not somebody is called Law. The one exception is the one the IRS
	makes: an attorney is reportable even when incorporated, so an entity
	*registered as a Corporation* whose name says law firm is borderline rather
	than exempt. A name-only attorney signal is borderline too, because nothing
	has said otherwise.
	"""
	tokens = _tokens(name)
	token_set = set(tokens)
	lowered = f" {str(name).lower()} "
	attorney = bool(token_set & _ATTORNEY_TOKENS)

	party_type = str((related or {}).get("party_type") or "")
	if party_type in ("Individual", "Partnership", "Family Member", "Trust"):
		return (
			CLASSIFICATION_REPORTABLE,
			f"Registered on the related-party register as {party_type}, which is reportable.",
		)
	if party_type == "Corporation":
		if attorney:
			return (
				CLASSIFICATION_BORDERLINE,
				f"Registered as a Corporation, but the name says law firm. {_ATTORNEY_REASON}",
			)
		return (
			CLASSIFICATION_EXEMPT,
			"Registered on the related-party register as a Corporation. Payments to corporations "
			"are generally exempt from 1099-NEC reporting.",
		)
	if party_type == "LLC":
		return (
			CLASSIFICATION_BORDERLINE,
			"Registered as an LLC. A single-member or partnership LLC is reportable; one that has "
			"elected corporate treatment is exempt. Only the W-9 says which.",
		)

	if attorney:
		return (CLASSIFICATION_BORDERLINE, f"Looks like a law firm. {_ATTORNEY_REASON}")

	supplier_type = str(supplier.get("supplier_type") or "").strip().lower()
	if supplier_type in _REPORTABLE_SUPPLIER_TYPES:
		return (
			CLASSIFICATION_REPORTABLE,
			f"Supplier type is {supplier.get('supplier_type')}, which is reportable.",
		)

	if (token_set & _GOVERNMENT_TOKENS) or any(phrase in lowered for phrase in _GOVERNMENT_PHRASES):
		return (
			CLASSIFICATION_BORDERLINE,
			"The name suggests a government body or public agency, whose payments are not "
			"reportable — but a name is a hint, not a determination, so this is flagged rather "
			"than dropped.",
		)

	if tokens and tokens[-1] in _CORPORATION_ENDINGS:
		return (
			CLASSIFICATION_EXEMPT,
			"The name ends in a corporate suffix, so payments are generally exempt. Override this "
			"by registering the vendor on the related-party register with its real party type.",
		)
	if "llc" in token_set:
		return (
			CLASSIFICATION_BORDERLINE,
			"The name says LLC. A single-member or partnership LLC is reportable; one taxed as a "
			"corporation is exempt. Register it as a Related Party once the W-9 settles it.",
		)
	if supplier_type in _EXEMPT_SUPPLIER_TYPES:
		return (
			CLASSIFICATION_BORDERLINE,
			f"Supplier type is {supplier.get('supplier_type')}, which usually means a company and "
			"therefore exempt — but ERPNext's 'Company' covers every entity that is not a natural "
			"person, including the LLCs and partnerships that ARE reportable. Confirm from the W-9.",
		)

	return (
		CLASSIFICATION_BORDERLINE,
		"Nothing on this site says what kind of entity this is: the Supplier has no type and there "
		"is no related-party entry. Register it, or read the W-9.",
	)


def _supplier(party: str) -> dict:
	if not compat.doctype_exists("Supplier") or not frappe.db.exists("Supplier", party):
		return {}
	fields = compat.existing_fields(
		"Supplier",
		("name", "supplier_name", "supplier_type", "supplier_group", "tax_id", "disabled", "country"),
	)
	return dict(frappe.db.get_value("Supplier", party, fields, as_dict=True) or {})


def _related_party(company: str, supplier: str) -> dict:
	if not compat.doctype_exists(RELATED_PARTY):
		return {}
	fields = compat.existing_fields(
		RELATED_PARTY,
		(
			"name",
			"party_name",
			"party_type",
			"relationship_to_company",
			"tax_id_type",
			"tax_id_last4",
			"address",
			"end_date",
		),
	)
	rows = frappe.db.get_all(
		RELATED_PARTY,
		filters={"supplier": supplier, "company": company},
		fields=fields,
		order_by="effective_date desc",
		limit=5,
	)
	return dict(rows[0]) if rows else {}


def _looks_like_an_employee(name: str) -> bool:
	"""Is somebody with this name also on the payroll?

	Not a reason to drop the payment — a person can be an employee and separately
	a landlord — but it is exactly the confusion that puts wages on a 1099, so it
	is surfaced on the row.
	"""
	if not compat.doctype_exists("Employee"):
		return False
	return bool(frappe.db.get_value("Employee", {"employee_name": name}, "name"))


# ── the workbook ────────────────────────────────────────────────────────────
def _workbook(company, tax_year, threshold, payer, forms, below, exempt_above, ledger) -> XlsxWorkbook:
	workbook = XlsxWorkbook(title=f"1099-NEC pre-fill {tax_year} — {company}")

	workbook.add(
		Sheet(
			title="1099-NEC",
			headers=[
				"Recipient Name",
				"TIN Type",
				"TIN Last 4",
				"Address",
				"Box 1 Nonemployee Compensation",
				"Classification",
				"Reason",
				"Related Party",
				"Relationship",
				"Supplier",
				"Vouchers",
				"First Payment",
				"Last Payment",
			],
			rows=[
				[
					row["recipient"],
					row["tin_type"],
					(row["tin_last4"], STYLE_TEXT),
					row["address"],
					(row["total_payments"], STYLE_MONEY),
					row["classification"],
					row["reason"],
					row["related_party"] or "",
					row["relationship"] or "",
					row["supplier"],
					row["voucher_count"],
					row["first_payment"] or "",
					row["last_payment"] or "",
				]
				for row in forms
			],
		)
	)

	cost_center_rows = []
	for row in forms:
		for cost_center, amount in sorted(row["by_cost_center"].items()):
			cost_center_rows.append([row["recipient"], cost_center, (amount, STYLE_MONEY)])
	workbook.add(
		Sheet(
			title="Cost Centers",
			headers=["Recipient", "Cost Center", "Amount"],
			rows=cost_center_rows,
		)
	)

	excluded_rows = [
		[
			row["recipient"],
			(row["total_payments"], STYLE_MONEY),
			"below threshold",
			f"under {threshold:,.2f}",
		]
		for row in below
	] + [
		[row["recipient"], (row["total_payments"], STYLE_MONEY), "exempt", row["reason"]]
		for row in exempt_above
	]
	if ledger["employee_count"]:
		excluded_rows.append(
			[
				f"({ledger['employee_count']} employee-party postings)",
				(_money(ledger["employee_total"]), STYLE_MONEY),
				"W-2 territory",
				"Employee payments are wages and are reported on a W-2, never a 1099-NEC.",
			]
		)
	if ledger["opening_count"]:
		excluded_rows.append(
			[
				f"({ledger['opening_count']} opening entries)",
				("", STYLE_TEXT),
				"not a payment",
				"An opening balance is a starting position, not money that moved this year.",
			]
		)
	workbook.add(
		Sheet(title="Excluded", headers=["Name", "Amount", "Why", "Detail"], rows=excluded_rows)
	)

	workbook.add(
		Sheet(
			title="Provenance",
			headers=["Field", "Value"],
			rows=[
				["Payer", company],
				["Payer TIN", (payer["tin_display"], STYLE_TEXT)],
				["Tax year", (str(tax_year), STYLE_TEXT)],
				["Threshold", (threshold, STYLE_MONEY)],
				["Source", "GL Entry (submitted vouchers only; cancelled entries carry none)"],
				["Generator", f"erpnext_mcp {__version__}"],
				["Generated at", (str(frappe.utils.now()), STYLE_TEXT)],
				["Generated by", (str(frappe.session.user), STYLE_TEXT)],
				["Site", (str(frappe.local.site), STYLE_TEXT)],
				["Basis", _basis_note()],
				[
					"Completeness",
					"Recipient TINs are the last four digits only. Complete them from the signed "
					"W-9 before filing.",
				],
			],
			widths=[26, 90],
		)
	)
	return workbook


def _basis_note() -> str:
	return (
		"Payments are summed from GL Entry rows carrying a Supplier party: debits only on "
		"Payable-type accounts (a debit to payables is a bill being paid; a credit is one being "
		"raised), debits minus credits everywhere else (the party sits on the expense line, so a "
		"credit is a refund). Cancelled vouchers produce no GL Entry. Opening entries and "
		"Employee-party postings are excluded and counted."
	)


# ── the forms ───────────────────────────────────────────────────────────────
#: The three copies of a paper 1099-NEC, and who each one is for.
_COPIES = (
	(
		"Copy A",
		"For Internal Revenue Service Center",
		"INFORMATION COPY - NOT FOR IRS SUBMISSION. Copy A must be the official scannable "
		"red-ink form, or an electronic filing. Printing this page and mailing it is not a "
		"filing and may incur a penalty.",
	),
	("Copy B", "For Recipient", "Print on plain paper and furnish to the recipient."),
	("Copy C", "For Payer", "Print on plain paper and retain with the payer's records."),
)


def _form_pdf(payer: dict, row: dict, tax_year: int) -> bytes:
	"""One recipient's 1099-NEC, three copies, one page each."""
	document = PdfDocument(
		title=f"Form 1099-NEC {tax_year} — {row['recipient']}",
		author="erpnext_mcp",
		subject=f"Nonemployee compensation {tax_year}",
		footer=f"PRE-FILL — complete the recipient TIN from the signed W-9 before filing. {tax_year}",
	)
	for index, (copy_name, who, caveat) in enumerate(_COPIES):
		if index:
			document.page_break()
		document.title_block(
			f"FORM 1099-NEC ({tax_year})",
			"Nonemployee Compensation",
			f"{copy_name} - {who}",
		)
		document.paragraph(caveat)
		document.spacer(6)

		document.heading("Payer")
		document.key_values(
			[
				("PAYER'S name", payer["name"]),
				("PAYER'S address", payer["address"] or "(not recorded on this site)"),
				("PAYER'S TIN", payer["tin_display"]),
			]
		)

		document.heading("Recipient")
		document.key_values(
			[
				("RECIPIENT'S name", row["recipient"]),
				("RECIPIENT'S address", row["address"] or "(not recorded on this site)"),
				("RECIPIENT'S TIN", _masked_tin(row)),
			]
		)

		document.heading("Amounts")
		document.key_values(
			[
				("Box 1  Nonemployee compensation", f"{row['total_payments']:,.2f}"),
				("Box 4  Federal income tax withheld", "0.00"),
				("Box 5  State tax withheld", "0.00"),
				("Box 6  State/Payer's state no.", ""),
				("Box 7  State income", ""),
			]
		)

		first = row["first_payment"] or "the start of the year"
		last = row["last_payment"] or "the end of it"
		document.heading("How this figure was reached")
		document.paragraph(
			f"{row['voucher_count']} voucher(s) between {first} and {last}, summed from the "
			"general ledger."
		)
		document.table(
			["Cost centre", "Amount"],
			[[cost_center, f"{amount:,.2f}"] for cost_center, amount in sorted(row["by_cost_center"].items())],
			align=("l", "r"),
		)
		document.spacer(4)
		document.paragraph(f"Classification: {row['classification']}. {row['reason']}")
		if row["possible_employee"]:
			document.paragraph(
				"NOTE: somebody with this name is also an Employee on this site. Wages belong on a "
				"W-2 — confirm this payment was for something else before issuing a 1099."
			)
		if row["disclosable"]:
			document.paragraph(
				f"NOTE: this recipient is on the related-party register as {row['relationship']}. "
				"The payment is reportable and is ALSO a related-party transaction to disclose."
			)
	return document.render()


def _masked_tin(row: dict) -> str:
	if not row["tin_last4"]:
		return "__________  (no taxpayer id on file - obtain a signed W-9)"
	shape = "XXX-XX-" if row["tin_type"] == "SSN" else "XX-XXX"
	return f"{shape}{row['tin_last4']}   (last four only - complete from the W-9)"


def _payer(company: str, args: dict) -> dict:
	"""The payer block. The full TIN reaches the form and never the tool result."""
	fields = compat.existing_fields("Company", ("name", "abbr", "tax_id", "country"))
	row = dict(frappe.db.get_value("Company", company, fields, as_dict=True) or {})
	tax_id = str(row.get("tax_id") or "").strip()
	address = as_str(args, "payer_address")
	return {
		"name": company,
		"address": address,
		"tin_display": tax_id or "__________  (set the company's Tax ID)",
		"public": {
			"name": company,
			"address": address or None,
			"tin_on_file": bool(tax_id),
			"note": (
				"The payer's own TIN is printed on the forms and is deliberately not returned "
				"here. This app does not hand taxpayer identification numbers back through a "
				"tool result."
			),
		},
	}


def _public_row(row: dict) -> dict:
	"""A recipient row as it appears in the tool result — no more than the form needs."""
	return {
		"recipient": row["recipient"],
		"supplier": row["supplier"],
		"total_payments": row["total_payments"],
		"classification": row["classification"],
		"reason": row["reason"],
		"tin_type": row["tin_type"],
		"tin_last4": row["tin_last4"] or None,
		"address_on_file": bool(row["address"]),
		"related_party": row["related_party"],
		"relationship": row["relationship"],
		"disclosable": row["disclosable"],
		"possible_employee": row["possible_employee"],
		"by_cost_center": row["by_cost_center"],
		"by_account": row["by_account"],
		"voucher_count": row["voucher_count"],
		"first_payment": row["first_payment"],
		"last_payment": row["last_payment"],
	}


def _archive_title(company: str, tax_year: int) -> str:
	return f"Form 1099-NEC Pre-Fill {tax_year} — {company}"


def _archive(company: str, tax_year: int, year_end: str, count: int, total: float, args: dict):
	"""File the run in the governance archive, so the workbook has a home and a date."""
	compat.require_doctype(GOVERNANCE_DOCUMENT, "It ships with erpnext_mcp.")
	title = as_str(args, "title") or _archive_title(company, tax_year)
	existing = frappe.db.get_value(GOVERNANCE_DOCUMENT, {"company": company, "title": title}, "name")
	if existing:
		raise ToolError(
			f"{company} already has a governance document titled {title!r} ({existing}) — a "
			f"1099 pre-fill for {tax_year} has been produced before. Producing a second under the "
			"same title would leave two documents claiming to be the run of record. Pass a "
			"`title` that says which run this is, or read the existing one back with "
			"get_governance_document_content. Nothing was created."
		)
	doc = frappe.new_doc(GOVERNANCE_DOCUMENT)
	doc.title = title
	doc.category = "Tax Filing"
	doc.company = company
	doc.effective_date = year_end
	doc.parties = (
		f"{count} recipient(s) at or above the reporting threshold, {total:,.2f} in total Box 1 "
		"nonemployee compensation."
	)
	doc.notes = _basis_note()
	doc.insert()
	return doc


def _slug(text: str) -> str:
	"""A filename-safe fragment. Kept ASCII so a printed batch sorts predictably."""
	out = []
	for char in str(text):
		out.append(char if (char.isalnum() or char in "-_") else "-")
	slug = "".join(out).strip("-")
	while "--" in slug:
		slug = slug.replace("--", "-")
	return slug[:60] or "recipient"
