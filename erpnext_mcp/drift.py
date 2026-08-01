# SPDX-License-Identifier: MIT
"""The weekly watch for Journal Entries whose voucher and ledger disagree.

v0.17.1. `find_drifted_je_attributions` and `repair_drifted_je_attributions`
shipped in v0.14.0, in response to ACC-JV-2026-00073 — a Journal Entry whose
voucher named one party and whose GL rows named another, where nothing in either
table admitted to the disagreement. v0.13.0's `update_journal_entry_party` looked
its GL rows up by the wrong column, wrote the voucher, matched zero ledger rows,
and reported success.

**The tools were the fix. This is the smoke alarm.** A damage class whose entire
character is that nothing complains about it is a damage class that needs
somebody to go and look, and "somebody remembers to run a scan" is not a control.

────────────────────────────────────────────────────────────────────────────
IT REPORTS. IT DOES NOT REPAIR, AND IT NEVER WILL FROM HERE.
────────────────────────────────────────────────────────────────────────────

`repair_drifted_je_attributions` exists, works, and is deliberately NOT called by
this job. Repairing drift rewrites GL rows on submitted accounting documents, and
doing that on a timer with nobody watching is the single worst thing this app
could be persuaded to do — a repair that got it wrong would be indistinguishable
from the bug it was fixing, and would have been applied to a year of ledger
before anybody read the email.

So the job's whole output is a sentence saying where to look and which tool to
run. A human decides, in daylight, with the report in front of them.

────────────────────────────────────────────────────────────────────────────
WHY IT STILL HONOURS THE "TWO SCHEDULED JOBS" DOCTRINE
────────────────────────────────────────────────────────────────────────────

`hooks.py` argues that every scheduled job must write only this app's own
doctypes and must never raise. This one writes NOTHING AT ALL — it is three
queries and, at most, an email — so it clears the higher bar rather than the
stated one. That is the reason it survived the argument the API-key sweeper had
to have separately.

────────────────────────────────────────────────────────────────────────────
THE WINDOW, AND WHY IT IS NOT "EVERYTHING"
────────────────────────────────────────────────────────────────────────────

The scan takes a date range and caps at `DRIFT_SCAN_CAP` entries. A job that
asked for all history would silently truncate at the cap and report a clean bill
of health for the years it never reached — which is worse than not running,
because it produces a record saying somebody checked.

So the job scans a TRAILING WINDOW it can actually cover, says in the report
exactly which dates it covered, and names the tool for a full-history pass. A
report that states its own boundary can be trusted at its boundary; one that
does not cannot be trusted anywhere.
"""

from __future__ import annotations

import frappe

from . import settings
from .compat import traceback_text

#: How far back each weekly pass looks. A fiscal year plus a month, so an entry
#: posted in the first week of a year is still being watched when that year is
#: closed and reopened for adjustments.
WINDOW_DAYS = 400

#: Who hears about it when nobody is configured. System Managers are the people
#: who can act on a ledger finding, and asking the site who they are beats
#: asking an operator to type an address they will change jobs away from.
FALLBACK_ROLE = "System Manager"

SUBJECT = "ERPNext MCP: Journal Entry attribution drift found"


def scan() -> int:
	"""The scheduler's entry point. Returns the number of drifted entries found.

	NEVER RAISES, like every other job in this app: it runs on somebody's
	scheduler beside their real work, and an accounting watchdog that took the
	site's scheduler down would have caused more damage than the drift it watches
	for. A return value because a job that reports nothing is a job nobody can
	tell has stopped working.
	"""
	try:
		report = collect()
		if not report or not report.get("drifted_entry_count"):
			return 0
		notify(report)
		return int(report["drifted_entry_count"])
	except Exception:
		try:
			frappe.log_error(title="erpnext_mcp: the JE drift watch failed", message=traceback_text())
		except Exception:
			pass
		return 0


def collect() -> dict:
	"""Run the scan over the trailing window. Read-only, and returns the finding."""
	from .tools import read

	today = str(frappe.utils.today())
	from_date = str(frappe.utils.add_days(today, -WINDOW_DAYS))
	result = read.find_drifted_je_attributions(
		{"from_date": from_date, "to_date": today, "limit": read.DRIFT_SCAN_CAP}
	)
	data = dict(result.data)
	data["window_from"] = from_date
	data["window_to"] = today
	# THE CAP HAS TO BE REPORTED, not merely respected. A scan that stopped at
	# five hundred entries and said "0 drifted" would be describing the entries it
	# reached as though they were the ledger.
	data["truncated"] = int(data.get("entries_scanned") or 0) >= read.DRIFT_SCAN_CAP
	return data


def recipients() -> list:
	"""Where the report goes. The configured address, else the System Managers."""
	configured = settings.drift_report_email()
	if configured:
		return [part.strip() for part in configured.replace(";", ",").split(",") if part.strip()]
	try:
		rows = frappe.db.get_all(
			"Has Role",
			filters={"role": FALLBACK_ROLE, "parenttype": "User"},
			pluck="parent",
			limit=50,
		)
	except Exception:  # pragma: no cover
		return []
	out = []
	for user in rows or []:
		if user in ("Administrator", "Guest"):
			continue
		if frappe.db.get_value("User", user, "enabled"):
			out.append(str(user))
	return out


def notify(report: dict) -> bool:
	"""Send the report. Falls back to the Error Log when there is no mail account.

	A finding that could not be emailed must not evaporate. A site with no
	outgoing email account is an ordinary state — and losing the one message that
	says the ledger disagrees with itself, because SMTP was not set up, would
	reproduce the original bug's defining property exactly.
	"""
	body = compose(report)
	to = recipients()
	if to:
		try:
			frappe.sendmail(recipients=to, subject=SUBJECT, message=body, now=False)
			return True
		except Exception:
			pass
	try:
		frappe.log_error(title=SUBJECT, message=body)
	except Exception:  # pragma: no cover
		pass
	return False


def compose(report: dict) -> str:
	"""The message. Says what was found, where it looked, and what to run next."""
	entries = report.get("drifted_entries") or []
	shown = entries[:20]
	lines = [
		f"<p><b>{report.get('drifted_entry_count')} Journal Entry/Entries</b> have a voucher and a "
		"ledger that disagree about a party. Every ageing report, party ledger and statement of "
		"account reads the ledger; the entry shows the voucher.</p>",
		f"<p>Scanned {report.get('entries_scanned')} submitted entries posted between "
		f"<b>{report.get('window_from')}</b> and <b>{report.get('window_to')}</b>, "
		f"covering {report.get('drifted_line_count')} drifted line(s).</p>",
	]
	if report.get("truncated"):
		lines.append(
			"<p><b>THIS SCAN HIT ITS CAP.</b> It stopped at the entry limit, so entries older than "
			"the ones listed were not examined and this is not a clean bill of health for them. "
			"Run find_drifted_je_attributions by hand with a narrower date range to cover the rest."
			"</p>"
		)
	if shown:
		lines.append("<p>" + "<br>".join(frappe.utils.escape_html(str(name)) for name in shown) + "</p>")
	if len(entries) > len(shown):
		lines.append(f"<p>…and {len(entries) - len(shown)} more.</p>")
	lines.append(
		"<p><b>Nothing has been changed.</b> This watch reports and never repairs — rewriting GL "
		"rows on submitted accounting documents on a timer, with nobody watching, is not something "
		"this app does. Run <code>find_drifted_je_attributions</code> to see the detail and "
		"<code>repair_drifted_je_attributions</code> to fix it, in that order, with the report in "
		"front of you.</p>"
	)
	return "".join(lines)
