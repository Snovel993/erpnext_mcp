# SPDX-License-Identifier: MIT
"""Investment advisory agreements: what is being charged, and against what.

v0.73.0, the Bank Bridge consolidation. An advisory fee is the one recurring
cost on a farm's books that arrives ALREADY DEDUCTED. Nobody approves it, no
invoice precedes it, and it appears on a brokerage statement as a line that
looks exactly like every other line. The only thing in the world that says
whether it is the right number is the agreement it was charged under.

So the agreement is a record here, and the fee is arithmetic:
`get_advisory_agreement_summary` computes what the terms say a period costs on
the assets actually under management, and reports it beside what was actually
charged. Where those two differ, one of them is wrong, and until both numbers
exist in one place nobody can even ask.

WHERE THE AUM COMES FROM, AND WHY IT IS REPORTED WITH ITS SOURCE. Assets under
management is the portfolio value, which this app knows only from the anchors a
statement parser has pushed — `portfolio_closing_value` on the most recent
period. A caller may pass `assets_under_management` directly, and where they do
the computed fee is reported against THAT and says so. What this module refuses
to do is guess: an agreement whose account has no portfolio value on file gets a
summary with a null fee and a sentence saying why, rather than a fee computed
against a bank balance that is not the portfolio.

AMENDMENT IS VERSIONING, NOT EDITING. `update_advisory_agreement` creates a NEW
agreement pointing back at the old one and marks the old one Superseded. That is
not ceremony: last quarter's fee was charged under last quarter's terms, and an
in-place edit would leave the site unable to justify a charge it has already
taken. The controller enforces one Active agreement per account, which is what
makes "the terms in force" a single well-defined thing.

NOTHING HERE POSTS. Not one function writes a Journal Entry, a Payment Entry or
a GL row. A computed fee is a comparison, not a charge — booking one is
`create_journal_entry`, with its own switch and its own review.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, today

from .. import compat
from ..args import as_bool, as_date, as_float, as_int, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import anchors

AGREEMENT = "Advisory Agreement"
BANK_ACCOUNT = "Bank Account"
GOVERNANCE_DOCUMENT = "Governance Document"

STATUSES = ("Active", "Terminated", "Superseded")
FEE_TYPES = ("Percent of AUM", "Flat Annual", "Hybrid")
BILLING_FREQUENCIES = ("Monthly", "Quarterly", "Annually")
OBJECTIVES = ("Growth", "Income", "Balanced", "Preservation")

PERIODS_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "Annually": 1}

MAX_SCAN = 2000

_AGREEMENT_HINT = "It ships with erpnext_mcp — run `bench migrate` after installing v0.73.0."

_AGREEMENT_FIELDS = (
	"name",
	"agreement_name",
	"company",
	"bank_account",
	"status",
	"client_entity",
	"advisor_entity",
	"document_reference",
	"objective",
	"investment_horizon_years",
	"fee_type",
	"fee_percent_of_aum",
	"fee_flat_annual",
	"billing_frequency",
	"effective_date",
	"termination_date",
	"amended_from",
	"amendment_reason",
)

#: The fields an amendment carries forward unless the caller changes them. The
#: list is explicit rather than "everything except a denylist" because the two
#: things it must NOT carry — `amended_from` and `status` — are exactly the two
#: a copy-everything approach would carry by accident, producing an amendment
#: that points at its own predecessor's predecessor and is Superseded on arrival.
_AMENDABLE = (
	"agreement_name",
	"bank_account",
	"client_entity",
	"advisor_entity",
	"document_reference",
	"objective",
	"investment_horizon_years",
	"fee_type",
	"fee_percent_of_aum",
	"fee_flat_annual",
	"billing_frequency",
	"effective_date",
	"termination_date",
)


def _require_agreements() -> None:
	compat.require_doctype(AGREEMENT, _AGREEMENT_HINT)


def _agreement_row(name: str) -> dict:
	fields = compat.existing_fields(AGREEMENT, list(_AGREEMENT_FIELDS))
	row = frappe.db.get_value(AGREEMENT, name, fields, as_dict=True)
	return dict(row) if row else {}


def _agreement_out(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"agreement_name": row.get("agreement_name"),
		"company": row.get("company"),
		"bank_account": row.get("bank_account") or None,
		"status": row.get("status"),
		"client_entity": row.get("client_entity") or None,
		"advisor_entity": row.get("advisor_entity") or None,
		"document_reference": row.get("document_reference") or None,
		"objective": row.get("objective") or None,
		"investment_horizon_years": int(row.get("investment_horizon_years") or 0) or None,
		"fee_type": row.get("fee_type"),
		"fee_percent_of_aum": _optional_number(row.get("fee_percent_of_aum")),
		"fee_flat_annual": _optional_number(row.get("fee_flat_annual")),
		"billing_frequency": row.get("billing_frequency"),
		"effective_date": _date_text(row.get("effective_date")),
		"termination_date": _date_text(row.get("termination_date")),
		"amended_from": row.get("amended_from") or None,
		"amendment_reason": row.get("amendment_reason") or None,
	}


def _optional_number(value):
	if value in (None, ""):
		return None
	return round(float(value), 4)


def _date_text(value) -> str | None:
	if not value:
		return None
	if hasattr(value, "isoformat"):
		return value.isoformat()[:10]
	return str(value)[:10]


def _choice(value: str, options: tuple, label: str, default: str = "") -> str:
	"""One Select value, matched case-insensitively, refused by name when wrong."""
	wanted = str(value or "").strip()
	if not wanted:
		return default
	for option in options:
		if wanted.lower() == option.lower():
			return option
	raise ToolError(f"{label} must be one of: {', '.join(options)} — got {wanted!r}. Nothing was written.")


def _resolve_agreement(args: dict, key: str = "agreement") -> dict:
	"""One agreement, by docname or by its human name within a company."""
	named = as_str(args, key) or as_str(args, "name") or as_str(args, "agreement_name")
	if not named:
		raise ToolError(f"{key} is required — the docname, or the agreement name within a company.")
	row = _agreement_row(named)
	if row:
		return row
	filters = {"agreement_name": named}
	company = resolve_company(as_str(args, "company"))
	if company:
		filters["company"] = company
	hits = frappe.db.get_all(
		AGREEMENT, filters=filters, fields=["name", "status"], order_by="creation desc", limit=20
	)
	if not hits:
		raise ToolError(f"no Advisory Agreement named {named!r} on this site.")
	# AN AMENDMENT KEEPS THE NAME, so a versioned agreement matches its own
	# history — and the one anybody means by the name is the one in force. Only a
	# genuine ambiguity, two LIVE agreements sharing a name, is refused.
	live = [row for row in hits if str(row.get("status") or "") != "Superseded"]
	candidates = live or hits
	if len(candidates) > 1:
		raise ToolError(
			f"{len(candidates)} agreements are called {named!r}: "
			f"{', '.join(row['name'] for row in candidates)}. Name one of them, or pass company."
		)
	return _agreement_row(candidates[0]["name"])


# ── 1. create_advisory_agreement ─────────────────────────────────────────────


def create_advisory_agreement(args: dict) -> ToolResult:
	"""Register the terms an investment account is managed under. MUTATING.

	EVERY REFUSAL HERE IS A NUMBER THAT WOULD OTHERWISE COMPUTE WRONG RATHER THAN
	FAIL. A `Percent of AUM` agreement with no percentage computes a fee of zero,
	which looks exactly like an account being managed for free; a `Hybrid` missing
	its flat half computes the percentage alone and looks entirely reasonable. The
	controller refuses both, and refuses a second Active agreement on one account
	— because two live sets of terms is two answers to what the fee is, and
	nothing to say which was in force when a charge came through.

	It posts nothing and charges nothing. What it produces is the record a charge
	can be checked against.
	"""
	_require_agreements()
	company = resolve_company(as_str(args, "company"), required=True)
	agreement_name = as_str(args, "agreement_name", required=True)

	bank_account = as_str(args, "bank_account")
	if bank_account:
		if not frappe.db.exists(BANK_ACCOUNT, bank_account):
			resolved = anchors.accounts_by_mask(bank_account, company)
			if len(resolved) != 1:
				raise ToolError(
					f"no Bank Account named {bank_account!r} in {company}. get_account_pairing lists "
					"the accounts this site has, with their masks. Nothing was created."
				)
			bank_account = resolved[0]

	document = as_str(args, "document_reference")
	if (
		document
		and compat.doctype_exists(GOVERNANCE_DOCUMENT)
		and not frappe.db.exists(GOVERNANCE_DOCUMENT, document)
	):
		raise ToolError(
			f"no Governance Document named {document!r}. list_governance_documents has the register. "
			"Nothing was created."
		)

	doc = frappe.new_doc(AGREEMENT)
	doc.agreement_name = agreement_name
	doc.company = company
	if bank_account:
		doc.bank_account = bank_account
	doc.status = _choice(as_str(args, "status"), STATUSES, "status", "Active")
	doc.client_entity = as_str(args, "client_entity")
	doc.advisor_entity = as_str(args, "advisor_entity")
	if document:
		doc.document_reference = document
	doc.objective = _choice(as_str(args, "objective"), OBJECTIVES, "objective", "")
	horizon = as_int(args, "investment_horizon_years")
	if horizon is not None:
		doc.investment_horizon_years = horizon
	doc.fee_type = _choice(as_str(args, "fee_type"), FEE_TYPES, "fee_type", "Percent of AUM")
	for key in ("fee_percent_of_aum", "fee_flat_annual"):
		if args.get(key) not in (None, ""):
			doc.set(key, as_float(args[key], key))
	doc.billing_frequency = _choice(
		as_str(args, "billing_frequency"), BILLING_FREQUENCIES, "billing_frequency", "Quarterly"
	)
	doc.effective_date = as_date(args, "effective_date", required=True)
	termination = as_date(args, "termination_date")
	if termination:
		doc.termination_date = termination
	doc.insert()

	row = _agreement_row(doc.name)
	data = {
		"name": doc.name,
		"agreement": _agreement_out(row),
		"annual_fee_at_1m": _annual_fee(row, 1_000_000.0),
		"note": (
			"Registering terms charges nothing and posts nothing. What it makes possible is the "
			"comparison: get_advisory_agreement_summary computes what these terms say a period costs "
			"on the assets actually under management, which is the only way a fee that arrives "
			"already deducted can be checked."
		),
	}
	if not bank_account:
		data["account_note"] = (
			"No Bank Account is linked, so no assets under management can be read and no fee can be "
			"computed from this agreement. Link one when the account is open."
		)

	return ToolResult(
		data,
		f"created advisory agreement {doc.name} ({agreement_name}) for {company}"
		+ (f" on {bank_account}" if bank_account else ""),
		docstatus_delta="none → 0 (created)",
	)


# ── 2. get_advisory_agreement_summary ────────────────────────────────────────


def get_advisory_agreement_summary(args: dict) -> ToolResult:
	"""The terms, the assets they apply to, and what the fee should be. Read-only.

	THE COMPUTED FEE ALWAYS CARRIES ITS BASIS. `aum_source` says whether the
	assets under management came from a caller's own figure, from the most recent
	anchored portfolio value, or from nowhere at all — and in the last case the
	fee is null rather than zero. A fee of zero and a fee nobody can compute are
	opposite findings, and reporting the second as the first would say an account
	is managed for free.

	The bank BALANCE is deliberately not used as a fallback for the portfolio
	value. A managed account's cash balance is a fraction of what is under
	management, so a fee computed against it would be plausibly small and wrong in
	the direction nobody checks.
	"""
	_require_agreements()
	row = _resolve_agreement(args)
	agreement = _agreement_out(row)

	supplied = args.get("assets_under_management")
	aum = None
	aum_source = "not available"
	aum_as_of = None
	if supplied not in (None, ""):
		aum = round(as_float(supplied, "assets_under_management"), 2)
		aum_source = "supplied by the caller"
	elif agreement["bank_account"]:
		portfolio = _latest_portfolio_value(agreement["bank_account"])
		if portfolio:
			aum = portfolio["value"]
			aum_as_of = portfolio["period_end"]
			aum_source = f"portfolio_closing_value on Statement Anchor {portfolio['anchor']}"

	annual = _annual_fee(row, aum) if aum is not None else None
	periods = PERIODS_PER_YEAR.get(agreement["billing_frequency"] or "Quarterly", 4)
	periodic = round(annual / periods, 2) if annual is not None else None

	chain = _account_chain_summary(agreement["bank_account"]) if agreement["bank_account"] else {}
	history = _amendment_history(row)

	data = {
		"agreement": agreement,
		"in_force": _in_force(row),
		"assets_under_management": aum,
		"aum_source": aum_source,
		"aum_as_of": aum_as_of,
		"computed_annual_fee": annual,
		"computed_fee_per_billing_period": periodic,
		"billing_periods_per_year": periods,
		"account": chain,
		"amendment_history": history,
		"amendment_count": max(len(history) - 1, 0),
		"note": (
			"The computed fee is what THESE TERMS say a period costs on the assets named in "
			"`aum_source`. It is not a charge, it is not booked, and it is not what the advisor "
			"actually deducted — comparing it to that is the point, and the deduction itself shows "
			"up as a period variance on the account's anchor chain."
		),
	}
	if aum is None:
		data["warning"] = (
			"No assets under management could be established, so no fee was computed. Pass "
			"assets_under_management, or push a statement anchor carrying portfolio_closing_value "
			"for this account. A bank balance is deliberately NOT used as a substitute: it is a "
			"fraction of the portfolio, so a fee computed against it would be wrong and plausible."
		)
	elif chain.get("unexplained_periods"):
		data["next_step"] = (
			f"{chain['unexplained_periods']} anchored period(s) on {agreement['bank_account']} are out "
			"of tolerance with no explanation. On a managed account the usual cause is exactly this "
			f"fee — compare {periodic} against the variances with get_statement_anchor_chain."
		)

	return ToolResult(
		data,
		f"{agreement['name']} ({agreement['agreement_name']}, {agreement['status']}): "
		+ (
			f"{periodic} per {agreement['billing_frequency'].lower()} period"
			if periodic is not None
			else "fee not computable"
		),
	)


def _in_force(row: dict) -> bool:
	"""Whether these terms are the ones in force today.

	Status AND dates, because they fail apart: an agreement whose termination date
	has passed but which nobody marked Terminated is still Active in the database
	and is not in force in the world.
	"""
	if str(row.get("status") or "") != "Active":
		return False
	stamp = getdate(today())
	if row.get("effective_date") and getdate(row["effective_date"]) > stamp:
		return False
	if row.get("termination_date") and getdate(row["termination_date"]) < stamp:
		return False
	return True


def _annual_fee(row: dict, aum) -> float | None:
	"""What a year of these terms costs on `aum`. One formula, in the controller.

	Delegated rather than reimplemented: `AdvisoryAgreement.annual_fee_on` is what
	the Desk would use, and a second copy here would be a second answer to what a
	client owes.
	"""
	if aum is None:
		return None
	payload = {key: value for key, value in dict(row).items() if key not in ("doctype", "name")}
	payload["doctype"] = AGREEMENT
	return frappe.get_doc(payload).annual_fee_on(flt(aum))


def _latest_portfolio_value(bank_account: str):
	"""The most recent anchored portfolio value on the account, or None.

	Reads the CLOSING value of the latest period that has one, which is not
	necessarily the latest period — a parser that stopped supplying portfolio
	values would otherwise make an account with ten years of history look like an
	account with none.
	"""
	if not compat.doctype_exists(anchors.ANCHOR):
		return None
	rows = frappe.db.get_all(
		anchors.ANCHOR,
		filters={"bank_account": bank_account},
		fields=["name", "period_end", "portfolio_closing_value"],
		order_by="period_end desc",
		limit=MAX_SCAN,
	)
	for row in rows:
		if row.get("portfolio_closing_value") in (None, ""):
			continue
		return {
			"anchor": row["name"],
			"period_end": _date_text(row.get("period_end")),
			"value": round(float(row["portfolio_closing_value"]), 2),
		}
	return None


def _account_chain_summary(bank_account: str) -> dict:
	"""Enough of the account's anchor chain to say whether the fee is showing up."""
	if not compat.doctype_exists(anchors.ANCHOR):
		return {"bank_account": bank_account, "anchored_periods": 0}
	rows = frappe.db.get_all(
		anchors.ANCHOR,
		filters={"bank_account": bank_account},
		fields=["name", "period_start", "period_end", "variance", "variance_reason", "reconciled"],
		order_by="period_start asc",
		limit=MAX_SCAN,
	)
	unexplained = [
		row for row in rows if not int(row.get("reconciled") or 0) and not row.get("variance_reason")
	]
	return {
		"bank_account": bank_account,
		"anchored_periods": len(rows),
		"first_period_start": _date_text(rows[0].get("period_start")) if rows else None,
		"last_period_end": _date_text(rows[-1].get("period_end")) if rows else None,
		"cumulative_variance": round(sum(float(row.get("variance") or 0) for row in rows), 2),
		"unreconciled_periods": sum(1 for row in rows if not int(row.get("reconciled") or 0)),
		"unexplained_periods": len(unexplained),
	}


def _amendment_history(row: dict) -> list:
	"""This agreement and every version behind it, oldest first.

	Walks `amended_from` with a visited set, because a chain edited by hand in the
	Desk can be made to point at itself, and a version history that hangs is worse
	than one that stops.
	"""
	chain, seen = [], set()
	current = dict(row)
	while current and current.get("name") and current["name"] not in seen:
		seen.add(current["name"])
		chain.append(
			{
				"name": current.get("name"),
				"status": current.get("status"),
				"effective_date": _date_text(current.get("effective_date")),
				"fee_type": current.get("fee_type"),
				"fee_percent_of_aum": _optional_number(current.get("fee_percent_of_aum")),
				"fee_flat_annual": _optional_number(current.get("fee_flat_annual")),
				"amendment_reason": current.get("amendment_reason") or None,
			}
		)
		parent = current.get("amended_from")
		current = _agreement_row(parent) if parent else {}
	chain.reverse()
	return chain


# ── 3. update_advisory_agreement ─────────────────────────────────────────────


def update_advisory_agreement(args: dict) -> ToolResult:
	"""Amend the terms: a NEW agreement, the old one Superseded. MUTATING.

	NOT AN EDIT, AND THAT IS THE WHOLE DESIGN. Last quarter's fee was charged
	under last quarter's terms. An in-place change would leave the site holding a
	charge it cannot justify and a record that says something else, which is the
	exact failure a fee-disclosure question turns on.

	So: the prior agreement becomes Superseded and keeps every number it had, the
	new one carries everything not being changed, `amended_from` links them, and
	`amendment_reason` records why. `get_advisory_agreement_summary` walks the
	chain and returns the whole history.

	`in_place=true` exists for the one honest case — fixing a TYPO in a field that
	is not a term: the client's spelling, a governance document link, an objective
	nobody had recorded. It refuses to touch a fee, a date or a status, because
	those are the terms and changing them is what amendment is for.
	"""
	_require_agreements()
	row = _resolve_agreement(args)
	before = _agreement_out(row)
	if before["status"] == "Superseded":
		raise ToolError(
			f"{before['name']} has already been superseded. Amend the agreement that replaced it — "
			"get_advisory_agreement_summary returns the whole chain. Nothing was written."
		)

	if bool(as_bool(args, "terminate", False)):
		return _terminate(row, before, args)

	in_place = bool(as_bool(args, "in_place", False))
	changes = _requested_changes(args)
	if not changes and not in_place:
		raise ToolError(
			"nothing to change. Pass at least one of: " + ", ".join(_AMENDABLE) + ". Nothing was written."
		)

	if in_place:
		return _amend_in_place(row, before, changes, args)

	reason = as_str(args, "amendment_reason") or as_str(args, "reason")
	if not reason:
		raise ToolError(
			"amendment_reason is required. The new record shows WHAT changed and says nothing about "
			"why, and a fee disclosure question is always about the why. Nothing was written."
		)

	payload = {
		fieldname: row.get(fieldname) for fieldname in _AMENDABLE if row.get(fieldname) not in (None, "")
	}
	payload.update(changes)
	payload["doctype"] = AGREEMENT
	payload["status"] = "Active"
	payload["amended_from"] = before["name"]
	payload["amendment_reason"] = reason
	payload["company"] = row.get("company")

	# The prior agreement is superseded BEFORE the new one is inserted, because
	# the controller refuses a second Active agreement on one account — and it is
	# right to. The order is the whole reason this is a tool and not two calls.
	frappe.db.set_value(AGREEMENT, before["name"], "status", "Superseded")
	try:
		doc = frappe.get_doc(payload)
		doc.insert()
	except Exception:
		frappe.db.set_value(AGREEMENT, before["name"], "status", before["status"])
		raise

	after = _agreement_out(_agreement_row(doc.name))
	data = {
		"name": doc.name,
		"agreement": after,
		"superseded": before["name"],
		"previous": before,
		"changed": {
			key: {"was": before.get(key), "now": after.get(key)}
			for key in _AMENDABLE
			if before.get(key) != after.get(key)
		},
		"amendment_reason": reason,
		"note": (
			"The prior agreement is Superseded, not deleted, and keeps every number it had. That is "
			"what makes a charge taken under the old terms still justifiable — an in-place edit "
			"would leave the site unable to explain money it has already paid."
		),
	}
	return ToolResult(
		data,
		f"amended {before['name']} → {doc.name} ({after['agreement_name']}): {reason}",
		docstatus_delta="none → 0 (new version created; prior marked Superseded)",
	)


def _terminate(row: dict, before: dict, args: dict) -> ToolResult:
	"""End an agreement. In place, because ending terms does not change them.

	No new version: the terms that were in force are still the terms that were in
	force, and the only new fact is the date they stopped. That date is REQUIRED
	rather than defaulted to today, because an agreement that ended in March and
	is being recorded in August would otherwise silently claim five months of
	coverage it did not have.
	"""
	if before["status"] == "Terminated":
		raise ToolError(
			f"{before['name']} was already terminated on {before['termination_date']}. Nothing was written."
		)
	termination = as_date(args, "termination_date")
	if not termination:
		raise ToolError(
			"termination_date is required to terminate. Defaulting it to today would claim coverage "
			"for every month between the real end and the day somebody got round to recording it. "
			"Nothing was written."
		)
	doc = frappe.get_doc(AGREEMENT, before["name"])
	doc.status = "Terminated"
	doc.termination_date = termination
	reason = as_str(args, "amendment_reason") or as_str(args, "reason")
	if reason:
		doc.amendment_reason = reason
	doc.save()

	after = _agreement_out(_agreement_row(before["name"]))
	data = {
		"name": before["name"],
		"agreement": after,
		"previous_status": before["status"],
		"termination_date": after["termination_date"],
		"note": (
			"Terminated in place and no version was created: the terms did not change, they stopped. "
			"The record stays readable because it is what justifies every charge taken while it ran."
		),
	}
	return ToolResult(
		data,
		f"terminated {before['name']} ({after['agreement_name']}) effective {after['termination_date']}",
		docstatus_delta="none (status and termination date on an existing agreement)",
	)


def _requested_changes(args: dict) -> dict:
	"""The amendable fields the caller actually named, validated."""
	changes = {}
	for key in _AMENDABLE:
		if key not in args or args[key] in (None, ""):
			continue
		value = args[key]
		if key in ("fee_percent_of_aum", "fee_flat_annual"):
			changes[key] = as_float(value, key)
		elif key == "investment_horizon_years":
			changes[key] = as_int(args, key)
		elif key == "fee_type":
			changes[key] = _choice(str(value), FEE_TYPES, "fee_type")
		elif key == "billing_frequency":
			changes[key] = _choice(str(value), BILLING_FREQUENCIES, "billing_frequency")
		elif key == "objective":
			changes[key] = _choice(str(value), OBJECTIVES, "objective")
		elif key in ("effective_date", "termination_date"):
			changes[key] = as_date(args, key)
		else:
			changes[key] = str(value).strip()
	if "status" in args and args["status"] not in (None, ""):
		raise ToolError(
			"status is not amendable here. A new version is Active by definition and the one it "
			"replaces becomes Superseded; ending an agreement is terminate=true. Nothing was written."
		)
	return changes


#: What `in_place=true` will touch. Everything absent from this list is a TERM,
#: and a term changes through the amendment path or not at all.
_IN_PLACE_FIELDS = ("agreement_name", "client_entity", "advisor_entity", "document_reference", "objective")


def _amend_in_place(row: dict, before: dict, changes: dict, args: dict) -> ToolResult:
	"""Correct a description without creating a version. Terms are refused."""
	terms = sorted(set(changes) - set(_IN_PLACE_FIELDS))
	if terms:
		raise ToolError(
			f"in_place=true will not change {', '.join(terms)} — those are TERMS, and terms change "
			"through amendment so that a charge taken under the old ones stays justifiable. Drop "
			"in_place, or drop those fields. Nothing was written."
		)
	if not changes:
		raise ToolError(
			"in_place=true with nothing to correct. It takes "
			+ ", ".join(_IN_PLACE_FIELDS)
			+ ". Nothing was written."
		)
	document = changes.get("document_reference")
	if (
		document
		and compat.doctype_exists(GOVERNANCE_DOCUMENT)
		and not frappe.db.exists(GOVERNANCE_DOCUMENT, document)
	):
		raise ToolError(f"no Governance Document named {document!r}. Nothing was written.")

	frappe.db.set_value(AGREEMENT, before["name"], changes)
	after = _agreement_out(_agreement_row(before["name"]))
	data = {
		"name": before["name"],
		"agreement": after,
		"changed": {key: {"was": before.get(key), "now": after.get(key)} for key in changes},
		"note": (
			"Corrected in place and NO version was created, because none of these fields is a term. "
			"A fee, a date or a status changes through amendment, which records what it replaced."
		),
	}
	return ToolResult(
		data,
		f"corrected {before['name']} in place: {', '.join(sorted(changes))}",
		docstatus_delta="none (fields on an existing agreement)",
	)


# ── the register, used by the summary and by tests ───────────────────────────


def list_advisory_agreements(args: dict) -> ToolResult:
	"""Every agreement on file, newest term first. Read-only."""
	_require_agreements()
	filters = {}
	company = resolve_company(as_str(args, "company"))
	if company:
		filters["company"] = company
	status = as_str(args, "status")
	if status:
		filters["status"] = _choice(status, STATUSES, "status")
	bank_account = as_str(args, "bank_account")
	if bank_account:
		filters["bank_account"] = bank_account
	limit = as_limit(args)

	fields = compat.existing_fields(AGREEMENT, list(_AGREEMENT_FIELDS))
	rows = frappe.db.get_all(
		AGREEMENT, filters=filters, fields=fields, order_by="effective_date desc, name desc", limit=limit
	)
	agreements = [_agreement_out(dict(row)) for row in rows]
	active = [row for row in agreements if row["status"] == "Active"]

	data = {
		"company": company,
		"agreements": agreements,
		"count": len(agreements),
		"limit": limit,
		"truncated": len(agreements) == limit,
		"active_count": len(active),
		"accounts_without_an_agreement": _accounts_without_agreements(company),
		"note": (
			"Superseded agreements are listed as well as Active ones. They are what justifies a "
			"charge taken under terms that have since changed, which is the only reason to keep them."
		),
	}
	return ToolResult(data, f"{len(agreements)} advisory agreement(s), {len(active)} active")


def _accounts_without_agreements(company: str) -> list:
	"""Investment accounts with no Active agreement on file.

	The one thing a register of agreements can say that reading the agreements
	cannot: which managed account has NO terms recorded, which is the account
	whose fee nobody can check.
	"""
	if not compat.has_field(BANK_ACCOUNT, anchors.PLAID_TYPE_FIELD):
		return []
	filters = {anchors.PLAID_TYPE_FIELD: "investment"}
	if company:
		filters["company"] = company
	accounts = frappe.db.get_all(BANK_ACCOUNT, filters=filters, fields=["name"], limit=MAX_SCAN)
	if not accounts:
		return []
	covered = {
		row["bank_account"]
		for row in frappe.db.get_all(
			AGREEMENT,
			filters={"status": "Active", "bank_account": ("in", [row["name"] for row in accounts])},
			fields=["bank_account"],
			limit=MAX_SCAN,
		)
	}
	return [row["name"] for row in accounts if row["name"] not in covered]
