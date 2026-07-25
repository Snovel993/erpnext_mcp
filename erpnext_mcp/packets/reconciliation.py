# SPDX-License-Identifier: MIT
"""`reconciliation_packet` — one account, one period, everything needed to sign it off.

The question this answers is the one somebody actually asks: *what happened in
this account last month, and is there anything about it I should not sign?*

WHAT MAKES IT AN AUDIT ARTEFACT RATHER THAN A QUERY

  * **Cancelled entries get their own section.** A cancelled Journal Entry
    leaves no live GL row, so it is invisible to a balance query — and it is the
    single most interesting thing in a period to a reviewer, because somebody
    posted it and then unposted it. Reporting the balance without reporting the
    cancellations is technically correct and practically misleading.
  * **Drafts get their own section too**, with the movement they *would* cause.
    An account that reconciles today and will not once three drafts are
    submitted is not reconciled; it is about to not be.
  * **The arithmetic is checked against itself.** `opening + net_change` must
    equal `closing`. Both come from the same ledger by different routes, so if
    they disagree the packet says ERROR rather than presenting two numbers and
    letting the reader assume.

A NOTE ON WHERE THE NUMBERS COME FROM. Balances are summed from GL Entry with
`is_cancelled` excluded, which is what ERPNext's own General Ledger report does.
The Journal Entry lists come from the `Journal Entry Account` child table
instead, because that is the only source that can see drafts and cancellations —
GL Entry cannot.

FUTURE: BANK BRIDGE. When this is wired to an external reconciliation source,
its variance belongs in `flags` as a WARN with the anchor chain in `detail`, and
in `external_sources` as a named entry. Both are already in the payload shape —
`external_sources` ships as an empty list rather than being absent, so a consumer
written today does not have to change when it fills up.
"""

import frappe

from .. import compat
from ..args import as_date, as_str, resolve_account, resolve_company
from ..errors import ToolError
from .base import (
	SEVERITY_ERROR,
	SEVERITY_INFO,
	SEVERITY_WARN,
	TOLERANCE,
	Flag,
	PacketResult,
	PacketSpec,
	cap,
	money,
	register,
)

#: A single entry at or above this share of the period's gross movement is worth
#: a reviewer's eye. Materiality is a judgement, so this is INFO, not WARN — the
#: packet points, it does not conclude.
LARGE_ENTRY_SHARE = 0.25

#: Days without a single posting inside the period before that is worth noting.
#: Two weeks of silence on an operating bank account usually means a feed
#: stopped, not that nothing happened.
QUIET_PERIOD_DAYS = 14

_CREDIT_ROOTS = ("Liability", "Income", "Equity")


def build(filters: dict) -> PacketResult:
	company = resolve_company(as_str(filters, "company"))
	account = resolve_account(as_str(filters, "account", required=True), company or "")
	start = as_date(filters, "period_start", required=True)
	end = as_date(filters, "period_end", required=True)
	if start > end:
		raise ToolError(f"period_start {start} is after period_end {end}")

	meta = (
		frappe.db.get_value(
			"Account",
			account,
			["account_name", "account_number", "account_type", "root_type", "company", "account_currency"],
			as_dict=True,
		)
		or {}
	)
	flags: list = []

	day_before = frappe.utils.add_days(frappe.utils.getdate(start), -1).isoformat()
	opening = _balance_as_of(account, day_before)
	closing = _balance_as_of(account, end)
	movement = _movement(account, start, end)

	entries = _journal_entries(account, start, end, docstatus=1, flags=flags)
	drafts = _journal_entries(account, start, end, docstatus=0, flags=flags)
	cancelled = _journal_entries(account, start, end, docstatus=2, flags=flags)

	data = {
		"account": {
			"name": account,
			"number": meta.get("account_number"),
			"type": meta.get("account_type"),
			"root_type": meta.get("root_type"),
			"company": meta.get("company"),
			"currency": meta.get("account_currency"),
			"account_name": meta.get("account_name"),
		},
		"period": {"start": start, "end": end},
		"opening_balance": {
			"amount": opening["balance"],
			"as_of": day_before,
			"source": "GL Entry",
			"gl_entry_count": opening["entries"],
		},
		"closing_balance": {
			"amount": closing["balance"],
			"as_of": end,
			"source": "GL Entry",
			"gl_entry_count": closing["entries"],
		},
		"movement_summary": {
			"total_debits": movement["debit"],
			"total_credits": movement["credit"],
			"net_change": movement["net"],
			"count_transactions": movement["entries"],
		},
		"journal_entries": entries,
		"unposted_drafts": drafts,
		"cancelled_entries": cancelled,
		"sign_convention": (
			"Amounts are raw ledger convention: debit positive, credit negative in "
			"net_change. balance is debit - credit."
		),
		# Reserved for v0.4, when an external reconciliation source (Bank Bridge)
		# contributes its own variance. Shipped empty rather than absent so a
		# consumer written against v0.3 keeps working.
		"external_sources": [],
	}

	_check_arithmetic(data, flags)
	_check_cancellations(cancelled, flags)
	_check_drafts(drafts, flags)
	_check_unbalanced(entries + drafts + cancelled, flags)
	_check_activity(account, start, end, opening["balance"], meta, flags)
	_check_large_entries(entries, movement, flags)

	summary = (
		f"reconciliation packet for {account} {start}..{end}: "
		f"opening {opening['balance']}, closing {closing['balance']}, "
		f"{movement['entries']} GL row(s), {len(flags)} flag(s)"
	)
	return PacketResult(data=data, flags=flags, summary=summary)


# ── ledger reads ────────────────────────────────────────────────────────────
def _gl_filters(account: str) -> dict:
	filters = {"account": account}
	if compat.has_field("GL Entry", "is_cancelled"):
		filters["is_cancelled"] = 0
	return filters


def _balance_as_of(account: str, as_of: str) -> dict:
	filters = _gl_filters(account)
	filters["posting_date"] = ("<=", as_of)
	row = (
		frappe.db.get_all(
			"GL Entry",
			filters=filters,
			fields=["sum(debit) as debit", "sum(credit) as credit", "count(name) as entries"],
		)
		or [{}]
	)[0] or {}
	return {
		"balance": money(float(row.get("debit") or 0) - float(row.get("credit") or 0)),
		"entries": int(row.get("entries") or 0),
	}


def _movement(account: str, start: str, end: str) -> dict:
	filters = _gl_filters(account)
	filters["posting_date"] = ("between", [start, end])
	row = (
		frappe.db.get_all(
			"GL Entry",
			filters=filters,
			fields=["sum(debit) as debit", "sum(credit) as credit", "count(name) as entries"],
		)
		or [{}]
	)[0] or {}
	debit = money(row.get("debit"))
	credit = money(row.get("credit"))
	return {
		"debit": debit,
		"credit": credit,
		"net": money(debit - credit),
		"entries": int(row.get("entries") or 0),
	}


def _journal_entries(account: str, start: str, end: str, docstatus: int, flags: list) -> list:
	"""Journal Entries touching this account, at one docstatus.

	Read through `Journal Entry Account` rather than GL Entry because drafts have
	no GL rows and cancelled entries' rows are marked `is_cancelled` — and both
	are things this packet exists to surface.
	"""
	lines = frappe.db.get_all(
		"Journal Entry Account",
		filters={"account": account, "parenttype": "Journal Entry"},
		fields=["parent", "debit", "credit"],
	)
	if not lines:
		return []

	per_parent: dict = {}
	for line in lines:
		bucket = per_parent.setdefault(line["parent"], {"debit": 0.0, "credit": 0.0})
		bucket["debit"] += float(line.get("debit") or 0)
		bucket["credit"] += float(line.get("credit") or 0)

	headers = frappe.db.get_all(
		"Journal Entry",
		filters={
			"name": ("in", sorted(per_parent)),
			"posting_date": ("between", [start, end]),
			"docstatus": docstatus,
		},
		fields=compat.existing_fields(
			"Journal Entry",
			[
				"name",
				"posting_date",
				"docstatus",
				"user_remark",
				"total_debit",
				"total_credit",
				"voucher_type",
				"cheque_no",
				"owner",
			],
		),
		order_by="posting_date asc, name asc",
	)
	out = []
	for header in headers:
		amounts = per_parent.get(header["name"], {})
		out.append(
			{
				**header,
				"this_account_debit": money(amounts.get("debit")),
				"this_account_credit": money(amounts.get("credit")),
				"this_account_net": money(
					float(amounts.get("debit") or 0) - float(amounts.get("credit") or 0)
				),
			}
		)
	label = {0: "unposted_drafts", 1: "journal_entries", 2: "cancelled_entries"}[docstatus]
	return cap(out, flags, label)


# ── anomaly detection ───────────────────────────────────────────────────────
def _check_arithmetic(data: dict, flags: list) -> None:
	"""opening + net_change must equal closing.

	Two independent aggregates over the same ledger. If they disagree the packet
	is internally inconsistent, which is an ERROR — not a business observation.
	"""
	opening = data["opening_balance"]["amount"]
	net = data["movement_summary"]["net_change"]
	closing = data["closing_balance"]["amount"]
	difference = money(opening + net - closing)
	data["arithmetic_check"] = {
		"opening_plus_net": money(opening + net),
		"closing": closing,
		"difference": difference,
		"reconciles": abs(difference) <= TOLERANCE,
	}
	if abs(difference) > TOLERANCE:
		flags.append(
			Flag(
				code="BALANCE_DOES_NOT_RECONCILE",
				severity=SEVERITY_ERROR,
				description=(
					f"opening ({opening}) + net change ({net}) = {money(opening + net)}, "
					f"but the closing balance is {closing} — a difference of "
					f"{difference}. These come from the same ledger by different "
					"routes, so this packet is internally inconsistent. Do not sign it."
				),
				detail=data["arithmetic_check"],
			)
		)


def _check_cancellations(cancelled: list, flags: list) -> None:
	if not cancelled:
		return
	total = money(sum(abs(row["this_account_net"]) for row in cancelled))
	flags.append(
		Flag(
			code="CANCELLED_ENTRIES",
			severity=SEVERITY_WARN,
			description=(
				f"{len(cancelled)} Journal Entry/Entries touching this account were "
				f"cancelled in the period, {total} in gross movement. Cancelled "
				"entries leave no live GL rows, so they do not appear in the "
				"balance — somebody posted these and then unposted them."
			),
			detail={
				"count": len(cancelled),
				"gross_amount": total,
				"entries": [row["name"] for row in cancelled],
			},
		)
	)


def _check_drafts(drafts: list, flags: list) -> None:
	if not drafts:
		return
	net = money(sum(row["this_account_net"] for row in drafts))
	flags.append(
		Flag(
			code="UNPOSTED_DRAFTS",
			severity=SEVERITY_WARN,
			description=(
				f"{len(drafts)} draft Journal Entry/Entries dated in the period would "
				f"move this account by {net} if submitted. The closing balance in "
				"this packet does not include them."
			),
			detail={
				"count": len(drafts),
				"net_if_submitted": net,
				"entries": [row["name"] for row in drafts],
			},
		)
	)


def _check_unbalanced(entries: list, flags: list) -> None:
	"""A Journal Entry whose own debits and credits disagree.

	ERPNext will not submit one, so a submitted example means something wrote
	around the doctype. Worth an ERROR wherever it turns up.
	"""
	broken = [
		row["name"]
		for row in entries
		if abs(float(row.get("total_debit") or 0) - float(row.get("total_credit") or 0)) > TOLERANCE
	]
	if not broken:
		return
	flags.append(
		Flag(
			code="UNBALANCED_JOURNAL_ENTRY",
			severity=SEVERITY_ERROR,
			description=(
				f"{len(broken)} Journal Entry/Entries in this period have total_debit "
				"!= total_credit. ERPNext does not permit that through the doctype, "
				"so these were written around it."
			),
			detail={"entries": broken},
		)
	)


def _check_activity(account: str, start: str, end: str, opening: float, meta: dict, flags: list) -> None:
	"""No activity at all, long silences, and dates where the balance goes negative."""
	filters = _gl_filters(account)
	filters["posting_date"] = ("between", [start, end])
	rows = frappe.db.get_all(
		"GL Entry",
		filters=filters,
		fields=["posting_date", "debit", "credit", "voucher_type", "voucher_no"],
		order_by="posting_date asc",
	)
	if not rows:
		flags.append(
			Flag(
				code="NO_ACTIVITY",
				severity=SEVERITY_INFO,
				description=f"No posted GL entries against this account between {start} and {end}.",
				detail={"period": {"start": start, "end": end}},
			)
		)
		return

	# Running balance, to find the dates the account went negative.
	natural_credit = meta.get("root_type") in _CREDIT_ROOTS
	running = opening
	negative_dates = []
	for row in rows:
		running += float(row.get("debit") or 0) - float(row.get("credit") or 0)
		natural = -running if natural_credit else running
		if natural < -TOLERANCE:
			date = str(row["posting_date"])
			if date not in negative_dates:
				negative_dates.append(date)
	if negative_dates:
		flags.append(
			Flag(
				code="NEGATIVE_BALANCE",
				severity=SEVERITY_WARN,
				description=(
					f"The account's natural balance went negative on "
					f"{len(negative_dates)} date(s) during the period — an overdrawn "
					f"{meta.get('account_type') or meta.get('root_type')} account, or "
					"entries posted out of order."
				),
				detail={"dates": negative_dates[:20], "count": len(negative_dates)},
			)
		)

	# Long silences between postings.
	dates = sorted({str(row["posting_date"]) for row in rows})
	gaps = []
	previous = start
	for date in [*dates, end]:
		days = frappe.utils.date_diff(date, previous)
		if days > QUIET_PERIOD_DAYS:
			gaps.append({"from": previous, "to": date, "days": days})
		previous = date
	if gaps:
		flags.append(
			Flag(
				code="QUIET_PERIOD",
				severity=SEVERITY_INFO,
				description=(
					f"{len(gaps)} stretch(es) of more than {QUIET_PERIOD_DAYS} days with "
					"no posting. On an account with a live feed that usually means the "
					"feed stopped rather than that nothing happened."
				),
				detail={"gaps": gaps[:20]},
			)
		)

	future = [str(row["posting_date"]) for row in rows if str(row["posting_date"]) > frappe.utils.today()]
	if future:
		flags.append(
			Flag(
				code="FUTURE_DATED",
				severity=SEVERITY_INFO,
				description=f"{len(future)} posting(s) are dated after today.",
				detail={"dates": sorted(set(future))[:20]},
			)
		)


def _check_large_entries(entries: list, movement: dict, flags: list) -> None:
	gross = movement["debit"] + movement["credit"]
	if gross <= 0 or not entries:
		return
	threshold = gross * LARGE_ENTRY_SHARE
	large = [
		{
			"name": row["name"],
			"posting_date": str(row["posting_date"]),
			"amount": abs(row["this_account_net"]),
			"share_of_period": round(abs(row["this_account_net"]) / gross, 4),
			"user_remark": row.get("user_remark"),
		}
		for row in entries
		if abs(row["this_account_net"]) >= threshold
	]
	if not large:
		return
	flags.append(
		Flag(
			code="LARGE_ENTRY",
			severity=SEVERITY_INFO,
			description=(
				f"{len(large)} entry/entries account for at least "
				f"{int(LARGE_ENTRY_SHARE * 100)}% of the period's gross movement each. "
				"Materiality is a judgement — this points, it does not conclude."
			),
			detail={"threshold": money(threshold), "entries": large[:20]},
		)
	)


register(
	PacketSpec(
		packet_type="reconciliation_packet",
		title="Account reconciliation packet",
		purpose=(
			"Everything needed to understand and sign off one account for one "
			"period: opening and closing balances from the ledger, the movement "
			"between them, every Journal Entry that touched it, the drafts that "
			"would change it, the cancellations that are invisible to a balance "
			"query, and a self-check that the arithmetic closes."
		),
		audience="An accountant or auditor signing off a period.",
		build=build,
		filters={
			"account": {
				"type": "string",
				"description": "Account docname, number or exact name.",
			},
			"period_start": {"type": "string", "description": "First day of the period, YYYY-MM-DD."},
			"period_end": {"type": "string", "description": "Last day of the period, YYYY-MM-DD."},
			"company": {
				"type": "string",
				"description": "Company name or abbreviation. Inferred on a single-company site.",
			},
		},
		required=("account", "period_start", "period_end"),
	)
)
