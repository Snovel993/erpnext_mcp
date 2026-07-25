# SPDX-License-Identifier: MIT
"""Attachments — the two tools that enforce Frappe permissions."""

import base64

from .fixtures import APPROVER, ATTACHED_JE, BUYER, V2TestCase
from .harness import STORE


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
