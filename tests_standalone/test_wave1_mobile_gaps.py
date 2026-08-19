# SPDX-License-Identifier: MIT
"""Wave 1 — the seven server gaps the iOS app shipped code against.

Every one of these was found from the handset side and written up in
`fafo_ios/SERVER_CHANGES.md`. They have almost nothing in common as features
and one thing in common as failures: each is a place where the server and the
app disagreed about a name, a value or a shape, and the disagreement was
invisible from either side alone. So the tests here are deliberately written
against the ARGUMENT the phone sends rather than against the tool's own
vocabulary — a test that called `resolve_company("X")` directly would have
passed on every day `get_break_policy` was answering HTTP 500.

SEVEN CLAIMS, in the order the wave was worked.

1. `TheBreakPolicyAnswers` — item 13. `get_break_policy` with a company in the
   body returns a policy instead of raising AttributeError. THE NEGATIVE
   CONTROL IS THE TEST: `_resolve_company_signature` asserts the call the fix
   replaced would still blow up, so a future refactor that reintroduces it
   fails here rather than in an orchard.

2. `TheSecondIsTheResolution` — item 8. A crew member joining in the same
   second the shift started is on the crew. Asserted with a microsecond start
   and a whole-second join, which is exactly the pair the handset produces.

3. `AWaterBreakIsAWaterBreak` — item 9. `Water Break` and `Shade Break` are
   loggable, land as their own event types, and are counted apart from
   `Cool-Down` while sharing its cool-down clock.

4. `TheCloseTakesEitherSpelling` — item 1. `end_shift` accepts `farm_shift`,
   accepts `shift`, and refuses a body that carries both differently.

5. `ThePhaseSurvivesTheUpload` — item 6. `phase` round-trips through both
   normalisers, unset stays legal, and a phase does not change the completion
   signature — which is what stops it breaking offline replay.

6. `TheTaskNamesItsTemplate` — item 7. `shape.task` carries `template` and the
   checklist, and carries neither on a task raised by hand.

7. `AWarningCanCarryAPhotograph` — item 10. `create_discipline_record` files
   evidence against the record, a file already spoken for is reported rather
   than stolen, and the read side is gated on the HR role.
"""

import inspect
import json

import frappe

from erpnext_mcp import breaks as breaks_mod
from erpnext_mcp import compat, completions, roles, shifts
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.api import shape
from erpnext_mcp.args import resolve_company, select_options
from erpnext_mcp.farmops_api import routes as farmops_routes
from erpnext_mcp.tools import discipline as discipline_tools
from erpnext_mcp.tools import inspections
from erpnext_mcp.tools import shifts as shift_tools

from .fixtures import MAIN, V12TestCase, install_hrms
from .harness import ROLES, STORE

FOREMAN = "HR-EMP-00001"
WORKER = "HR-EMP-00002"
SIGNATURE = "/files/wave1-signature.png"

ON = {
	f"allow_{name}": 1
	for name in (
		"start_shift",
		"add_worker_to_shift",
		"remove_worker_from_shift",
		"end_shift",
		"cancel_shift",
		"log_shift_event",
		"log_shift_break",
		"end_shift_break",
		"get_break_policy",
		"get_shift",
		"list_shifts",
		"create_incident_record",
		"get_incident_record",
	)
}


class Wave1TestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		roles.install_roles()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	# -- furniture -----------------------------------------------------------
	def a_policy(self, work_state="OR", **overrides):
		row = {
			"name": "LBP-OR-2026",
			"policy_id": "LBP-OR-2026",
			"work_state": work_state,
			"enabled": 1,
			"effective_from": "2026-01-01",
			"human_approved_by": "ada@example.test",
			"regulation_citations": "OAR 437-004-1131",
			"max_hours_without_rest": 4,
			"rest_schedule": [
				{"hours_from": 2, "hours_to": 6, "periods_owed": 1, "minutes_each": 10, "paid": 1}
			],
			"meal_schedule": [
				{"hours_from": 6, "hours_to": 12, "periods_owed": 1, "minutes_each": 30, "paid": 0}
			],
			"heat_schedule": [
				{
					"heat_index_from": 90,
					"heat_index_to": 200,
					"minutes_each": 10,
					"every_hours": 2,
					"concurrent_with_rest": 0,
				}
			],
		}
		row.update(overrides)
		STORE.seed("Labor Break Policy", [row])
		return row["name"]

	def a_shift(self, **overrides):
		payload = {
			"foreman": FOREMAN,
			"location": "Block 7 North",
			"shift_type": "Harvest",
			"start_datetime": f"{frappe.utils.today()} 06:00:00",
			"crew_employees": [WORKER],
		}
		payload.update(overrides)
		return self.tool_data("start_shift", payload)


# ── 1. item 13: get_break_policy answered HTTP 500 for every call ───────────
class TheBreakPolicyAnswers(Wave1TestCase):
	"""`get_break_policy` crashed on the one argument the handset always sends.

	The endpoint read `resolve_company(args, actor)`, which is not that
	function's signature — it takes `(company, required)`. So the args DICT went
	in where a docname belongs, `(company or "").strip()` met a dict, and every
	call with a non-empty body raised AttributeError before it reached a query.
	A body with nothing in it survived, because an empty dict is falsy; that is
	why the crash was not caught by the tests that enumerate the surface.
	"""

	def test_a_company_in_the_body_returns_a_policy_rather_than_raising(self):
		self.a_policy()
		data = self.tool_data("get_break_policy", {"company": MAIN})
		self.assertEqual(data["policy"], "LBP-OR-2026")
		self.assertEqual(data["work_state"], "OR")
		self.assertTrue(data["approved"])

	def test_the_schedules_the_handsets_break_coach_counts_from_are_all_there(self):
		self.a_policy()
		data = self.tool_data("get_break_policy", {"company": MAIN})
		self.assertEqual(len(data["rest_schedule"]), 1)
		self.assertEqual(data["rest_schedule"][0]["minutes_each"], 10)
		self.assertEqual(len(data["meal_schedule"]), 1)
		self.assertEqual(len(data["heat_schedule"]), 1)
		self.assertEqual(data["max_hours_without_rest"], 4)

	def test_the_company_asked_about_is_echoed_back(self):
		self.a_policy()
		self.assertEqual(self.tool_data("get_break_policy", {"company": MAIN})["company"], MAIN)

	def test_a_work_state_with_no_policy_says_so_instead_of_failing(self):
		self.a_policy(work_state="OR")
		data = self.tool_data("get_break_policy", {"company": MAIN, "work_state": "WA"})
		self.assertIsNone(data["policy"])
		self.assertIn("No enabled break policy", data["note"])

	def test_a_company_that_is_not_on_this_site_is_named_rather_than_crashed_on(self):
		self.a_policy()
		message = self.tool_error("get_break_policy", {"company": "Nowhere Orchards LLC"})
		self.assertIn("Nowhere Orchards LLC", message)
		self.assertIn("Known companies", message)

	def test_the_negative_control_the_old_call_still_raises_AttributeError(self):
		"""The bug, reproduced against the real `resolve_company`.

		Without this the fix is only asserted by its absence, and "the endpoint
		works now" is a claim any rewrite satisfies. This pins the actual
		mechanism: a dict where a docname belongs still explodes, so a future
		edit that hands one over fails here.
		"""
		with self.assertRaises(AttributeError):
			resolve_company({"company": MAIN}, "HR-EMP-00001")

	def test_the_endpoint_resolves_the_company_off_the_named_argument(self):
		"""The shape of the call, not just its result.

		`resolve_company(args, actor)` and `resolve_company(as_str(args,
		"company"))` both read as "resolve the company" at a glance, and only
		one of them is a function call this module has. Pinning the second is
		what makes the negative control above mean something.
		"""
		source = inspect.getsource(shift_tools.get_break_policy)
		body = source.split('"""', 2)[-1]
		calls = [line.strip() for line in body.splitlines() if "resolve_company(" in line]
		calls = [line for line in calls if not line.startswith("#")]
		self.assertEqual(calls, ['company = resolve_company(as_str(args, "company"), required=False)'])


# ── 2. item 8: the crew-leader scan, refused on sub-second rounding ─────────
class TheSecondIsTheResolution(Wave1TestCase):
	"""A foreman who starts a shift and scans their own badge is on the crew.

	`Farm Shift` compared `joined_at` against `start_datetime` as STRINGS.
	`"…17:01:04"` is a PREFIX of `"…17:01:04.560880"` and therefore sorts before
	it, so the same instant read as 0.56 of a second early and the join was
	refused. The handset sends whole seconds — `yyyy-MM-dd HH:mm:ss` — and
	Frappe stores microseconds, so the two are never the same width.
	"""

	def test_joining_in_the_same_second_the_shift_started_is_allowed(self):
		start = f"{frappe.utils.today()} 17:01:04.560880"
		shift = self.a_shift(start_datetime=start, crew_employees=[])["name"]
		data = self.tool_data(
			"add_worker_to_shift",
			{"shift": shift, "employee": FOREMAN, "joined_at": f"{frappe.utils.today()} 17:01:04"},
		)
		self.assertEqual(data["added"]["employee"], FOREMAN)

	def test_a_join_genuinely_before_the_shift_is_still_refused(self):
		"""The negative control. The guard is narrowed, not removed.

		An hour early is the transposition this check exists to catch, and it
		has to keep failing — otherwise the fix reads as "the rule was deleted",
		which is a different change with the same green suite.
		"""
		start = f"{frappe.utils.today()} 17:01:04.560880"
		shift = self.a_shift(start_datetime=start, crew_employees=[])["name"]
		message = self.tool_error(
			"add_worker_to_shift",
			{"shift": shift, "employee": FOREMAN, "joined_at": f"{frappe.utils.today()} 16:01:04"},
		)
		self.assertIn("before the shift started", message)

	def test_closing_a_shift_in_the_second_it_started_is_allowed(self):
		start = f"{frappe.utils.today()} 17:01:04.560880"
		shift = self.a_shift(start_datetime=start)["name"]
		data = self.close(shift, end_datetime=f"{frappe.utils.today()} 17:01:04")
		self.assertEqual(data["status"], "Closed")

	def test_a_close_genuinely_before_the_start_is_still_refused(self):
		start = f"{frappe.utils.today()} 17:01:04.560880"
		shift = self.a_shift(start_datetime=start)["name"]
		message = self.tool_error(
			"end_shift",
			{
				"shift": shift,
				"end_datetime": f"{frappe.utils.today()} 09:00:00",
				"supervisor_signature_file_token": SIGNATURE,
			},
		)
		self.assertIn("finished before it began", message)

	def test_the_truncation_itself_keeps_anything_that_is_not_a_timestamp(self):
		self.assertEqual(shifts.to_the_second("2026-08-18 17:01:04.560880"), "2026-08-18 17:01:04")
		self.assertEqual(shifts.to_the_second("2026-08-18 17:01:04"), "2026-08-18 17:01:04")
		self.assertEqual(shifts.to_the_second(""), "")
		self.assertEqual(shifts.to_the_second(None), "")
		self.assertEqual(shifts.to_the_second("not a timestamp"), "not a timestamp")

	def close(self, shift, **overrides):
		payload = {
			"shift": shift,
			"end_datetime": f"{frappe.utils.today()} 15:00:00",
			"supervisor_signature_file_token": SIGNATURE,
		}
		payload.update(overrides)
		return self.tool_data("end_shift", payload)


# ── 3. item 9: break_kind had no value for water or shade ──────────────────
class AWaterBreakIsAWaterBreak(Wave1TestCase):
	"""The two break kinds the heat rules are written about were the two refused.

	"Recorded locally" on the handset means the break never reached the farm.
	Under OAR 437-004-1131 and WAC 296-307-097 the break log IS the evidence
	that heat relief was provided, so the log was silently failing to be created
	for exactly the events an inspector opens it to find.
	"""

	def test_a_water_break_files(self):
		shift = self.a_shift()["name"]
		data = self.tool_data(
			"log_shift_break",
			{"shift": shift, "break_kind": "Water Break", "duration_minutes": 10},
		)
		self.assertEqual(data["logged"]["break_kind"], "Water Break")

	def test_a_shade_break_files(self):
		shift = self.a_shift()["name"]
		data = self.tool_data(
			"log_shift_break",
			{"shift": shift, "break_kind": "Shade Break", "duration_minutes": 10},
		)
		self.assertEqual(data["logged"]["break_kind"], "Shade Break")

	def test_each_lands_as_its_own_event_type_rather_than_as_a_cool_down(self):
		"""The distinction is the whole point, so it has to reach the timeline.

		An inspector after a heat event asks whether SHADE was provided, not
		whether a cool-down was. Folding both onto `Cool-Down` — which is what
		the app is doing as a workaround today — loses the only column that
		could answer.
		"""
		shift = self.a_shift()["name"]
		self.tool_data("log_shift_break", {"shift": shift, "break_kind": "Water Break"})
		self.tool_data("log_shift_break", {"shift": shift, "break_kind": "Shade Break"})
		# THROUGH THE PARENT, NOT BY FILTERING ON `parent`. A child doctype
		# queried by its parent column answers on a bench and answers with
		# nothing in this harness, which is a green test that proves the
		# opposite of what it says.
		kinds = {
			str(row.get("event_type") or "")
			for row in (STORE.get_raw("Farm Shift", shift) or {}).get("compliance_events") or []
		}
		self.assertIn("Water Break", kinds)
		self.assertIn("Shade Break", kinds)
		self.assertNotIn("Cool-Down", kinds)

	def test_both_are_paid_because_payroll_has_no_quarrel_with_the_regulation(self):
		self.assertTrue(shift_tools.BREAK_KINDS["Water Break"]["paid"])
		self.assertTrue(shift_tools.BREAK_KINDS["Shade Break"]["paid"])

	def test_the_doctype_itself_offers_them(self):
		options = select_options("Farm Shift Compliance Event", "break_kind")
		self.assertIn("Water Break", options)
		self.assertIn("Shade Break", options)
		# The three that were already there are untouched.
		self.assertIn("Paid Rest", options)
		self.assertIn("Unpaid Meal", options)
		self.assertIn("Cool-Down", options)

	def test_an_invented_kind_is_still_refused_with_the_list(self):
		shift = self.a_shift()["name"]
		message = self.tool_error("log_shift_break", {"shift": shift, "break_kind": "Tea Break"})
		self.assertIn("Water Break", message)
		self.assertIn("Shade Break", message)

	def test_all_three_heat_kinds_share_one_cool_down_clock(self):
		"""Three records on the timeline, one countdown.

		A crew sent to the shade at the top of the hour has had its relief for
		that hour whichever word the foreman tapped — counting the three apart
		here would tell a crew that had just come out of the shade that a
		cool-down was overdue.
		"""
		self.assertEqual(
			set(breaks_mod.HEAT_RELIEF_KINDS), {"Cool-Down", "Water Break", "Shade Break"}
		)


# ── 4. item 1: the close-out, refused on a parameter name ──────────────────
class TheCloseTakesEitherSpelling(Wave1TestCase):
	"""`routes.bind` reduces a body to the keys the method DECLARES.

	So an undeclared `farm_shift` was dropped before any guard saw it, and the
	close came back saying the argument was required while it sat in the body.
	`farm_shift` is the spelling the dispatch surface already uses and the
	column the Farm Task and the Attendance row actually carry.
	"""

	def test_the_method_declares_both_names(self):
		accepted = farmops_routes.accepted_arguments(mobile_api.end_shift)
		self.assertIn("shift", accepted)
		self.assertIn("farm_shift", accepted)

	def test_bind_no_longer_drops_it(self):
		"""The mechanism, at the layer that was doing the dropping."""
		route = next(
			r for r in farmops_routes.ROUTES if r.handler is mobile_api.end_shift
		)
		bound = farmops_routes.bind(route, {"farm_shift": "SHIFT-2026-0009", "_auth": "x"})
		self.assertEqual(bound, {"farm_shift": "SHIFT-2026-0009"})

	def test_the_negative_control_a_method_that_declares_neither_still_drops_it(self):
		"""Proof that `bind` is what was doing it, rather than something else.

		If this passed for every method, the test above would be asserting a
		property of the harness rather than of the fix.
		"""
		route = next(r for r in farmops_routes.ROUTES if r.handler is mobile_api.get_break_policy)
		self.assertEqual(farmops_routes.bind(route, {"farm_shift": "SHIFT-2026-0009"}), {})

	def test_the_two_spellings_disagreeing_is_refused_rather_than_guessed(self):
		"""Attendance for a whole crew is written off this call.

		One of the two names a shift that is not being closed and nothing in the
		body says which, so guessing is not available.
		"""
		with self.assertRaises(Exception) as caught:
			mobile_api._one_spelling("SHIFT-A", "SHIFT-B", "shift", "farm_shift")
		self.assertIn("two spellings of one argument", str(caught.exception))

	def test_either_alone_resolves_to_that_value(self):
		self.assertEqual(
			mobile_api._one_spelling("SHIFT-A", None, "shift", "farm_shift"), ("SHIFT-A", "shift")
		)
		self.assertEqual(
			mobile_api._one_spelling(None, "SHIFT-B", "shift", "farm_shift"),
			("SHIFT-B", "farm_shift"),
		)


# ── 5. item 6: before and after, as data rather than as a filename ─────────
class ThePhaseSurvivesTheUpload(Wave1TestCase):
	"""The phase travelled in the filename because there was nowhere else.

	`FT-…_photo_before_….jpg` is readable in the Desk and queryable nowhere. The
	app would not send an unrecognised fifth key because a strict validator
	refusing the whole completion would lose the photographs — and a completion
	is the one submission on this surface a worker cannot redo.
	"""

	def a_file(self, docname="FILE-EV-1", url="/private/files/before.jpg"):
		STORE.seed("File", [{"name": docname, "file_url": url, "file_name": "before.jpg"}])
		return docname

	def test_the_column_exists_and_offers_the_two_values_the_handset_sends(self):
		options = select_options("Farm Task Evidence", "phase")
		self.assertIn("before", options)
		self.assertIn("after", options)

	def test_unset_stays_legal_and_emits_no_key_at_all(self):
		"""Most completions have no before frame and never will."""
		self.a_file()
		row = inspections.normalise_evidence([{"file": "FILE-EV-1", "kind": "photo"}])[0]
		self.assertNotIn("phase", row)

	def test_a_phase_the_handset_sent_reaches_the_row(self):
		self.a_file()
		row = inspections.normalise_evidence(
			[{"file_token": "FILE-EV-1", "kind": "photo", "phase": "before"}]
		)[0]
		self.assertEqual(row["phase"], "before")

	def test_a_title_cased_phase_is_accepted_the_way_a_title_cased_kind_is(self):
		self.a_file()
		row = inspections.normalise_evidence([{"file": "FILE-EV-1", "phase": "After"}])[0]
		self.assertEqual(row["phase"], "after")

	def test_an_invented_phase_is_refused_by_the_tool_with_the_doctypes_own_list(self):
		self.a_file()
		with self.assertRaises(Exception) as caught:
			inspections.normalise_evidence([{"file": "FILE-EV-1", "phase": "during"}])
		self.assertIn("during", str(caught.exception))

	def test_the_mobile_normaliser_carries_it_through(self):
		rows = mobile_api._evidence(
			[{"file_token": "FILE-EV-1", "file_name": "a.jpg", "kind": "photo", "phase": "before"}]
		)
		self.assertEqual(rows[0]["phase"], "before")

	def test_the_mobile_normaliser_drops_a_bad_one_rather_than_losing_the_completion(self):
		"""The same leniency an unknown `kind` already gets two lines above it.

		Losing a label is recoverable from the filename; losing the photographs
		is not, and the strict refusal is still available on the MCP tool.
		"""
		rows = mobile_api._evidence([{"file_token": "FILE-EV-1", "kind": "photo", "phase": "sideways"}])
		self.assertNotIn("phase", rows[0])
		self.assertEqual(rows[0]["file"], "FILE-EV-1")

	def test_a_phase_does_not_change_the_completion_signature(self):
		"""Which is what stops it breaking offline replay.

		`completions._digest` hashes file references and nothing else, so a
		queued completion signed before this release still matches after it. If
		that ever stops being true, every phone with an unsent completion files
		it twice.
		"""
		plain = [{"file": "FILE-EV-1", "evidence_type": "Photo"}]
		phased = [{"file": "FILE-EV-1", "evidence_type": "Photo", "phase": "before"}]
		self.assertEqual(
			completions.signature("A-1", "EMP-1", plain, "", "", ""),
			completions.signature("A-1", "EMP-1", phased, "", "", ""),
		)


# ── 6. item 7: a task could not reach its own SOP ──────────────────────────
class TheTaskNamesItsTemplate(Wave1TestCase):
	"""`get_task` answered with 32 fields and none of them named the template.

	`dispatch._describe_task` has reported it since v0.41.0; `shape.task`
	rebuilds the payload key by key and dropped it on the way to the handset. A
	worker holding a task therefore had no way to reach the template's SOP, its
	checklist or its instructions, because nothing in the answer said which
	template to ask for.
	"""

	def test_the_template_reaches_the_handset(self):
		out = shape.task({"name": "FT-1", "template": "Cabin Habitability Inspection"})
		self.assertEqual(out["template"], "Cabin Habitability Inspection")

	def test_the_checklist_and_its_counters_come_with_it(self):
		out = shape.task(
			{
				"name": "FT-1",
				"template": "Cabin Habitability Inspection",
				"checklist": [{"item_name": "Smoke detector", "done": True}],
				"checklist_done": 1,
				"checklist_outstanding_required": [],
			}
		)
		self.assertEqual(len(out["checklist"]), 1)
		self.assertEqual(out["checklist_done"], 1)
		self.assertEqual(out["checklist_outstanding_required"], [])

	def test_a_task_raised_by_hand_carries_neither_key(self):
		"""The payload of every hand-raised task is what it was before.

		Two permanent nulls on every row to serve the rows that came off a
		template is a change to the common case for the sake of the rare one.
		"""
		out = shape.task({"name": "FT-2"})
		self.assertNotIn("template", out)
		self.assertNotIn("checklist", out)


# ── 7. item 10: a discipline record took no evidence ───────────────────────
class AWarningCanCarryAPhotograph(Wave1TestCase):
	"""`create_discipline_record` accepted no file token at all.

	So a foreman photographing the thing a warning is about had nowhere to send
	the photograph, and the record carried a sentence describing it instead —
	which is what a dispute then turns on.
	"""

	def setUp(self):
		super().setUp()
		STORE.seed(
			"Employee",
			[
				{
					"name": "HR-EMP-00050",
					"employee_name": "Marco Reyes",
					"status": "Active",
					"company": MAIN,
					"date_of_joining": "2026-06-01",
				}
			],
		)

	def a_file(self, docname="FILE-DISC-1", **overrides):
		row = {"name": docname, "file_url": f"/private/files/{docname}.jpg", "file_name": "bin.jpg"}
		row.update(overrides)
		STORE.seed("File", [row])
		return docname

	def a_warning(self, **overrides):
		payload = {
			"employee": "HR-EMP-00050",
			"discipline_type": "Verbal Warning",
			"incident_description": "Bin left in the row overnight.",
			"expected_improvement": "Bins stacked at the headland at the end of the shift.",
			"followup_date": str(frappe.utils.add_days(frappe.utils.today(), 14)),
			"company": MAIN,
		}
		payload.update(overrides)
		return self.tool_data("create_incident_record", payload)

	def test_a_photograph_is_filed_against_the_record(self):
		self.a_file()
		data = self.a_warning(evidence_files=[{"file": "FILE-DISC-1", "kind": "photo"}])
		self.assertEqual(data["evidence_count"], 1)
		self.assertEqual(data["evidence_filed"][0]["file"], "FILE-DISC-1")
		stored = STORE.get_raw("File", "FILE-DISC-1")
		self.assertEqual(stored["attached_to_doctype"], "Farm Incident Record")
		self.assertEqual(stored["attached_to_name"], data["name"])

	def test_the_photograph_is_made_private_on_the_way_in(self):
		"""A picture in somebody's disciplinary file is not a public URL."""
		self.a_file(is_private=0)
		data = self.a_warning(evidence_files=["FILE-DISC-1"])
		self.assertTrue(STORE.get_raw("File", "FILE-DISC-1")["is_private"])
		self.assertEqual(data["evidence_count"], 1)

	def test_a_record_with_no_evidence_is_unchanged(self):
		data = self.a_warning()
		self.assertEqual(data["evidence_count"], 0)
		self.assertIsNone(data["evidence_skipped"])

	def test_a_file_already_spoken_for_is_reported_rather_than_stolen(self):
		"""Re-pointing it would take evidence off whatever it was filed against.

		And the record is NOT failed for it: the incident is the thing being
		preserved, and an attachment that did not stick can be sent again.
		"""
		self.a_file(attached_to_doctype="Employee", attached_to_name="HR-EMP-00050")
		data = self.a_warning(evidence_files=["FILE-DISC-1"])
		self.assertEqual(data["evidence_count"], 0)
		self.assertIn("already attached to Employee", data["evidence_skipped"][0]["reason"])
		self.assertTrue(any("were not filed" in note for note in data["warnings"] or []))
		# and the record itself exists
		self.assertTrue(STORE.get_raw("Farm Incident Record", data["name"]))

	def test_a_token_pointing_at_nothing_is_refused_before_the_record_is_written(self):
		"""`normalise_evidence`'s rule, unchanged and unforked.

		Evidence pointing at nothing is worse than no evidence: it satisfies a
		contract and produces a record whose proof is a broken link.
		"""
		before = len(STORE.rows("Farm Incident Record"))
		message = self.tool_error(
			"create_incident_record",
			{
				"employee": "HR-EMP-00050",
				"discipline_type": "Verbal Warning",
				"incident_description": "x",
				"expected_improvement": "y",
				"followup_date": str(frappe.utils.add_days(frappe.utils.today(), 14)),
				"company": MAIN,
				"evidence_files": ["FILE-THAT-IS-NOT-THERE"],
			},
		)
		self.assertIn("not on this site", message)
		self.assertEqual(len(STORE.rows("Farm Incident Record")), before)

	def test_the_read_side_is_on_the_allow_list_and_carries_the_hr_gate(self):
		"""So the photographs can be got back, by the accounts entitled to them.

		`True` is the HR gate. Reporting an incident is the field role's since
		v0.94.0; reading somebody's disciplinary file is not, which is the line
		`get_discipline_record` already draws.
		"""
		self.assertIn(discipline_tools.DISCIPLINE, mobile_api.ATTACHMENT_PARENTS)
		self.assertTrue(mobile_api.ATTACHMENT_PARENTS[discipline_tools.DISCIPLINE])

	def test_the_mobile_route_declares_the_argument_its_handler_reads(self):
		"""A schema that omits an argument the handler reads is a dropped argument.

		`routes.bind` filters the body to the declared parameters, so an
		`evidence_files` the wrapper did not name would never arrive.
		"""
		accepted = farmops_routes.accepted_arguments(mobile_api.create_discipline_record)
		self.assertIn("evidence_files", accepted)
