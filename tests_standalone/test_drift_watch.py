# SPDX-License-Identifier: MIT
"""The weekly Journal Entry drift watch — v0.17.1.

ACC-JV-2026-00073 was a Journal Entry whose voucher named one party and whose GL
rows named another. v0.13.0's `update_journal_entry_party` looked its GL rows up
by the wrong column, matched none, wrote the voucher and reported success.
v0.14.0 shipped the scan and the repair. **Neither of them fixes the thing that
actually went wrong, which is that nothing complained for a week.**

This is the smoke alarm, and these are the four things that have to be true of a
smoke alarm.

1. `ItFindsAndReports` — a clean ledger is silent and a drifted one is not. An
   alarm that goes off every week is an alarm somebody unplugs.
2. `ItNeverRepairs` — it does not call the repair tool, ever. Rewriting GL rows
   on submitted accounting documents on a timer, with nobody watching, would be
   a worse bug than the one it watches for, applied to a year of ledger before
   anybody read the email.
3. `AFindingIsNeverLost` — no outgoing email account is an ordinary state, and
   losing the one message that says the ledger disagrees with itself because
   SMTP was not configured would reproduce the original bug's defining property
   exactly. It falls back to the Error Log.
4. `ItNeverTakesTheSchedulerDown` — it runs beside somebody's real work. An
   accounting watchdog that raised would have caused more damage than the drift.
"""

import frappe

from erpnext_mcp import drift

from .fixtures import V12TestCase
from .harness import STORE, set_roles

MANAGER = "manager@example.test"


class DriftWatchTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		# The role goes on the User's own `roles` child table, which is where a
		# real site keeps it and where `frappe.db.get_all("Has Role", ...)` reads
		# it from. Seeding a standalone "Has Role" table would be inventing a
		# storage shape Frappe does not have, and the query would find nothing.
		STORE.seed(
			"User",
			[
				{
					"name": MANAGER,
					"enabled": 1,
					"full_name": "A Manager",
					"roles": [{"role": "System Manager"}],
				}
			],
		)
		set_roles(MANAGER, ["System Manager"])

	def report(self, count=2):
		return {
			"drifted_entry_count": count,
			"drifted_line_count": count * 2,
			"drifted_entries": [f"ACC-JV-2026-{index:05d}" for index in range(count)],
			"entries_scanned": 40,
			"window_from": "2025-06-27",
			"window_to": "2026-08-01",
			"truncated": False,
		}


# ── 1 ───────────────────────────────────────────────────────────────────────
class ItFindsAndReports(DriftWatchTestCase):
	def test_a_clean_ledger_sends_nothing(self):
		"""An alarm that goes off every week is an alarm somebody unplugs."""
		self.assertEqual(drift.scan(), 0)
		self.assertEqual(STORE.emails, [])

	def test_the_scan_reads_a_trailing_window_and_says_which_one(self):
		found = drift.collect()
		self.assertEqual(found["window_to"], str(frappe.utils.today()))
		self.assertEqual(
			found["window_from"],
			str(frappe.utils.add_days(frappe.utils.today(), -drift.WINDOW_DAYS)),
		)

	def test_a_finding_names_the_entries_and_the_window_it_covered(self):
		drift.notify(self.report())
		message = STORE.emails[0]["message"]
		self.assertIn("ACC-JV-2026-00000", message)
		self.assertIn("2026-08-01", message)
		self.assertIn("2 Journal Entry", message)

	def test_a_capped_scan_says_so_instead_of_implying_a_clean_bill_of_health(self):
		"""A scan that stopped at its limit and reported nothing wrong would be
		describing the entries it reached as though they were the ledger."""
		drift.notify({**self.report(), "truncated": True})
		self.assertIn("HIT ITS CAP", STORE.emails[0]["message"])
		self.assertIn("not a clean bill of health", STORE.emails[0]["message"])

	def test_a_long_list_is_trimmed_and_admits_to_being_trimmed(self):
		drift.notify(self.report(count=45))
		message = STORE.emails[0]["message"]
		self.assertIn("and 25 more", message)

	def test_it_goes_to_the_system_managers_when_nobody_is_configured(self):
		drift.notify(self.report())
		self.assertIn(MANAGER, STORE.emails[0]["recipients"])

	def test_a_configured_address_wins_and_may_be_a_list(self):
		self.configure(enabled=1, drift_report_email="books@example.test, tim@example.test")
		drift.notify(self.report())
		self.assertEqual(STORE.emails[0]["recipients"], ["books@example.test", "tim@example.test"])

	def test_administrator_is_not_emailed_as_a_person(self):
		"""It is a shared login, not somebody's inbox."""
		self.assertNotIn("Administrator", drift.recipients())

	def test_a_disabled_manager_is_not_emailed(self):
		frappe.db.set_value("User", MANAGER, "enabled", 0)
		self.assertNotIn(MANAGER, drift.recipients())


# ── 2 ───────────────────────────────────────────────────────────────────────
class ItNeverRepairs(DriftWatchTestCase):
	def test_the_module_does_not_reference_the_repair_tool_at_all(self):
		"""Asserted against the SOURCE, not against behaviour, because the whole
		claim is that there is no code path — reachable or otherwise — from a
		timer to a rewrite of submitted GL rows."""
		import inspect

		source = inspect.getsource(drift)
		self.assertNotIn("repair_drifted_je_attributions(", source)
		self.assertNotIn("mutate.", source)

	def test_a_scan_that_finds_drift_writes_nothing_at_all(self):
		before = {doctype: len(STORE.rows(doctype)) for doctype in ("Journal Entry", "GL Entry")}
		drift.scan()
		for doctype, count in before.items():
			self.assertEqual(len(STORE.rows(doctype)), count, doctype)

	def test_the_report_tells_a_human_which_tools_to_run_and_in_what_order(self):
		drift.notify(self.report())
		message = STORE.emails[0]["message"]
		self.assertIn("Nothing has been changed", message)
		self.assertIn("find_drifted_je_attributions", message)
		self.assertIn("repair_drifted_je_attributions", message)
		self.assertLess(
			message.index("find_drifted_je_attributions"),
			message.index("repair_drifted_je_attributions"),
			"the report should name the scan before the repair",
		)


# ── 3 ───────────────────────────────────────────────────────────────────────
class AFindingIsNeverLost(DriftWatchTestCase):
	def test_no_mail_account_falls_back_to_the_error_log(self):
		STORE.mail_fails = True
		self.assertFalse(drift.notify(self.report()))
		self.assertTrue(STORE.errors)
		self.assertIn("drift", STORE.errors[-1]["title"].lower())
		self.assertIn("ACC-JV-2026-00000", STORE.errors[-1]["message"])

	def test_no_recipients_at_all_still_lands_in_the_error_log(self):
		frappe.db.set_value("User", MANAGER, "enabled", 0)
		self.assertFalse(drift.notify(self.report()))
		self.assertTrue(STORE.errors)

	def test_a_successful_send_does_not_also_spam_the_error_log(self):
		before = len(STORE.errors)
		self.assertTrue(drift.notify(self.report()))
		self.assertEqual(len(STORE.errors), before)


# ── 4 ───────────────────────────────────────────────────────────────────────
class ItNeverTakesTheSchedulerDown(DriftWatchTestCase):
	def test_it_takes_no_arguments(self):
		"""`scheduler_events` calls it bare. A job whose signature needs an
		argument raises TypeError on its first tick, on somebody's site."""
		import inspect

		self.assertEqual(list(inspect.signature(drift.scan).parameters), [])

	def test_a_scan_that_throws_is_swallowed_and_logged(self):
		from erpnext_mcp.tools import read

		original = read.find_drifted_je_attributions
		try:
			read.find_drifted_je_attributions = lambda args: 1 / 0

			self.assertEqual(drift.scan(), 0)
		finally:
			read.find_drifted_je_attributions = original
		self.assertTrue(any("drift watch failed" in row["title"] for row in STORE.errors))

	def test_a_notify_that_throws_is_swallowed_too(self):
		original = drift.notify
		try:
			drift.notify = lambda report: 1 / 0
			drift.collect = drift.collect
			self.assertEqual(drift.scan(), 0)
		finally:
			drift.notify = original

	def test_it_returns_what_it_found_so_a_stopped_job_is_noticeable(self):
		"""A job that reports nothing is a job nobody can tell has stopped."""
		self.assertIsInstance(drift.scan(), int)
