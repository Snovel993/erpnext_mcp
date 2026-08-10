# SPDX-License-Identifier: MIT
"""The badge where somebody is already looking: the Employee record. v0.56.0.

`test_badges.py` proves a badge is minted, recorded and drawn. `test_badge_sheet`
and `test_badge_print_format` prove it can be laid out on paper. None of them
answered the complaint that produced this release, which was not about making
badges at all: badges were being issued and then being unfindable, because every
call returned base64 in a JSON payload and wrote nothing to the record an HR
manager actually opens.

FOUR CLAIMS.

1. `TheQRLandsOnTheEmployee` — issuing a badge puts the QR in the Employee's own
   Attachments, privately, once per badge however many times it is reprinted.

2. `TheIDCard` — `generate_employee_id_card` issues or reuses a badge, draws the
   card in the print format's own layout, and attaches it. It does NOT consume a
   second identifier for a reprint, and it survives a bench with no PDF binary
   with the badge still issued and the reason said out loud.

3. `TheDeskButton` — `api/badges.employee_badge_card` checks `write` on the
   SPECIFIC record, turns a ToolError into a modal rather than a 500, and hands
   the card back as HTML rather than as a URL to a file that may not exist.

4. `TheClientScripts` — the two buttons are Client Script ROWS and not
   `doctype_js` hooks, they are created only when absent, and each names a
   method that actually exists.
"""

import unittest.mock

import frappe

from erpnext_mcp import badge_form_action, badge_list_action
from erpnext_mcp.api import badges as badge_api
from erpnext_mcp.tools import badges

from .fixtures import MAIN, MAIN_ABBR, SeededTestCase, install_hrms
from .harness import STORE

EMP = "HR-EMP-00001"
EMP_NAME = "Ana Reyes"
SECOND = "HR-EMP-00002"

ON = {
	f"allow_{name}": 1
	for name in ("generate_employee_badge_qr", "generate_employee_id_card", "resolve_badge")
}


class EmployeeBadgeTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		install_hrms()
		self.configure(enabled=1, **ON)
		STORE.seed(
			"Employee",
			[
				{
					"name": EMP,
					"employee_name": EMP_NAME,
					"first_name": "Ana",
					"last_name": "Reyes",
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": SECOND,
					"employee_name": "Marco Vega",
					"first_name": "Marco",
					"last_name": "Vega",
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
					"date_of_joining": "2025-01-15",
				},
			],
		)

	def attachments(self, employee: str = EMP) -> list:
		return [
			row
			for row in STORE.rows("File")
			if row.get("attached_to_doctype") == "Employee"
			and row.get("attached_to_name") == employee
		]

	def issue(self, **overrides) -> dict:
		payload = {"employee": EMP}
		payload.update(overrides)
		return self.tool_data("generate_employee_badge_qr", payload)

	def card(self, **overrides) -> dict:
		payload = {"employee": EMP}
		payload.update(overrides)
		return self.tool_data("generate_employee_id_card", payload)


# ── 1. the QR on the record ──────────────────────────────────────────────
class TheQRLandsOnTheEmployee(EmployeeBadgeTestCase):
	def test_issuing_a_badge_attaches_its_qr_to_the_employee(self):
		result = self.issue()
		files = self.attachments()
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0]["file_name"], f"badge-{MAIN_ABBR}-0001-qr.png")
		self.assertTrue(result["attachment"]["attached"])
		self.assertEqual(result["attachment"]["file_url"], files[0]["file_url"])

	def test_the_attachment_is_private(self):
		"""Every file this app writes is private. A badge QR is the least
		sensitive of them and is private anyway — "which of our files are safe to
		serve to anybody who guesses the URL" is not a question worth two
		answers."""
		self.issue()
		self.assertTrue(self.attachments()[0]["is_private"])

	def test_reprinting_replaces_its_own_file_rather_than_piling_up(self):
		first = self.issue()
		second = self.issue()
		# Same badge — a reprint must not consume an identifier — and therefore
		# the same filename, replaced in place.
		self.assertEqual(second["badge_id"], first["badge_id"])
		self.assertTrue(second["reused"])
		self.assertEqual(len(self.attachments()), 1)
		self.assertTrue(second["attachment"]["replaced"])

	def test_reissuing_leaves_the_retired_badges_file_alone(self):
		"""A new badge ID is a new filename. The old card's QR is the evidence of
		what that card was, and a foreman holding it should still be able to see
		which one it is."""
		first = self.issue()
		second = self.issue(regenerate=True)
		self.assertNotEqual(second["badge_id"], first["badge_id"])
		names = sorted(row["file_name"] for row in self.attachments())
		self.assertEqual(
			names,
			[f"badge-{first['badge_id']}-qr.png", f"badge-{second['badge_id']}-qr.png"],
		)

	def test_attach_false_writes_nothing(self):
		result = self.issue(attach=False)
		self.assertEqual(self.attachments(), [])
		self.assertFalse(result["attachment"]["attached"])

	def test_a_failed_attachment_does_not_lose_the_badge(self):
		"""The badge is issued by the time the file is written. Failing the whole
		call because a sidebar entry did not appear would take a working badge
		away from somebody for a cosmetic reason."""
		with unittest.mock.patch.object(
			badges.artifacts, "attach_bytes", side_effect=RuntimeError("disk full")
		):
			result = self.issue()
		self.assertEqual(result["badge_id"], f"{MAIN_ABBR}-0001")
		self.assertFalse(result["attachment"]["attached"])
		self.assertIn("disk full", result["attachment"]["note"])
		# And the register row — the thing a scan resolves through — is written.
		self.assertEqual(len(STORE.rows("Bucket Log Badge Map")), 1)


# ── 2. the card ──────────────────────────────────────────────────────────
class TheIDCard(EmployeeBadgeTestCase):
	def test_it_issues_a_badge_and_returns_the_card(self):
		data = self.card()
		self.assertEqual(data["badge_id"], f"{MAIN_ABBR}-0001")
		self.assertTrue(data["created"])
		self.assertIn("badge-card", data["card_html"])
		self.assertIn(EMP_NAME, data["card_html"])

	def test_the_card_is_the_print_formats_layout_and_not_a_second_one(self):
		"""Three layouts is two too many for a thing that has to line up with a
		pre-printed lanyard slot."""
		from erpnext_mcp import badge_sheet

		data = self.card()
		one = badge_sheet.card_html(
			{
				"employee": EMP,
				"employee_name": EMP_NAME,
				"badge_id": data["badge_id"],
				"company": MAIN,
				"designation": "Picker",
				"photo_placeholder": "AR",
			}
		)
		# The card's own markup, not the page around it.
		self.assertIn('<div class="bc-abs bc-name">Ana Reyes</div>', one)
		self.assertIn('<div class="bc-abs bc-name">Ana Reyes</div>', data["card_html"])

	def test_a_reprint_does_not_consume_a_second_identifier(self):
		first = self.card()
		second = self.card()
		self.assertEqual(second["badge_id"], first["badge_id"])
		self.assertTrue(second["reused"])
		self.assertEqual(len(STORE.rows("Bucket Log Badge Map")), 1)

	def test_the_qr_is_attached_even_when_the_card_pdf_is_not(self):
		"""The standalone suite runs on a bench with no wkhtmltopdf, which is the
		case this asserts rather than a limitation of the test: the badge is
		issued, the QR lands, the card comes back as HTML, and the note says what
		is missing."""
		data = self.card()
		self.assertTrue(data["qr_attachment"]["attached"])
		self.assertFalse(data["card_attachment"]["attached"])
		self.assertTrue(data["card_attachment"]["note"])
		self.assertIn("card_html", data)

	def test_the_card_attaches_where_a_pdf_renderer_exists(self):
		with unittest.mock.patch.object(
			badges, "_card_pdf", return_value={"pdf": b"%PDF-1.4 fake", "note": ""}
		):
			data = self.card()
		self.assertTrue(data["card_attachment"]["attached"])
		names = sorted(row["file_name"] for row in self.attachments())
		self.assertEqual(
			names, [f"badge-{data['badge_id']}-card.pdf", f"badge-{data['badge_id']}-qr.png"]
		)

	def test_a_renderer_that_raises_is_reported_not_propagated(self):
		with unittest.mock.patch.object(
			badges, "_card_pdf", side_effect=None, return_value={"pdf": None, "note": "boom"}
		):
			data = self.card()
		self.assertFalse(data["card_attachment"]["attached"])
		self.assertEqual(data["card_attachment"]["note"], "boom")
		self.assertTrue(data["badge_id"])

	def test_it_ships_off(self):
		self.configure(enabled=1, allow_generate_employee_id_card=0)
		self.assertTrue(self.tool_error("generate_employee_id_card", {"employee": EMP}))


# ── 3. the Desk route ────────────────────────────────────────────────────
class TheDeskButton(EmployeeBadgeTestCase):
	def test_it_hands_back_the_card_and_both_urls(self):
		with unittest.mock.patch.object(
			badges, "_card_pdf", return_value={"pdf": b"%PDF-1.4 fake", "note": ""}
		):
			answer = badge_api.employee_badge_card(employee=EMP)
		self.assertEqual(answer["employee"], EMP)
		self.assertEqual(answer["badge_id"], f"{MAIN_ABBR}-0001")
		self.assertIn("bc-name", answer["html"])
		self.assertTrue(answer["qr_url"])
		self.assertTrue(answer["card_url"])
		self.assertTrue(answer["attached"])

	def test_it_checks_write_on_the_specific_record(self):
		"""Not the doctype in general — a User Permission scoping somebody to one
		company has to scope the badge button too."""
		with unittest.mock.patch.object(frappe, "has_permission") as gate:
			badge_api.employee_badge_card(employee=EMP)
		gate.assert_called_once()
		self.assertEqual(gate.call_args.args[:2], ("Employee", "write"))
		self.assertEqual(gate.call_args.kwargs.get("doc"), EMP)
		self.assertTrue(gate.call_args.kwargs.get("throw"))

	def test_a_tool_refusal_becomes_a_modal_rather_than_a_traceback(self):
		"""`frappe.throw` is the Desk's channel for "you asked for something I
		cannot do". Raised out of a whitelisted method the sentence would never
		reach the person who needs it."""
		frappe.db.set_value("Employee", EMP, "status", "Left")
		with self.assertRaises(frappe.ValidationError) as caught:
			badge_api.employee_badge_card(employee=EMP)
		self.assertIn("Left", str(caught.exception))

	def test_no_employee_named_is_refused_before_anything_is_written(self):
		with self.assertRaises(frappe.ValidationError):
			badge_api.employee_badge_card(employee="")
		self.assertEqual(STORE.rows("Bucket Log Badge Map"), [])

	def test_regenerate_arrives_as_a_string_and_is_read_as_one(self):
		"""`frappe.call` posts booleans as "0" and "1", and "0" is truthy in
		Python — which would make every Desk press of the button mint a new badge
		and retire the card in somebody's pocket."""
		first = badge_api.employee_badge_card(employee=EMP, regenerate="0")
		second = badge_api.employee_badge_card(employee=EMP, regenerate="0")
		self.assertEqual(second["badge_id"], first["badge_id"])
		self.assertTrue(second["reused"])

		third = badge_api.employee_badge_card(employee=EMP, regenerate="1")
		self.assertNotEqual(third["badge_id"], first["badge_id"])

	def test_it_is_whitelisted_for_post(self):
		# The attribute the test double's own `frappe.whitelist` sets. Asserting
		# on `is_whitelisted` with a default of True — as it would be tempting to
		# do — passes on a function nobody decorated at all.
		self.assertTrue(getattr(badge_api.employee_badge_card, "__wrapped_whitelisted__", False))


# ── 4. the two buttons are records ───────────────────────────────────────
class TheClientScripts(EmployeeBadgeTestCase):
	def test_neither_button_is_a_doctype_js_hook_on_somebody_elses_form(self):
		"""`hooks.py` promises installing this app cannot change how a form the
		operator already had behaves, and `test_hooks.TheFormScripts` holds every
		doctype_js entry to one this app created. Employee is ERPNext's."""
		from erpnext_mcp import hooks

		self.assertNotIn("Employee", hooks.doctype_js)
		self.assertFalse(hasattr(hooks, "doctype_list_js"))

	def test_the_form_script_is_seeded_once_and_not_again(self):
		first = badge_form_action.seed_badge_form_action()
		self.assertTrue(first["created"])
		again = badge_form_action.seed_badge_form_action()
		self.assertFalse(again["created"])
		self.assertEqual(again["reason"], "already present")
		rows = [row for row in STORE.rows("Client Script") if row.get("view") == "Form"]
		self.assertEqual(len(rows), 1)

	def test_a_deleted_script_stays_deleted(self):
		"""An operator who deleted it has declined it. A customisation that
		reappears at every migrate is one nobody can decline."""
		badge_form_action.seed_badge_form_action()
		badge_form_action.remove_badge_form_action()
		self.assertEqual(
			[row for row in STORE.rows("Client Script") if row.get("view") == "Form"], []
		)
		# Seeding again DOES recreate it — that is `after_migrate`'s job and the
		# uninstall path is what removes it for good. What must not happen is a
		# second row beside the first.
		badge_form_action.seed_badge_form_action()
		rows = [row for row in STORE.rows("Client Script") if row.get("view") == "Form"]
		self.assertEqual(len(rows), 1)

	def test_each_script_names_a_method_that_exists(self):
		"""A button calling a dotted path that resolves to nothing is a button
		that does nothing and says nothing."""
		for path in (badge_form_action.CARD_METHOD, badge_list_action.SHEET_METHOD):
			with self.subTest(path=path):
				module_path, _, attribute = path.rpartition(".")
				module = __import__(module_path, fromlist=[attribute])
				self.assertTrue(callable(getattr(module, attribute)))
				self.assertIn(path, self._source_for(path))

	def _source_for(self, path: str) -> str:
		if path == badge_form_action.CARD_METHOD:
			return badge_form_action.SCRIPT_SOURCE
		return badge_list_action.SCRIPT_SOURCE

	def test_the_form_script_targets_the_form_view_and_the_list_script_the_list(self):
		self.assertEqual(badge_form_action.FORM_VIEW, "Form")
		self.assertEqual(badge_list_action.LIST_VIEW, "List")
		self.assertNotEqual(badge_form_action.SCRIPT_MARKER, badge_list_action.SCRIPT_MARKER)
