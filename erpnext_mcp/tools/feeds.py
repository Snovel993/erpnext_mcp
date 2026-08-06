# SPDX-License-Identifier: MIT
"""Seven tools over the regulation register: four that write it, three that read it.

v0.38.0. THE LAYER v0.37.0 LEFT OUT. A model can now draft a compliance rule from
a regulation and a person can approve it — and neither of those knows anything
about the regulation six months later. These tools are the pointer that was
missing: where the rule was read from, how often to look, and what to do with the
answer when the page has moved.

  * `create_regulation_feed` and `update_regulation_feed` register a source and
    keep its description, its frequency and its rule links honest.
  * `list_regulation_feeds` is the register — every source, when it was last
    looked at, and which ones are erroring.
  * `get_regulation_feed` is one source in full, including the change log, which
    is the only account anywhere of what this source has done over time.
  * `check_regulation_feed` is the one-source check, for the moment somebody has
    heard a rule changed and does not want to wait for the sweep.
  * `check_all_regulation_feeds` is the sweep, on demand, through the same code
    path the scheduler takes.
  * `list_regulation_changes` is the question the whole release exists to answer:
    WHAT MOVED SINCE OUR LAST COMPLIANCE REVIEW.

────────────────────────────────────────────────────────────────────────────
WHAT A CHANGE ENTITLES A CALLER TO DO, WHICH IS LESS THAN IT LOOKS
────────────────────────────────────────────────────────────────────────────

Nothing here modifies a Compliance Rule, and nothing here can. A detected change
writes a hash, a timestamp and a log line naming the rules that were derived from
the source; the rules themselves go on running on the definitions a person
approved. Where a rule genuinely needs to change, the path is the one v0.37.0
built and this release does not go round: read the regulation,
`propose_compliance_rule` drafts the replacement DISABLED and marked
`AI-proposed` with its citation, and `approve_compliance_rule` supersedes the
live rule with somebody's name on the row.

That separation is the reason a change detector is safe to put on a timer at all.
A sweep that could act on what it found would be a sweep that rewrote a farm's
compliance calendar at three in the morning off a website redesign.

────────────────────────────────────────────────────────────────────────────
THERE IS NO `delete_regulation_feed`, AND ITS ABSENCE IS DELIBERATE
────────────────────────────────────────────────────────────────────────────

`status` = Paused is the stand-down, and it keeps the change log. A source this
operation watched for two seasons is a record of what it was watching and when,
which is exactly the sort of question an auditor asks about a rule whose citation
has been renumbered twice. Deleting the row would take the log with it and leave
nothing behind saying the source had ever been watched — the same reasoning that
gives Compliance Rule a deactivate and no delete.
"""

from __future__ import annotations

import frappe

from .. import compat, compliance_rules
from .. import training as regimes_vocabulary
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..services import regulation_feed as service

DOCTYPE = service.DOCTYPE

#: How much of a change log one read returns, newest first. A feed checked weekly
#: for three years and changed at every check has a hundred and fifty entries; the
#: register is read to answer a question, not to be exported.
LOG_CAP = 50

#: The default window for `list_regulation_changes`. Ninety days is a quarter,
#: which is the interval most operations review compliance on.
DEFAULT_CHANGE_WINDOW_DAYS = 90


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _resolve(args: dict, key: str = "name") -> str:
	"""A feed docname, from `name` or `feed`, refused by name if it is not there."""
	reference = as_str(args, key) or as_str(args, "feed", required=True)
	if frappe.db.exists(DOCTYPE, reference):
		return reference
	matches = frappe.db.get_all(
		DOCTYPE, filters={"feed_name": ("like", f"%{reference}%")}, pluck="name", limit=3
	)
	if len(matches) == 1:
		return matches[0]
	raise ToolError(
		f"no Regulation Feed for {reference!r} on this site."
		+ (f" Did you mean one of: {', '.join(matches)}?" if matches else "")
		+ " list_regulation_feeds has the register."
	)


def _regime_or_refuse(value: str) -> str:
	"""A regime this site knows, canonically spelled, or a refusal naming the ten.

	CANONICAL FIRST, THEN THE TABLE. `training.canon` is what the whole app agrees
	a regime is, and it is what stops 'OSHA' being filed where no packet looks for
	'OR-OSHA'. But an operation answering to a scheme this app has never heard of
	adds a Compliance Regime row by hand, and a tool that refused the row an
	operator deliberately created would be telling them their own site is wrong.
	"""
	text = str(value or "").strip()
	if not text:
		return ""
	canonical = regimes_vocabulary.canon(text)
	if canonical:
		return canonical
	if compat.doctype_exists(regimes_vocabulary.REGIME_DOCTYPE) and frappe.db.exists(
		regimes_vocabulary.REGIME_DOCTYPE, text
	):
		return text
	raise ToolError(
		f"{text!r} is not a regime this site knows. {regimes_vocabulary.vocabulary_note()} A tag "
		"that is nearly right would file this source where nobody preparing for that audit looks "
		"for it, so it is refused rather than corrected. An operation answering to a scheme this "
		"app does not model adds a Compliance Regime row in the Desk first. Nothing was written."
	)


def _frequency_or_refuse(value: str, default: str = "") -> str:
	text = str(value or "").strip()
	if not text:
		return default
	for option in service.FREQUENCY_DAYS:
		if text.lower() == option.lower():
			return option
	raise ToolError(
		f"check_frequency {text!r} is not one of {', '.join(service.FREQUENCY_DAYS)}. Nothing was written."
	)


def _status_or_refuse(value: str, default: str = "") -> str:
	text = str(value or "").strip()
	if not text:
		return default
	for option in service.STATUSES:
		if text.lower() == option.lower():
			if option == service.STATUS_ERROR:
				raise ToolError(
					"status cannot be set to Error by hand. Error is what the LAST CHECK reported, "
					"and a feed marked Error with nothing wrong is a register that lies about "
					"itself. Paused is the stand-down, and it is the one an operator or a tool "
					"chooses. Nothing was written."
				)
			return option
	raise ToolError(f"status {text!r} is not one of {', '.join(service.STATUSES)}. Nothing was written.")


def _url_or_refuse(url: str) -> str:
	text = str(url or "").strip()
	if not text.lower().startswith(("http://", "https://")):
		raise ToolError(
			f"url {text!r} is not an http(s) URL. This field is handed to an outbound HTTP request "
			"by a scheduled job, so anything else is refused here rather than at three in the "
			"morning. Nothing was written."
		)
	if any(character in text for character in " \t\n"):
		raise ToolError(f"url {text!r} contains whitespace, so it is not a URL anything can fetch.")
	return text


def _company_filter(args: dict) -> str:
	"""A Company to filter a READ on — only when the caller actually named one.

	`resolve_company("")` INFERS the single company on a single-company site, which
	is exactly right for a create and exactly wrong for a filter: a feed registered
	without a company would silently disappear out of the register on the very
	sites where nobody ever types a company name.
	"""
	named = as_str(args, "company")
	return resolve_company(named) or "" if named else ""


def _rules_or_refuse(raw) -> list:
	"""Compliance Rule docnames for whatever the caller called the rules.

	Accepts docnames and rule_ids alike, through `compliance_rules.resolve`, which
	is the same resolver every rule tool uses — so `training_expiring` means the
	same rule here as it does to `get_compliance_rule`.
	"""
	if raw in (None, ""):
		return []
	pieces = raw if isinstance(raw, (list, tuple, set)) else str(raw).replace("\n", ",").split(",")
	out = []
	unknown = []
	for piece in pieces:
		reference = str(piece).strip()
		if not reference:
			continue
		name = compliance_rules.resolve(reference) if compat.doctype_exists(compliance_rules.DOCTYPE) else ""
		if not name:
			unknown.append(reference)
		elif name not in out:
			out.append(name)
	if unknown:
		raise ToolError(
			f"affected_rules does not resolve {', '.join(repr(piece) for piece in unknown)} to any "
			"Compliance Rule on this site. Each entry is a docname (CRULE-2026-0004) or a rule_id "
			"(`training_expiring`); list_compliance_rules has the register. A link to a rule that "
			"is not there would put a name in a change log entry that nobody can follow. Nothing "
			"was written."
		)
	return out


def _describe(row: dict, with_log: bool = False, log_limit: int = LOG_CAP) -> dict:
	described = {
		"name": row.get("name"),
		"feed_name": row.get("feed_name"),
		"url": row.get("url"),
		"regime": row.get("regime") or None,
		"company": row.get("company") or None,
		"description": row.get("description") or None,
		"check_frequency": row.get("check_frequency"),
		"status": row.get("status"),
		"last_checked": str(row.get("last_checked") or "") or None,
		"last_content_hash": row.get("last_content_hash") or None,
		"last_change_detected": str(row.get("last_change_detected") or "") or None,
		"error_message": row.get("error_message") or None,
		"due_now": service.is_due(row) and str(row.get("status") or "") != service.STATUS_PAUSED,
	}
	if with_log:
		described["affected_rules"] = row.get("affected_rules") or []
		described["affected_rule_ids"] = service.rule_ids_for(row.get("affected_rules") or [])
		entries = service.parse_change_log(row.get("change_log"), limit=log_limit)
		described["change_log"] = entries
		described["change_log_entries"] = len(service.parse_change_log(row.get("change_log")))
	return described


#: The sentence every mutating tool here ends on, so the separation is restated
#: at the moment a caller is holding a change rather than only in a docstring.
_NO_REMEDIATION = (
	"THIS IS DETECTION ONLY. No Compliance Rule was read, modified, disabled or superseded, and "
	"nothing here can do any of those. Where a rule genuinely needs to change, read the source "
	"and use propose_compliance_rule — the draft lands disabled with its citation on it, and "
	"approve_compliance_rule is where a person's name goes on the replacement."
)


# ── 1. create_regulation_feed ───────────────────────────────────────────────
def create_regulation_feed(args: dict) -> ToolResult:
	"""Register a regulatory source so the site can notice when it moves."""
	_require()
	feed_name = as_str(args, "feed_name", required=True)
	if frappe.db.exists(DOCTYPE, feed_name):
		raise ToolError(
			f"there is already a Regulation Feed called {feed_name!r} on this site. The feed name is "
			"the docname. update_regulation_feed edits that one; pick another name if this is "
			"genuinely a second source. Nothing was written."
		)

	url = _url_or_refuse(as_str(args, "url", required=True))
	description = as_str(args, "description")
	if len(description.strip()) < 20:
		raise ToolError(
			"description must say what is at that URL, in the words of somebody who has read it: "
			"which subject, which sections, and what on this operation turns on them. It is what a "
			"person reads when the log says this source moved, and it is the difference between "
			"'go and read a rulebook' and 'go and check whether the shade requirement changed'. "
			"Nothing was written."
		)

	doc = frappe.new_doc(DOCTYPE)
	doc.feed_name = feed_name
	doc.url = url
	doc.description = description
	doc.regime = _regime_or_refuse(as_str(args, "regime"))
	doc.check_frequency = _frequency_or_refuse(as_str(args, "check_frequency"), service.FREQUENCY_WEEKLY)
	doc.status = _status_or_refuse(as_str(args, "status"), service.STATUS_ACTIVE)
	company = resolve_company(as_str(args, "company"))
	if company:
		doc.company = company
	for rule in _rules_or_refuse(args.get("affected_rules")):
		doc.append("affected_rules", {"rule": rule})

	try:
		doc.insert(ignore_permissions=True)
	except Exception as exc:
		raise ToolError(f"{exc}") from None

	row = service.feed_row(doc.name)
	return ToolResult(
		data={
			**_describe(row, with_log=True),
			"note": (
				"Registered, and NOT yet checked — the first check records a baseline hash and "
				f"cannot report a change, because there is nothing to compare against. The daily "
				f"sweep will take it within a day; check_regulation_feed does it now. "
				f"{_NO_REMEDIATION}"
			),
			"next": ["check_regulation_feed", "list_regulation_feeds"],
		},
		summary=(
			f"registered regulation feed {doc.name} ({doc.check_frequency}, {doc.status})"
			+ (f" for {doc.regime}" if doc.regime else "")
		),
		docstatus_delta="none → 0 (created)",
	)


# ── 2. update_regulation_feed ───────────────────────────────────────────────
def update_regulation_feed(args: dict) -> ToolResult:
	"""Edit a feed's URL, description, regime, frequency, status or rule links.

	IT CANNOT WRITE THE DETECTOR'S OWN MEMORY. `last_content_hash`,
	`last_checked`, `last_change_detected` and `change_log` are refused as
	arguments rather than ignored: a hash somebody typed is a change that will
	never be reported, and a change log somebody edited is the one record here
	whose entire value is that nobody edited it.

	CHANGING THE URL CLEARS THE HASH, and that is the one side effect worth
	stating. A hash taken over one page says nothing about another, and leaving it
	in place would make the next check report a change that is really a change of
	subject. The old hash goes into the log so the record of what was watched
	stays continuous.
	"""
	_require()
	name = _resolve(args)
	doc = frappe.get_doc(DOCTYPE, name)
	before = service.feed_row(name)

	refused = [
		field
		for field in ("last_content_hash", "last_checked", "last_change_detected", "change_log")
		if field in args
	]
	if refused:
		raise ToolError(
			f"{', '.join(refused)} cannot be set through this tool. Those four fields are the "
			"detector's own memory: a hash somebody typed is a change that will never be reported, "
			"and a change log somebody edited is the one record here whose whole value is that "
			"nobody edited it. They are written only by check_regulation_feed and the sweep. "
			"Nothing was changed."
		)

	changes = {}
	if "url" in args:
		url = _url_or_refuse(as_str(args, "url", required=True))
		if url != str(doc.url or ""):
			changes["url"] = url
			doc.url = url
			# A hash over the old page is not a fact about the new one.
			if doc.last_content_hash:
				doc.change_log = service.append_log(
					doc.get("change_log"),
					f"[{frappe.utils.now()}] RETARGETED — url changed to {url}; the stored hash "
					f"{service.short_hash(doc.last_content_hash)} was cleared, because a hash taken "
					"over one page says nothing about another. The next check is a new baseline.",
				)
				doc.last_content_hash = None
	if "description" in args:
		doc.description = as_str(args, "description")
		changes["description"] = doc.description
	if "regime" in args:
		doc.regime = _regime_or_refuse(as_str(args, "regime"))
		changes["regime"] = doc.regime
	if "check_frequency" in args:
		doc.check_frequency = _frequency_or_refuse(as_str(args, "check_frequency", required=True))
		changes["check_frequency"] = doc.check_frequency
	if "status" in args:
		doc.status = _status_or_refuse(as_str(args, "status", required=True))
		changes["status"] = doc.status
	if "company" in args:
		doc.company = resolve_company(as_str(args, "company")) or None
		changes["company"] = doc.company
	if "affected_rules" in args:
		rules = _rules_or_refuse(args.get("affected_rules"))
		doc.set("affected_rules", [])
		for rule in rules:
			doc.append("affected_rules", {"rule": rule})
		changes["affected_rules"] = rules

	if not changes:
		raise ToolError(
			"nothing to change. Pass at least one of url, description, regime, check_frequency, "
			"status, company or affected_rules."
		)

	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		raise ToolError(f"{exc}") from None

	after = service.feed_row(name)
	diff = {
		key: {"before": before.get(key), "after": after.get(key)}
		for key in sorted(changes)
		if before.get(key) != after.get(key)
	}
	return ToolResult(
		data={
			**_describe(after, with_log=True),
			"changed": diff,
			"note": _NO_REMEDIATION,
		},
		summary=f"updated regulation feed {name}: {', '.join(sorted(diff)) or 'no effective change'}",
		docstatus_delta="0 → 0 (updated)",
	)


# ── 3. list_regulation_feeds ────────────────────────────────────────────────
def list_regulation_feeds(args: dict) -> ToolResult:
	"""The register: every source being watched, with its state. Read-only."""
	_require()
	filters = {}
	due_only = bool(as_bool(args, "due_only", default=False))
	status = as_str(args, "status")
	if status:
		filters["status"] = _match_status(status)
	elif due_only:
		# Paused is the only state that keeps a feed out of the sweep, so "due"
		# means "not paused and old enough" rather than "Active and old enough":
		# an errored feed IS due, and leaving it out would hide the feeds most
		# worth looking at.
		filters["status"] = ("!=", service.STATUS_PAUSED)
	regime = as_str(args, "regime")
	if regime:
		filters["regime"] = _regime_or_refuse(regime)
	company = _company_filter(args)
	if company:
		filters["company"] = company

	found = service.rows(filters, limit=as_limit(args), order_by="last_change_detected desc, feed_name asc")
	if due_only:
		now = frappe.utils.now()
		found = [row for row in found if service.is_due(row, now)]

	described = [_describe(row) for row in found]
	errored = [row["name"] for row in described if row["status"] == service.STATUS_ERROR]
	never = [row["name"] for row in described if not row["last_checked"]]
	return ToolResult(
		data={
			"count": len(described),
			"feeds": described,
			"erroring": errored,
			"never_checked": never,
			"note": (
				(
					f"{len(errored)} feed(s) are in Error, which is what their LAST CHECK reported "
					"rather than a decision anybody made — the sweep retries them, and a successful "
					"check clears them back to Active. "
					if errored
					else ""
				)
				+ (
					f"{len(never)} feed(s) have never been checked, so nothing is known about "
					"whether they have moved. "
					if never
					else ""
				)
				+ "Paused is the only state that keeps a feed out of the sweep."
			),
		},
		summary=f"{len(described)} regulation feed(s)" + (f", {len(errored)} erroring" if errored else ""),
	)


def _match_status(value: str) -> str:
	for option in service.STATUSES:
		if str(value).strip().lower() == option.lower():
			return option
	raise ToolError(f"status {value!r} is not one of {', '.join(service.STATUSES)}.")


# ── 4. get_regulation_feed ──────────────────────────────────────────────────
def get_regulation_feed(args: dict) -> ToolResult:
	"""One source in full, including its change log. Read-only."""
	_require()
	name = _resolve(args)
	try:
		row = service.feed_row(name)
	except ValueError as exc:
		raise ToolError(str(exc)) from None
	described = _describe(
		row, with_log=True, log_limit=as_limit(args, "log_limit") if "log_limit" in args else LOG_CAP
	)
	described["note"] = (
		"The change log is the only account anywhere of what this source has done over time — "
		"append-only, newest first here and chronological on the record, and never edited. A "
		"CHANGED entry names the rules derived from this source; it does not mean any of them was "
		"touched. " + _NO_REMEDIATION
	)
	return ToolResult(
		data=described,
		summary=(
			f"{name} ({described['status']}, {described['check_frequency']}) — "
			+ (
				f"last changed {described['last_change_detected']}"
				if described["last_change_detected"]
				else "no change ever detected"
			)
		),
	)


# ── 5. check_regulation_feed ────────────────────────────────────────────────
def check_regulation_feed(args: dict) -> ToolResult:
	"""Fetch one source now and say whether its content changed.

	FOR THE MOMENT THE SWEEP IS TOO SLOW FOR — somebody has heard that a rule
	changed, or has just registered a source and wants its baseline on the record
	rather than tomorrow. It takes the same code path the scheduler takes, which
	is the property that makes it worth having: a manual check with a second
	implementation is a manual check that can disagree with the nightly one.
	"""
	_require()
	name = _resolve(args)
	force = as_bool(args, "force", default=False)
	try:
		report = service.check_feed(name, force=bool(force))
	except Exception as exc:
		raise ToolError(f"{name} could not be checked: {type(exc).__name__}: {exc}") from None

	data = {
		**_describe(service.feed_row(name), with_log=True),
		"checked": report.get("checked"),
		"changed": report.get("changed"),
		"baseline": report.get("baseline"),
		"skipped": report.get("skipped"),
		"previous_hash": report.get("previous_hash"),
		"content_hash": report.get("content_hash"),
		"content_length": report.get("content_length"),
		"error": report.get("error"),
		"note": report.get("summary", "") + " " + _NO_REMEDIATION,
	}
	if report.get("changed"):
		data["next"] = ["get_regulation_feed", "propose_compliance_rule"]
	return ToolResult(
		data=data,
		summary=report.get("summary") or f"checked {name}",
		docstatus_delta="0 → 0 (updated)",
	)


# ── 6. check_all_regulation_feeds ───────────────────────────────────────────
def check_all_regulation_feeds(args: dict) -> ToolResult:
	"""Run the sweep now: every active feed that is due for a check.

	THE SAME FUNCTION THE SCHEDULER CALLS, with the same due logic. `force` checks
	every unpaused feed regardless of when it was last looked at, which is what
	somebody wants before a certification audit and is rude to a public server as
	a habit.
	"""
	_require()
	company = _company_filter(args)
	force = bool(as_bool(args, "force", default=False))
	report = service.check_due_feeds(company=company or "", force=force)

	changed = [
		{
			"name": result.get("name"),
			"feed_name": result.get("feed_name"),
			"url": result.get("url"),
			"regime": result.get("regime"),
			"previous_hash": result.get("previous_hash"),
			"content_hash": result.get("content_hash"),
			"affected_rules": result.get("affected_rules") or [],
			"affected_rule_ids": result.get("affected_rule_ids") or [],
		}
		for result in report["changed"]
	]
	errors = [{"name": result.get("name"), "error": result.get("error")} for result in report["errors"]]
	return ToolResult(
		data={
			"checked": report["checked"],
			"changed_count": len(changed),
			"changed": changed,
			"errors": errors,
			"forced": force,
			"company": company,
			"note": (
				(
					f"{len(changed)} source(s) MOVED. Read each one and decide; the rules named "
					"beside them were derived from those sources and were not touched. "
					if changed
					else "Nothing moved. That is what a healthy register looks like on most days. "
				)
				+ (
					f"{len(errors)} source(s) could not be reached — their `last_checked` was NOT "
					"moved, so the next sweep tries again rather than waiting out their whole "
					"frequency. "
					if errors
					else ""
				)
				+ _NO_REMEDIATION
			),
			"next": ["list_regulation_changes", "get_regulation_feed"] if changed else [],
		},
		summary=(
			f"checked {report['checked']} regulation feed(s); {len(changed)} changed"
			+ (f", {len(errors)} errored" if errors else "")
		),
		docstatus_delta="0 → 0 (updated)",
	)


# ── 7. list_regulation_changes ──────────────────────────────────────────────
def list_regulation_changes(args: dict) -> ToolResult:
	"""Which sources have moved since a date, and which rules came from them. Read-only.

	THE QUESTION THIS WHOLE RELEASE EXISTS TO ANSWER: what regulations moved since
	our last compliance review. It is a filter on `last_change_detected` and
	nothing cleverer, which is the point — the fact was recorded when it happened,
	by the sweep, so answering it later costs one query and no network at all.
	"""
	_require()
	since = as_date(args, "since") or frappe.utils.add_days(frappe.utils.today(), -DEFAULT_CHANGE_WINDOW_DAYS)
	filters = {"last_change_detected": (">=", since)}
	regime = as_str(args, "regime")
	if regime:
		filters["regime"] = _regime_or_refuse(regime)
	company = _company_filter(args)
	if company:
		filters["company"] = company

	found = service.rows(filters, limit=as_limit(args), order_by="last_change_detected desc")
	changes = []
	for row in found:
		try:
			full = service.feed_row(row["name"])
		except ValueError:  # pragma: no cover - a row deleted between the query and the read
			continue
		described = _describe(full, with_log=True, log_limit=5)
		changes.append(
			{
				**described,
				"latest_change": next(
					(entry for entry in described["change_log"] if entry["kind"] == "CHANGED"),
					None,
				),
			}
		)

	rules = sorted({rule for change in changes for rule in change.get("affected_rules") or []})
	return ToolResult(
		data={
			"since": since,
			"count": len(changes),
			"changes": changes,
			"rules_to_review": service.rule_ids_for(rules),
			"note": (
				(
					f"{len(changes)} source(s) have moved since {since}, and {len(rules)} rule(s) "
					"were written from them. NONE of those rules was modified: this is a reading "
					"list, not a changelog of your compliance calendar. "
					if changes
					else f"No source has changed since {since}. "
				)
				+ "A source with no rules against it is not less important — it is a source nobody "
				"has written a rule from yet, which is often where a new obligation is hiding. "
				+ _NO_REMEDIATION
			),
		},
		summary=f"{len(changes)} regulation source(s) changed since {since}",
	)
