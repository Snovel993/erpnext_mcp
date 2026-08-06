# SPDX-License-Identifier: MIT
"""Change detection over the regulations this operation answers to. v0.38.0.

v0.22.0 MADE A COMPLIANCE RULE A RECORD. v0.37.0 LET A MODEL DRAFT ONE FROM
REGULATION TEXT AND A PERSON APPROVE IT. Both of those are about the moment a
rule is written, and neither has anything to say six months later, when OR-OSHA
renumbers a subsection, the ODA reissues its GAP checklist, or a certifier
publishes a handbook with a new water-testing interval in it. What happened
between those releases and this one is that the rules got easy to write and
stayed exactly as hard to keep current, which is the half that decays quietly.

This module is the missing pointer: a URL, a hash of what was there last time,
and a job that looks.

────────────────────────────────────────────────────────────────────────────
IT DETECTS. IT DOES NOT REMEDIATE. THAT LINE IS THE DESIGN
────────────────────────────────────────────────────────────────────────────

A changed page is evidence that somebody should read a regulation again. It is
NOT evidence about what the regulation now says, and it is emphatically not
authority to rewrite a rule that is firing on somebody's compliance calendar.
The failure mode of the other design is specific and bad: a page reflows, a
scraper reads a threshold out of the new layout wrong, a rule is silently
updated, and an operation spends a season inspecting to a number nobody chose.

So a detected change writes four things — a hash, a timestamp, a log line, and
the rule_ids of every rule derived from this source — and stops. What happens
next is a person reading the source, and, if a rule genuinely needs to change,
`propose_compliance_rule` drafting the replacement and `approve_compliance_rule`
putting a name on it. The same gate as every other rule change, unchanged.

Nothing here calls a model, for the same reason nothing in `proposals.py` does:
the AI is the MCP client. This service says "this source moved"; reading what
moved is the client's job, and approving anything about it is a person's.

────────────────────────────────────────────────────────────────────────────
WHY THE HASH IS OF NORMALISED TEXT AND NOT OF THE BYTES
────────────────────────────────────────────────────────────────────────────

Because a detector that always fires detects nothing. A government rulebook page
carries a "Last updated 08/05/2026 14:02" stamp, a session nonce in a hidden
input, a build hash on its stylesheet link, and a copyright year — and a hash of
the bytes reports a change on every single check, for ever. The first week
somebody reads those alerts; the second week nobody does; the month the rule
actually changes, the alert is indistinguishable from the noise it has been
buried in since it was switched on.

So `normalise` throws away: scripts, styles, HTML comments, every tag, entity
escapes, ISO and US and month-name dates, clock times, long hex strings, and all
whitespace differences. What is left is the readable prose, and a hash of that
changes when the prose changes.

THE COST IS STATED RATHER THAN HIDDEN, because it is real: **a change that is
ONLY a date is invisible to this detector.** A compliance deadline moved from
March 1 to April 1, with no other edit anywhere on the page, does not change the
hash. That is the price of not crying wolf daily, and the mitigation is the
`description` field — a feed whose whole content is a date is a feed to describe
as such and to read on its own schedule rather than to trust a hash about.

────────────────────────────────────────────────────────────────────────────
WHAT THIS MODULE MAY AND MAY NOT DO
────────────────────────────────────────────────────────────────────────────

IT WRITES ONLY `Regulation Feed`. Not Compliance Rule, not Compliance Alert, not
anything belonging to another app. The seven-job promise in `hooks.py` holds.

IT NEVER RAISES. `sweep_due_feeds` runs on somebody's scheduler beside their real
work, and it talks to servers this app does not control — a state agency's
rulebook host, a certifier's WordPress. A timeout is the ordinary case, not the
exception, and one unreachable source is one unreachable source: the other
eleven feeds are still checked.

IT IS SAFE TO RUN AT ANY CADENCE. Every run is a full comparison against stored
state rather than an increment, so running it twice in an hour produces exactly
what running it once does: the second run finds the hash it just wrote and
reports no change. `is_due` is what keeps it from being rude to the far end.
"""

from __future__ import annotations

import hashlib
import html
import re

import frappe

from .. import compat

DOCTYPE = "Regulation Feed"
RULE_DOCTYPE = "Compliance Rule"

STATUS_ACTIVE = "Active"
STATUS_PAUSED = "Paused"
STATUS_ERROR = "Error"
STATUSES = (STATUS_ACTIVE, STATUS_PAUSED, STATUS_ERROR)

FREQUENCY_DAILY = "Daily"
FREQUENCY_WEEKLY = "Weekly"
FREQUENCY_MONTHLY = "Monthly"

#: How old a successful check has to be before the sweep asks again. Monthly is
#: thirty days rather than a calendar month on purpose: a calendar month is four
#: different numbers and none of them is what "check this about as often as the
#: certifier changes it" means.
FREQUENCY_DAYS = {
	FREQUENCY_DAILY: 1,
	FREQUENCY_WEEKLY: 7,
	FREQUENCY_MONTHLY: 30,
}

#: Slack on the due calculation, so a daily feed checked at 03:00:07 yesterday is
#: due at 03:00:02 today. Without it a fixed-time cron skips a day in two runs out
#: of three, and a "daily" feed is checked every other day — the kind of bug that
#: is invisible until somebody counts the log lines a year later.
DUE_TOLERANCE_SECONDS = 3600

#: One outbound GET, with a ceiling. Not a setting: the only thing an operator
#: would tune here is patience with a slow state website, and twenty seconds is
#: already past the point where the answer is "that host is down".
HTTP_TIMEOUT_SECONDS = 20

#: What this app calls itself to somebody else's server. A named agent is what
#: lets a webmaster who sees the requests work out who to ask, which is the
#: courtesy owed to a public server nobody is paying for.
USER_AGENT = "erpnext_mcp regulation-feed-check (compliance change detection)"

#: Most content one check will hash. A rulebook division is tens of kilobytes; a
#: whole-title PDF can be tens of megabytes, and a feed pointed at one is a feed
#: pointed at the wrong page. Truncation is reported rather than silent.
MAX_CONTENT_BYTES = 2_000_000

#: Most feeds one sweep will check. A register larger than this is not a register.
SWEEP_CAP = 200

#: How long the change log is allowed to get before the OLDEST lines are dropped.
#: Dropping the oldest rather than refusing the newest, because a detector whose
#: log filled up and stopped recording is a detector that switched itself off.
CHANGE_LOG_CAP = 20000

#: How much of a fetch failure is worth keeping. A traceback-length error message
#: in a Small Text field is a message nobody reads.
ERROR_CAP = 500


# ── normalising ─────────────────────────────────────────────────────────────
#: Removed whole — content, tags and all. A script block is a page's behaviour
#: rather than its text, and it carries the nonces and build stamps that change
#: on every deploy of a site whose regulation has not moved in four years.
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<[^<>]*>", re.DOTALL)

#: Everything below is replaced with a single space rather than deleted, so
#: removing a timestamp cannot glue the words either side of it together and
#: manufacture a change out of a normalisation.
_REDACTIONS = (
	# ISO-8601, with or without a time: 2026-08-05, 2026-08-05T14:02:11Z.
	re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"),
	# US-style: 8/5/2026, 08-05-26. Anchored so an OAR citation survives —
	# `437-004-1131` has three digits in its first group and does not match.
	re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
	# Month-name dates, both orders: August 5, 2026 and 5 Aug. 2026.
	re.compile(
		r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
		re.IGNORECASE,
	),
	re.compile(
		r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b",
		re.IGNORECASE,
	),
	# Clock times, with or without a meridiem: 14:02, 2:02:11 pm.
	re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?"),
	# Session ids, CSRF nonces, asset fingerprints. Sixteen hex characters is far
	# past anything a regulation numbers itself with.
	re.compile(r"\b[0-9a-fA-F]{16,}\b"),
)

_WHITESPACE = re.compile(r"\s+")


def normalise(content: str) -> str:
	"""The readable prose of a page, with everything that moves on its own removed.

	Deterministic and pure — no clock, no database, no network — because the whole
	value of the hash is that the same page normalises to the same string on two
	different days on two different sites.
	"""
	text = str(content or "")
	if not text:
		return ""
	text = _SCRIPT_STYLE.sub(" ", text)
	text = _COMMENT.sub(" ", text)
	text = _TAG.sub(" ", text)
	# AFTER the tags come out, not before: `&lt;div&gt;` in the body is text that
	# the page is quoting, and unescaping first would turn it into a tag and then
	# throw the quoted text away.
	text = html.unescape(text)
	# Written as escapes rather than as literals: a non-breaking space and a
	# zero-width space are invisible in a source file, and a reader who could not
	# see them would delete one by accident.
	text = text.replace("\xa0", " ").replace("\u200b", "")
	for pattern in _REDACTIONS:
		text = pattern.sub(" ", text)
	return _WHITESPACE.sub(" ", text).strip()


def content_hash(content: str) -> str:
	"""SHA-256 of the normalised text. The empty page hashes to the empty string's."""
	return hashlib.sha256(normalise(content).encode("utf-8")).hexdigest()


def short_hash(digest: str) -> str:
	"""The first eight characters, which is what a log line and a summary carry."""
	return str(digest or "")[:8] or "—"


# ── the HTTP layer ──────────────────────────────────────────────────────────
def _log(message: str) -> None:
	"""Say what went wrong, without ever being the thing that goes wrong.

	`frappe.log_error` is a database write, which is a thing that can fail on the
	site where this is already failing. Same wrapper, same reasoning, as
	`services/weather.py`.
	"""
	try:
		frappe.log_error(title="erpnext_mcp: regulation feed", message=message)
	except Exception:  # pragma: no cover - a site that cannot write its own Error Log
		pass


def fetch(url: str, timeout: int = 0) -> tuple:
	"""One GET. Returns `(content, error)` — exactly one of which is meaningful.

	NEVER RAISES. Every branch is a real thing a state agency's website has done
	to somebody: a connection that hangs until the timeout, a 404 after a site
	redesign moved the rulebook, a 403 from a WAF that decided this looked like a
	scraper, a 500, and a 200 carrying a login page because the source went behind
	a subscription.

	IT DOES NOT INTERPRET THE BODY. A PDF's bytes decoded as text are not prose,
	and hashing them is still a perfectly good change detector — the same PDF
	gives the same hash and a reissued one does not. What it costs is that the
	`description` field is the only human-readable account of what a PDF feed
	covers, which is true of every feed anyway.
	"""
	url = str(url or "").strip()
	if not url.lower().startswith(("http://", "https://")):
		return "", (
			f"{url!r} is not an http(s) URL, so nothing was fetched. The DocType refuses one of "
			"these on save; a row that predates that check is refused here."
		)
	try:
		import requests
	except Exception:  # pragma: no cover - Frappe depends on requests
		return "", "the `requests` package is not importable, so no source can be checked."

	try:
		response = requests.get(
			url,
			timeout=int(timeout or HTTP_TIMEOUT_SECONDS),
			headers={"User-Agent": USER_AGENT},
			allow_redirects=True,
		)
	except Exception as exc:
		return "", f"{type(exc).__name__}: {exc}"

	status = int(getattr(response, "status_code", 0) or 0)
	if status >= 400:
		body = str(getattr(response, "text", "") or "")[:200]
		return "", f"the source answered HTTP {status}." + (f" It said: {body}" if body else "")

	try:
		content = str(getattr(response, "text", "") or "")
	except Exception as exc:
		return "", f"the response body could not be read as text: {type(exc).__name__}: {exc}"

	if len(content) > MAX_CONTENT_BYTES:
		content = content[:MAX_CONTENT_BYTES]
	return content, ""


# ── reading the register ────────────────────────────────────────────────────
def feed_row(name: str) -> dict:
	"""One feed as a plain dict, with its rule links resolved. Raises ValueError."""
	try:
		doc = frappe.get_doc(DOCTYPE, name)
	except Exception:
		raise ValueError(f"no {DOCTYPE} named {name!r} on this site.") from None
	return _row_of(doc)


def rule_of(row) -> str:
	"""The `rule` on one `affected_rules` child row, however Frappe handed it over.

	A saved child row is a Document and a row appended in this request may still
	be a plain dict, and both arrive here — `getattr` alone silently answers ""
	for the dict, which is how a feed with three rules attached reports none.
	"""
	if isinstance(row, dict):
		return str(row.get("rule") or "").strip()
	return str(getattr(row, "rule", "") or "").strip()


def _row_of(doc) -> dict:
	return {
		"name": doc.name,
		"feed_name": doc.get("feed_name"),
		"url": doc.get("url"),
		"regime": doc.get("regime"),
		"company": doc.get("company"),
		"description": doc.get("description"),
		"check_frequency": doc.get("check_frequency"),
		"status": doc.get("status"),
		"last_checked": str(doc.get("last_checked") or "") or None,
		"last_content_hash": doc.get("last_content_hash") or None,
		"last_change_detected": str(doc.get("last_change_detected") or "") or None,
		"error_message": doc.get("error_message") or None,
		"affected_rules": [
			rule for rule in (rule_of(row) for row in (doc.get("affected_rules") or [])) if rule
		],
		"change_log": doc.get("change_log") or "",
	}


def rows(filters: dict | None = None, limit: int = 100, order_by: str = "modified desc") -> list:
	"""Feeds matching `filters`, as dicts. Empty list on a site without the doctype."""
	if not compat.doctype_exists(DOCTYPE):
		return []
	return frappe.db.get_all(
		DOCTYPE,
		filters=filters or {},
		fields=[
			"name",
			"feed_name",
			"url",
			"regime",
			"company",
			"description",
			"check_frequency",
			"status",
			"last_checked",
			"last_content_hash",
			"last_change_detected",
			"error_message",
		],
		limit=min(int(limit or 100), SWEEP_CAP),
		order_by=order_by,
	)


def rule_ids_for(rule_names) -> list:
	"""`rule_id` for each Compliance Rule docname, falling back to the docname.

	The fallback matters: a rule that has been deleted, or a site whose Compliance
	Rule doctype has not migrated, must not make the change log entry unwritable.
	A docname in the log is less useful than a rule_id and infinitely more useful
	than a traceback.
	"""
	out = []
	for name in rule_names or []:
		label = str(name)
		try:
			rule_id = frappe.db.get_value(RULE_DOCTYPE, name, "rule_id")
			if rule_id:
				label = f"{rule_id} ({name})"
		except Exception:
			pass
		out.append(label)
	return out


def frequency_days(check_frequency: str) -> int:
	return FREQUENCY_DAYS.get(
		str(check_frequency or "").strip() or FREQUENCY_WEEKLY, FREQUENCY_DAYS[FREQUENCY_WEEKLY]
	)


def is_due(row: dict, now: str = "") -> bool:
	"""Whether this feed's last successful check is older than its own frequency.

	THE CRON CANNOT BE CHANGED FROM A FORM. A Frappe cron expression is a static
	string in `hooks.py`, so the sweep runs daily on every site whatever a feed
	says — and `check_frequency` is honoured HERE, by skipping a feed checked
	recently enough. Which means Monthly works exactly as an operator expects and
	an hourly frequency, if the Select ever grew one, would change nothing: the
	sweep cannot run more often than it is scheduled. The same ceiling-and-floor
	arrangement `Weather Settings.fetch_interval_minutes` has.

	A FEED THAT HAS NEVER BEEN CHECKED IS ALWAYS DUE, and so is one whose
	`last_checked` this site cannot parse — a due calculation that failed closed
	would be a source silently never checked, which is the one outcome this whole
	module exists to prevent.
	"""
	last = str(row.get("last_checked") or "").strip()
	if not last:
		return True
	try:
		age = float(frappe.utils.time_diff_in_seconds(now or frappe.utils.now(), last))
	except Exception:
		return True
	# A check stamped in the future is a clock disagreement, not a reason to stop
	# looking at a regulation for a month.
	if age < 0:
		return True
	return age >= (frequency_days(row.get("check_frequency")) * 86400) - DUE_TOLERANCE_SECONDS


def due_feeds(company: str = "", force: bool = False) -> list:
	"""Every feed the sweep would check now: not Paused, and due — or all, if forced.

	AN ERRORED FEED IS INCLUDED. `Error` is what the last check reported, not a
	decision anybody made, and a source that was unreachable for an afternoon must
	not be a source nobody checks again. `Paused` is the decision, and it is the
	only state that keeps a feed out of this list.
	"""
	filters = {"status": ("!=", STATUS_PAUSED)}
	if company:
		filters["company"] = company
	now = frappe.utils.now()
	return [
		row
		for row in rows(filters, limit=SWEEP_CAP, order_by="last_checked asc")
		if force or is_due(row, now)
	]


# ── the change log ──────────────────────────────────────────────────────────
def append_log(existing: str, line: str) -> str:
	"""Append one line, trimming the OLDEST if the log has reached its cap."""
	text = (str(existing or "").rstrip() + "\n" + line).strip()
	if len(text) <= CHANGE_LOG_CAP:
		return text
	lines = text.split("\n")
	dropped = 0
	while lines and len("\n".join(lines)) > CHANGE_LOG_CAP - 120:
		lines.pop(0)
		dropped += 1
	notice = (
		f"[log trimmed] {dropped} older entrie(s) were dropped to keep this field under "
		f"{CHANGE_LOG_CAP} characters. Nothing below has been edited."
	)
	return "\n".join([notice, *lines])


def parse_change_log(text: str, limit: int = 0) -> list:
	"""The log as a list of `{timestamp, kind, message}`, newest first.

	Chronological on the record and newest-first here, deliberately: append-only
	is a property of how it is WRITTEN, and 'what happened to this source most
	recently' is the question every reader of it is asking.
	"""
	entries = []
	for line in str(text or "").splitlines():
		line = line.strip()
		if not line:
			continue
		match = re.match(r"^\[([^\]]*)\]\s*([A-Z]+)?\s*(?:—\s*)?(.*)$", line)
		if match:
			entries.append(
				{
					"timestamp": match.group(1),
					"kind": match.group(2) or "NOTE",
					"message": match.group(3).strip(),
				}
			)
		else:
			entries.append({"timestamp": None, "kind": "NOTE", "message": line})
	entries.reverse()
	return entries[: int(limit)] if limit else entries


# ── the check ───────────────────────────────────────────────────────────────
def check_feed(name: str, force: bool = False) -> dict:
	"""Fetch one source, compare it to the stored hash, and record what happened.

	THE ONE FUNCTION THAT WRITES ANYTHING IN THIS MODULE, and it writes exactly
	one document: the feed it was handed. Four outcomes, and each of them lands on
	the record so the register can be read without re-fetching anything.

	  * BASELINE — a feed with no stored hash. The first check cannot be a change,
	    because there is nothing to have changed from. The hash is recorded and
	    `last_change_detected` is deliberately left empty: a feed registered today
	    has not moved, and saying it has would put it at the top of every "what
	    changed" list on the site.
	  * UNCHANGED — the hash matches. `last_checked` moves and nothing else does.
	  * CHANGED — the hash differs. The new hash, the timestamp, and a log line
	    naming every rule derived from this source. NOTHING ELSE HAPPENS: no rule
	    is edited, no alert is raised, nothing is proposed. See the module
	    docstring.
	  * ERROR — the fetch failed. Status and message on the record, a log line,
	    and `last_checked` DELIBERATELY NOT MOVED, so a monthly feed that failed
	    today is retried tomorrow rather than in thirty days.

	It raises only if the document cannot be loaded or cannot be saved. Callers
	that run unattended — `sweep_due_feeds` — catch even that.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	row = _row_of(doc)
	stamp = frappe.utils.now()

	if str(doc.get("status") or "") == STATUS_PAUSED and not force:
		return {
			**row,
			"checked": False,
			"changed": False,
			"baseline": False,
			"skipped": True,
			"skipped_reason": "paused",
			"error": None,
			"summary": f"{doc.name} is Paused, so it was not fetched. Nothing was changed.",
		}

	content, error = fetch(str(doc.get("url") or ""))

	if error:
		message = str(error)[:ERROR_CAP]
		doc.status = STATUS_ERROR
		doc.error_message = message
		doc.change_log = append_log(doc.get("change_log"), f"[{stamp}] ERROR — {message}")
		doc.save(ignore_permissions=True)
		return {
			**_row_of(doc),
			"checked": False,
			"changed": False,
			"baseline": False,
			"skipped": False,
			"error": message,
			"summary": (
				f"{doc.name} could not be checked: {message} `last_checked` was NOT moved, so the "
				"next sweep tries again rather than waiting out this feed's whole frequency."
			),
		}

	previous = str(doc.get("last_content_hash") or "").strip()
	# Normalised ONCE and measured from the same string that is hashed, so the
	# character count in a log line can never describe text other than the text
	# the hash was taken over.
	normalised = normalise(content)
	digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
	length = len(normalised)
	recovered = str(doc.get("status") or "") == STATUS_ERROR

	doc.status = STATUS_ACTIVE
	doc.error_message = None
	doc.last_checked = stamp
	doc.last_content_hash = digest

	if not previous:
		kind = "BASELINE"
		line = (
			f"[{stamp}] BASELINE — first check. Hash {short_hash(digest)} recorded over "
			f"{length:,} characters of normalised text. Nothing to compare against, so this is "
			"not a change."
		)
	elif digest == previous:
		kind = "UNCHANGED"
		line = (
			f"[{stamp}] RECOVERED — the source answered again; content unchanged at {short_hash(digest)}."
			if recovered
			else ""
		)
	else:
		kind = "CHANGED"
		doc.last_change_detected = stamp
		affected = rule_ids_for(row["affected_rules"])
		line = (
			f"[{stamp}] CHANGED — {short_hash(previous)} → {short_hash(digest)}, "
			f"{length:,} characters of normalised text. "
			+ (
				f"Rules derived from this source: {', '.join(affected)}. They are NOT modified by "
				"this — read the source and use propose_compliance_rule if one needs to change."
				if affected
				else "No Compliance Rule on this site records this source as its origin."
			)
		)

	if line:
		doc.change_log = append_log(doc.get("change_log"), line)
	doc.save(ignore_permissions=True)

	after = _row_of(doc)
	changed = kind == "CHANGED"
	return {
		**after,
		"checked": True,
		"changed": changed,
		"baseline": kind == "BASELINE",
		"skipped": False,
		"recovered": recovered,
		"previous_hash": previous or None,
		"content_hash": digest,
		"content_length": length,
		"error": None,
		"affected_rule_ids": rule_ids_for(after["affected_rules"]),
		"summary": _summary(after, kind, previous, digest, length),
	}


def _summary(row: dict, kind: str, previous: str, digest: str, length: int) -> str:
	if kind == "BASELINE":
		return (
			f"{row['name']}: baseline recorded ({short_hash(digest)}, {length:,} characters). A "
			"first check cannot be a change; the next one can."
		)
	if kind == "CHANGED":
		count = len(row.get("affected_rules") or [])
		return (
			f"{row['name']}: CONTENT CHANGED ({short_hash(previous)} → {short_hash(digest)}). "
			+ (
				f"{count} rule(s) were written from this source and NONE of them was modified. "
				if count
				else "No rule on this site names this source. "
			)
			+ "Read the regulation and, if a rule needs to change, propose_compliance_rule drafts "
			"the replacement for a person to approve."
		)
	return f"{row['name']}: unchanged ({short_hash(digest)})."


def check_due_feeds(company: str = "", force: bool = False) -> dict:
	"""Check every feed that is due, and report what each one did.

	SHARED BY THE TOOL AND THE SCHEDULED JOB, deliberately: a manual sweep that
	took a second code path could disagree with the nightly one, which is the
	single property a manual run of a scheduled job must not have.

	One feed's failure is one feed's failure. A source that has moved behind a
	login does not stop the other eleven being checked, and it does not raise.
	"""
	results = []
	for row in due_feeds(company=company, force=force):
		try:
			results.append(check_feed(row["name"], force=force))
		except Exception as exc:
			_log(f"regulation feed sweep skipped {row.get('name')}: {type(exc).__name__}: {exc}")
			results.append(
				{
					"name": row.get("name"),
					"feed_name": row.get("feed_name"),
					"checked": False,
					"changed": False,
					"baseline": False,
					"skipped": False,
					"error": f"{type(exc).__name__}: {exc}",
					"summary": f"{row.get('name')} could not be checked and the sweep carried on.",
				}
			)
	return {
		"checked": sum(1 for result in results if result.get("checked")),
		"changed": [result for result in results if result.get("changed")],
		"errors": [result for result in results if result.get("error")],
		"results": results,
	}


def sweep_due_feeds() -> int:
	"""The scheduled job. Returns how many sources were found to have CHANGED.

	NEVER RAISES AND TAKES NO ARGUMENTS. `scheduler_events` calls it bare on a
	cron, on somebody's bench, beside their real work — a job whose signature
	needed an argument would be a TypeError at three in the morning, and a job
	that raised would take the tick down for every other job in it.

	THE RETURN VALUE IS THE NUMBER WORTH WAKING UP TO. How many feeds were checked
	is bookkeeping; how many regulations moved is the only figure anybody acts on,
	and it is almost always zero, which is what a healthy register looks like.

	IT IS SAFE TO RUN AT ANY CADENCE. The second run of an hour finds the hash the
	first one wrote and reports no change, and `is_due` keeps it from being rude to
	a public server nobody is paying for.
	"""
	try:
		if not compat.doctype_exists(DOCTYPE):
			return 0
		report = check_due_feeds()
		if report["errors"]:
			_log(
				f"{len(report['errors'])} regulation feed(s) could not be checked: "
				+ "; ".join(f"{result.get('name')}: {result.get('error')}" for result in report["errors"])
			)
		return len(report["changed"])
	except Exception:
		_log(f"the regulation feed sweep failed before it started: {compat.traceback_text()}")
		return 0
