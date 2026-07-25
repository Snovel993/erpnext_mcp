# SPDX-License-Identifier: MIT
"""Site-customisation tools."""

from .fixtures import V2TestCase
from .harness import INSTALLED_DOCTYPES, META, STORE


class CustomFields(V2TestCase):
	def test_lists_every_custom_field(self):
		data = self.tool_data("list_custom_fields")
		self.assertEqual(data["count"], 3)

	def test_filters_to_one_doctype(self):
		data = self.tool_data("list_custom_fields", {"doctype": "Journal Entry"})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["doctype_filter"], "Journal Entry")

	def test_orders_by_doctype_then_form_position(self):
		"""Form order is the order the question is usually about."""
		data = self.tool_data("list_custom_fields", {"doctype": "Journal Entry"})
		self.assertEqual(
			[row["fieldname"] for row in data["custom_fields"]],
			["orchard_block", "hidden_note"],
		)

	def test_surfaces_the_flags_that_explain_a_missing_field(self):
		data = self.tool_data("list_custom_fields", {"doctype": "Journal Entry"})
		hidden = next(r for r in data["custom_fields"] if r["fieldname"] == "hidden_note")
		self.assertEqual(hidden["hidden"], 1)
		self.assertEqual(hidden["insert_after"], "orchard_block")
		self.assertIn("hidden", data["note"])

	def test_counts_by_doctype(self):
		data = self.tool_data("list_custom_fields")
		self.assertEqual(data["by_doctype"], {"Journal Entry": 2, "Sales Order": 1})

	def test_an_unknown_doctype_is_refused(self):
		message = self.tool_error("list_custom_fields", {"doctype": "Invented Doctype"})
		self.assertIn("no DocType named", message)


class ClientScripts(V2TestCase):
	def test_enabled_only_by_default(self):
		data = self.tool_data("list_client_scripts")
		self.assertEqual([row["name"] for row in data["client_scripts"]], ["Journal Entry autofill"])

	def test_disabled_can_be_asked_for(self):
		data = self.tool_data("list_client_scripts", {"enabled": False})
		self.assertEqual([row["name"] for row in data["client_scripts"]], ["Sales Order legacy"])

	def test_any_returns_both(self):
		data = self.tool_data("list_client_scripts", {"enabled": "any"})
		self.assertEqual(data["count"], 2)
		self.assertIsNone(data["filters"]["enabled"])

	def test_the_body_is_truncated_and_says_so(self):
		"""Thousands of lines of form JS is expensive and rarely the question."""
		data = self.tool_data("list_client_scripts")
		row = data["client_scripts"][0]
		self.assertEqual(len(row["script_preview"]), 500)
		self.assertTrue(row["script_truncated"])
		self.assertGreater(row["script_length"], 500)

	def test_the_full_body_is_never_returned(self):
		data = self.tool_data("list_client_scripts")
		self.assertNotIn("script", data["client_scripts"][0])

	def test_a_short_script_is_not_marked_truncated(self):
		data = self.tool_data("list_client_scripts", {"enabled": False})
		row = data["client_scripts"][0]
		self.assertFalse(row["script_truncated"])
		self.assertEqual(row["script_preview"], "console.log('disabled');")

	def test_filters_to_one_doctype(self):
		data = self.tool_data("list_client_scripts", {"doctype": "Sales Order", "enabled": "any"})
		self.assertEqual(data["count"], 1)

	def test_a_nonsense_enabled_value_is_refused(self):
		message = self.tool_error("list_client_scripts", {"enabled": "sometimes"})
		self.assertIn("must be true, false, or 'any'", message)

	def test_it_points_at_where_to_read_the_rest(self):
		data = self.tool_data("list_client_scripts")
		self.assertIn("/app/client-script/", data["note"])

	def test_it_falls_back_to_custom_script_on_older_frappe(self):
		"""Client Script was called Custom Script before v13."""
		META["Custom Script"] = META["Client Script"]
		INSTALLED_DOCTYPES.discard("Client Script")
		INSTALLED_DOCTYPES.add("Custom Script")
		STORE.tables["Custom Script"] = STORE.tables.pop("Client Script")
		try:
			data = self.tool_data("list_client_scripts", {"enabled": "any"})
		finally:
			META.pop("Custom Script", None)
		self.assertEqual(data["source_doctype"], "Custom Script")
		self.assertEqual(data["count"], 2)

	def test_it_is_not_advertised_where_neither_doctype_exists(self):
		INSTALLED_DOCTYPES.discard("Client Script")
		body, _ = self.call("tools/list")
		self.assertNotIn("list_client_scripts", [tool["name"] for tool in body["result"]["tools"]])
