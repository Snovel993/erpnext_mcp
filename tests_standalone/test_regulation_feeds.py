# SPDX-License-Identifier: MIT
"""The regulation register and its change detector — v0.38.0.

THE CLAIM IS NARROW AND THE TESTS ARE ABOUT THE EDGES OF IT. This site can notice
that a regulation moved. It cannot decide what the regulation now says, and it
must not touch a compliance rule about it. Everything below is an attempt to
break one of those two halves.

`ItDetectsAndItDoesNotRemediate` is the class to read if you only read one. A
sweep that could act on what it found would be a farm's compliance calendar
rewritten at four in the morning off a website redesign, and the assertion that
the rule row is byte-for-byte identical after a detected change is the whole
guarantee, stated as a test rather than as a paragraph.

EIGHT CLAIMS.

 1. `NormalisingIsWhatMakesTheDetectorUsable` — a page that stamps itself with
    the minute it was served does not report a change; a page whose prose changed
    does. Including the cost, asserted rather than only documented: a change that
    is ONLY a date is invisible.

 2. `EveryFetchFailureIsAnErrorAndNotACrash` — timeout, 404, 500, a scheme that
    is not http, and a bench with no `requests`. None of them raises, all of them
    land on the record, and none of them moves `last_checked`.

 3. `TheRegisterCanBeWrittenAndRead` — create, update, list, get, and the six
    refusals that keep a feed from being registered in a state where nothing can
    ever check it.

 4. `TheCheckAndItsFourOutcomes` — baseline, unchanged, changed, error; and
    recovery back to Active.

 5. `TheDueLogicAndTheScheduledSweep` — Daily, Weekly and Monthly honoured
    against the clock, a never-checked feed always due, the sweep never raising,
    and Paused as the only state that keeps a feed out of it.

 6. `TheChangeLogIsAppendOnly` — entries are never edited, the OLDEST are dropped
    at the cap, and the four fields that are the detector's own memory are
    refused as arguments to the update tool.

 7. `ItDetectsAndItDoesNotRemediate` — the rule a change names is untouched, and
    no proposal, alert or supersession happens anywhere.

 8. `TheReadsAnswerTheQuestionTheReleaseIsFor` — what moved since a date, and
    which rules were written from it.
"""

import sys
import types
import unittest

import frappe

from erpnext_mcp.services import regulation_feed as service

from .fixtures import MAIN, OTHER, SeededTestCase
from .harness import STORE

ON = {
	f"allow_{name}": 1
	for name in (
		"create_regulation_feed",
		"update_regulation_feed",
		"list_regulation_feeds",
		"get_regulation_feed",
		"check_regulation_feed",
		"check_all_regulation_feeds",
		"list_regulation_changes",
	)
}

FEED = "OAR 437-004 Agricultural Labor"
URL = "https://osha.oregon.gov/OSHARules/div4/div4.pdf"
DESCRIPTION = "Oregon OSHA division 4: agricultural labour, heat illness prevention and field sanitation."

#: A page of the shape this detector exists for: real prose, wrapped in markup,
#: carrying a served-at timestamp and a build-fingerprinted stylesheet. Every one
#: of those three moving parts changes on a page whose regulation has not.
PAGE = """<!doctype html>
<html><head>
  <link rel="stylesheet" href="/assets/site.a1b2c3d4e5f60718.css">
  <script>var nonce = "9f8e7d6c5b4a39281706";</script>
  <!-- rendered by node 4 -->
</head><body>
  <h1>437-004-1131 Heat Illness Prevention</h1>
  <p>The employer shall provide shade when the heat index equals or exceeds 80 degrees Fahrenheit.</p>
  <footer>Last updated 08/05/2026 14:02:11. &copy; State of Oregon.</footer>
</body></html>"""


def served_again(page: str, at_time: str = "16:47:03", on_date: str = "09/12/2026") -> str:
	"""The same page, served later: new timestamp, new nonce, new asset hash."""
	return (
		page.replace("14:02:11", at_time)
		.replace("08/05/2026", on_date)
		.replace("9f8e7d6c5b4a39281706", "0011223344556677889a")
		.replace("a1b2c3d4e5f60718", "ffeeddccbbaa9988")
	)


class FarEnd:
	"""A stand-in for whatever is at the other end of a feed's URL.

	IT RECORDS EVERY CALL, which is what makes the due-logic assertions real: "a
	Monthly feed checked yesterday is skipped" is only a claim about `is_due` if
	the request it skipped would otherwise have been visible.
	"""

	def __init__(self, body: str = PAGE, status_code: int = 200):
		self.body = body
		self.status_code = status_code
		self.raise_with = None
		self.calls = []

	def get(self, url, timeout=None, headers=None, allow_redirects=None):
		self.calls.append({"url": url, "timeout": timeout, "headers": dict(headers or {})})
		if self.raise_with is not None:
			raise self.raise_with
		return types.SimpleNamespace(status_code=self.status_code, text=self.body)


class FeedTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		self.far = FarEnd()
		self._install_requests(self.far)

	def _install_requests(self, far, present: bool = True):
		"""Put a `requests` module into `sys.modules` that this fake answers.

		`services/regulation_feed.fetch` imports `requests` INSIDE the function —
		so a bench somehow missing it loses change detection and nothing else —
		which means the import has to be satisfied at call time rather than
		patched at module scope. Installing a module object exercises the real
		import statement rather than replacing the function that runs it, and
		`present=False` removes it so the missing-package branch is a real import
		failure and not a mocked one.
		"""
		previous = sys.modules.get("requests")
		if present:
			module = types.ModuleType("requests")
			module.get = far.get
			sys.modules["requests"] = module
		else:
			sys.modules["requests"] = None

		def restore():
			if previous is None:
				sys.modules.pop("requests", None)
			else:
				sys.modules["requests"] = previous

		self.addCleanup(restore)

	# ── fixtures ────────────────────────────────────────────────────────────
	def a_rule(self, rule_id="heat_shade_required", title="Shade required over the heat threshold"):
		doc = frappe.get_doc(
			{
				"doctype": "Compliance Rule",
				"rule_id": rule_id,
				"title": title,
				"category": "Workforce",
				"target_doctype": "Employee",
				"kairotic_gate_description": (
					"Ripe while the heat index is at or above the threshold and no shade was logged."
				),
			}
		).insert(ignore_permissions=True)
		return doc.name

	def a_feed(self, name=FEED, **overrides):
		payload = {"feed_name": name, "url": URL, "description": DESCRIPTION, "regime": "OR-OSHA"}
		payload.update(overrides)
		return self.tool_data("create_regulation_feed", payload)

	def check(self, name=FEED, **arguments):
		return self.tool_data("check_regulation_feed", {"name": name, **arguments})

	def row(self, name=FEED):
		return service.feed_row(name)

	def set_last_checked(self, name, when):
		"""Backdate a feed's last check without going through the detector."""
		frappe.db.set_value(service.DOCTYPE, name, "last_checked", when)
		STORE.commit()


# ── 1 ───────────────────────────────────────────────────────────────────────
class NormalisingIsWhatMakesTheDetectorUsable(FeedTestCase):
	def test_the_same_page_served_a_month_later_is_not_a_change(self):
		"""THE FAILURE THIS WHOLE DESIGN IS SHAPED AROUND. A hash of the bytes
		reports a change on every check of a page that stamps itself, and a
		detector that always fires detects nothing."""
		self.assertEqual(service.content_hash(PAGE), service.content_hash(served_again(PAGE)))

	def test_a_page_whose_prose_changed_is_a_change(self):
		moved = PAGE.replace("80 degrees", "78 degrees")
		self.assertNotEqual(service.content_hash(PAGE), service.content_hash(moved))

	def test_a_change_that_is_only_a_date_is_invisible_and_that_is_the_stated_cost(self):
		"""ASSERTED RATHER THAN ONLY DOCUMENTED. A compliance deadline moved from
		March 1 to April 1, with no other edit, does not change the hash. That is
		the price of not crying wolf daily, and a release that changed its mind
		about the trade should have to change this test on purpose."""
		before = "<p>Applications are due 03/01/2027.</p>"
		after = "<p>Applications are due 04/01/2027.</p>"
		self.assertEqual(service.content_hash(before), service.content_hash(after))

	def test_a_citation_number_is_not_mistaken_for_a_date(self):
		"""`437-004-1131` looks like a date to a careless regex, and a rulebook
		whose every citation was redacted would normalise two different divisions
		to the same string."""
		self.assertIn("437-004-1131", service.normalise("<p>OAR 437-004-1131 applies.</p>"))
		self.assertNotEqual(
			service.content_hash("<p>OAR 437-004-1131 applies.</p>"),
			service.content_hash("<p>OAR 437-004-1140 applies.</p>"),
		)

	def test_script_and_style_blocks_go_whole_rather_than_leaving_their_contents(self):
		normalised = service.normalise(PAGE)
		self.assertNotIn("nonce", normalised)
		self.assertIn("shade", normalised)

	def test_entities_are_unescaped_after_the_tags_come_out(self):
		"""Quoted markup in the body is TEXT the page is showing, and unescaping
		first would turn it into a tag and then throw the quoted text away."""
		self.assertIn("<section>", service.normalise("<p>the tag &lt;section&gt; means</p>"))

	def test_whitespace_and_reflow_are_not_a_change(self):
		reflowed = PAGE.replace("\n", "\n\n  ").replace("<p>", "<p>\n    ")
		self.assertEqual(service.content_hash(PAGE), service.content_hash(reflowed))

	def test_it_is_deterministic_and_touches_nothing(self):
		"""Pure: no clock, no database, no network. The whole value of the hash is
		that the same page normalises to the same string on two sites on two
		days."""
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.assertEqual(service.content_hash(PAGE), service.content_hash(PAGE))
		self.assertEqual(before, {doctype: len(rows) for doctype, rows in STORE.tables.items()})

	def test_an_empty_body_hashes_rather_than_raising(self):
		self.assertTrue(service.content_hash(""))
		self.assertEqual(service.normalise(None), "")


# ── 2 ───────────────────────────────────────────────────────────────────────
class EveryFetchFailureIsAnErrorAndNotACrash(FeedTestCase):
	def setUp(self):
		super().setUp()
		self.a_feed()

	def test_a_timeout_lands_on_the_record_rather_than_raising(self):
		self.far.raise_with = TimeoutError("the read timed out")
		data = self.check()
		self.assertFalse(data["checked"])
		self.assertEqual(data["status"], service.STATUS_ERROR)
		self.assertIn("timed out", data["error"])

	def test_a_404_is_an_error_with_the_status_in_it(self):
		self.far.status_code = 404
		data = self.check()
		self.assertEqual(data["status"], service.STATUS_ERROR)
		self.assertIn("404", data["error"])

	def test_a_500_is_an_error(self):
		self.far.status_code = 500
		self.assertEqual(self.check()["status"], service.STATUS_ERROR)

	def test_a_failed_check_does_not_move_last_checked(self):
		"""So a Monthly feed that failed today is retried tomorrow rather than in
		thirty days. `last_checked` means "last SUCCESSFULLY checked"."""
		self.check()
		checked_at = self.row()["last_checked"]
		self.far.raise_with = OSError("connection refused")
		self.check()
		self.assertEqual(self.row()["last_checked"], checked_at)

	def test_a_url_that_is_not_http_is_refused_before_any_client_is_reached(self):
		content, error = service.fetch("file:///etc/passwd")
		self.assertEqual(content, "")
		self.assertIn("not an http(s) URL", error)
		self.assertEqual(self.far.calls, [])

	def test_a_bench_without_requests_says_so_and_does_not_raise(self):
		self._install_requests(self.far, present=False)
		content, error = service.fetch(URL)
		self.assertEqual(content, "")
		self.assertIn("requests", error)

	def test_the_timeout_is_always_passed_and_is_the_configured_one(self):
		"""A GET with no timeout is a scheduled job that can hang for ever on a
		state website having a bad morning."""
		self.check()
		self.assertEqual(self.far.calls[-1]["timeout"], service.HTTP_TIMEOUT_SECONDS)

	def test_it_names_itself_to_the_far_end(self):
		self.check()
		self.assertIn("erpnext_mcp", self.far.calls[-1]["headers"]["User-Agent"])

	def test_an_enormous_body_is_truncated_rather_than_hashed_whole(self):
		self.far.body = "x" * (service.MAX_CONTENT_BYTES + 5000)
		content, error = service.fetch(URL)
		self.assertEqual(error, "")
		self.assertEqual(len(content), service.MAX_CONTENT_BYTES)

	def test_the_sweep_returns_zero_rather_than_raising_when_everything_fails(self):
		self.far.raise_with = TimeoutError("nothing is up")
		self.assertEqual(service.sweep_due_feeds(), 0)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheRegisterCanBeWrittenAndRead(FeedTestCase):
	def test_a_feed_is_registered_and_not_yet_checked(self):
		data = self.a_feed()
		self.assertEqual(data["name"], FEED)
		self.assertEqual(data["status"], service.STATUS_ACTIVE)
		self.assertEqual(data["check_frequency"], service.FREQUENCY_WEEKLY)
		self.assertIsNone(data["last_checked"])
		self.assertIsNone(data["last_content_hash"])
		self.assertEqual(self.far.calls, [])

	def test_registering_fetches_nothing(self):
		"""A create that reached out would make registering a source a thing an
		operator could not do while the source was down."""
		self.a_feed()
		self.assertEqual(self.far.calls, [])

	def test_a_second_feed_of_one_name_is_refused(self):
		self.a_feed()
		self.assertIn(
			"already a Regulation Feed",
			self.tool_error(
				"create_regulation_feed",
				{
					"feed_name": FEED,
					"url": URL,
					"description": DESCRIPTION,
				},
			),
		)

	def test_a_url_that_is_not_http_is_refused(self):
		error = self.tool_error(
			"create_regulation_feed",
			{
				"feed_name": "Local copy",
				"url": "file:///rules.pdf",
				"description": DESCRIPTION,
			},
		)
		self.assertIn("http(s)", error)

	def test_a_description_nobody_could_act_on_is_refused(self):
		error = self.tool_error(
			"create_regulation_feed",
			{
				"feed_name": "Vague",
				"url": URL,
				"description": "rules",
			},
		)
		self.assertIn("description", error)

	def test_a_regime_is_stored_in_the_one_spelling_the_whole_app_agrees_on(self):
		"""Through `training.canon`, so a source tagged 'oregon osha' is found by
		everything that looks for OR-OSHA. The alias table is the app's own
		decision about which near-misses are unambiguous."""
		data = self.a_feed("Aliased", url="https://example.com/rules", regime="oregon osha")
		self.assertEqual(data["regime"], "OR-OSHA")

	def test_a_regime_the_vocabulary_does_not_hold_is_refused_rather_than_invented(self):
		"""A tag nothing recognises would file this source where nobody preparing
		for any audit looks for it, and a Link to a row that is not there is a
		refusal at save time with a worse sentence on it."""
		error = self.tool_error(
			"create_regulation_feed",
			{
				"feed_name": "Unknown scheme",
				"url": URL,
				"description": DESCRIPTION,
				"regime": "Cascadia Fruit Council",
			},
		)
		self.assertIn("not a regime this site knows", error)

	def test_a_regime_an_operator_added_by_hand_is_accepted(self):
		"""An operation answering to a scheme this app has never modelled adds a
		Compliance Regime row, and a tool that refused it would be telling them
		their own site is wrong."""
		frappe.get_doc({"doctype": "Compliance Regime", "regime_name": "Cascadia Fruit Council"}).insert(
			ignore_permissions=True
		)
		data = self.a_feed("Local scheme", url="https://example.com/rules", regime="Cascadia Fruit Council")
		self.assertEqual(data["regime"], "Cascadia Fruit Council")

	def test_a_rule_link_that_resolves_to_nothing_is_refused(self):
		error = self.tool_error(
			"create_regulation_feed",
			{
				"feed_name": "Dangling",
				"url": URL,
				"description": DESCRIPTION,
				"affected_rules": ["no_such_rule"],
			},
		)
		self.assertIn("does not resolve", error)

	def test_a_rule_can_be_linked_by_rule_id_or_by_docname(self):
		docname = self.a_rule()
		data = self.a_feed(affected_rules=["heat_shade_required"])
		self.assertEqual(data["affected_rules"], [docname])

	def test_the_same_rule_twice_is_deduplicated_rather_than_refused(self):
		docname = self.a_rule()
		data = self.a_feed(affected_rules=[docname, "heat_shade_required"])
		self.assertEqual(data["affected_rules"], [docname])

	def test_status_cannot_be_set_to_error_by_hand(self):
		"""Error is what the LAST CHECK reported. A feed marked Error with nothing
		wrong is a register that lies about itself."""
		self.a_feed()
		error = self.tool_error("update_regulation_feed", {"name": FEED, "status": "Error"})
		self.assertIn("cannot be set to Error", error)

	def test_pausing_is_the_kill_switch_and_keeps_the_log(self):
		self.a_feed()
		self.check()
		self.tool_data("update_regulation_feed", {"name": FEED, "status": "Paused"})
		data = self.tool_data("get_regulation_feed", {"name": FEED})
		self.assertEqual(data["status"], service.STATUS_PAUSED)
		self.assertTrue(data["change_log"])

	def test_a_paused_feed_is_not_fetched(self):
		self.a_feed()
		self.tool_data("update_regulation_feed", {"name": FEED, "status": "Paused"})
		before = len(self.far.calls)
		data = self.check()
		self.assertTrue(data["skipped"])
		self.assertEqual(len(self.far.calls), before)

	def test_force_checks_a_paused_feed(self):
		self.a_feed()
		self.tool_data("update_regulation_feed", {"name": FEED, "status": "Paused"})
		self.assertTrue(self.check(force=True)["checked"])

	def test_an_update_with_nothing_in_it_is_refused(self):
		self.a_feed()
		self.assertIn("nothing to change", self.tool_error("update_regulation_feed", {"name": FEED}))

	def test_changing_the_url_clears_the_hash_and_says_so(self):
		"""A hash taken over one page says nothing about another, and leaving it
		would make the next check report a change of subject as a change of
		content."""
		self.a_feed()
		self.check()
		self.assertTrue(self.row()["last_content_hash"])
		self.tool_data("update_regulation_feed", {"name": FEED, "url": "https://osha.oregon.gov/rules/div4b"})
		self.assertIsNone(self.row()["last_content_hash"])
		self.assertIn("RETARGETED", self.row()["change_log"])

	def test_the_next_check_after_a_retarget_is_a_baseline_and_not_a_change(self):
		self.a_feed()
		self.check()
		self.tool_data("update_regulation_feed", {"name": FEED, "url": "https://osha.oregon.gov/rules/div4b"})
		self.far.body = "<p>an entirely different page</p>"
		data = self.check()
		self.assertTrue(data["baseline"])
		self.assertFalse(data["changed"])

	def test_the_register_lists_what_has_never_been_checked(self):
		"""A source nothing is known about looks exactly like a source that has
		not changed, which is why it is called out separately."""
		self.a_feed()
		self.a_feed("ODA GAP Checklist", url="https://oda.oregon.gov/gap")
		self.check()
		data = self.tool_data("list_regulation_feeds", {})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["never_checked"], ["ODA GAP Checklist"])

	def test_the_register_can_be_narrowed_to_one_regime(self):
		self.a_feed()
		self.a_feed("NOP handbook", url="https://ams.usda.gov/nop", regime="NOP")
		data = self.tool_data("list_regulation_feeds", {"regime": "NOP"})
		self.assertEqual([feed["name"] for feed in data["feeds"]], ["NOP handbook"])

	def test_a_feed_with_no_company_is_still_in_the_register_on_a_single_company_site(self):
		"""`resolve_company("")` INFERS the one company, which is right for a
		create and would be wrong as a filter: the feed nobody scoped would
		vanish from the register on exactly the sites where nobody types a
		company name."""
		self.a_feed()
		self.assertIsNone(self.row()["company"])
		self.assertEqual(self.tool_data("list_regulation_feeds", {})["count"], 1)

	def test_a_named_company_does_filter(self):
		self.a_feed(company=MAIN)
		self.a_feed("Second entity source", url="https://example.com/rules", company=OTHER)
		data = self.tool_data("list_regulation_feeds", {"company": OTHER})
		self.assertEqual([feed["name"] for feed in data["feeds"]], ["Second entity source"])

	def test_a_feed_can_be_found_by_part_of_its_name(self):
		self.a_feed()
		self.assertEqual(self.tool_data("get_regulation_feed", {"name": "437-004"})["name"], FEED)

	def test_a_name_that_matches_nothing_says_where_the_register_is(self):
		self.assertIn("list_regulation_feeds", self.tool_error("get_regulation_feed", {"name": "nothing"}))


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheCheckAndItsFourOutcomes(FeedTestCase):
	def setUp(self):
		super().setUp()
		self.a_feed()

	def test_the_first_check_is_a_baseline_and_cannot_be_a_change(self):
		"""A feed registered today has not moved, and saying it has would put it
		at the top of every 'what changed' list on the site."""
		data = self.check()
		self.assertTrue(data["baseline"])
		self.assertFalse(data["changed"])
		self.assertTrue(data["content_hash"])
		self.assertIsNone(self.row()["last_change_detected"])

	def test_an_unchanged_source_moves_only_last_checked(self):
		self.check()
		hash_before = self.row()["last_content_hash"]
		data = self.check()
		self.assertFalse(data["changed"])
		self.assertEqual(self.row()["last_content_hash"], hash_before)
		self.assertIsNone(self.row()["last_change_detected"])

	def test_a_changed_source_records_the_move(self):
		self.check()
		previous = self.row()["last_content_hash"]
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		data = self.check()
		self.assertTrue(data["changed"])
		self.assertEqual(data["previous_hash"], previous)
		self.assertNotEqual(data["content_hash"], previous)
		self.assertTrue(self.row()["last_change_detected"])

	def test_the_same_page_served_later_is_checked_and_reports_nothing(self):
		"""End to end rather than at the hash: the served-at stamp, the nonce and
		the asset fingerprint all move, and the feed reports no change."""
		self.check()
		self.far.body = served_again(PAGE)
		self.assertFalse(self.check()["changed"])

	def test_a_check_after_an_error_clears_the_status_back_to_active(self):
		"""A source unreachable for an afternoon must not be a source nobody
		checks again."""
		self.far.raise_with = TimeoutError("down")
		self.check()
		self.assertEqual(self.row()["status"], service.STATUS_ERROR)
		self.far.raise_with = None
		self.check()
		self.assertEqual(self.row()["status"], service.STATUS_ACTIVE)
		self.assertIsNone(self.row()["error_message"])

	def test_a_recovery_is_written_into_the_log(self):
		self.check()
		self.far.raise_with = TimeoutError("down")
		self.check()
		self.far.raise_with = None
		self.check()
		self.assertIn("RECOVERED", self.row()["change_log"])

	def test_an_unchanged_check_with_nothing_to_report_writes_no_log_line(self):
		"""Otherwise a weekly feed accumulates a hundred and fifty 'still the
		same' lines a year and the changes are buried in them."""
		self.check()
		before = len(service.parse_change_log(self.row()["change_log"]))
		self.check()
		self.assertEqual(len(service.parse_change_log(self.row()["change_log"])), before)

	def test_checking_twice_in_a_minute_is_the_same_as_checking_once(self):
		"""Which is what makes the cadence a tuning decision rather than a
		correctness one."""
		self.check()
		state = dict(self.row())
		self.check()
		after = dict(self.row())
		for field in ("last_content_hash", "last_change_detected", "change_log", "status"):
			self.assertEqual(state[field], after[field], field)

	def test_the_result_says_what_to_do_next_only_when_something_moved(self):
		self.assertNotIn("next", self.check())
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.assertIn("propose_compliance_rule", self.check()["next"])


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheDueLogicAndTheScheduledSweep(FeedTestCase):
	def test_a_feed_that_has_never_been_checked_is_due(self):
		self.assertTrue(service.is_due({"last_checked": None, "check_frequency": "Monthly"}))

	def test_a_last_checked_this_site_cannot_parse_is_due(self):
		"""A due calculation that failed CLOSED would be a source silently never
		checked, which is the one outcome this module exists to prevent."""
		self.assertTrue(service.is_due({"last_checked": "not a date", "check_frequency": "Daily"}))

	def test_a_check_stamped_in_the_future_is_due(self):
		ahead = frappe.utils.add_days(frappe.utils.now(), 5)
		self.assertTrue(service.is_due({"last_checked": ahead, "check_frequency": "Monthly"}))

	def test_each_frequency_is_honoured_against_the_clock(self):
		for frequency, days, expected in (
			("Daily", 0, False),
			("Daily", 2, True),
			("Weekly", 3, False),
			("Weekly", 9, True),
			("Monthly", 20, False),
			("Monthly", 40, True),
		):
			with self.subTest(frequency=frequency, days_ago=days):
				row = {
					"last_checked": frappe.utils.add_days(frappe.utils.now(), -days),
					"check_frequency": frequency,
				}
				self.assertEqual(service.is_due(row), expected)

	def test_a_daily_feed_checked_at_this_time_yesterday_is_due_today(self):
		"""Without the tolerance a fixed-time cron skips a day in two runs out of
		three, and a 'daily' feed is checked every other day — invisible until
		somebody counts the log lines a year later."""
		row = {
			"last_checked": frappe.utils.add_to_date(frappe.utils.now(), days=-1, seconds=5),
			"check_frequency": "Daily",
		}
		self.assertTrue(service.is_due(row))

	def test_the_sweep_checks_a_due_feed_and_skips_one_that_is_not(self):
		self.a_feed()
		self.a_feed("ODA GAP Checklist", url="https://oda.oregon.gov/gap", check_frequency="Monthly")
		self.check(FEED)
		self.set_last_checked("ODA GAP Checklist", frappe.utils.add_days(frappe.utils.now(), -2))
		before = len(self.far.calls)
		service.sweep_due_feeds()
		self.assertEqual(len(self.far.calls), before, "a feed checked two days ago is not due monthly")

	def test_the_sweep_returns_how_many_sources_moved(self):
		"""How many were checked is bookkeeping; how many regulations moved is the
		only figure anybody acts on."""
		self.a_feed()
		self.a_feed("ODA GAP Checklist", url="https://oda.oregon.gov/gap")
		self.assertEqual(service.sweep_due_feeds(), 0, "two baselines are not two changes")
		self.set_last_checked(FEED, frappe.utils.add_days(frappe.utils.now(), -30))
		self.set_last_checked("ODA GAP Checklist", frappe.utils.add_days(frappe.utils.now(), -30))
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.assertEqual(service.sweep_due_feeds(), 2)

	def test_the_sweep_takes_no_arguments(self):
		"""`scheduler_events` calls it bare on a cron. A job whose signature
		needed an argument would be a TypeError at four in the morning."""
		import inspect

		self.assertEqual(list(inspect.signature(service.sweep_due_feeds).parameters), [])

	def test_the_sweep_returns_zero_on_a_site_without_the_doctype(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard(service.DOCTYPE)
		try:
			self.assertEqual(service.sweep_due_feeds(), 0)
		finally:
			INSTALLED_DOCTYPES.add(service.DOCTYPE)

	def test_a_paused_feed_is_the_only_one_the_sweep_leaves_alone(self):
		self.a_feed()
		self.tool_data("update_regulation_feed", {"name": FEED, "status": "Paused"})
		before = len(self.far.calls)
		service.sweep_due_feeds()
		self.assertEqual(len(self.far.calls), before)

	def test_an_errored_feed_is_retried_by_the_sweep(self):
		"""Error is a report, not a decision. Excluding it would turn one bad
		afternoon into a source nobody ever looks at again."""
		self.a_feed()
		self.far.raise_with = TimeoutError("down")
		self.check()
		self.assertEqual(self.row()["status"], service.STATUS_ERROR)
		self.far.raise_with = None
		before = len(self.far.calls)
		service.sweep_due_feeds()
		self.assertGreater(len(self.far.calls), before)
		self.assertEqual(self.row()["status"], service.STATUS_ACTIVE)

	def test_one_sources_failure_does_not_stop_the_others_being_checked(self):
		self.a_feed()
		self.a_feed("ODA GAP Checklist", url="https://oda.oregon.gov/gap")

		calls = []
		original = self.far.get

		def flaky(url, **kwargs):
			calls.append(url)
			if "osha" in url:
				raise TimeoutError("that host is down")
			return original(url, **kwargs)

		sys.modules["requests"].get = flaky
		service.sweep_due_feeds()
		self.assertEqual(len(calls), 2)
		self.assertEqual(self.row(FEED)["status"], service.STATUS_ERROR)
		self.assertEqual(self.row("ODA GAP Checklist")["status"], service.STATUS_ACTIVE)

	def test_the_manual_sweep_takes_the_same_path_as_the_scheduled_one(self):
		"""A manual sweep with a second implementation is a manual sweep that can
		disagree with the nightly one."""
		self.a_feed()
		data = self.tool_data("check_all_regulation_feeds", {})
		self.assertEqual(data["checked"], 1)
		self.assertEqual(data["changed_count"], 0)
		self.assertTrue(self.row()["last_content_hash"])

	def test_force_checks_a_feed_that_is_not_due(self):
		self.a_feed()
		self.check()
		before = len(self.far.calls)
		self.tool_data("check_all_regulation_feeds", {})
		self.assertEqual(len(self.far.calls), before, "not due, so not checked")
		self.tool_data("check_all_regulation_feeds", {"force": True})
		self.assertGreater(len(self.far.calls), before)


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheChangeLogIsAppendOnly(FeedTestCase):
	def setUp(self):
		super().setUp()
		self.a_feed()

	def test_entries_are_added_and_never_edited(self):
		self.check()
		first = self.row()["change_log"]
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check()
		self.assertTrue(self.row()["change_log"].startswith(first))

	def test_it_is_chronological_on_the_record_and_newest_first_when_read(self):
		self.check()
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check()
		entries = self.tool_data("get_regulation_feed", {"name": FEED})["change_log"]
		self.assertEqual([entry["kind"] for entry in entries], ["CHANGED", "BASELINE"])

	def test_a_changed_entry_carries_both_hashes(self):
		self.check()
		previous = self.row()["last_content_hash"]
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check()
		latest = self.tool_data("get_regulation_feed", {"name": FEED})["change_log"][0]
		self.assertIn(previous[:8], latest["message"])
		self.assertIn(self.row()["last_content_hash"][:8], latest["message"])

	def test_the_oldest_lines_are_dropped_at_the_cap_and_it_says_so(self):
		"""Dropping the oldest rather than refusing the newest: a detector whose
		log filled up and stopped recording is a detector that switched itself
		off."""
		log = "\n".join(
			f"[2026-01-{day:02d} 04:00:00] CHANGED — entry {day} " + "x" * 400 for day in range(1, 60)
		)
		trimmed = service.append_log(log, "[2026-03-01 04:00:00] CHANGED — the newest one")
		self.assertLessEqual(len(trimmed), service.CHANGE_LOG_CAP)
		self.assertIn("log trimmed", trimmed)
		self.assertIn("the newest one", trimmed)
		self.assertNotIn("entry 01 ", trimmed)

	def test_the_four_memory_fields_are_refused_as_arguments(self):
		"""A hash somebody typed is a change that will never be reported, and a
		change log somebody edited is the one record here whose entire value is
		that nobody edited it."""
		self.check()
		for field, value in (
			("last_content_hash", "0" * 64),
			("last_checked", "2026-01-01 00:00:00"),
			("last_change_detected", "2026-01-01 00:00:00"),
			("change_log", "nothing ever happened"),
		):
			with self.subTest(field=field):
				error = self.tool_error("update_regulation_feed", {"name": FEED, field: value})
				self.assertIn(field, error)
				self.assertIn("Nothing was changed", error)

	def test_a_refused_update_changes_nothing(self):
		self.check()
		before = dict(self.row())
		self.tool_error("update_regulation_feed", {"name": FEED, "change_log": "wiped"})
		self.assertEqual(dict(self.row()), before)

	def test_a_line_the_parser_does_not_recognise_is_kept_rather_than_dropped(self):
		entries = service.parse_change_log(
			"a line from some other version\n[2026-01-01 00:00:00] CHANGED — x"
		)
		self.assertEqual(len(entries), 2)
		self.assertEqual(entries[-1]["message"], "a line from some other version")


# ── 7 ───────────────────────────────────────────────────────────────────────
class ItDetectsAndItDoesNotRemediate(FeedTestCase):
	"""THE CLASS THIS RELEASE IS ACTUALLY ABOUT.

	A sweep that could act on what it found would be a farm's compliance calendar
	rewritten at four in the morning off a website redesign. Every assertion here
	is that the detector's reach stops at its own doctype.
	"""

	def setUp(self):
		super().setUp()
		self.rule = self.a_rule()
		self.a_feed(affected_rules=[self.rule])
		self.check()

	def _rule_row(self):
		return dict(frappe.get_doc("Compliance Rule", self.rule).as_dict())

	def test_a_detected_change_leaves_the_rule_exactly_as_it_was(self):
		before = self._rule_row()
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.assertTrue(self.check()["changed"])
		self.assertEqual(self._rule_row(), before)

	def test_the_sweep_leaves_the_rule_exactly_as_it_was(self):
		before = self._rule_row()
		self.set_last_checked(FEED, frappe.utils.add_days(frappe.utils.now(), -30))
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.assertEqual(service.sweep_due_feeds(), 1)
		self.assertEqual(self._rule_row(), before)

	def test_a_change_writes_no_rule_no_alert_and_no_proposal(self):
		"""The only table that grows is the feed's own, plus the audit row every
		tool call writes."""
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check()
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		for doctype in ("Compliance Rule", "Compliance Alert", "Inspection Template", "Farm Task"):
			self.assertEqual(before.get(doctype, 0), after.get(doctype, 0), doctype)

	def test_the_change_names_the_rule_so_a_person_knows_where_to_look(self):
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		data = self.check()
		self.assertIn("heat_shade_required", data["change_log"][0]["message"])
		self.assertIn("NOT modified", data["change_log"][0]["message"])

	def test_a_source_with_no_rules_says_so_rather_than_saying_nothing(self):
		self.a_feed("ODA GAP Checklist", url="https://oda.oregon.gov/gap")
		self.check("ODA GAP Checklist")
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		data = self.check("ODA GAP Checklist")
		self.assertIn("No Compliance Rule", data["change_log"][0]["message"])

	def test_every_mutating_tool_says_the_separation_out_loud(self):
		"""Restated at the moment a caller is holding a change rather than only in
		a docstring the caller never reads."""
		self.assertIn("propose_compliance_rule", self.check()["note"])
		self.assertIn(
			"propose_compliance_rule", self.tool_data("check_all_regulation_feeds", {"force": True})["note"]
		)

	def test_a_deleted_rule_does_not_make_the_log_entry_unwritable(self):
		"""A docname in the log is less useful than a rule_id and infinitely more
		useful than a traceback."""
		frappe.delete_doc("Compliance Rule", self.rule, ignore_permissions=True)
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.assertTrue(self.check()["changed"])


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheReadsAnswerTheQuestionTheReleaseIsFor(FeedTestCase):
	def setUp(self):
		super().setUp()
		self.rule = self.a_rule()
		self.a_feed(affected_rules=[self.rule])
		self.a_feed("ODA GAP Checklist", url="https://oda.oregon.gov/gap", regime="GAP")
		self.check(FEED)
		self.check("ODA GAP Checklist")

	def test_nothing_has_moved_before_anything_moves(self):
		self.assertEqual(self.tool_data("list_regulation_changes", {})["count"], 0)

	def test_a_moved_source_appears_with_the_rules_written_from_it(self):
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check(FEED)
		data = self.tool_data("list_regulation_changes", {})
		self.assertEqual([change["name"] for change in data["changes"]], [FEED])
		self.assertEqual(data["rules_to_review"], [f"heat_shade_required ({self.rule})"])

	def test_it_is_a_reading_list_and_says_so(self):
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check(FEED)
		self.assertIn("reading list", self.tool_data("list_regulation_changes", {})["note"])

	def test_a_change_before_the_window_is_not_in_it(self):
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check(FEED)
		tomorrow = str(frappe.utils.add_days(frappe.utils.today(), 1))
		self.assertEqual(self.tool_data("list_regulation_changes", {"since": tomorrow})["count"], 0)

	def test_it_can_be_narrowed_to_one_regime(self):
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check(FEED)
		self.check("ODA GAP Checklist")
		self.assertEqual(
			[
				change["name"]
				for change in self.tool_data("list_regulation_changes", {"regime": "GAP"})["changes"]
			],
			["ODA GAP Checklist"],
		)

	def test_each_change_carries_its_own_latest_entry(self):
		self.far.body = PAGE.replace("80 degrees", "78 degrees")
		self.check(FEED)
		change = self.tool_data("list_regulation_changes", {})["changes"][0]
		self.assertEqual(change["latest_change"]["kind"], "CHANGED")

	def test_the_reads_write_nothing(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("list_regulation_feeds", {})
		self.tool_data("get_regulation_feed", {"name": FEED})
		self.tool_data("list_regulation_changes", {})
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_the_three_reads_ship_on_and_the_four_writes_ship_off(self):
		"""The register is readable out of the box; nothing REACHES OUT of the box.
		Two of the four writes make outbound requests to servers this operation
		does not own, which is a decision an operator makes rather than inherits."""
		self.configure(enabled=1)
		for name in ("list_regulation_feeds", "get_regulation_feed", "list_regulation_changes"):
			with self.subTest(read=name):
				self.tool_data(name, {"name": FEED})
		for name in (
			"create_regulation_feed",
			"update_regulation_feed",
			"check_regulation_feed",
			"check_all_regulation_feeds",
		):
			with self.subTest(write=name):
				error = self.tool_error(name, {"name": FEED})
				self.assertIn("switched off", error)
				self.assertIn(f"allow_{name}", error)

	def test_a_switched_off_check_makes_no_outbound_request(self):
		"""The switch is checked before the tool runs, so an operator who has not
		ticked it has not merely disabled an answer — nothing on this site talks
		to anybody."""
		self.configure(enabled=1)
		before = len(self.far.calls)
		self.tool_error("check_all_regulation_feeds", {})
		self.assertEqual(len(self.far.calls), before)


if __name__ == "__main__":  # pragma: no cover
	unittest.main()
