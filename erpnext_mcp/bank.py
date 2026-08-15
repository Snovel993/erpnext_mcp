# SPDX-License-Identifier: MIT
"""The door a bank pipe pushes reconciliation truth through.

v0.73.0. Two whitelisted methods, and they exist because the thing that can talk
to a bank cannot be the thing that owns the books. Reading a statement PDF needs
credentials, an aggregator session and a parser that changes whenever a bank
redesigns its layout; owning the reconciliation record needs to be beside the
Bank Transactions, the GL, the company and the audit trail. Those are different
jobs with different lifetimes, and the seam between them is here:

    POST /api/method/erpnext_mcp.bank.push_statement_anchor
    POST /api/method/erpnext_mcp.bank.push_account_pairing

After this, the pipe holds NO authoritative data. It parses, it pushes, and if
this site is unreachable it retries on the next sync. Everything a person or an
AI asks about reconciliation is answered here, from records that get exported
with the company they belong to.

IDEMPOTENT ON (bank_account, period_start, period_end), which is the single most
important property of this endpoint. A pipe retries. A parse gets re-run. An
operator triggers a sync by hand while the scheduled one is still going. Every
one of those pushes the same month again, and an endpoint that appended would
leave the site holding two anchors for October that disagree — with nothing to
say which is right, which is precisely the failure the consolidation exists to
end. So: same period, same record, later numbers win.

THE GATE IS FRAPPE'S OWN PERMISSION, THE SAME CHOICE `api/gis.py` MAKES AND FOR
THE SAME REASON. `api/guard.py`'s seven checks are built for a phone on the open
internet and include an Active Mobile Access Grant — the wrong gate entirely for
a server-to-server credential. What is right here is a named user (Guest is
refused before anything is read) plus `frappe.has_permission(..., "write")` on
the doctype being written, which respects a User Permission scoping a credential
to one company. A pipe therefore gets its own ERPNext user with exactly the
roles it needs, and no path here widens that.

EVERY CALL WRITES AN AUDIT ROW, including the refused ones, to the same MCP
Action Log the AI transport uses. "Which credential pushed the October anchor,
and when" is the first question asked when two systems disagree about a number,
and an endpoint that answered it with silence would put the operator back to
reading server logs.

WHAT THESE METHODS WILL NOT DO. They do not create Bank Accounts, they do not
create Companies, and they do not post anything. A push naming an account this
site has never heard of is REFUSED by name rather than accommodated: an
auto-created Bank Account has no GL account, no company anybody chose, and no
way to be noticed — and the anchors hanging off it would look exactly like
reconciliation.
"""

from __future__ import annotations

import json

import frappe

from . import audit, security
from .errors import ToolError
from .tools import anchors

#: Ceiling on one pushed batch. A decade of monthly anchors for one account is a
#: hundred and twenty; a sync pushing more than this in one request is a bug or a
#: loop, and either way the refusal is better than the timeout.
MAX_BATCH = 500


# ── gates ───────────────────────────────────────────────────────────────────


def _named_user() -> str:
	"""The authenticated caller, or a refusal. Guest never gets past this line."""
	user = str(getattr(frappe.session, "user", "") or "")
	if not user or user == "Guest":
		frappe.throw(
			"This endpoint requires an authenticated ERPNext credential. A bank pipe gets its own "
			"user with an API key; nothing here is reachable as Guest.",
			frappe.PermissionError,
		)
	return user


def _may_write(doctype: str) -> None:
	"""Frappe's own answer to "may this credential write this doctype"."""
	frappe.has_permission(doctype, "write", throw=True)


def _payloads(single: dict, batch, label: str) -> tuple:
	"""`(rows, is_batch)`. One payload or a batch of them, refused when neither.

	A batch is accepted because a sync has thirteen periods and thirteen HTTP
	round trips to push them is thirteen chances for one to be lost halfway. Each
	item is upserted on its own and reported on its own, so a batch where one row
	is malformed still lands the other twelve — a pipe that had to re-push
	everything because of one bad month would eventually stop pushing.

	`is_batch` matters and is why this returns a pair. Per-row tolerance is right
	for a batch and WRONG for a single push: one payload that failed and came
	back 200 with `failed_count: 1` is a pipe that believes it succeeded. A
	single push that fails raises, so the caller's own retry sees it.
	"""
	if batch not in (None, ""):
		rows = _as_list(batch, label)
		if len(rows) > MAX_BATCH:
			raise ToolError(
				f"{len(rows)} rows in one push; the ceiling is {MAX_BATCH}. Split the batch. "
				"Nothing was written."
			)
		return rows, True
	if not any(value not in (None, "") for value in single.values()):
		raise ToolError(f"nothing to push: give the fields directly, or a list as {label}.")
	return [single], False


def _as_list(value, label: str) -> list:
	"""A list of dicts, whether it arrived as JSON text or as a parsed body."""
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError as exc:
			raise ToolError(f"{label} is not valid JSON: {exc}. Nothing was written.") from exc
	if isinstance(value, dict):
		value = [value]
	if not isinstance(value, list):
		raise ToolError(f"{label} must be a list of objects. Nothing was written.")
	rows = []
	for item in value:
		if isinstance(item, str):
			try:
				item = json.loads(item)
			except ValueError as exc:
				raise ToolError(f"an item in {label} is not valid JSON: {exc}.") from exc
		if not isinstance(item, dict):
			raise ToolError(f"every item in {label} must be an object. Nothing was written.")
		rows.append(item)
	return rows


def _finish(method: str, arguments: dict, result: dict, user: str) -> dict:
	"""The success audit row, and the payload as it goes back on the wire."""
	audit.record(
		f"push:{method}",
		arguments,
		audit.STATUS_SUCCESS,
		f"{result.get('summary') or method} — {user}",
		caller_ip=security.caller_ip(),
	)
	return result


def _refuse(method: str, arguments: dict, user: str, exc: Exception):
	"""The refusal audit row, committed on its own so it outlives the rollback."""
	audit.record(
		f"push:{method}",
		arguments,
		audit.STATUS_ERROR,
		f"refused — {user or 'Guest'}: {type(exc).__name__}: {exc}",
		caller_ip=security.caller_ip(),
		commit=True,
	)


# ── 1. push_statement_anchor ────────────────────────────────────────────────


@frappe.whitelist()
def push_statement_anchor(
	bank_account=None,
	period_start=None,
	period_end=None,
	anchored_opening=None,
	anchored_closing=None,
	transaction_sum=None,
	plaid_account_mask=None,
	variance_reason=None,
	variance_tolerance=None,
	parser_version=None,
	mark_to_market_delta=None,
	portfolio_opening_value=None,
	portfolio_closing_value=None,
	statement_pdf=None,
	source_statement_id=None,
	statement_lines=None,
	anchors_batch=None,
):
	"""Upsert one statement anchor, or a batch of them. Idempotent on the period.

	THE THREE ANCHORED NUMBERS ARE THE ONLY THING THIS TAKES ON TRUST, and they
	are the only three it should: `anchored_opening`, `anchored_closing` and
	`transaction_sum` came off a bank statement that this site did not read.
	Everything derived from them — the computed closing, the variance, whether the
	period reconciles, whether the chain has a gap — is recomputed by the doctype
	on save, so a pipe that pushed its own arithmetic gets this site's arithmetic
	back and any disagreement between the two shows up immediately rather than
	being adopted.

	`statement_lines` REPLACE what is there rather than adding to it. A re-parse
	produces the same lines again, and appending would double a month of them —
	after which every line matches a transaction and the count of unmatched ones,
	which is the entire product of the exercise, is quietly wrong.

	`variance_reason` is the ONE field a later push will not overwrite. It lands
	on an anchor that has none — which is what makes a one-time migration of a
	pipe's own variance tags work — and never over one that does, because after
	consolidation the sentence is a person's and a nightly sync would otherwise
	erase it.

	Pass `anchors_batch` as a list to push a whole account's history in one call.
	Each row is upserted independently: one malformed month does not lose the
	other twelve, and the failures come back named.
	"""
	user = _named_user()
	single = {
		"bank_account": bank_account,
		"period_start": period_start,
		"period_end": period_end,
		"anchored_opening": anchored_opening,
		"anchored_closing": anchored_closing,
		"transaction_sum": transaction_sum,
		"plaid_account_mask": plaid_account_mask,
		"variance_reason": variance_reason,
		"variance_tolerance": variance_tolerance,
		"parser_version": parser_version,
		"mark_to_market_delta": mark_to_market_delta,
		"portfolio_opening_value": portfolio_opening_value,
		"portfolio_closing_value": portfolio_closing_value,
		"statement_pdf": statement_pdf,
		"source_statement_id": source_statement_id,
		"statement_lines": _lines(statement_lines),
	}
	arguments = {key: value for key, value in single.items() if value not in (None, "")}

	try:
		_may_write("Statement Anchor")
		rows, is_batch = _payloads(single, anchors_batch, "anchors_batch")
		created, updated, failed = [], [], []
		for row in rows:
			try:
				row = dict(row)
				row["statement_lines"] = _lines(row.get("statement_lines"))
				outcome = anchors.upsert_anchor(row)
			except Exception as exc:
				# Per-row tolerance is a BATCH property. A single push that
				# failed must not come back 200 — see `_payloads`.
				if not is_batch:
					raise
				failed.append(
					{
						"bank_account": row.get("bank_account"),
						"period_start": row.get("period_start"),
						"period_end": row.get("period_end"),
						"error": f"{type(exc).__name__}: {exc}",
					}
				)
				continue
			(created if outcome["created"] else updated).append(outcome["anchor"])

		result = {
			"created": created,
			"created_count": len(created),
			"updated": updated,
			"updated_count": len(updated),
			"failed": failed,
			"failed_count": len(failed),
			"idempotent_on": ["bank_account", "period_start", "period_end"],
			"summary": (
				f"pushed {len(created)} new and {len(updated)} updated anchor(s)"
				+ (f", {len(failed)} refused" if failed else "")
			),
			"note": (
				"Every derived value — computed_closing, variance, reconciled, chain_gap_from_prior — "
				"was recomputed here from the three anchored numbers. A pushed value for any of them "
				"is ignored on purpose: the whole point of this record is that the variance cannot be "
				"asserted."
			),
		}
		if failed:
			result["warning"] = (
				f"{len(failed)} row(s) were refused and the rest were written. Each refusal names its "
				"period and its reason; re-push those rows once the reason is fixed."
			)
		return _finish("push_statement_anchor", arguments, result, user)
	except ToolError as exc:
		_refuse("push_statement_anchor", arguments, user, exc)
		frappe.throw(str(exc), frappe.ValidationError)
	except Exception as exc:
		_refuse("push_statement_anchor", arguments, user, exc)
		raise


def _lines(value):
	"""Statement lines as a list of dicts, or None when none were sent.

	None and an empty list are DIFFERENT here and both are meaningful: None means
	"this push says nothing about lines", so whatever is on file is left alone;
	`[]` means "this period has no lines", which clears them.
	"""
	if value in (None, ""):
		return None
	return _as_list(value, "statement_lines")


# ── 2. push_account_pairing ─────────────────────────────────────────────────


@frappe.whitelist()
def push_account_pairing(
	bank_account=None,
	paired_bank_account=None,
	pairing_type=None,
	plaid_account_id=None,
	plaid_account_mask=None,
	plaid_account_type=None,
	plaid_account_subtype=None,
	sync_enabled=None,
	accounts=None,
):
	"""Write a Bank Account's aggregator identity, and its companion, from a sync.

	WHAT THIS IS FOR: the pipe knows things this site cannot work out — which
	aggregator account a Bank Account is, what kind it is, and whether it is being
	pulled at all. Recorded here so `get_account_pairing` can answer a topology
	question without a round trip to the pipe, and so "why has this account no
	transactions since March" has an answer.

	ONLY THE KEYS ACTUALLY SENT ARE WRITTEN. A pipe that knows the mask and not
	the subtype must not blank a subtype somebody typed, which is what a
	write-everything endpoint would do on every sync.

	A pairing writes BOTH accounts, through the same code path as
	`pair_bank_accounts` — a companion relationship recorded on one side only
	reads as working from that side and as absent from the other.
	"""
	user = _named_user()
	single = {
		"bank_account": bank_account,
		"paired_bank_account": paired_bank_account,
		"pairing_type": pairing_type,
		"plaid_account_id": plaid_account_id,
		"plaid_account_mask": plaid_account_mask,
		"plaid_account_type": plaid_account_type,
		"plaid_account_subtype": plaid_account_subtype,
		"sync_enabled": sync_enabled,
	}
	arguments = {key: value for key, value in single.items() if value not in (None, "")}

	try:
		_may_write("Bank Account")
		rows, is_batch = _payloads(single, accounts, "accounts")
		updated, failed = [], []
		for row in rows:
			try:
				updated.append(anchors.upsert_pairing(dict(row)))
			except Exception as exc:
				if not is_batch:
					raise
				failed.append(
					{"bank_account": row.get("bank_account"), "error": f"{type(exc).__name__}: {exc}"}
				)

		result = {
			"updated": updated,
			"updated_count": len(updated),
			"failed": failed,
			"failed_count": len(failed),
			"summary": f"updated {len(updated)} bank account(s)"
			+ (f", {len(failed)} refused" if failed else ""),
			"note": (
				"Only the keys this push actually carried were written. A pairing writes both sides, "
				"because a companion relationship recorded on one account and not the other reads as "
				"working from one end and absent from the other."
			),
		}
		if failed:
			result["warning"] = (
				f"{len(failed)} account(s) were refused and the rest were written. This endpoint does "
				"not create Bank Accounts: an account nobody chose has no GL account and no company, "
				"and anchors hanging off it would look exactly like reconciliation."
			)
		return _finish("push_account_pairing", arguments, result, user)
	except ToolError as exc:
		_refuse("push_account_pairing", arguments, user, exc)
		frappe.throw(str(exc), frappe.ValidationError)
	except Exception as exc:
		_refuse("push_account_pairing", arguments, user, exc)
		raise
