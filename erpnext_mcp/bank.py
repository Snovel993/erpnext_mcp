# SPDX-License-Identifier: MIT
"""The door a bank pipe pushes reconciliation truth through.

v0.74.0. Three whitelisted methods, and they exist because the thing that can talk
to a bank cannot be the thing that owns the books. Reading a statement PDF needs
credentials, an aggregator session and a parser that changes whenever a bank
redesigns its layout; owning the reconciliation record needs to be beside the
Bank Transactions, the GL, the company and the audit trail. Those are different
jobs with different lifetimes, and the seam between them is here:

    POST /api/method/erpnext_mcp.bank.push_statement_anchor
    POST /api/method/erpnext_mcp.bank.push_account_pairing
    POST /api/method/erpnext_mcp.bank.push_account_metadata

THE THIRD IS THE SECOND WITH THE PAIRING TAKEN OUT, and it is separate for a
reason worth stating. `push_account_pairing` can repoint which two accounts are
companions; a nightly metadata refresh has no business being able to do that, and
a pipe that only ever wants to say "this is what the aggregator calls this
account today" should not have to send a payload whose other half could rewrite a
relationship an operator set by hand. Both land through one implementation, so
the two can never drift; the narrow one REFUSES a pairing key by name rather than
ignoring it, because a dropped key that returns 200 is the failure this whole
module is shaped to avoid.

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


# ── 2 and 3: the two account pushes, over one implementation ────────────────

#: The keys `push_account_metadata` refuses by name. See the module docstring:
#: the narrow endpoint exists precisely so a metadata refresh cannot repoint a
#: relationship, and a key it silently dropped would be indistinguishable from a
#: key it honoured.
PAIRING_KEYS = ("paired_bank_account", "pairing_type")


def _refuse_pairing_keys(row: dict) -> None:
	carried = [key for key in PAIRING_KEYS if row.get(key) not in (None, "")]
	if carried:
		raise ToolError(
			f"push_account_metadata does not write {', '.join(carried)} — it writes an account's "
			"aggregator identity and nothing about which account it is paired with. Use "
			"push_account_pairing for that. Nothing was written for this account."
		)


def _push_accounts(method, single, accounts, user, note, warning, guard=None) -> dict:
	"""Gate, batch, upsert, audit — the body both account endpoints run.

	ONE IMPLEMENTATION ON PURPOSE. Two endpoints writing the same six columns
	through two code paths is two answers to what "only the keys actually sent
	are written" means, and the day they diverge is the day a metadata refresh
	blanks something a pairing push preserved.
	"""
	arguments = {key: value for key, value in single.items() if value not in (None, "")}

	try:
		_may_write("Bank Account")
		rows, is_batch = _payloads(single, accounts, "accounts")
		updated, failed = [], []
		for row in rows:
			row = dict(row)
			try:
				if guard is not None:
					guard(row)
				updated.append(anchors.upsert_pairing(row))
			except Exception as exc:
				if not is_batch:
					raise
				failed.append(
					{"bank_account": row.get("bank_account"), "error": f"{type(exc).__name__}: {exc}"}
				)

		# Spelled out rather than left to be read off the history array. A
		# re-link is the one thing in a metadata push that changes what an
		# identifier MEANS, and a sync log that did not name it would leave an
		# operator comparing two id columns to find out whether it happened.
		repointed = [
			{
				"bank_account": outcome.get("bank_account"),
				"was": outcome.get("plaid_account_id_superseded"),
				"now": (outcome.get("account") or {}).get("plaid_account_id"),
			}
			for outcome in updated
			if outcome.get("plaid_account_id_superseded")
		]

		result = {
			"updated": updated,
			"updated_count": len(updated),
			"failed": failed,
			"failed_count": len(failed),
			"repointed": repointed,
			"repointed_count": len(repointed),
			"summary": f"updated {len(updated)} bank account(s)"
			+ (f", {len(repointed)} repointed" if repointed else "")
			+ (f", {len(failed)} refused" if failed else ""),
			"note": note,
		}
		if repointed:
			result["repoint_note"] = (
				"An account's aggregator id changed, which is what a re-linked bank connection looks "
				"like from here. The id it replaced was appended to plaid_account_id_history in the "
				"same write, so the feed rows, tickets and logs that name the old id still lead back "
				"to this account."
			)
		if failed:
			result["warning"] = warning.format(count=len(failed))
		return _finish(method, arguments, result, user)
	except ToolError as exc:
		_refuse(method, arguments, user, exc)
		frappe.throw(str(exc), frappe.ValidationError)
	except Exception as exc:
		_refuse(method, arguments, user, exc)
		raise


_NO_SUCH_ACCOUNT_WARNING = (
	"{count} account(s) were refused and the rest were written. This endpoint does not create "
	"Bank Accounts: an account nobody chose has no GL account and no company, and anchors hanging "
	"off it would look exactly like reconciliation."
)


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
	plaid_account_id_history=None,
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

	`plaid_account_id_history` is a JSON array of ids this account used to have,
	and it is OPTIONAL IN BOTH DIRECTIONS. Send it and the chain the pipe kept is
	merged with the chain this site observed; send nothing and the observed half
	still stands on its own, because a `plaid_account_id` that differs from the
	one on file appends the old one here automatically. That is the point: the
	chain survives a re-link whether or not the pipe was ever taught to track it.
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
		"plaid_account_id_history": plaid_account_id_history,
	}
	return _push_accounts(
		"push_account_pairing",
		single,
		accounts,
		user,
		note=(
			"Only the keys this push actually carried were written. A pairing writes both sides, "
			"because a companion relationship recorded on one account and not the other reads as "
			"working from one end and absent from the other."
		),
		warning=_NO_SUCH_ACCOUNT_WARNING,
	)


# ── 3. push_account_metadata ────────────────────────────────────────────────


@frappe.whitelist()
def push_account_metadata(
	bank_account=None,
	plaid_account_id=None,
	plaid_account_mask=None,
	plaid_account_type=None,
	plaid_account_subtype=None,
	sync_enabled=None,
	plaid_account_id_history=None,
	accounts=None,
	# DECLARED IN ORDER TO BE REFUSED, which is not a contradiction. Frappe hands
	# a whitelisted method only the kwargs its signature names and drops the rest
	# without a word, so a pipe that sent a pairing to this endpoint would get the
	# same 200 as one that sent nothing — and would go on believing it had set a
	# relationship that was never written. Naming them here is what turns that
	# silence into a sentence that says which endpoint to call instead.
	paired_bank_account=None,
	pairing_type=None,
):
	"""Write a Bank Account's aggregator identity from a sync. No pairing, ever.

	WHAT A SYNC IS ACTUALLY SAYING when it calls this: "here is what the
	aggregator calls this account today". That is a fact with a short life — the
	id changes when a connection is re-linked, the subtype changes when a bank
	reclassifies, `sync_enabled` changes when somebody turns the pull off — and
	it is pushed on every sync, forever. What it is NOT is a statement about
	which two accounts are companions, and this endpoint cannot make one: a
	payload carrying `paired_bank_account` or `pairing_type` is refused by name
	rather than quietly ignored, so a pipe that thought it was setting a pairing
	finds out on the first call instead of on the day somebody notices the
	relationship is missing.

	KEYED ON THE ERPNEXT DOCNAME, WHICH IS THE ONLY STABLE IDENTIFIER HERE. The
	Plaid id is not: a re-link issues a new one for the same real account, so the
	whole point of this call is often to REPLACE an id that has gone dead. The id
	being replaced is appended to `plaid_account_id_history` in the same write,
	which is what keeps a year of stored feed rows, an aggregator's support logs
	and this site's records pointing at the same account across any number of
	reconnections. When that happens the reply names it under `repointed` rather
	than leaving it to be spotted.

	`bank_account` also accepts a four-digit mask, and refuses it when more than
	one account carries it — the mask is what a person has, and a metadata write
	onto the wrong account of two that end in 6030 looks exactly like a right one.

	ONLY THE KEYS ACTUALLY SENT ARE WRITTEN, and `sync_enabled` is the exception
	that proves it: `0` is a value, not an absence, and "this account is no longer
	pulled" is precisely the fact that must survive the trip.

	`plaid_account_id_history` is accepted here for the same reason
	`push_account_pairing` accepts it: a pipe that kept its own chain holds the
	re-links that happened before this site was told about the account, and they
	are merged with what this site observed rather than replacing it. It is
	optional in both directions — send nothing and the observed chain still builds
	itself from the ids that arrive.

	Pass `accounts` as a list to push a sync's whole account set in one call.
	"""
	user = _named_user()
	single = {
		"bank_account": bank_account,
		"plaid_account_id": plaid_account_id,
		"plaid_account_mask": plaid_account_mask,
		"plaid_account_type": plaid_account_type,
		"plaid_account_subtype": plaid_account_subtype,
		"sync_enabled": sync_enabled,
		"plaid_account_id_history": plaid_account_id_history,
		"paired_bank_account": paired_bank_account,
		"pairing_type": pairing_type,
	}
	return _push_accounts(
		"push_account_metadata",
		single,
		accounts,
		user,
		note=(
			"Only the keys this push actually carried were written, and the account was found by its "
			"ERPNext docname — not by its aggregator id, which is the identifier that changes when a "
			"connection is re-linked. A superseded id is appended to plaid_account_id_history rather "
			"than overwritten away."
		),
		warning=_NO_SUCH_ACCOUNT_WARNING,
		guard=_refuse_pairing_keys,
	)
