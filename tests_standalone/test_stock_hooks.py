# SPDX-License-Identifier: MIT
"""The stock a completion moves, the windows a spray opens, and the shelf that ran low.

FOUR CLAIMS, AND EVERY CLASS BELOW IS ONE OF THEM.

1. **WORK MOVES STOCK, AND STOCK NEVER BLOCKS WORK.** `TheSprayDrawdown` and
   `WhenTheStockCannotMove` are the two halves of the same promise: completing a
   spray task issues its tank mix, and a movement that cannot be written comes
   back as a warning on a completion that succeeded. The second half matters
   more than the first — a worker holding a signature, two photographs and a
   finished spray is holding a compliance record, and a shed count is not worth
   destroying one.

2. **THE INTERVALS ARE STAMPED ONCE AND CLEAR THEMSELVES.** `TheSprayWindows`
   and `TheIntervalRulesFire`. The REI and PHI are computed from the strictest
   product in the tank at the moment the sprayer stops, written onto the task,
   and never recomputed — and the two rules that read them go away by their own
   clock, to the hour, with nobody dismissing anything.

3. **A SHELF BELOW ITS LEVEL IS A COMPLIANCE ALERT LIKE ANY OTHER.**
   `TheReorderRule`, including the row a scan of the balances would miss: an
   item with a reorder rule and no Bin at all is at zero, not absent.

4. **AN INBOUND DELIVERY IS PUT AWAY ONCE.** `ThePurchaseReceiptHook`. On a site
   whose Purchase Receipt posts its own ledger — which is every site with
   ERPNext's Stock module — this app writes NOTHING more, because counting every
   delivery twice is a worse inventory than no automation at all.
"""

import contextlib
import json

import frappe

from erpnext_mcp import compliance_rules, stock_bridge
from erpnext_mcp.alerts import base as alerts_base

from .fixtures import (
	MAIN,
	MASTER_SUPPLIER,
	SHOP,
	SPRAY,
	STORES,
	TWINE,
	V12TestCase,
	seed_masters,
	seed_stock,
)
from .harness import INSTALLED_DOCTYPES, META, STORE

#: A second chemical, so "the longest interval in the tank wins" has two
#: intervals to choose between rather than one to report.
SULFUR = "SULFUR-90"

#: A product with no interval at all — a foliar nutrient. What proves that "no
#: window" is a real answer rather than the absence of a computation.
NUTRIENT = "FOLIAR-N"

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_farm_task",
		"claim_farm_task",
		"complete_farm_task",
		"get_farm_task",
		"create_purchase_receipt",
		"submit_purchase_receipt",
		"refresh_compliance_alerts",
		"list_compliance_rules",
	)
}

A_PHOTO = [{"file_url": "/files/block-a.jpg", "evidence_type": "Photo", "caption": "north row"}]

SPRAY_EVIDENCE = {"photos": True, "findings_text": True}


class StockHookTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		seed_masters()
		seed_stock()
		self.configure(enabled=1, **ALL_ON)
		self._install_item_intervals()

	# ── the fixture this module adds on top of the stock one ────────────
	def _install_item_intervals(self):
		"""The REI/PHI columns `install_compliance_fields` adds, and three products.

		Installed through the real installer rather than by hand: the whole
		mechanism depends on `compat.has_field` seeing the columns, and a test
		that added them directly would prove the drawdown works on a site
		configured in a way the app never produces.
		"""
		from erpnext_mcp import compliance_fields

		compliance_fields.install_compliance_fields()
		STORE.seed("UOM", [{"name": "Gal", "enabled": 1}])
		STORE.seed(
			"Item",
			[
				{
					"name": SULFUR,
					"item_code": SULFUR,
					"item_name": "Micronised Sulfur 90",
					"stock_uom": "Lb",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [{"company": MAIN, "default_warehouse": STORES}],
					"reorder_levels": [],
					"rei_hours": 24,
					"phi_days": 1,
				},
				{
					"name": NUTRIENT,
					"item_code": NUTRIENT,
					"item_name": "Foliar Nitrogen",
					"stock_uom": "Gal",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [{"company": MAIN, "default_warehouse": STORES}],
					"reorder_levels": [],
					"rei_hours": 0,
					"phi_days": 0,
				},
			],
		)
		# SURROUND-WP is a real restricted-entry product; the fixture's item
		# predates these columns, so the values go on here.
		spray = STORE.get_raw("Item", SPRAY)
		spray["rei_hours"] = 4
		spray["phi_days"] = 14

	# ── helpers ─────────────────────────────────────────────────────────
	def a_spray_task(self, materials=None, **overrides):
		payload = {
			"task_name": "Spray Block A",
			"task_type": "Spray",
			"evidence_required": dict(SPRAY_EVIDENCE),
			"company": MAIN,
		}
		if materials is not None:
			payload["materials_used"] = materials
		payload.update(overrides)
		return self.tool_data("create_farm_task", payload)["name"]

	def claimed(self, **kwargs):
		task = self.a_spray_task(**kwargs)
		self.tool_data("claim_farm_task", {"task": task, "worker_id": "EMP-001", "worker_name": "Ana"})
		return task

	def complete(self, task, **overrides):
		payload = {
			"task": task,
			"worker_id": "EMP-001",
			"evidence_files": list(A_PHOTO),
			"findings_text": "",
			"completion_narrative": "sprayed the block",
		}
		payload.update(overrides)
		return self.tool_data("complete_farm_task", payload)

	def entries(self, purpose=None) -> list:
		rows = [dict(row) for row in STORE.rows("Stock Entry")]
		if purpose:
			rows = [row for row in rows if row.get("stock_entry_type") == purpose]
		return rows

	def seed_rules(self):
		report = compliance_rules.seed_compliance_rules()
		self.assertEqual(report["failed"], [], f"the seeder could not write every rule: {report}")

	def sweep(self, alert_types=None):
		return alerts_base.refresh_compliance_alerts(company=MAIN, alert_types=alert_types)

	@contextlib.contextmanager
	def without_doctype(self, doctype: str):
		"""A site that has not got one, for the length of a `with`."""
		INSTALLED_DOCTYPES.discard(doctype)
		try:
			yield
		finally:
			INSTALLED_DOCTYPES.add(doctype)

	@contextlib.contextmanager
	def without_field(self, doctype: str, fieldname: str):
		"""A site whose installer never ran, for the length of a `with`."""
		meta = META[doctype]
		field = meta._by_name.pop(fieldname, None)
		if field is not None:
			meta.fields.remove(field)
		try:
			yield
		finally:
			if field is not None:
				meta.add(field)

	def alerts_of(self, alert_type: str) -> list:
		return [
			dict(row)
			for row in STORE.rows("Compliance Alert")
			if row.get("alert_type") == alert_type and not frappe.utils.cint(row.get("dismissed"))
		]


# ── 1a ──────────────────────────────────────────────────────────────────────
class TheSprayDrawdown(StockHookTestCase):
	"""Completing the work is what moves the chemical off the count."""

	def test_a_spray_tasks_tank_mix_is_issued_when_the_task_is_completed(self):
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5, "warehouse": STORES}])
		data = self.complete(task)

		consumed = data["materials_consumed"]
		self.assertEqual(consumed["source"], "task_tank_mix")
		self.assertEqual(consumed["requested"], 1)
		self.assertEqual(consumed["moved"], 1)
		self.assertEqual(consumed["warnings"], [])

		issues = self.entries("Material Issue")
		self.assertEqual(len(issues), 1)
		self.assertEqual(int(issues[0]["docstatus"]), 1, "a draft moves nothing")
		line = issues[0]["items"][0]
		self.assertEqual(line["item_code"], SPRAY)
		self.assertEqual(float(line["qty"]), 5.0)
		self.assertEqual(line["s_warehouse"], STORES)
		self.assertIsNone(line.get("t_warehouse"))

	def test_the_entry_names_the_task_it_came_out_of(self):
		"""Provenance through the SAME mechanism an operator's own call uses —
		no second linkage invented for the automatic path."""
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 2}])
		data = self.complete(task)
		source = data["materials_consumed"]["stock_entries"][0]["source"]
		self.assertEqual(source["doctype"], "Farm Task")
		self.assertEqual(source["name"], task)
		self.assertIn(task, self.entries("Material Issue")[0]["remarks"])

	def test_a_line_with_no_warehouse_falls_back_to_the_items_default(self):
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 3}])
		self.complete(task)
		self.assertEqual(self.entries("Material Issue")[0]["items"][0]["s_warehouse"], STORES)

	def test_each_chemical_gets_its_own_entry(self):
		"""One short chemical must not cost the other four their drawdown, which
		is only possible if the entries are separate."""
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}, {"item_code": SULFUR, "qty": 10}])
		data = self.complete(task)
		self.assertEqual(data["materials_consumed"]["moved"], 2)
		self.assertEqual(len(self.entries("Material Issue")), 2)

	def test_the_drawdown_runs_with_the_stock_write_tools_switched_off(self):
		"""`allow_create_stock_entry` gates an MCP caller writing stock directly.
		This is the app recording a consequence of work the operator already
		enabled somebody to file, and gating it there would leave an operation
		whose crew closes spray tasks with a count that has not moved."""
		self.configure(enabled=1, **ALL_ON, allow_create_stock_entry=0, allow_submit_stock_entry=0)
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}])
		self.assertEqual(self.complete(task)["materials_consumed"]["moved"], 1)

	def test_an_explicit_list_outranks_the_tank_mix(self):
		"""The plan is not the event: what the worker says they used wins."""
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}])
		data = self.complete(task, materials_used=[{"item_code": SULFUR, "qty": 7}])
		consumed = data["materials_consumed"]
		self.assertEqual(consumed["source"], "completion_argument")
		self.assertEqual(self.entries("Material Issue")[0]["items"][0]["item_code"], SULFUR)
		# And the task now records what was actually used, not what was planned.
		self.assertEqual(
			self.tool_data("get_farm_task", {"task": task})["materials_used"],
			[{"item_code": SULFUR, "qty": 7.0}],
		)

	def test_a_non_spray_task_with_no_list_consumes_nothing(self):
		"""Guessing that a repair used the parts somebody once listed would issue
		stock on a task whose point may have been that the part was not needed."""
		task = self.claimed(task_type="Repair", materials=[{"item_code": TWINE, "qty": 1}])
		data = self.complete(task)
		self.assertIsNone(data["materials_consumed"])
		self.assertEqual(self.entries("Material Issue"), [])

	def test_a_non_spray_task_issues_what_the_completion_names(self):
		task = self.claimed(task_type="Repair")
		data = self.complete(task, materials_used=[{"item_code": SPRAY, "qty": 1}])
		self.assertEqual(data["materials_consumed"]["moved"], 1)
		self.assertIsNone(data["spray_windows"], "a repair opens no interval")

	def test_the_mobile_wrapper_forwards_the_list(self):
		"""`complete_task_via_mobile` re-implements NONE of this — the parse, the
		refusal, the drawdown and the windows all stay in `complete_farm_task`."""
		from erpnext_mcp.tools import fieldwork

		STORE.seed("User", [{"name": "ana@example.test", "full_name": "Ana Ramos", "enabled": 1}])
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-001",
					"employee_name": "Ana Ramos",
					"user_id": "ana@example.test",
					"company": MAIN,
					"status": "Active",
				}
			],
		)
		task = self.claimed(task_type="Repair")
		data = fieldwork.complete_task_via_mobile(
			{
				"task": task,
				"user": "ana@example.test",
				"evidence": list(A_PHOTO),
				"findings_text": "",
				"materials_used": [{"item_code": SPRAY, "qty": 2}],
			}
		).data
		self.assertEqual(data["materials_consumed"]["moved"], 1)
		self.assertEqual(float(self.entries("Material Issue")[0]["items"][0]["qty"]), 2.0)

	def test_an_identical_resubmission_does_not_issue_the_stock_twice(self):
		"""A phone that lost an acknowledgement must not take a real quantity off
		a real shed count a second time."""
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}])
		first = self.complete(task, completed_at="2026-07-24 15:00:00")
		again = self.complete(task, completed_at="2026-07-24 15:00:00")
		self.assertTrue(again["x_idempotent"])
		self.assertEqual(len(self.entries("Material Issue")), 1)
		self.assertTrue(again["materials_consumed"]["replayed"])
		self.assertEqual(
			again["materials_consumed"]["stock_entries"][0]["name"],
			first["materials_consumed"]["stock_entries"][0]["name"],
		)
		self.assertTrue(again["spray_windows"]["replayed"])


# ── 1b ──────────────────────────────────────────────────────────────────────
class WhenTheStockCannotMove(StockHookTestCase):
	"""The completion is the compliance record. Nothing about stock costs one."""

	def test_a_line_with_nowhere_to_come_from_warns_and_the_work_still_files(self):
		task = self.claimed(materials=[{"item_code": TWINE, "qty": 4}])
		data = self.complete(task)
		self.assertEqual(data["final_state"], "Completed")
		self.assertEqual(data["evidence_filed"], 1)
		consumed = data["materials_consumed"]
		self.assertEqual(consumed["moved"], 0)
		self.assertEqual(len(consumed["warnings"]), 1)
		self.assertIn("no warehouse", consumed["warnings"][0])
		self.assertIn("does not know about it yet", consumed["note"])
		self.assertIn("no stock question can ever cost somebody", data["stock_note"])

	def test_one_short_line_does_not_cost_the_others_their_drawdown(self):
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}, {"item_code": TWINE, "qty": 4}])
		consumed = self.complete(task)["materials_consumed"]
		self.assertEqual(consumed["moved"], 1)
		self.assertEqual(consumed["requested"], 2)
		self.assertEqual(len(consumed["warnings"]), 1)

	def test_an_unknown_item_is_a_warning_rather_than_a_lost_completion(self):
		task = self.claimed(materials=[{"item_code": "NOT-AN-ITEM", "qty": 1}])
		data = self.complete(task)
		self.assertEqual(data["final_state"], "Completed")
		self.assertIn("NOT-AN-ITEM", " ".join(data["materials_consumed"]["warnings"]))

	def test_a_malformed_argument_is_refused_before_anything_is_written(self):
		"""The ONE stock refusal in the whole path, and it happens where every
		other refusal happens: before the write, while the client can fix it."""
		task = self.claimed()
		message = self.tool_error(
			"complete_farm_task",
			{
				"task": task,
				"worker_id": "EMP-001",
				"evidence_files": list(A_PHOTO),
				"findings_text": "",
				"materials_used": [{"item_code": SPRAY, "qty": -3}],
			},
		)
		self.assertIn("must be positive", message)
		self.assertIn("Nothing was written", message)
		self.assertEqual(STORE.rows("Stock Entry"), [])
		self.assertEqual(self.tool_data("get_farm_task", {"task": task})["state"], "Claimed")

	def test_a_corrupt_tank_mix_on_the_task_warns_rather_than_stranding_the_worker(self):
		"""A column written weeks ago by an import is not the worker's mistake."""
		task = self.claimed()
		STORE.get_raw("Farm Task", task)["materials_used"] = "{not json"
		data = self.complete(task)
		self.assertEqual(data["final_state"], "Completed")
		self.assertIn("not valid JSON", " ".join(data["materials_consumed"]["warnings"]))

	def test_a_site_with_no_stock_entry_doctype_says_so(self):
		task = self.a_spray_task()
		moved = stock_bridge.issue_materials([{"item_code": SPRAY, "qty": 1}], MAIN, "Farm Task", task)
		with self.without_doctype("Stock Entry"):
			absent = stock_bridge.issue_materials([{"item_code": SPRAY, "qty": 1}], MAIN, "Farm Task", task)
		self.assertEqual(moved["moved"], 1)
		self.assertEqual(absent["moved"], 0)
		self.assertIn("no Stock Entry DocType", absent["warnings"][0])


# ── 2a ──────────────────────────────────────────────────────────────────────
class TheSprayWindows(StockHookTestCase):
	"""A mix is under the strictest product in it, from the hour it finished."""

	def test_the_longest_rei_in_the_tank_sets_the_window(self):
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}, {"item_code": SULFUR, "qty": 10}])
		windows = self.complete(task, completed_at="2026-07-24 14:00:00")["spray_windows"]
		self.assertEqual(windows["rei_hours"], 24)
		self.assertEqual(windows["rei_source_item"], SULFUR)
		self.assertEqual(str(windows["rei_expires_at"]), "2026-07-25 14:00:00")

	def test_the_longest_phi_can_come_off_a_different_product(self):
		"""SURROUND has the shorter REI and the longer PHI, which is why the two
		are folded separately rather than taken off one 'worst' product."""
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}, {"item_code": SULFUR, "qty": 10}])
		windows = self.complete(task, completed_at="2026-07-24 14:00:00")["spray_windows"]
		self.assertEqual(windows["phi_days"], 14)
		self.assertEqual(windows["phi_source_item"], SPRAY)
		self.assertEqual(str(windows["phi_clears_on"]), "2026-08-07")

	def test_a_product_with_no_interval_opens_no_window(self):
		task = self.claimed(materials=[{"item_code": NUTRIENT, "qty": 20}])
		data = self.complete(task)
		self.assertEqual(data["materials_consumed"]["moved"], 1)
		self.assertIsNone(data["spray_windows"])

	def test_the_window_is_stamped_on_the_task_and_not_recomputed(self):
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}])
		self.complete(task, completed_at="2026-07-24 14:00:00")
		row = STORE.get_raw("Farm Task", task)
		self.assertEqual(str(row["rei_expires_at"]), "2026-07-24 18:00:00")
		self.assertEqual(str(row["phi_clears_on"]), "2026-08-07")
		self.assertEqual(row["rei_source_item"], SPRAY)

		# A label corrected next season does not reopen a block posted this one.
		STORE.get_raw("Item", SPRAY)["rei_hours"] = 72
		self.assertEqual(str(STORE.get_raw("Farm Task", task)["rei_expires_at"]), "2026-07-24 18:00:00")

	def test_the_drawdown_is_recorded_on_the_task_as_well_as_returned(self):
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}])
		self.complete(task)
		stored = json.loads(STORE.get_raw("Farm Task", task)["stock_drawdown"])
		self.assertEqual(stored["moved"], 1)
		self.assertEqual(stored["source"], "task_tank_mix")
		self.assertEqual(len(stored["stock_entries"]), 1)

	def test_a_site_that_cannot_answer_says_so_rather_than_reporting_no_window(self):
		"""'No restriction' and 'cannot say' are the two answers a person must
		never confuse."""
		with self.without_field("Item", "rei_hours"), self.without_field("Item", "phi_days"):
			windows = stock_bridge.spray_windows([{"item_code": SPRAY, "qty": 5}], "2026-07-24 14:00:00")
		self.assertNotIn("rei_expires_at", windows)
		self.assertIn("install_compliance_fields", windows["note"])


# ── 2b ──────────────────────────────────────────────────────────────────────
class TheIntervalRulesFire(StockHookTestCase):
	"""Raised in the same call the spray finished in; gone by their own clock."""

	def a_sprayed_block(self, completed_at="2026-07-24 14:00:00"):
		self.seed_rules()
		task = self.claimed(materials=[{"item_code": SPRAY, "qty": 5}])
		return task, self.complete(task, completed_at=completed_at)

	def test_the_rei_alert_is_raised_by_the_completion_itself(self):
		_task, data = self.a_sprayed_block()
		evaluation = data["spray_windows"]["evaluation"]
		self.assertTrue(evaluation["evaluated"])
		self.assertEqual(evaluation["rules_asked"], ["rei_active_block_entry", "phi_harvest_window"])
		self.assertGreaterEqual(evaluation["created"], 2)

		alerts = self.alerts_of("rei_active_block_entry")
		self.assertEqual(len(alerts), 1)
		self.assertEqual(alerts[0]["severity"], "Critical")
		self.assertEqual(alerts[0]["category"], "Spray and Pesticides")
		self.assertIn("REI active — no worker entry until 2026-07-24 18:00:00", alerts[0]["alert_message"])

	def test_the_rei_alert_dismisses_itself_when_the_interval_closes(self):
		"""NOBODY DISMISSES IT. The window shuts, the row stops matching the
		rule's own filter, and the sweep dismisses what it stops observing."""
		task, _data = self.a_sprayed_block()
		self.assertEqual(len(self.alerts_of("rei_active_block_entry")), 1)

		STORE.get_raw("Farm Task", task)["rei_expires_at"] = "2020-01-01 00:00:00"
		self.sweep(alert_types=["rei_active_block_entry"])

		self.assertEqual(self.alerts_of("rei_active_block_entry"), [])
		row = next(
			dict(entry)
			for entry in STORE.rows("Compliance Alert")
			if entry.get("alert_type") == "rei_active_block_entry"
		)
		self.assertTrue(frappe.utils.cint(row["auto_dismissed"]))

	def test_the_phi_alert_names_the_clear_date_and_carries_it_as_the_due_date(self):
		self.a_sprayed_block()
		alerts = self.alerts_of("phi_harvest_window")
		self.assertEqual(len(alerts), 1)
		self.assertEqual(alerts[0]["severity"], "Warning")
		self.assertIn("PHI active — no harvest until 2026-08-07", alerts[0]["alert_message"])
		self.assertEqual(str(alerts[0]["due_date"]), "2026-08-07")

	def test_the_phi_alert_stands_on_the_clearing_day_itself(self):
		"""A day of over-caution against a residue violation on a shipped load is
		not a close call — the alert goes the day AFTER the interval closes."""
		task, _data = self.a_sprayed_block()
		STORE.get_raw("Farm Task", task)["phi_clears_on"] = frappe.utils.today()
		self.sweep(alert_types=["phi_harvest_window"])
		self.assertEqual(len(self.alerts_of("phi_harvest_window")), 1)

		STORE.get_raw("Farm Task", task)["phi_clears_on"] = frappe.utils.add_days(frappe.utils.today(), -1)
		self.sweep(alert_types=["phi_harvest_window"])
		self.assertEqual(self.alerts_of("phi_harvest_window"), [])

	def test_a_spray_of_nothing_restricted_raises_nothing_and_says_so(self):
		self.seed_rules()
		task = self.claimed(materials=[{"item_code": NUTRIENT, "qty": 20}])
		data = self.complete(task)
		self.assertIsNone(data["spray_windows"])
		self.sweep(alert_types=["rei_active_block_entry", "phi_harvest_window"])
		self.assertEqual(self.alerts_of("rei_active_block_entry"), [])

	def test_current_datetime_resolves_at_sweep_time_and_not_at_authoring_time(self):
		"""The template variable the whole hour-hand mechanism rests on."""
		first = compliance_rules.resolve_template("{{current_datetime}}")
		self.assertIn("-", first)
		self.assertIn(":", first)
		self.assertEqual(compliance_rules.resolve_template("not a template"), "not a template")

	def test_the_two_rules_offer_no_button_and_say_why(self):
		"""A button implies the worker can end the interval, and the entire value
		of the alert is that they cannot."""
		from erpnext_mcp.api import rectify

		for alert_type in ("rei_active_block_entry", "phi_harvest_window"):
			with self.subTest(alert_type=alert_type):
				built = rectify.describe_rectification({"alert_type": alert_type})
				self.assertFalse(built["can_rectify_mobile"])
				self.assertIsNone(built["action_endpoint"])
				self.assertNotEqual(built["explanation"], rectify._NO_MOBILE_FIX)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheReorderRule(StockHookTestCase):
	"""What must be bought, on the same calendar as everything else."""

	def alerts(self) -> list:
		self.seed_rules()
		self.sweep(alert_types=["item_below_reorder"])
		return self.alerts_of("item_below_reorder")

	def test_an_item_under_its_level_raises_against_the_item(self):
		"""KEYED ON THE ITEM AND NEVER THE BIN: an alert key that moved when a bin
		appeared would lose the row's first_seen and anybody's snooze."""
		rows = {row["source_docname"]: row for row in self.alerts()}
		self.assertIn(SPRAY, rows)
		self.assertEqual(rows[SPRAY]["source_doctype"], "Item")
		self.assertEqual(rows[SPRAY]["severity"], "Info")
		self.assertEqual(rows[SPRAY]["category"], "Inventory")
		self.assertIn("Stock below reorder level: 80 / 100 Lb", rows[SPRAY]["alert_message"])
		self.assertIn(STORES, rows[SPRAY]["alert_message"])

	def test_an_item_with_a_rule_and_no_bin_at_all_raises_at_zero(self):
		"""The row a scan of the balances would miss, and the most complete
		shortage there is."""
		rows = {row["source_docname"]: row for row in self.alerts()}
		self.assertIn(TWINE, rows)
		self.assertIn("Stock below reorder level: 0 / 20", rows[TWINE]["alert_message"])
		self.assertIn("no bin for it at all", rows[TWINE]["alert_message"])

	def test_it_dismisses_when_the_balance_comes_back_up(self):
		"""Receiving the order IS the fix; nothing else has to happen."""
		self.assertIn(SPRAY, {row["source_docname"] for row in self.alerts()})
		STORE.get_raw("Bin", f"{SPRAY}-{STORES}")["actual_qty"] = 400.0
		self.sweep(alert_types=["item_below_reorder"])
		self.assertNotIn(SPRAY, {row["source_docname"] for row in self.alerts_of("item_below_reorder")})

	def test_an_item_with_no_reorder_rule_raises_nothing_ever(self):
		"""Nobody has said what 'enough' means for it."""
		self.assertNotIn(NUTRIENT, {row["source_docname"] for row in self.alerts()})

	def test_a_level_of_zero_is_not_a_level(self):
		STORE.get_raw("Item", TWINE)["reorder_levels"][0]["warehouse_reorder_level"] = 0
		self.assertNotIn(TWINE, {row["source_docname"] for row in self.alerts()})

	def test_the_fix_it_offers_is_the_one_thing_somebody_can_actually_do(self):
		from erpnext_mcp.api import rectify

		built = rectify.describe_rectification({"alert_type": "item_below_reorder"})
		self.assertTrue(built["can_rectify_mobile"])
		self.assertEqual(built["action_endpoint"], rectify._RECTIFY_ALERT)
		self.assertIn("reorder", built["action_label"].lower())


# ── 4 ───────────────────────────────────────────────────────────────────────
class ThePurchaseReceiptHook(StockHookTestCase):
	"""A delivery is put away once, by whichever half of the stack posts it."""

	def a_receipt(self):
		return self.tool_data(
			"create_purchase_receipt",
			{
				"company": MAIN,
				"supplier": MASTER_SUPPLIER,
				"items": [{"item_code": SPRAY, "qty": 60, "rate": 2.5, "warehouse": SHOP}],
			},
		)["name"]

	def test_a_receipt_whose_own_submit_posts_the_ledger_is_not_mirrored(self):
		"""THE GUARD. Every site with ERPNext's Stock module posts its own Stock
		Ledger Entries at submit; a Material Receipt on top of that would count
		every delivery twice, which is a worse inventory than none."""
		name = self.a_receipt()
		# What a real ERPNext submit writes for itself. Seeded against the draft
		# so the guard meets the state it exists to recognise.
		STORE.seed(
			"Stock Ledger Entry",
			[
				{
					"name": "SLE-PR-MIRROR",
					"item_code": SPRAY,
					"warehouse": SHOP,
					"actual_qty": 60.0,
					"voucher_type": "Purchase Receipt",
					"voucher_no": name,
					"company": MAIN,
					"is_cancelled": 0,
				}
			],
		)
		data = self.tool_data("submit_purchase_receipt", {"name": name})

		self.assertEqual(data["docstatus"], 1)
		self.assertEqual(data["inbound_stock"]["posted_by"], "purchase_receipt")
		self.assertEqual(data["inbound_stock"]["created"], [])
		self.assertIn("count every delivery twice", data["inbound_stock"]["note"])
		self.assertEqual(self.entries("Material Receipt"), [])
		self.assertTrue(stock_bridge.receipt_already_posted(name))

	def test_a_receipt_that_posts_no_ledger_is_mirrored_as_material_receipts(self):
		name = self.a_receipt()
		data = self.tool_data("submit_purchase_receipt", {"name": name})
		inbound = data["inbound_stock"]
		self.assertEqual(inbound["posted_by"], "erpnext_mcp")
		self.assertEqual(len(inbound["created"]), 1)
		self.assertEqual(inbound["warnings"], [])

		receipts = self.entries("Material Receipt")
		self.assertEqual(len(receipts), 1)
		line = receipts[0]["items"][0]
		self.assertEqual(line["item_code"], SPRAY)
		self.assertEqual(float(line["qty"]), 60.0)
		self.assertEqual(line["t_warehouse"], SHOP)
		self.assertIsNone(line.get("s_warehouse"))

	def test_the_mirror_names_the_receipt_it_came_from(self):
		name = self.a_receipt()
		data = self.tool_data("submit_purchase_receipt", {"name": name})
		source = data["inbound_stock"]["created"][0]["source"]
		self.assertEqual(source["doctype"], "Purchase Receipt")
		self.assertEqual(source["name"], name)
		# ERPNext HAS a column for this one, so it is stored queryably rather
		# than in a remarks marker — the same choice `create_stock_entry` makes.
		self.assertEqual(source["stored_on"], "Stock Entry.purchase_receipt_no")

	def test_the_receipt_stays_submitted_when_the_movement_cannot_be_written(self):
		"""THE RECEIPT IS SUBMITTED — the failure is about the warehouse, not
		about the document."""
		name = self.a_receipt()
		with self.without_doctype("Stock Entry"):
			data = self.tool_data("submit_purchase_receipt", {"name": name})
		self.assertEqual(data["docstatus"], 1)
		self.assertEqual(data["inbound_stock"]["posted_by"], "nobody")
		self.assertIn("no Stock Entry DocType", " ".join(data["inbound_stock"]["warnings"]))
