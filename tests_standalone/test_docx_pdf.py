# SPDX-License-Identifier: MIT
"""`regenerate_governance_document_pdf` — a fixed copy beside the editable one.

WHAT THESE TESTS ARE ABOUT.

THE ARCHIVE GAINS AND NEVER LOSES. Every path here asserts the .docx is still
attached afterwards. An archive that threw away the editable original to gain a
fixed one would have traded a problem for a worse one, and `overwrite=true` — the
one flag that does delete something — has to say which File it deleted.

THE CONVERSION IS SOMEBODY ELSE'S JOB, AND THE WIRING IS OURS. Turning a .docx
into a PDF is LibreOffice's problem; finding the right attachment, refusing the
ambiguous cases, hashing the result, attaching it privately and repointing
`attached_file` are this app's. So the conversion is substituted in most tests
and the wiring is asserted around it — and one test at the bottom does the real
thing where a converter is actually installed, so the substitution can never be
the only evidence.

A HOST WITHOUT LibreOffice IS AN ORDINARY CASE, not a broken one, and it gets a
refusal that names the package. That refusal is checked BEFORE anything is read,
so a container missing the binary does not discover it three megabytes in.
"""

import hashlib
import unittest

from erpnext_mcp import convert

from .fixtures import MAIN, V7TestCase
from .harness import STORE

ON = {
	"allow_regenerate_governance_document_pdf": 1,
	"allow_attach_governance_document": 1,
	"allow_get_governance_document_content": 1,
}

#: A PDF is anything starting `%PDF`. This is what the substituted converter
#: hands back, and what the tool's own sanity check looks for.
FAKE_PDF = b"%PDF-1.4\n% a converted governance document\n%%EOF\n"

#: A .docx is a zip. Only the first two bytes matter to anything here.
FAKE_DOCX = b"PK\x03\x04" + b"word/document.xml" + bytes(range(256)) * 4


class DocxPdfTestCase(V7TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		self._converted = []

	# -- the archive entry ---------------------------------------------------
	def an_entry(self, docx_names=("q2-2026.docx",), pdf_names=(), title="Q2 2026 Quarterly"):
		data = self.tool_data(
			"attach_governance_document",
			{
				"company": MAIN,
				"title": title,
				"category": "Prior Statement",
				"effective_date": "2026-06-30",
			},
		)
		for file_name in list(docx_names) + list(pdf_names):
			content = FAKE_DOCX if file_name.endswith(".docx") else FAKE_PDF
			STORE.file_contents[f"F-{file_name}"] = content
			STORE.seed(
				"File",
				[
					{
						"name": f"F-{file_name}",
						"file_name": file_name,
						"file_url": f"/private/files/{file_name}",
						"file_size": len(content),
						"is_private": 1,
						"attached_to_doctype": "Governance Document",
						"attached_to_name": data["name"],
					}
				],
			)
		return data["name"]

	def attachments(self, name):
		return sorted(row["file_name"] for row in STORE.rows("File") if row.get("attached_to_name") == name)

	# -- the substituted converter --------------------------------------------
	def with_converter(self, pdf=FAKE_PDF, via="libreoffice", error=None):
		"""Stand in for LibreOffice, recording what it was asked to convert.

		Substituting `convert.available` as well as `convert.docx_to_pdf` matters:
		the tool checks availability BEFORE it reads the source file, and a test
		that only replaced the conversion would pass on a host with LibreOffice and
		fail everywhere else.
		"""
		route = {"ready": True, "via": via, "binary": "/usr/bin/soffice", "detail": f"stub {via}"}
		original_available, original_convert = convert.available, convert.docx_to_pdf

		def fake_convert(content, file_name="document.docx"):
			self._converted.append((content, file_name))
			if error:
				raise convert.ConversionError(error)
			return pdf

		convert.available = lambda: route
		convert.docx_to_pdf = fake_convert
		self.addCleanup(setattr, convert, "available", original_available)
		self.addCleanup(setattr, convert, "docx_to_pdf", original_convert)

	def without_converter(self):
		original = convert.available
		convert.available = lambda: {
			"ready": False,
			"via": "",
			"binary": "",
			"detail": f"no converter on this host — {convert.INSTALL_HINT}",
		}
		self.addCleanup(setattr, convert, "available", original)


class RoundTrip(DocxPdfTestCase):
	def test_it_attaches_a_pdf_named_after_the_docx(self):
		self.with_converter()
		name = self.an_entry()
		data = self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertTrue(data["regenerated"])
		self.assertEqual(data["pdf_file_name"], "q2-2026.pdf")
		self.assertIn("q2-2026.pdf", self.attachments(name))

	def test_the_docx_is_kept(self):
		"""The whole point: this ADDS a fixed copy, it does not replace the
		editable one."""
		self.with_converter()
		name = self.an_entry()
		self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertEqual(self.attachments(name), ["q2-2026.docx", "q2-2026.pdf"])

	def test_the_converter_is_handed_the_docx_bytes(self):
		self.with_converter()
		name = self.an_entry()
		self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		content, file_name = self._converted[-1]
		self.assertEqual(content, FAKE_DOCX)
		self.assertEqual(file_name, "q2-2026.docx")

	def test_it_reports_the_size_and_sha256_of_what_it_wrote(self):
		self.with_converter()
		name = self.an_entry()
		data = self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertEqual(data["file_size"], len(FAKE_PDF))
		self.assertEqual(data["sha256"], hashlib.sha256(FAKE_PDF).hexdigest())
		self.assertEqual(STORE.file_contents[data["file"]], FAKE_PDF)

	def test_the_pdf_is_private(self):
		self.with_converter()
		name = self.an_entry()
		data = self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertTrue(data["is_private"])
		self.assertEqual(STORE.get_raw("File", data["file"])["is_private"], 1)

	def test_the_entry_now_points_at_the_pdf(self):
		"""So a reader following the archive lands on something that opens."""
		self.with_converter()
		name = self.an_entry()
		data = self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertEqual(STORE.get_raw("Governance Document", name)["attached_file"], data["file_url"])
		self.assertEqual(data["attached_file_now"], data["file_url"])

	def test_it_is_readable_back_through_the_governance_reader(self):
		self.with_converter()
		name = self.an_entry()
		self.tool_data("regenerate_governance_document_pdf", {"governance_document": name})
		read = self.tool_data("get_governance_document_content", {"name": name, "file": "q2-2026.pdf"})
		self.assertEqual(read["content"]["file_name"], "q2-2026.pdf")
		self.assertEqual(read["content"]["file_size"], len(FAKE_PDF))

	def test_a_named_source_is_used(self):
		self.with_converter()
		name = self.an_entry(docx_names=("original.docx", "amendment.docx"))
		data = self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "source_docx_file": "amendment.docx"},
		)
		self.assertEqual(data["source_file_name"], "amendment.docx")
		self.assertEqual(data["pdf_file_name"], "amendment.pdf")


class Refusals(DocxPdfTestCase):
	def test_an_entry_with_no_docx_is_refused(self):
		self.with_converter()
		name = self.an_entry(docx_names=(), pdf_names=("already.pdf",))
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertIn("has no .docx attachment", message)
		self.assertIn("already.pdf", message)

	def test_an_entry_with_nothing_attached_says_so(self):
		self.with_converter()
		name = self.an_entry(docx_names=())
		self.assertIn(
			"<nothing attached>",
			self.tool_error("regenerate_governance_document_pdf", {"governance_document": name}),
		)

	def test_two_docx_attachments_are_refused_rather_than_guessed_between(self):
		"""An original and an amendment filed together is a real thing, and being
		right half the time is worse than asking."""
		self.with_converter()
		name = self.an_entry(docx_names=("original.docx", "amendment.docx"))
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertIn("has 2 .docx attachments", message)
		self.assertIn("source_docx_file", message)

	def test_a_source_that_is_not_attached_here_is_refused(self):
		self.with_converter()
		name = self.an_entry()
		message = self.tool_error(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "source_docx_file": "somebody-elses.docx"},
		)
		self.assertIn("no attachment called 'somebody-elses.docx'", message)

	def test_a_source_that_is_not_a_docx_is_refused(self):
		self.with_converter()
		name = self.an_entry(pdf_names=("scan.pdf",))
		message = self.tool_error(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "source_docx_file": "scan.pdf"},
		)
		self.assertIn("is not a .docx", message)

	def test_an_existing_pdf_is_refused_without_overwrite(self):
		self.with_converter()
		name = self.an_entry(pdf_names=("q2-2026.pdf",))
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertIn("already has 1 PDF attachment(s)", message)
		self.assertIn("overwrite=true", message)
		self.assertIn("the .docx is kept either way", message)

	def test_a_refused_run_writes_nothing(self):
		self.with_converter()
		name = self.an_entry(pdf_names=("q2-2026.pdf",))
		self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertEqual(self.attachments(name), ["q2-2026.docx", "q2-2026.pdf"])
		self.assertEqual(self._converted, [])

	def test_an_entry_that_does_not_exist_is_refused_by_name(self):
		self.with_converter()
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": "GD-NOPE"})
		self.assertIn("no Governance Document named 'GD-NOPE'", message)
		self.assertIn("list_governance_documents", message)

	def test_a_conversion_that_fails_leaves_the_archive_alone(self):
		self.with_converter(error="LibreOffice exited 1: could not load document")
		name = self.an_entry()
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertIn("could not load document", message)
		self.assertIn("no attachment was removed", message.lower())
		self.assertEqual(self.attachments(name), ["q2-2026.docx"])


class Overwrite(DocxPdfTestCase):
	def test_it_replaces_the_pdf_and_names_what_it_deleted(self):
		"""Removing an attachment from a governance archive is not something to do
		quietly."""
		self.with_converter()
		name = self.an_entry(pdf_names=("q2-2026.pdf",))
		data = self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "overwrite": True},
		)
		self.assertEqual([entry["file_name"] for entry in data["removed"]], ["q2-2026.pdf"])
		self.assertEqual(self.attachments(name), ["q2-2026.docx", "q2-2026.pdf"])
		self.assertIn("were DELETED", data["note"])

	def test_the_docx_survives_an_overwrite(self):
		self.with_converter()
		name = self.an_entry(pdf_names=("q2-2026.pdf",))
		self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "overwrite": True},
		)
		self.assertIn("q2-2026.docx", self.attachments(name))
		self.assertEqual(STORE.file_contents["F-q2-2026.docx"], FAKE_DOCX)

	def test_the_new_pdf_is_the_converted_one(self):
		self.with_converter(pdf=b"%PDF-1.7\nfresh\n%%EOF\n")
		name = self.an_entry(pdf_names=("q2-2026.pdf",))
		data = self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "overwrite": True},
		)
		self.assertEqual(STORE.file_contents[data["file"]], b"%PDF-1.7\nfresh\n%%EOF\n")

	def test_a_differently_named_pdf_is_left_alone(self):
		"""Only the file this run would have collided with is replaced."""
		self.with_converter()
		name = self.an_entry(pdf_names=("board-minutes.pdf",))
		data = self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "overwrite": True},
		)
		self.assertEqual(data["removed"], [])
		self.assertEqual(self.attachments(name), ["board-minutes.pdf", "q2-2026.docx", "q2-2026.pdf"])


class MissingConverter(DocxPdfTestCase):
	def test_a_host_with_no_converter_is_refused_with_the_package_to_install(self):
		self.without_converter()
		name = self.an_entry()
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertIn("cannot convert a .docx", message)
		self.assertIn("libreoffice-headless", message)
		self.assertIn("docx2pdf", message)

	def test_nothing_is_installed_at_runtime(self):
		"""A tool that apt-gets a package mid-request hangs a worker and leaves the
		container different from its image."""
		self.assertIn("Nothing is installed at runtime", convert.INSTALL_HINT)

	def test_the_refusal_comes_before_the_source_file_is_read(self):
		"""So a container missing the binary does not discover it three megabytes
		in. Proven by removing the stored bytes: reading them would raise something
		other than the converter refusal."""
		self.without_converter()
		name = self.an_entry()
		STORE.file_contents.pop("F-q2-2026.docx")
		self.assertIn(
			"cannot convert a .docx",
			self.tool_error("regenerate_governance_document_pdf", {"governance_document": name}),
		)

	def test_availability_never_raises_and_always_says_what_it_found(self):
		route = convert.available()
		self.assertIn(route["ready"], (True, False))
		self.assertTrue(route["detail"])
		if not route["ready"]:
			self.assertIn("libreoffice", route["detail"])


class DryRun(DocxPdfTestCase):
	def test_it_reports_the_plan_without_converting(self):
		self.with_converter()
		name = self.an_entry()
		data = self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "dry_run": True},
		)
		self.assertFalse(data["regenerated"])
		self.assertEqual(data["source_file_name"], "q2-2026.docx")
		self.assertEqual(data["pdf_file_name"], "q2-2026.pdf")
		self.assertEqual(data["converter"], "libreoffice")
		self.assertEqual(self._converted, [])
		self.assertEqual(self.attachments(name), ["q2-2026.docx"])

	def test_a_dry_run_says_what_would_be_replaced(self):
		self.with_converter()
		name = self.an_entry(pdf_names=("q2-2026.pdf",))
		data = self.tool_data(
			"regenerate_governance_document_pdf",
			{"governance_document": name, "overwrite": True, "dry_run": True},
		)
		self.assertEqual(data["would_replace"], ["F-q2-2026.pdf"])
		self.assertIn("replacing File(s)", data["note"])

	def test_a_dry_run_still_refuses_a_missing_converter(self):
		self.without_converter()
		name = self.an_entry()
		self.assertIn(
			"cannot convert a .docx",
			self.tool_error(
				"regenerate_governance_document_pdf",
				{"governance_document": name, "dry_run": True},
			),
		)


class SwitchAndShape(DocxPdfTestCase):
	def test_it_is_off_out_of_the_box(self):
		self.with_converter()
		name = self.an_entry()
		self.configure(enabled=1)
		self.assertIn(
			"allow_regenerate_governance_document_pdf",
			self.tool_error("regenerate_governance_document_pdf", {"governance_document": name}),
		)

	def test_it_is_declared_mutating(self):
		from erpnext_mcp import registry

		self.assertIn("regenerate_governance_document_pdf", registry.MUTATING_TOOLS)

	def test_it_honours_write_permission_on_the_entry(self):
		self.with_converter()
		name = self.an_entry()
		STORE.denied_permissions.add(("Governance Document", "write"))
		message = self.tool_error("regenerate_governance_document_pdf", {"governance_document": name})
		self.assertIn("not permitted to write", message)
		self.assertIn("docs/security.md", message)


class TheConverterItself(unittest.TestCase):
	"""The parts of `erpnext_mcp.convert` that need no LibreOffice."""

	def test_a_filename_cannot_climb_out_of_the_temp_directory(self):
		self.assertEqual(convert._safe_stem("../../etc/passwd"), "passwd")
		self.assertEqual(convert._safe_stem("a b/c;d.docx"), "c_d")

	def test_an_unnameable_file_still_gets_a_stem(self):
		self.assertEqual(convert._safe_stem(""), "document")
		self.assertEqual(convert._safe_stem("///"), "document")

	def test_libreoffice_is_preferred_over_docx2pdf(self):
		"""docx2pdf drives Microsoft Word, so on the Linux container this app runs
		in it does nothing at all."""
		self.assertIn("LibreOffice headless is the workhorse", convert.__doc__)

	def test_the_timeout_is_bounded(self):
		"""A malformed file must not pin a Frappe worker until somebody notices."""
		self.assertLessEqual(convert.TIMEOUT_SECONDS, 300)


@unittest.skipUnless(convert.available()["ready"], "needs LibreOffice headless or docx2pdf on this host")
class TheRealConversion(unittest.TestCase):
	"""The end-to-end conversion, where a converter is actually installed.

	Skipped on a bare CI runner and on a laptop without LibreOffice, which is why
	the tests above substitute it — but not skipped in the container this app
	deploys into, so the substitution is never the only evidence that any of this
	works.
	"""

	def test_a_real_docx_becomes_a_real_pdf(self):
		from erpnext_mcp.render import DocxDocument

		document = DocxDocument("Q2 2026 Quarterly")
		document.heading("Quarterly Investment Report")
		document.paragraph("Prepared for the members.")
		pdf = convert.docx_to_pdf(document.render(), "q2-2026.docx")
		self.assertTrue(pdf.startswith(b"%PDF"))
		self.assertGreater(len(pdf), 400)

	def test_something_that_is_not_a_docx_is_refused_rather_than_half_converted(self):
		with self.assertRaises(convert.ConversionError):
			convert.docx_to_pdf(b"this is not a zip at all", "broken.docx")
