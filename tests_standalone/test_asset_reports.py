# SPDX-License-Identifier: MIT
"""v0.26.0 — Field-initiated task creation from asset scan.

FIVE CLAIMS, AND EVERY CLASS IN THIS FILE IS ONE OF THEM.

1. **REPORT WITH ASSET CONTEXT CREATES A LINKED TASK.** `ReportWithAssetContext`
   proves that report_field_task with an asset parameter creates a Farm Task with
   the asset link set, and that report_asset_issue does the same through its
   convenience wrapper.

2. **SKILL IS AUTO-MAPPED FROM ASSET TYPE.** `SkillAutoMapping` proves the
   ASSET_TYPE_SKILL_MAP is applied when skill_required is not given explicitly,
   and that an explicit skill overrides the mapping.

3. **BACKWARD COMPATIBILITY WITHOUT ASSET.** `BackwardCompatWithoutAsset` proves
   that report_field_task still works exactly as before when no asset is given.

4. **ASSET HISTORY INCLUDES FIELD-REPORTED TASKS.** `AssetHistoryIncludesFieldReports`
   proves that a task created with an asset link appears in get_asset_detail's
   history timeline.

5. **SCAN ASSET INCLUDES REPORTABILITY.** `ScanAssetIncludesReportability` proves
   that scan_asset returns can_report and suggested_skill fields.
"""

from erpnext_mcp.tools.asset_tags import ASSET_TYPE_SKILL_MAP

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"register_asset",
		"list_assets",
		"get_asset_detail",
		"get_asset_history",
		"scan_asset",
		"report_field_task",
		"report_asset_issue",
		"create_farm_task",
	)
}

WORKER = "HR-EMP-00002"


def _a_photo(name="asset-report-photo-001"):
	"""Seed a File record the harness can find."""
	if not STORE.get_raw("File", name):
		STORE.seed(
			"File",
			[
				{
					"name": name,
					"file_name": "problem.jpg",
					"file_url": "/files/problem.jpg",
					"file_size": 48000,
					"is_private": 1,
					"folder": "Home",
				}
			],
		)
	return name


class AssetReportTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def an_asset(self, name="MC-Valve-05", asset_type="Irrigation Valve", company=MAIN, **kw):
		payload = {"name": name, "asset_type": asset_type, "company": company, **kw}
		return self.tool_data("register_asset", payload)


class ReportWithAssetContext(AssetReportTestCase):
	"""report_field_task with asset param and report_asset_issue both link the task."""

	def test_report_field_task_with_asset_links_the_task(self):
		self.an_asset()
		data = self.tool_data(
			"report_field_task",
			{
				"reported_by": WORKER,
				"photo_file_token": _a_photo(),
				"description": "Valve leaking",
				"asset": "MC-Valve-05",
			},
		)
		self.assertIn("FT-", data["name"])
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertEqual(task["asset"], "MC-Valve-05")

	def test_report_field_task_with_asset_sets_origin(self):
		self.an_asset()
		data = self.tool_data(
			"report_field_task",
			{
				"reported_by": WORKER,
				"photo_file_token": _a_photo("origin-photo"),
				"description": "Valve broken",
				"asset": "MC-Valve-05",
			},
		)
		self.assertEqual(data["origin"], "field_reported")

	def test_report_asset_issue_creates_linked_task(self):
		self.an_asset()
		data = self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("rai-photo"),
				"description": "Broken handle",
			},
		)
		self.assertIn("FT-", data["name"])
		self.assertEqual(data["asset"], "MC-Valve-05")
		self.assertEqual(data["asset_type"], "Irrigation Valve")

	def test_report_asset_issue_auto_fills_company_from_asset(self):
		self.an_asset()
		data = self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("company-photo"),
				"description": "Needs fixing",
			},
		)
		self.assertEqual(data["company"], MAIN)

	def test_report_asset_issue_refuses_nonexistent_asset(self):
		error = self.tool_error(
			"report_asset_issue",
			{
				"asset_name": "NONEXISTENT",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("noasset-photo"),
				"description": "Nothing here",
			},
		)
		self.assertIn("no Asset Register", error)


class SkillAutoMapping(AssetReportTestCase):
	"""Skill is auto-mapped from asset type when not provided."""

	def test_valve_maps_to_irrigation(self):
		self.an_asset(asset_type="Irrigation Valve")
		data = self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("valve-photo"),
				"description": "Leaking",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertEqual(task["skill_required"], "irrigation")

	def test_housing_unit_maps_to_camp_maintenance(self):
		self.an_asset(name="MC-Cabin-01", asset_type="Housing Unit")
		data = self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Cabin-01",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("cabin-photo"),
				"description": "Window broken",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertEqual(task["skill_required"], "camp_maintenance")

	def test_tractor_maps_to_equipment_maintenance(self):
		self.an_asset(name="MC-Tractor-01", asset_type="Tractor")
		data = self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Tractor-01",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("tractor-photo"),
				"description": "Flat tire",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertEqual(task["skill_required"], "equipment_maintenance")

	def test_explicit_skill_overrides_mapping(self):
		self.an_asset()
		data = self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("override-photo"),
				"description": "Needs welding",
				"skill_required": "welding",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertEqual(task["skill_required"], "welding")

	def test_report_field_task_auto_maps_skill_when_asset_given(self):
		self.an_asset(name="MC-Sprayer-01", asset_type="Sprayer")
		data = self.tool_data(
			"report_field_task",
			{
				"reported_by": WORKER,
				"photo_file_token": _a_photo("sprayer-photo"),
				"description": "Hose cracked",
				"asset": "MC-Sprayer-01",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertEqual(task["skill_required"], "equipment_maintenance")

	def test_every_asset_type_has_a_skill_mapping(self):
		from erpnext_mcp.tools.asset_tags import ASSET_TYPES

		for asset_type in ASSET_TYPES:
			self.assertIn(
				asset_type,
				ASSET_TYPE_SKILL_MAP,
				f"asset type {asset_type!r} has no entry in ASSET_TYPE_SKILL_MAP",
			)


class BackwardCompatWithoutAsset(AssetReportTestCase):
	"""report_field_task without asset works exactly as before."""

	def test_no_asset_no_link(self):
		data = self.tool_data(
			"report_field_task",
			{
				"reported_by": WORKER,
				"photo_file_token": _a_photo("noasset-compat"),
				"description": "Broken ladder",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertFalse(task.get("asset"))

	def test_no_asset_no_auto_skill(self):
		data = self.tool_data(
			"report_field_task",
			{
				"reported_by": WORKER,
				"photo_file_token": _a_photo("noskill-compat"),
				"description": "Something wrong",
			},
		)
		task = STORE.get_raw("Farm Task", data["name"])
		self.assertFalse(task.get("skill_required"))

	def test_no_asset_still_creates_available_task(self):
		data = self.tool_data(
			"report_field_task",
			{
				"reported_by": WORKER,
				"photo_file_token": _a_photo("avail-compat"),
				"description": "Old behavior",
			},
		)
		self.assertEqual(data["state"], "Available")
		self.assertEqual(data["origin"], "field_reported")


class AssetHistoryIncludesFieldReports(AssetReportTestCase):
	"""A task linked to an asset appears in get_asset_detail's history."""

	def test_field_report_appears_in_asset_history(self):
		self.an_asset()
		self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("history-photo"),
				"description": "Valve stuck open",
			},
		)
		detail = self.tool_data("get_asset_detail", {"asset_name": "MC-Valve-05"})
		farm_task_events = [e for e in detail["history"] if e["doctype"] == "Farm Task"]
		self.assertGreaterEqual(len(farm_task_events), 1)

	def test_field_report_appears_in_open_tasks(self):
		self.an_asset()
		self.tool_data(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("open-photo"),
				"description": "Valve leaking badly",
			},
		)
		detail = self.tool_data("get_asset_detail", {"asset_name": "MC-Valve-05"})
		self.assertGreaterEqual(detail["open_task_count"], 1)


class ScanAssetIncludesReportability(AssetReportTestCase):
	"""scan_asset response includes can_report and suggested_skill."""

	def test_scan_response_has_can_report(self):
		self.an_asset()
		data = self.tool_data("scan_asset", {"asset_name": "MC-Valve-05"})
		self.assertIn("can_report", data)
		self.assertTrue(data["can_report"])

	def test_scan_response_has_suggested_skill(self):
		self.an_asset()
		data = self.tool_data("scan_asset", {"asset_name": "MC-Valve-05"})
		self.assertEqual(data["suggested_skill"], "irrigation")

	def test_scan_housing_unit_suggests_camp_maintenance(self):
		self.an_asset(name="MC-Cabin-01", asset_type="Housing Unit")
		data = self.tool_data("scan_asset", {"asset_name": "MC-Cabin-01"})
		self.assertEqual(data["suggested_skill"], "camp_maintenance")


class KillSwitch(AssetReportTestCase):
	"""report_asset_issue is off by default like other mutating tools."""

	def test_report_asset_issue_off_by_default(self):
		self.configure(enabled=1, allow_register_asset=1)
		self.an_asset()
		error = self.tool_error(
			"report_asset_issue",
			{
				"asset_name": "MC-Valve-05",
				"reported_by": WORKER,
				"photo_file_token": _a_photo("killswitch-photo"),
				"description": "Test",
			},
		)
		self.assertIn("allow_report_asset_issue", error)
