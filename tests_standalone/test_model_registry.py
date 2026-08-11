# SPDX-License-Identifier: MIT
"""ML Model Registry — v0.43.0. The pure engine, tested as a pure engine.

`model_registry.py` imports nothing from `frappe` and reads no database, the
same contract `budget_engine.py` is tested under in `test_budget.py`: every
function here takes a plain dict in and returns a plain dict out, so a
`unittest.TestCase` with no setup is the honest test for that. The impure
half — reading and writing an `ML Model` document, finding the Active sibling
a candidate would conflict with — lives in `tools/ml_model.py` and is exercised
through the tool layer elsewhere; this file is about the three claims the pure
engine makes.

THREE CLAIMS, ONE CLASS EACH, PLUS THE REGISTRATION ITSELF.

1. `ValidatingRegistration` — `validate_model_registration` finds exactly the
   reasons a candidate record cannot be registered (or re-saved), and reports
   none when there are none.
2. `BuildingTheManifest` — `build_model_manifest` reshapes an ERPNext record
   into Volume Vision's own `to_dict()` shape, tolerates a JSON field arriving
   as either a string or an already-parsed value, and falls back to the
   docname for `uuid` when there is no `source_uuid`.
3. `CheckingConflicts` — `check_model_conflicts` finds exactly the collision
   activating a candidate would cause against whatever the caller already
   found to be Active — no existing Active model, an unrelated one, and the
   candidate's own record are all "not a conflict"; the true collision names
   what it would supersede.
4. `ToolRegistration` — the nine tools exist in `registry.TOOLS`, split four
   read / five write the way the release describes, and the registry's total
   counts reflect them.
"""

import unittest

from erpnext_mcp import model_registry as engine


def model(**overrides):
	base = {
		"model_name": "Cherry Fill Detection",
		"version": "3.2",
		"company": "Highland Orchards",
		"piecework_activity": "bucket_fill_detection",
	}
	base.update(overrides)
	return base


class ValidatingRegistration(unittest.TestCase):
	def test_a_complete_candidate_is_valid(self):
		self.assertEqual(engine.validate_model_registration(model()), [])

	def test_model_name_is_required(self):
		errors = engine.validate_model_registration(model(model_name=""))
		self.assertTrue(any("model_name" in e for e in errors))

	def test_version_is_required(self):
		errors = engine.validate_model_registration(model(version=""))
		self.assertTrue(any("version is required" in e for e in errors))

	def test_version_must_look_like_a_version(self):
		errors = engine.validate_model_registration(model(version="v3.2-beta"))
		self.assertTrue(any("version" in e and "not a recognized format" in e for e in errors))

	def test_version_accepts_a_bare_integer(self):
		self.assertEqual(engine.validate_model_registration(model(version="3")), [])

	def test_version_accepts_several_dotted_segments(self):
		self.assertEqual(engine.validate_model_registration(model(version="3.2.1")), [])

	def test_company_is_required(self):
		errors = engine.validate_model_registration(model(company=""))
		self.assertTrue(any("company is required" in e for e in errors))

	def test_piecework_activity_is_required(self):
		errors = engine.validate_model_registration(model(piecework_activity=""))
		self.assertTrue(any("piecework_activity is required" in e for e in errors))

	def test_a_well_formed_source_uuid_is_valid(self):
		errors = engine.validate_model_registration(model(source_uuid="4b6f6e1a-2c3d-4e5f-8a9b-0c1d2e3f4a5b"))
		self.assertEqual(errors, [])

	def test_a_malformed_source_uuid_is_refused(self):
		errors = engine.validate_model_registration(model(source_uuid="not-a-uuid"))
		self.assertTrue(any("source_uuid" in e for e in errors))

	def test_an_empty_source_uuid_is_not_refused(self):
		"""Optional field — absent is fine, only a present-but-malformed value errors."""
		self.assertEqual(engine.validate_model_registration(model(source_uuid="")), [])

	def test_model_kind_must_be_a_known_value(self):
		errors = engine.validate_model_registration(model(model_kind="Regression"))
		self.assertTrue(any("model_kind" in e for e in errors))

	def test_model_kind_accepts_a_known_value(self):
		self.assertEqual(engine.validate_model_registration(model(model_kind="Detection")), [])

	def test_model_format_must_be_a_known_value(self):
		errors = engine.validate_model_registration(model(model_format="PyTorch"))
		self.assertTrue(any("model_format" in e for e in errors))

	def test_status_must_be_a_known_value(self):
		errors = engine.validate_model_registration(model(status="Retired"))
		self.assertTrue(any("status" in e for e in errors))

	def test_class_names_as_a_native_list_is_valid(self):
		errors = engine.validate_model_registration(model(class_names=["empty", "partial", "full"]))
		self.assertEqual(errors, [])

	def test_class_names_as_a_json_string_is_valid(self):
		errors = engine.validate_model_registration(model(class_names='["empty", "full"]'))
		self.assertEqual(errors, [])

	def test_class_names_that_is_not_valid_json_is_refused(self):
		errors = engine.validate_model_registration(model(class_names="{not json"))
		self.assertTrue(any("class_names" in e for e in errors))

	def test_class_names_that_is_a_json_object_rather_than_an_array_is_refused(self):
		errors = engine.validate_model_registration(model(class_names='{"a": 1}'))
		self.assertTrue(any("class_names" in e for e in errors))

	def test_metrics_as_a_native_dict_is_valid(self):
		self.assertEqual(engine.validate_model_registration(model(metrics={"accuracy": 0.94})), [])

	def test_metrics_that_is_a_json_array_rather_than_an_object_is_refused(self):
		errors = engine.validate_model_registration(model(metrics="[1, 2, 3]"))
		self.assertTrue(any("metrics" in e for e in errors))

	def test_every_missing_required_field_is_reported_at_once(self):
		errors = engine.validate_model_registration({})
		for expected in ("model_name", "version", "company", "piecework_activity"):
			with self.subTest(field=expected):
				self.assertTrue(any(expected in e for e in errors))

	def test_an_empty_dict_does_not_raise(self):
		self.assertIsInstance(engine.validate_model_registration({}), list)

	def test_none_does_not_raise(self):
		self.assertIsInstance(engine.validate_model_registration(None), list)


class BuildingTheManifest(unittest.TestCase):
	def test_uuid_prefers_source_uuid_over_the_docname(self):
		manifest = engine.build_model_manifest(
			model(name="MLM-2026-0001", source_uuid="4b6f6e1a-2c3d-4e5f-8a9b-0c1d2e3f4a5b")
		)
		self.assertEqual(manifest["uuid"], "4b6f6e1a-2c3d-4e5f-8a9b-0c1d2e3f4a5b")

	def test_uuid_falls_back_to_the_docname_when_there_is_no_source_uuid(self):
		manifest = engine.build_model_manifest(model(name="MLM-2026-0001"))
		self.assertEqual(manifest["uuid"], "MLM-2026-0001")

	def test_name_and_class_names_are_carried_through(self):
		manifest = engine.build_model_manifest(model(class_names=["empty", "partial", "full"]))
		self.assertEqual(manifest["name"], "Cherry Fill Detection")
		self.assertEqual(manifest["class_names"], ["empty", "partial", "full"])

	def test_class_names_absent_becomes_an_empty_list_not_none(self):
		manifest = engine.build_model_manifest(model())
		self.assertEqual(manifest["class_names"], [])

	def test_class_names_as_a_json_string_is_parsed(self):
		manifest = engine.build_model_manifest(model(class_names='["a", "b"]'))
		self.assertEqual(manifest["class_names"], ["a", "b"])

	def test_metadata_carries_version_kind_format_and_activity(self):
		manifest = engine.build_model_manifest(model(model_kind="Detection", model_format="CoreML"))
		metadata = manifest["metadata"]
		self.assertEqual(metadata["version"], "3.2")
		self.assertEqual(metadata["kind"], "Detection")
		self.assertEqual(metadata["format"], "CoreML")
		self.assertEqual(metadata["piecework_activity"], "bucket_fill_detection")

	def test_model_format_defaults_when_absent(self):
		manifest = engine.build_model_manifest(model())
		self.assertEqual(manifest["metadata"]["format"], engine.DEFAULT_MODEL_FORMAT)

	def test_metrics_as_a_json_string_is_parsed_into_the_metadata(self):
		manifest = engine.build_model_manifest(model(metrics='{"accuracy": 0.94}'))
		self.assertEqual(manifest["metadata"]["metrics"], {"accuracy": 0.94})

	def test_metrics_absent_becomes_an_empty_dict_not_none(self):
		manifest = engine.build_model_manifest(model())
		self.assertEqual(manifest["metadata"]["metrics"], {})

	def test_the_shape_matches_volume_visions_own_to_dict(self):
		"""uuid / name / class_names / metadata — no other top-level keys, so an
		iOS client's existing Volume Vision manifest parser reads this too."""
		manifest = engine.build_model_manifest(model())
		self.assertEqual(set(manifest.keys()), {"uuid", "name", "class_names", "metadata"})


class CheckingConflicts(unittest.TestCase):
	def test_no_existing_active_model_is_not_a_conflict(self):
		result = engine.check_model_conflicts(model(name="MLM-2026-0002"), None)
		self.assertEqual(result, {"conflict": False, "supersedes": None})

	def test_a_different_company_is_not_a_conflict(self):
		existing = model(name="MLM-2026-0001", company="Constancy Farms")
		candidate = model(name="MLM-2026-0002", company="Highland Orchards")
		result = engine.check_model_conflicts(candidate, existing)
		self.assertFalse(result["conflict"])

	def test_a_different_piecework_activity_is_not_a_conflict(self):
		existing = model(name="MLM-2026-0001", piecework_activity="harvest_quality")
		candidate = model(name="MLM-2026-0002", piecework_activity="bucket_fill_detection")
		result = engine.check_model_conflicts(candidate, existing)
		self.assertFalse(result["conflict"])

	def test_the_same_company_and_activity_is_a_conflict(self):
		existing = model(name="MLM-2026-0001", model_name="Cherry Fill Detection v3")
		candidate = model(name="MLM-2026-0002", model_name="Cherry Fill Detection v4")
		result = engine.check_model_conflicts(candidate, existing)
		self.assertTrue(result["conflict"])
		self.assertEqual(result["supersedes"], "Cherry Fill Detection v3")

	def test_supersedes_falls_back_to_the_docname_when_there_is_no_model_name(self):
		existing = {
			"name": "MLM-2026-0001",
			"company": "Highland Orchards",
			"piecework_activity": "bucket_fill_detection",
		}
		candidate = model(name="MLM-2026-0002")
		result = engine.check_model_conflicts(candidate, existing)
		self.assertEqual(result["supersedes"], "MLM-2026-0001")

	def test_reactivating_the_same_record_is_not_a_conflict(self):
		"""A candidate that IS the existing Active record — same `name` — is
		activate_model refreshing deployed_at, not a supersession."""
		existing = model(name="MLM-2026-0001")
		candidate = model(name="MLM-2026-0001")
		result = engine.check_model_conflicts(candidate, existing)
		self.assertEqual(result, {"conflict": False, "supersedes": None})

	def test_a_candidate_with_no_name_never_matches_by_accident(self):
		"""Two candidates that both happen to have no `name` must not be read as
		'the same record' — an unsaved candidate is never its own sibling."""
		existing = {"name": "", "company": "Highland Orchards", "piecework_activity": "bucket_fill_detection"}
		candidate = model(name="")
		result = engine.check_model_conflicts(candidate, existing)
		self.assertTrue(result["conflict"])


class ToolRegistration(unittest.TestCase):
	"""Nine tools, four reads and five writes, wired into the catalogue."""

	READ_TOOLS = ("get_model", "list_models", "get_active_model", "get_model_file_chunk")
	MUTATING_TOOLS = (
		"register_model",
		"update_model",
		"activate_model",
		"deprecate_model",
		"attach_model_file",
	)

	def setUp(self):
		from erpnext_mcp import registry

		self.registry = registry

	def test_every_model_tool_is_registered(self):
		for name in self.READ_TOOLS + self.MUTATING_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, self.registry.TOOLS)

	def test_the_read_tools_are_read_only(self):
		for name in self.READ_TOOLS:
			with self.subTest(tool=name):
				self.assertFalse(self.registry.TOOLS[name]["mutating"])
				self.assertIn(name, self.registry.READ_TOOLS)

	def test_the_mutating_tools_are_marked_mutating(self):
		for name in self.MUTATING_TOOLS:
			with self.subTest(tool=name):
				self.assertTrue(self.registry.TOOLS[name]["mutating"])
				self.assertIn(name, self.registry.MUTATING_TOOLS)
				self.assertIn("MUTATING", self.registry.TOOLS[name]["description"])

	def test_none_of_the_five_are_on_by_default(self):
		"""Mutating tools default off is the whole point of the switch — none of
		these five belong in registry.DEFAULT_ON_MUTATING_TOOLS."""
		for name in self.MUTATING_TOOLS:
			with self.subTest(tool=name):
				self.assertNotIn(name, self.registry.DEFAULT_ON_MUTATING_TOOLS)

	def test_the_registry_totals_include_the_nine(self):
		# 389/178/211 as of v0.51.1, plus v0.52.0's `attach_model_file` (write)
		# and `get_model_file_chunk` (read) — the file-serving pair that lets
		# ERPNext hand an iOS app the model binary itself instead of the model
		# only ever pointing at where Volume Vision keeps it. v0.53.0 adds one
		# more write, `generate_employee_badge_pass`, and v0.55.0 a second,
		# `collect_form_signature` — the call that files the capture a
		# missing-signature alert raised a task to go and collect. v0.56.0 adds
		# `generate_employee_id_card`, which puts the badge in the Attachments
		# sidebar of the Employee form somebody already has open. v0.57.0 adds
		# one more write, `dismiss_compliance_alert` — the same dismissal
		# `dismiss_alert` makes, gated on the alert's own say-so, for the callers
		# who are not sitting in front of the record.
		self.assertEqual(len(self.registry.TOOLS), 399)
		self.assertEqual(len(self.registry.READ_TOOLS), 181)
		self.assertEqual(len(self.registry.MUTATING_TOOLS), 218)


if __name__ == "__main__":
	unittest.main()
