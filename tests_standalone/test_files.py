# SPDX-License-Identifier: MIT
"""Attachments — the three tools that enforce Frappe permissions."""

import base64
import hashlib
import json

from erpnext_mcp import registry
from erpnext_mcp.errors import ToolError
from erpnext_mcp.tools import files

from .fixtures import APPROVER, ATTACHED_JE, BUYER, MAIN, OTHER, V2TestCase
from .harness import META, STORE, frappe, register_doctype


class ListAttachments(V2TestCase):
	def test_lists_every_file_on_the_document(self):
		data = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertEqual(data["count"], 3)
		self.assertNotIn("file-orphan-private", [row["name"] for row in data["attachments"]])

	def test_normalises_uploader_and_timestamp(self):
		data = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		row = next(r for r in data["attachments"] if r["name"] == "file-public-invoice")
		self.assertEqual(row["uploaded_by"], BUYER)
		self.assertTrue(row["uploaded_on"])

	def test_reports_mime_type_and_human_size(self):
		data = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		row = next(r for r in data["attachments"] if r["file_name"] == "contract.pdf")
		self.assertEqual(row["mime_type"], "application/pdf")
		self.assertTrue(row["size_human"])

	def test_flags_files_too_large_to_retrieve(self):
		data = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		by_name = {row["name"]: row for row in data["attachments"]}
		self.assertTrue(by_name["file-public-invoice"]["retrievable"])
		self.assertFalse(by_name["file-huge-export"]["retrievable"])

	def test_totals_the_attachment_size(self):
		data = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertEqual(data["total_size"], 21 + 9 + 5 * 1024 * 1024)

	def test_a_document_with_none_returns_an_empty_list(self):
		data = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": "ACC-JV-2026-00002"})
		self.assertEqual(data["count"], 0)

	def test_an_unknown_document_is_a_clean_error(self):
		message = self.tool_error("list_attachments", {"doctype": "Journal Entry", "name": "ACC-JV-NOPE"})
		self.assertIn("no Journal Entry named", message)

	def test_no_permission_on_the_parent_means_no_attachment_list(self):
		"""Listing what is attached to a document you cannot read is itself a
		leak — the filenames alone often say enough."""
		STORE.denied_permissions.add(("Journal Entry", ATTACHED_JE))
		message = self.tool_error("list_attachments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertIn("not permitted to read", message)
		self.assertIn("docs/security.md", message)


class GetAttachmentContent(V2TestCase):
	def test_returns_base64_content(self):
		data = self.tool_data("get_attachment_content", {"name": "file-public-invoice"})
		self.assertEqual(data["encoding"], "base64")
		self.assertEqual(base64.b64decode(data["content_base64"]), b"invoice line one\r\n---")

	def test_reports_the_real_size_and_mime_type(self):
		data = self.tool_data("get_attachment_content", {"name": "file-public-invoice"})
		self.assertEqual(data["file_size"], 21)
		self.assertEqual(data["mime_type"], "text/plain")

	def test_a_private_file_on_a_readable_parent_is_allowed(self):
		"""Private means "inherits the document's permission", not "nobody"."""
		data = self.tool_data("get_attachment_content", {"name": "file-private-contract"})
		self.assertTrue(data["is_private"])
		self.assertEqual(base64.b64decode(data["content_base64"]), b"top secret")

	def test_a_private_file_on_an_unreadable_parent_is_refused(self):
		STORE.denied_permissions.add(("Journal Entry", ATTACHED_JE))
		message = self.tool_error("get_attachment_content", {"name": "file-private-contract"})
		self.assertIn("not permitted to read", message)
		self.assertIn(ATTACHED_JE, message)

	def test_the_file_doctypes_own_permission_is_consulted(self):
		STORE.denied_permissions.add(("File", "read"))
		message = self.tool_error("get_attachment_content", {"name": "file-public-invoice"})
		self.assertIn("not permitted to read File", message)

	def test_an_unattached_private_file_belongs_to_its_owner(self):
		self.configure(enabled=1, require_user_context=1, mcp_system_user=BUYER)
		message = self.tool_error("get_attachment_content", {"name": "file-orphan-private"})
		self.assertIn("attached to no document", message)
		self.assertIn("owner or a System Manager", message)

	def test_its_owner_can_read_it(self):
		self.configure(enabled=1, require_user_context=1, mcp_system_user=APPROVER)
		data = self.tool_data("get_attachment_content", {"name": "file-orphan-private"})
		self.assertEqual(base64.b64decode(data["content_base64"]), b"scratch")

	def test_a_system_manager_can_read_it(self):
		data = self.tool_data("get_attachment_content", {"name": "file-orphan-private"})
		self.assertEqual(base64.b64decode(data["content_base64"]), b"scratch")

	def test_an_oversized_file_is_refused_with_the_size_and_the_url(self):
		message = self.tool_error("get_attachment_content", {"name": "file-huge-export"})
		self.assertIn("5.0 MB", message)
		self.assertIn("/private/files/payroll-export.csv", message)
		self.assertIn("max_bytes", message)

	def test_raising_max_bytes_lets_a_larger_file_through(self):
		data = self.tool_data(
			"get_attachment_content",
			{"name": "file-huge-export", "max_bytes": 6 * 1024 * 1024},
		)
		self.assertEqual(data["file_size"], 5 * 1024 * 1024)

	def test_max_bytes_has_a_hard_ceiling(self):
		message = self.tool_error(
			"get_attachment_content",
			{"name": "file-public-invoice", "max_bytes": 50 * 1024 * 1024},
		)
		self.assertIn("hard ceiling", message)

	def test_a_non_positive_max_bytes_is_refused(self):
		message = self.tool_error("get_attachment_content", {"name": "file-public-invoice", "max_bytes": 0})
		self.assertIn("must be positive", message)

	def test_a_stale_file_size_does_not_get_past_the_cap(self):
		"""file_size is a stored number and can lie; the bytes on disk decide."""
		STORE.get_raw("File", "file-huge-export")["file_size"] = 10
		message = self.tool_error("get_attachment_content", {"name": "file-huge-export"})
		self.assertIn("actually 5.0 MB", message)
		self.assertIn("Nothing was returned", message)

	def test_a_missing_file_on_disk_is_explained(self):
		del STORE.file_contents["file-public-invoice"]
		message = self.tool_error("get_attachment_content", {"name": "file-public-invoice"})
		self.assertIn("could not read", message)
		self.assertIn("no longer exists on disk", message)

	def test_an_unknown_file_reminds_you_it_wants_the_docname(self):
		message = self.tool_error("get_attachment_content", {"name": "invoice.txt"})
		self.assertIn("not the filename", message)

	def test_reading_an_attachment_is_audited(self):
		self.tool_data("get_attachment_content", {"name": "file-private-contract"})
		row = self.assertAudited("get_attachment_content", status="Success")
		self.assertIn("contract.pdf", row["result_summary"])

	def test_the_content_never_lands_in_the_audit_log(self):
		"""The log records that a file was read, not what was in it."""
		self.tool_data("get_attachment_content", {"name": "file-private-contract"})
		row = self.assertAudited("get_attachment_content")
		self.assertNotIn("top secret", row["result_summary"])
		self.assertNotIn("top secret", row["arguments_json"])


ATTACH_ON = {"allow_attach_file_to_document": 1}
STATEMENT = b"%PDF-1.4 WFA statement 2025-12-31\n"
STATEMENT_B64 = base64.b64encode(STATEMENT).decode("ascii")
STATEMENT_SHA = hashlib.sha256(STATEMENT).hexdigest()

#: The two JEs the fixture ships: one submitted, one draft. Both are live
#: records that evidence belongs on, which is the point of testing against both.
SUBMITTED_JE = ATTACHED_JE
DRAFT_JE = "ACC-JV-2026-00002"


class ReadAttachedBytesUnchecked(V2TestCase):
	"""The one reader in the module that skips Frappe's permission on the parent.

	v0.92.2. It exists for a worker's own pay stub, which is attached to a payroll
	run they may not read — see the function's own docstring for why that right is
	narrower than any role. These are the checks that stand in place of the one it
	does not make.
	"""

	def reader(self, doctype, name, file_name):
		return files.read_attached_bytes_unchecked(doctype, name, file_name)

	def test_it_returns_the_bytes_of_the_named_file(self):
		content = self.reader("Journal Entry", ATTACHED_JE, "invoice.txt")
		self.assertEqual(content, b"invoice line one\r\n---")

	def test_it_reads_a_file_whose_parent_the_caller_may_not_read(self):
		"""THE WHOLE POINT. `get_attachment_content` refuses this exact case."""
		STORE.denied_permissions.add(("Journal Entry", ATTACHED_JE))
		self.assertIn(
			"not permitted to read",
			self.tool_error("get_attachment_content", {"name": "file-public-invoice"}),
		)
		self.assertEqual(self.reader("Journal Entry", ATTACHED_JE, "invoice.txt"), b"invoice line one\r\n---")

	def test_a_file_name_that_is_not_on_that_parent_is_refused(self):
		"""It takes a parent and a NAME, so it cannot be walked like a docname."""
		for doctype, name, file_name in (
			("Journal Entry", ATTACHED_JE, "somebody-elses.pdf"),
			("Journal Entry", "ACC-JV-2026-00002", "invoice.txt"),
			("Farm Payroll Entry", ATTACHED_JE, "invoice.txt"),
		):
			with self.subTest(file_name=file_name):
				with self.assertRaises(ToolError) as refusal:
					self.reader(doctype, name, file_name)
				self.assertIn("0 matched", str(refusal.exception))

	def test_two_files_of_one_name_on_one_parent_are_refused_rather_than_guessed(self):
		"""Which of two is 'the' file is not a question this reader may answer."""
		STORE.seed(
			"File",
			[
				{
					"name": "file-duplicate-invoice",
					"file_name": "invoice.txt",
					"file_url": "/files/invoice.txt",
					"attached_to_doctype": "Journal Entry",
					"attached_to_name": ATTACHED_JE,
				}
			],
		)
		with self.assertRaises(ToolError) as refusal:
			self.reader("Journal Entry", ATTACHED_JE, "invoice.txt")
		self.assertIn("2 matched", str(refusal.exception))

	def test_it_is_on_no_tool_schema(self):
		"""A reader that skips the permission check must not be callable as a tool."""
		self.assertNotIn("read_attached_bytes_unchecked", registry.TOOLS)


class AttachFileToDocumentIsOffUntilAnOperatorSaysOtherwise(V2TestCase):
	def test_the_switch_is_off_on_a_fresh_install(self):
		"""The whole point of shipping it with a kill switch present."""
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn("switched off", message)
		self.assertIn("allow_attach_file_to_document", message)

	def test_a_disabled_tool_is_not_advertised(self):
		body, _ = self.call("tools/list")
		names = [tool["name"] for tool in body["result"]["tools"]]
		self.assertNotIn("attach_file_to_document", names)

	def test_enabling_it_advertises_it_as_mutating(self):
		self.configure(**ATTACH_ON)
		body, _ = self.call("tools/list")
		tool = next(t for t in body["result"]["tools"] if t["name"] == "attach_file_to_document")
		self.assertFalse(tool["annotations"]["readOnlyHint"])

	def test_a_blocked_call_is_still_audited(self):
		self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertAudited("attach_file_to_document", status="Blocked")


class AttachFileToDocument(V2TestCase):
	def setUp(self):
		super().setUp()
		self.configure(**ATTACH_ON)

	def attach(self, **overrides):
		payload = {
			"doctype": "Journal Entry",
			"name": DRAFT_JE,
			"file_name": "wfa-statement-2025-12-31.pdf",
			"file_content": STATEMENT_B64,
		}
		payload.update({k: v for k, v in overrides.items() if v is not None})
		return self.tool_data("attach_file_to_document", payload)

	# -- the happy path ------------------------------------------------------
	def test_it_attaches_to_the_journal_entry_named(self):
		data = self.attach()
		self.assertEqual(data["attached_to_doctype"], "Journal Entry")
		self.assertEqual(data["attached_to_name"], DRAFT_JE)
		rows = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Journal Entry", "attached_to_name": DRAFT_JE},
			fields=["name", "file_name", "is_private"],
		)
		self.assertEqual([row["file_name"] for row in rows], ["wfa-statement-2025-12-31.pdf"])
		self.assertEqual(rows[0]["name"], data["file"])

	def test_the_bytes_round_trip(self):
		data = self.attach()
		read_back = self.tool_data("get_attachment_content", {"name": data["file"]})
		self.assertEqual(base64.b64decode(read_back["content_base64"]), STATEMENT)

	def test_it_is_private_by_default(self):
		data = self.attach()
		self.assertTrue(data["is_private"])
		self.assertEqual(frappe.db.get_value("File", data["file"], "is_private"), 1)

	def test_public_is_possible_and_says_so_loudly(self):
		data = self.attach(is_private=False)
		self.assertFalse(data["is_private"])
		self.assertIn("PUBLIC", data["note"])

	def test_it_reports_the_size_and_the_hash(self):
		data = self.attach()
		self.assertEqual(data["file_size"], len(STATEMENT))
		self.assertEqual(data["sha256"], STATEMENT_SHA)
		self.assertTrue(data["size_human"])
		self.assertEqual(data["mime_type"], "application/pdf")

	def test_attaching_to_a_submitted_entry_is_normal(self):
		"""A posted entry is exactly the thing a statement supports."""
		data = self.attach(name=SUBMITTED_JE)
		self.assertEqual(data["parent_docstatus"], 1)
		self.assertEqual(data["attachments_before"], 3)
		self.assertEqual(data["attachments_after"], 4)

	def test_it_shows_up_in_list_attachments(self):
		self.attach()
		listed = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": DRAFT_JE})
		self.assertEqual(listed["count"], 1)

	def test_a_url_can_be_recorded_instead_of_uploading(self):
		data = self.tool_data(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "wfa-statement-2025-12-31.pdf",
				"file_url": "https://vault.example.test/2025-12.pdf",
			},
		)
		self.assertEqual(data["source"], "file_url")
		self.assertEqual(data["file_url"], "https://vault.example.test/2025-12.pdf")
		self.assertIsNone(data["sha256"])

	def test_any_doctype_will_do_not_just_journal_entries(self):
		"""The gap this tool fills: receipts belong on bank transactions too."""
		data = self.attach(doctype="Bank Transaction", name="BT-2026-0001", file_name="receipt.pdf")
		self.assertEqual(data["attached_to_doctype"], "Bank Transaction")

	# -- refusals ------------------------------------------------------------
	def test_an_unknown_doctype_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{"doctype": "Warp Core", "name": "WC-1", "file_name": "x.pdf", "file_content": STATEMENT_B64},
		)
		self.assertIn("no DocType named 'Warp Core'", message)
		self.assertIn("Nothing was attached", message)

	def test_an_unknown_docname_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": "ACC-JV-9999-00001",
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn("no Journal Entry named", message)
		self.assertIn("Nothing was attached", message)

	def test_a_missing_doctype_argument_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document", {"name": DRAFT_JE, "file_name": "x.pdf", "file_content": STATEMENT_B64}
		)
		self.assertIn("doctype is required", message)

	def test_a_missing_name_argument_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{"doctype": "Journal Entry", "file_name": "x.pdf", "file_content": STATEMENT_B64},
		)
		self.assertIn("name is required", message)

	def test_a_missing_file_name_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{"doctype": "Journal Entry", "name": DRAFT_JE, "file_content": STATEMENT_B64},
		)
		self.assertIn("file_name is required", message)

	def test_no_content_and_no_url_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{"doctype": "Journal Entry", "name": DRAFT_JE, "file_name": "x.pdf"},
		)
		self.assertIn("nothing to attach", message)

	def test_content_and_a_url_together_are_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
				"file_url": "https://vault.example.test/x.pdf",
			},
		)
		self.assertIn("not both", message)

	def test_content_that_is_not_base64_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": "not base64 at all!!",
			},
		)
		self.assertIn("valid base64", message)
		self.assertIn("Nothing was attached", message)

	def test_content_over_the_ceiling_is_refused(self):
		oversized = base64.b64encode(b"x" * (9 * 1024 * 1024)).decode("ascii")
		message = self.tool_error(
			"attach_file_to_document",
			{"doctype": "Journal Entry", "name": DRAFT_JE, "file_name": "big.pdf", "file_content": oversized},
		)
		self.assertIn("ceiling", message)
		self.assertIn("file_url", message)

	def test_no_write_permission_on_the_parent_means_no_attach(self):
		STORE.denied_permissions.add(("Journal Entry", "write"))
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn("not permitted to write", message)
		self.assertIn("docs/security.md", message)

	def test_the_same_filename_twice_is_refused_and_names_the_first(self):
		first = self.attach()
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "wfa-statement-2025-12-31.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn(first["file"], message)
		self.assertIn("already done", message)

	def test_the_same_filename_on_a_different_document_is_fine(self):
		self.attach()
		data = self.attach(name=SUBMITTED_JE)
		self.assertEqual(data["attached_to_name"], SUBMITTED_JE)

	def test_the_doctypes_own_attachment_limit_is_honoured(self):
		META["Journal Entry"].max_attachments = 3
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": SUBMITTED_JE,
				"file_name": "fourth.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn("at most 3", message)
		self.assertIn("not by this app", message)

	# -- cancelled parents ---------------------------------------------------
	def test_a_cancelled_parent_is_refused(self):
		frappe.db.set_value("Journal Entry", DRAFT_JE, "docstatus", 2)
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn("CANCELLED", message)
		self.assertIn("allow_cancelled", message)

	def test_a_cancelled_parent_can_be_overridden_explicitly(self):
		frappe.db.set_value("Journal Entry", DRAFT_JE, "docstatus", 2)
		data = self.attach(allow_cancelled=True)
		self.assertEqual(data["parent_docstatus"], 2)
		self.assertTrue(data["file"])

	# -- the company guard ---------------------------------------------------
	def test_a_matching_company_passes(self):
		data = self.attach(company=MAIN)
		self.assertEqual(data["parent_company"], MAIN)

	def test_a_mismatched_company_is_refused(self):
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
				"company": OTHER,
			},
		)
		self.assertIn("belongs to company", message)
		self.assertIn("Nothing was attached", message)

	def test_a_company_guard_that_cannot_be_applied_is_an_error(self):
		"""A guard the caller believes ran and did not is worse than no guard."""
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "User",
				"name": BUYER,
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
				"company": MAIN,
			},
		)
		self.assertIn("has no company field", message)

	# -- extension allowlist, read off the site ------------------------------
	def declare_allowlist(self, extensions):
		"""Give the fake site the System Settings field a real one may or may not have.

		The double ships no System Settings meta at all, which is faithful to the
		case that matters most: a site whose System Settings has no
		`allowed_file_extensions` field must restrict nothing, and `compat.has_field`
		answering "no" is exactly what makes that true.
		"""
		register_doctype(
			"System Settings",
			[{"fieldname": "allowed_file_extensions", "fieldtype": "Small Text"}],
			issingle=True,
		)
		STORE.singles["System Settings"] = {"allowed_file_extensions": extensions}

	def test_no_allowlist_on_the_site_restricts_nothing(self):
		data = self.attach(file_name="statement.xyzzy")
		self.assertTrue(data["file"])

	def test_the_sites_own_allowlist_is_honoured(self):
		self.declare_allowlist("pdf, png")
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": DRAFT_JE,
				"file_name": "statement.exe",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertIn("pdf, png", message)
		self.assertIn("the site's", message)

	def test_an_extension_on_the_sites_allowlist_passes(self):
		self.declare_allowlist("pdf, png")
		self.assertTrue(self.attach()["file"])

	# -- dry run -------------------------------------------------------------
	def test_a_dry_run_writes_nothing(self):
		data = self.attach(dry_run=True)
		self.assertTrue(data["dry_run"])
		self.assertTrue(data["would_attach"])
		self.assertNotIn("file", data)
		self.assertEqual(
			frappe.db.get_all(
				"File", filters={"attached_to_doctype": "Journal Entry", "attached_to_name": DRAFT_JE}
			),
			[],
		)

	def test_a_dry_run_still_reports_the_size_and_hash(self):
		data = self.attach(dry_run=True)
		self.assertEqual(data["file_size"], len(STATEMENT))
		self.assertEqual(data["sha256"], STATEMENT_SHA)

	def test_a_dry_run_refuses_what_a_live_run_would_refuse(self):
		message = self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": "ACC-JV-9999-00001",
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
				"dry_run": True,
			},
		)
		self.assertIn("no Journal Entry named", message)

	def test_a_dry_run_followed_by_a_live_run_attaches_once(self):
		self.attach(dry_run=True)
		self.attach()
		listed = self.tool_data("list_attachments", {"doctype": "Journal Entry", "name": DRAFT_JE})
		self.assertEqual(listed["count"], 1)

	# -- audit ---------------------------------------------------------------
	def test_the_audit_row_carries_the_parent_the_size_and_the_hash(self):
		self.attach()
		row = self.assertAudited("attach_file_to_document", status="Success")
		self.assertIn("Journal Entry", row["result_summary"])
		self.assertIn(DRAFT_JE, row["result_summary"])
		self.assertIn(STATEMENT_SHA[:12], row["result_summary"])
		self.assertIn("wfa-statement-2025-12-31.pdf", row["result_summary"])

	def test_the_base64_payload_does_not_crowd_the_parent_out_of_the_log(self):
		"""A megabyte of base64 sorts before `name`; eliding it keeps the row useful."""
		big = base64.b64encode(b"x" * (1024 * 1024)).decode("ascii")
		self.tool_data(
			"attach_file_to_document",
			{"doctype": "Journal Entry", "name": DRAFT_JE, "file_name": "big.pdf", "file_content": big},
		)
		row = self.assertAudited("attach_file_to_document", status="Success")
		arguments = json.loads(row["arguments_json"])
		self.assertEqual(arguments["name"], DRAFT_JE)
		self.assertEqual(arguments["doctype"], "Journal Entry")
		self.assertEqual(arguments["file_name"], "big.pdf")
		self.assertIn("elided", arguments["file_content"])

	def test_a_refused_attach_is_audited_as_an_error(self):
		self.tool_error(
			"attach_file_to_document",
			{
				"doctype": "Journal Entry",
				"name": "ACC-JV-9999-00001",
				"file_name": "x.pdf",
				"file_content": STATEMENT_B64,
			},
		)
		self.assertAudited("attach_file_to_document", status="Error")
