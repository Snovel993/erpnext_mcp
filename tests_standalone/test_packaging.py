# SPDX-License-Identifier: MIT
"""What has to be on disk for `bench migrate` to survive.

WHY THIS MODULE EXISTS. v0.7.0 shipped two DocType folders with a JSON and no
Python module, and `bench migrate` died on a live site with

    ModuleNotFoundError: No module named
    'erpnext_mcp.erpnext_mcp.doctype.asset_depreciation_posting.asset_depreciation_posting'

Frappe imports `<folder>/<folder>.py` for **every** DocType it loads — child
tables included, and whether or not the controller has anything in it. The two
child tables had no logic, so they were given no module, which is the trap: an
empty controller is mandatory, not optional.

The in-bench suite had a test that the DocTypes existed. That is not the same
question — `frappe.db.exists("DocType", …)` reads a row, and a row can exist for
a doctype whose module cannot be imported. So the failing case sat exactly in
the gap between "the JSON is there" and "Frappe can load it".

This module closes the gap without a bench: it walks the app's own doctype
folders on disk and asserts each one is a package Frappe could import, with the
class it would look for. It is a packaging test, not a behaviour test, and it
runs in CI on every push.
"""

import importlib
import json
import os
import unittest

from .harness import DOCTYPE_DIR, frappe

#: Frappe's own scrub: a DocType's folder and module are its name lowercased with
#: spaces and hyphens as underscores. Reproduced rather than imported so this
#: test fails on a name the app got wrong rather than agreeing with it.
def scrub(name: str) -> str:
	return name.replace(" ", "_").replace("-", "_").lower()


def doctype_folders():
	for entry in sorted(os.listdir(DOCTYPE_DIR)):
		folder = os.path.join(DOCTYPE_DIR, entry)
		if entry == "__pycache__" or not os.path.isdir(folder):
			continue
		if not os.path.exists(os.path.join(folder, f"{entry}.json")):
			continue
		yield entry, folder


class ShippedDocTypes(unittest.TestCase):
	def test_there_are_doctype_folders_to_check(self):
		"""Guard against the walk silently finding nothing and passing."""
		self.assertGreaterEqual(len(list(doctype_folders())), 8)

	def test_every_doctype_folder_is_an_importable_package(self):
		"""`__init__.py`, or Python cannot import the module inside it."""
		for entry, folder in doctype_folders():
			with self.subTest(doctype=entry):
				self.assertTrue(
					os.path.exists(os.path.join(folder, "__init__.py")),
					f"{entry}/__init__.py is missing, so the folder is not a package",
				)

	def test_every_doctype_has_the_python_module_frappe_imports(self):
		"""THE v0.7.0 REGRESSION. A child table needs one too."""
		for entry, folder in doctype_folders():
			with self.subTest(doctype=entry):
				self.assertTrue(
					os.path.exists(os.path.join(folder, f"{entry}.py")),
					f"{entry}/{entry}.py is missing. Frappe imports it for every DocType, "
					"child tables included — bench migrate raises ModuleNotFoundError and "
					"stops. An empty controller is mandatory, not optional.",
				)

	def test_the_folder_name_is_the_scrubbed_doctype_name(self):
		"""Frappe derives the module path from the DocType name, not the folder."""
		for entry, folder in doctype_folders():
			with self.subTest(doctype=entry):
				with open(os.path.join(folder, f"{entry}.json")) as handle:
					payload = json.load(handle)
				self.assertEqual(
					scrub(payload["name"]),
					entry,
					f"DocType {payload['name']!r} would be imported from "
					f"{scrub(payload['name'])}/, but it lives in {entry}/",
				)

	def test_every_module_defines_the_controller_class_frappe_looks_for(self):
		"""`get_controller` asks for the class named after the DocType with the
		spaces removed. A module without it falls back to the base Document, so a
		typo here silently disables every validation the controller declares."""
		for entry, folder in doctype_folders():
			with self.subTest(doctype=entry):
				with open(os.path.join(folder, f"{entry}.json")) as handle:
					payload = json.load(handle)
				module = importlib.import_module(f"erpnext_mcp.erpnext_mcp.doctype.{entry}.{entry}")
				classname = payload["name"].replace(" ", "").replace("-", "")
				self.assertTrue(
					hasattr(module, classname),
					f"{entry}.py defines no class {classname!r}; Frappe would fall back to "
					"the base Document and every rule in it would stop running",
				)
				controller = getattr(module, classname)
				self.assertTrue(
					issubclass(controller, frappe.model.document.Document),
					f"{classname} does not subclass Document",
				)

	def test_the_app_owns_every_doctype_it_ships(self):
		"""A DocType filed under another app's module is one `bench migrate` puts
		somewhere this app cannot then find."""
		for entry, folder in doctype_folders():
			with self.subTest(doctype=entry):
				with open(os.path.join(folder, f"{entry}.json")) as handle:
					payload = json.load(handle)
				self.assertEqual(payload.get("module"), "ERPNext MCP")


class ChildTablesDeclareThemselves(unittest.TestCase):
	"""A child table with `istable` unset is created as a top-level doctype with a
	list view and permissions, and its parent's Table field then points at
	something Frappe will not embed."""

	CHILD_TABLES = ("asset_cost_center_allocation", "asset_depreciation_posting")

	def test_the_child_tables_are_marked_as_tables(self):
		for entry in self.CHILD_TABLES:
			with self.subTest(doctype=entry):
				with open(os.path.join(DOCTYPE_DIR, entry, f"{entry}.json")) as handle:
					payload = json.load(handle)
				self.assertEqual(int(payload.get("istable") or 0), 1)

	def test_every_table_field_points_at_a_doctype_this_app_ships(self):
		"""The other direction: a Table field whose options name a doctype that is
		not on disk is a form that cannot render."""
		shipped = {}
		for entry, folder in doctype_folders():
			with open(os.path.join(folder, f"{entry}.json")) as handle:
				payload = json.load(handle)
			shipped[payload["name"]] = payload
		for name, payload in shipped.items():
			for field in payload.get("fields", []):
				if field.get("fieldtype") != "Table":
					continue
				with self.subTest(doctype=name, field=field["fieldname"]):
					self.assertIn(
						field.get("options"),
						shipped,
						f"{name}.{field['fieldname']} is a Table of "
						f"{field.get('options')!r}, which this app does not ship",
					)
					self.assertEqual(int(shipped[field["options"]].get("istable") or 0), 1)
