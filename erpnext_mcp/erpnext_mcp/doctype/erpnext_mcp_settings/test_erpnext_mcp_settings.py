# SPDX-License-Identifier: MIT
"""Doctype tests for ERPNext MCP Settings — the form's guardrails, in a bench.

The same rules are covered far more thoroughly, and far faster, by
`tests_standalone/test_settings.py`. What only a bench can show is that Frappe
itself enforces them: that `frappe.throw` in `validate` really does abort the
save, and that a Password field really is unreadable after it is stored.
"""

import frappe

try:  # Frappe v16 renamed the base classes.
	from frappe.tests import IntegrationTestCase as BaseTestCase
except ImportError:  # Frappe v14 / v15
	from frappe.tests.utils import FrappeTestCase as BaseTestCase

from erpnext_mcp import registry, settings


class TestERPNextMCPSettings(BaseTestCase):
	def setUp(self):
		super().setUp()
		self.doc = frappe.get_single(settings.SETTINGS_DOCTYPE)
		self.doc.flags.ignore_permissions = True

	def tearDown(self):
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		super().tearDown()

	def test_an_invalid_cidr_aborts_the_save(self):
		self.doc.allowed_cidrs = "192.168.0.0/16,10.0.0.300/8"
		with self.assertRaises(frappe.ValidationError):
			self.doc.save()

	def test_a_valid_cidr_list_saves_and_parses(self):
		self.doc.allowed_cidrs = "10.42.0.0/16"
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		self.assertEqual(settings.allowed_cidrs(), ["10.42.0.0/16"])

	def test_enabling_without_a_token_aborts_the_save(self):
		self.doc.auth_token = ""
		self.doc.enabled = 0
		self.doc.save()
		frappe.db.delete("Singles", {"doctype": settings.SETTINGS_DOCTYPE, "field": "auth_token"})
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		fresh = frappe.get_single(settings.SETTINGS_DOCTYPE)
		fresh.flags.ignore_permissions = True
		fresh.enabled = 1
		with self.assertRaises(frappe.ValidationError):
			fresh.save()

	def test_enabling_with_an_empty_allowlist_aborts_the_save(self):
		self.doc.auth_token = "token-for-the-empty-allowlist-test"
		self.doc.enabled = 1
		self.doc.allowed_cidrs = ""
		with self.assertRaises(frappe.ValidationError):
			self.doc.save()

	def test_a_disabled_mcp_system_user_aborts_the_save(self):
		email = "erpnext-mcp-disabled-user@example.test"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Disabled",
					"send_welcome_email": 0,
					"enabled": 0,
				}
			).insert(ignore_permissions=True)
		self.doc.require_user_context = 1
		self.doc.mcp_system_user = email
		with self.assertRaises(frappe.ValidationError):
			self.doc.save()

	def test_the_generated_token_is_not_readable_from_the_document(self):
		"""Frappe stores a Password field in the encrypted auth table; the only
		way back out is get_password, which is why the token is shown once."""
		token = frappe.get_single(settings.SETTINGS_DOCTYPE).generate_token()["token"]
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		reloaded = frappe.get_single(settings.SETTINGS_DOCTYPE)
		self.assertNotEqual(reloaded.get("auth_token"), token)
		self.assertEqual(reloaded.get_password("auth_token", raise_exception=False), token)

	def test_generate_token_stamps_the_time(self):
		before = frappe.utils.now()
		frappe.get_single(settings.SETTINGS_DOCTYPE).generate_token()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		stamped = frappe.get_single(settings.SETTINGS_DOCTYPE).token_generated_on
		self.assertIsNotNone(stamped)
		self.assertGreaterEqual(str(stamped), before[:10])

	def test_check_fields_read_back_as_booleans_not_truthy_strings(self):
		"""tabSingles.value is a text column. If a switch ever reads back as the
		string "0", every gate in this app inverts."""
		self.doc.allow_create_journal_entry = 0
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		self.assertFalse(settings.tool_enabled("create_journal_entry"))

	def test_seed_defaults_leaves_an_operators_choice_alone(self):
		self.doc.allow_search_accounts = 0
		self.doc.save()
		settings.seed_defaults()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		self.assertFalse(settings.tool_enabled("search_accounts"))

	def test_every_tool_in_the_registry_is_switchable_on_this_site(self):
		meta = frappe.get_meta(settings.SETTINGS_DOCTYPE)
		missing = [name for name in registry.TOOLS if not meta.has_field(f"allow_{name}")]
		self.assertEqual(missing, [], f"tools with no switch: {missing}")
