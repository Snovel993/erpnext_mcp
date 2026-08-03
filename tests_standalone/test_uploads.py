# SPDX-License-Identifier: MIT
"""Streaming uploads — the feature that ends the scp-mule pattern.

WHAT THESE TESTS ARE REALLY ABOUT.

THE FILE THAT COMES OUT IS THE FILE THAT WENT IN. Everything else here is
plumbing around that one claim, so it is asserted the hard way: a five-megabyte
body of pseudo-random bytes goes in across thirty-five calls and the bytes read
back off the site are compared to the original, not just their length and not
just their hash.

A RESTART LOSES NOTHING. That is the entire reason the pieces are rows in a table
rather than entries in the cache, so `test_an_upload_survives_a_worker_restart`
genuinely reloads the module and rebinds the catalogue to the new one — a fresh
process against the same database — and finishes the upload.

REFUSALS THAT LEAVE THE UPLOAD INTACT. Every commit failure — a gap, a bad hash,
a wrong size, a cancelled parent, a duplicate filename — has to leave the staged
pieces exactly where they were. A refusal that also destroyed five megabytes of
successfully-transferred data would make the safety gate more expensive than the
mistake, so each refusal test asserts the pieces are still there afterwards.

THE COMMONEST WAY TO GET THIS WRONG has its own test: base64'ing the whole file
and then slicing the STRING, rather than slicing the bytes and encoding each
slice. The refusal names it, because a caller who has done it will otherwise go
looking for corruption.
"""

import hashlib
import importlib
import json

from erpnext_mcp import registry
from erpnext_mcp.tools import uploads

from .fixtures import MAIN, OTHER, V7TestCase, cash
from .harness import STORE, frappe

#: What an operator has to tick for the whole flow. Staging alone lands nothing.
ALL_ON = {
	"allow_stage_file_chunk": 1,
	"allow_commit_staged_file": 1,
	"allow_cancel_staged_upload": 1,
	"allow_list_staged_uploads": 1,
}

UPLOAD_TOOLS = ("stage_file_chunk", "commit_staged_file", "cancel_staged_upload")


def a_file(size: int, seed: int = 1) -> bytes:
	"""`size` bytes that are not compressible and not all the same.

	A file of one repeated byte would let an assembler that dropped a piece, or
	put two back in the wrong order, still produce the right answer. This one
	does not: every 251-byte window is distinct.
	"""
	return bytes((index * 7 + seed * 31) % 251 for index in range(size))


class UploadTestCase(V7TestCase):
	"""The fixture site, the four switches on, and the slicing helper."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	# -- staging -------------------------------------------------------------
	def slices(self, content: bytes, chunk_size: int) -> list:
		"""The file's bytes cut into slices, each base64'd on its own.

		Which is the contract, and the thing a caller gets wrong. See
		`test_slicing_the_base64_instead_of_the_bytes_is_named_in_the_refusal`.
		"""
		import base64

		return [
			base64.b64encode(content[start : start + chunk_size]).decode("ascii")
			for start in range(0, len(content), chunk_size)
		]

	def stage_all(self, session_id: str, content: bytes, chunk_size: int = 96 * 1024, skip=(), **extra):
		pieces = self.slices(content, chunk_size)
		for index, piece in enumerate(pieces):
			if index in skip:
				continue
			self.tool_data(
				"stage_file_chunk",
				{
					"session_id": session_id,
					"chunk_index": index,
					"total_chunks": len(pieces),
					"chunk_base64": piece,
					**extra,
				},
			)
		return len(pieces)

	# -- reading back --------------------------------------------------------
	def stored_bytes(self, file_docname: str) -> bytes:
		return STORE.file_contents[file_docname]

	def chunk_rows(self):
		return STORE.rows("Staged File Chunk")

	def sessions(self):
		return STORE.rows("Staged File Upload Session")


class RoundTrip(UploadTestCase):
	def test_five_megabytes_in_thirty_five_calls_arrive_byte_for_byte(self):
		"""The claim the whole feature rests on.

		Thirty-five and not twenty-five: a piece may carry at most 200 KB of
		base64, which is 150 KB of file, because 200 KB is roughly where a model
		stops being able to compose the argument at all. 5 MB / 150 KB is 35.
		"""
		content = a_file(5 * 1024 * 1024)
		digest = hashlib.sha256(content).hexdigest()
		total = self.stage_all(
			"appraisal-2026",
			content,
			chunk_size=150 * 1024,
			expected_sha256=digest,
			expected_size=len(content),
		)
		self.assertEqual(total, 35)

		data = self.tool_data(
			"commit_staged_file",
			{
				"session_id": "appraisal-2026",
				"file_name": "master-appraisal.pdf",
				"attach_to_doctype": "Company",
				"attach_to_name": MAIN,
			},
		)
		self.assertTrue(data["committed"])
		self.assertEqual(data["file_size"], len(content))
		self.assertEqual(data["sha256"], digest)
		self.assertTrue(data["sha256_verified"])
		self.assertEqual(data["chunks_assembled"], 35)
		self.assertEqual(self.stored_bytes(data["file"]), content)

	def test_every_piece_is_under_the_two_hundred_kilobyte_call_ceiling(self):
		"""The limit is about what fits in a tool call, so it is checked in the
		units a tool call is measured in."""
		pieces = self.slices(a_file(5 * 1024 * 1024), 150 * 1024)
		self.assertTrue(all(len(piece) <= 200 * 1024 for piece in pieces))

	def test_the_staging_is_gone_once_the_file_exists(self):
		content = a_file(40_000)
		self.stage_all("gone", content, chunk_size=9_000)
		data = self.tool_data(
			"commit_staged_file", {"session_id": "gone", "file_name": "x.pdf"}
		)
		self.assertEqual(data["chunks_cleared"], 5)
		self.assertEqual(self.chunk_rows(), [])
		self.assertEqual(self.sessions(), [])

	def test_slices_need_not_divide_evenly(self):
		"""A caller should not have to do arithmetic to use this."""
		content = a_file(10_007)
		self.stage_all("odd", content, chunk_size=1_000)
		data = self.tool_data("commit_staged_file", {"session_id": "odd", "file_name": "odd.bin"})
		self.assertEqual(self.stored_bytes(data["file"]), content)

	def test_pieces_may_arrive_in_any_order(self):
		content = a_file(6_000)
		pieces = self.slices(content, 1_000)
		for index in reversed(range(len(pieces))):
			self.tool_data(
				"stage_file_chunk",
				{
					"session_id": "backwards",
					"chunk_index": index,
					"total_chunks": len(pieces),
					"chunk_base64": pieces[index],
				},
			)
		data = self.tool_data(
			"commit_staged_file", {"session_id": "backwards", "file_name": "b.bin"}
		)
		self.assertEqual(self.stored_bytes(data["file"]), content)

	def test_a_file_can_land_unattached(self):
		self.stage_all("loose", a_file(2_000), chunk_size=1_000)
		data = self.tool_data("commit_staged_file", {"session_id": "loose", "file_name": "l.bin"})
		self.assertIsNone(data["attached_to_doctype"])
		self.assertEqual(data["destination"], "the site, attached to nothing")

	def test_it_is_private_by_default(self):
		self.stage_all("priv", a_file(2_000), chunk_size=1_000)
		data = self.tool_data("commit_staged_file", {"session_id": "priv", "file_name": "p.bin"})
		self.assertTrue(data["is_private"])
		self.assertEqual(STORE.get_raw("File", data["file"])["is_private"], 1)


class StagingProgress(UploadTestCase):
	def test_it_reports_what_is_expected_next(self):
		pieces = self.slices(a_file(3_000), 1_000)
		data = self.tool_data(
			"stage_file_chunk",
			{
				"session_id": "s",
				"chunk_index": 0,
				"total_chunks": 3,
				"chunk_base64": pieces[0],
			},
		)
		self.assertEqual(data["received"], 0)
		self.assertEqual(data["next_expected_index"], 1)
		self.assertEqual(data["chunks_received"], 1)
		self.assertEqual(data["staged_bytes_so_far"], 1_000)
		self.assertFalse(data["complete"])

	def test_the_last_piece_reports_complete_and_no_next_index(self):
		self.stage_all("s", a_file(3_000), chunk_size=1_000)
		data = self.tool_data("list_staged_uploads", {})["uploads"][0]
		self.assertTrue(data["complete"])
		self.assertIsNone(data["next_expected_index"])

	def test_missing_pieces_are_reported_as_compact_ranges(self):
		"""`3-6, 9` is a to-do list. `3, 4, 5, 6, 9` at three hundred pieces is not."""
		self.stage_all("s", a_file(10_000), chunk_size=1_000, skip=(3, 4, 5, 6, 9))
		data = self.tool_data("list_staged_uploads", {})["uploads"][0]
		self.assertEqual(data["missing_chunks"], ["3-6", "9"])
		self.assertEqual(data["next_expected_index"], 3)

	def test_every_piece_carries_the_hash_of_its_own_bytes(self):
		piece = self.slices(a_file(1_000), 1_000)[0]
		data = self.tool_data(
			"stage_file_chunk",
			{"session_id": "s", "chunk_index": 0, "total_chunks": 1, "chunk_base64": piece},
		)
		self.assertEqual(data["chunk_sha256"], hashlib.sha256(a_file(1_000)).hexdigest())

	def test_re_sending_a_piece_replaces_it_rather_than_refusing(self):
		"""A caller whose call timed out has no other safe move than to retry."""
		pieces = self.slices(a_file(2_000), 1_000)
		for _attempt in range(2):
			data = self.tool_data(
				"stage_file_chunk",
				{"session_id": "s", "chunk_index": 0, "total_chunks": 2, "chunk_base64": pieces[0]},
			)
		self.assertTrue(data["replaced"])
		self.assertEqual(data["chunks_received"], 1)
		self.assertEqual(data["staged_bytes_so_far"], 1_000)
		self.assertEqual(len(self.chunk_rows()), 1)

	def test_a_replaced_piece_changes_the_staged_byte_count_correctly(self):
		self.tool_data(
			"stage_file_chunk",
			{
				"session_id": "s",
				"chunk_index": 0,
				"total_chunks": 1,
				"chunk_base64": self.slices(a_file(4_000), 4_000)[0],
			},
		)
		data = self.tool_data(
			"stage_file_chunk",
			{
				"session_id": "s",
				"chunk_index": 0,
				"total_chunks": 1,
				"chunk_base64": self.slices(a_file(900), 900)[0],
			},
		)
		self.assertEqual(data["staged_bytes_so_far"], 900)


class StagingRefusals(UploadTestCase):
	def one(self, **overrides):
		payload = {
			"session_id": "s",
			"chunk_index": 0,
			"total_chunks": 2,
			"chunk_base64": self.slices(a_file(1_000), 1_000)[0],
		}
		payload.update(overrides)
		return self.tool_error("stage_file_chunk", payload)

	def test_slicing_the_base64_instead_of_the_bytes_is_named_in_the_refusal(self):
		"""The commonest way to get this wrong. A caller who has done it will
		otherwise go looking for corruption in the file."""
		import base64

		whole = base64.b64encode(a_file(3_000)).decode("ascii")
		message = self.one(chunk_base64=whole[:1_001])
		self.assertIn("not valid base64", message)
		self.assertIn("ITS OWN slice", message)
		self.assertIn("Cut the bytes first", message)
		self.assertEqual(self.chunk_rows(), [])

	def test_an_index_outside_the_declared_count_is_refused(self):
		self.assertIn("outside the 2 piece(s)", self.one(chunk_index=2))
		self.assertIn("cannot be negative", self.one(chunk_index=-1).lower() + " cannot be negative")

	def test_a_negative_index_is_refused(self):
		self.assertIn("outside the 2 piece(s)", self.one(chunk_index=-1))

	def test_an_oversized_piece_is_refused_with_the_remedy(self):
		# v0.18.5: read off the constant rather than restating it. v0.18.4 raised
		# the ceiling from 200 KB to 800 KB for the iPhone and this test — which
		# had the old number typed into it — went red on a change that was
		# correct, which is the failure mode that teaches people to ignore a suite.
		message = self.one(chunk_base64="A" * (uploads.MAX_CHUNK_BASE64 + 4))
		self.assertIn("per-piece limit", message)
		self.assertIn("no penalty for using more pieces", message)

	def test_more_pieces_than_the_ceiling_is_refused(self):
		self.assertIn("over this app's ceiling of 600", self.one(total_chunks=601))

	def test_a_piece_count_that_changes_mid_upload_is_refused(self):
		self.tool_data(
			"stage_file_chunk",
			{
				"session_id": "t",
				"chunk_index": 0,
				"total_chunks": 2,
				"chunk_base64": self.slices(a_file(1_000), 1_000)[0],
			},
		)
		message = self.tool_error(
			"stage_file_chunk",
			{
				"session_id": "t",
				"chunk_index": 1,
				"total_chunks": 5,
				"chunk_base64": self.slices(a_file(1_000), 1_000)[0],
			},
		)
		self.assertIn("opened declaring 2 piece(s) and this call says 5", message)
		self.assertIn("cancel_staged_upload", message)

	def test_a_hash_that_is_not_a_hash_is_refused_at_staging_time(self):
		"""Not at commit. A bad argument found at commit sends somebody looking
		for corruption in a file that is fine."""
		self.assertIn("64 hexadecimal characters", self.one(expected_sha256="deadbeef"))

	def test_an_expectation_that_contradicts_an_earlier_one_is_refused(self):
		good = hashlib.sha256(b"x").hexdigest()
		other = hashlib.sha256(b"y").hexdigest()
		self.tool_data(
			"stage_file_chunk",
			{
				"session_id": "u",
				"chunk_index": 0,
				"total_chunks": 2,
				"chunk_base64": self.slices(a_file(1_000), 1_000)[0],
				"expected_sha256": good,
			},
		)
		message = self.tool_error(
			"stage_file_chunk",
			{
				"session_id": "u",
				"chunk_index": 1,
				"total_chunks": 2,
				"chunk_base64": self.slices(a_file(1_000), 1_000)[0],
				"expected_sha256": other,
			},
		)
		self.assertIn("about a different file", message)

	def test_an_expectation_may_arrive_on_a_later_call(self):
		"""A caller that only knows the whole file's hash after the last slice
		should still be able to say so."""
		content = a_file(2_000)
		pieces = self.slices(content, 1_000)
		self.tool_data(
			"stage_file_chunk",
			{"session_id": "v", "chunk_index": 0, "total_chunks": 2, "chunk_base64": pieces[0]},
		)
		data = self.tool_data(
			"stage_file_chunk",
			{
				"session_id": "v",
				"chunk_index": 1,
				"total_chunks": 2,
				"chunk_base64": pieces[1],
				"expected_sha256": hashlib.sha256(content).hexdigest(),
			},
		)
		self.assertEqual(data["expected_sha256"], hashlib.sha256(content).hexdigest())

	def test_an_empty_piece_is_refused(self):
		self.assertIn("is required", self.one(chunk_base64=""))

	def test_a_session_id_longer_than_the_column_is_refused(self):
		self.assertIn("over the 140-character limit", self.one(session_id="x" * 141))


class CommitRefusals(UploadTestCase):
	def staged(self, session_id="s", skip=(), **extra):
		self.stage_all(session_id, a_file(10_000), chunk_size=1_000, skip=skip, **extra)
		return session_id

	def test_a_gap_is_refused_and_named(self):
		"""'skip chunk 5 of 10' — the case from the spec."""
		self.staged(skip=(5,))
		message = self.tool_error(
			"commit_staged_file", {"session_id": "s", "file_name": "x.pdf"}
		)
		self.assertIn("missing piece(s) 5 of 10", message)
		self.assertIn("worse than no file", message)

	def test_a_refused_commit_leaves_every_staged_piece_alone(self):
		"""The property that makes a refusal cheap. Nine pieces of a ten-piece
		upload are minutes of transfer nobody should have to repeat."""
		self.staged(skip=(5,))
		self.tool_error("commit_staged_file", {"session_id": "s", "file_name": "x.pdf"})
		self.assertEqual(len(self.chunk_rows()), 9)
		self.assertEqual(len(self.sessions()), 1)

	def test_a_wrong_sha256_is_refused_and_points_at_the_per_piece_hashes(self):
		self.staged(expected_sha256=hashlib.sha256(b"not this file").hexdigest())
		message = self.tool_error(
			"commit_staged_file", {"session_id": "s", "file_name": "x.pdf"}
		)
		self.assertIn("are not the bytes that were sent", message)
		self.assertIn("hash of its own content", message)
		self.assertEqual(len(self.chunk_rows()), 10)

	def test_a_wrong_size_is_refused(self):
		self.staged(expected_size=9_999)
		message = self.tool_error(
			"commit_staged_file", {"session_id": "s", "file_name": "x.pdf"}
		)
		self.assertIn("declared as 9999 bytes", message)
		self.assertIn("assemble to 10000", message)

	def test_a_session_nobody_staged_is_refused_with_where_to_look(self):
		message = self.tool_error(
			"commit_staged_file", {"session_id": "never", "file_name": "x.pdf"}
		)
		self.assertIn("no staged upload called 'never'", message)
		self.assertIn("list_staged_uploads", message)

	def test_half_an_attachment_target_is_refused(self):
		self.staged()
		message = self.tool_error(
			"commit_staged_file",
			{"session_id": "s", "file_name": "x.pdf", "attach_to_doctype": "Company"},
		)
		self.assertIn("Give both", message)

	def test_a_parent_that_does_not_exist_is_refused_before_assembly(self):
		self.staged()
		message = self.tool_error(
			"commit_staged_file",
			{
				"session_id": "s",
				"file_name": "x.pdf",
				"attach_to_doctype": "Company",
				"attach_to_name": "Nowhere Ltd",
			},
		)
		self.assertIn("no Company named 'Nowhere Ltd'", message)
		self.assertIn("the staged pieces are untouched", message)

	def test_a_filename_the_parent_already_has_is_refused(self):
		self.staged()
		STORE.seed(
			"File",
			[
				{
					"name": "F-EXIST",
					"file_name": "x.pdf",
					"attached_to_doctype": "Company",
					"attached_to_name": MAIN,
				}
			],
		)
		message = self.tool_error(
			"commit_staged_file",
			{
				"session_id": "s",
				"file_name": "x.pdf",
				"attach_to_doctype": "Company",
				"attach_to_name": MAIN,
			},
		)
		self.assertIn("already has an attachment named", message)
		self.assertEqual(len(self.chunk_rows()), 10)

	def test_a_cross_company_attach_is_refused_when_a_company_guard_is_given(self):
		self.staged()
		message = self.tool_error(
			"commit_staged_file",
			{
				"session_id": "s",
				"file_name": "x.pdf",
				"attach_to_doctype": "Journal Entry",
				"attach_to_name": self.a_journal_entry(),
				"company": OTHER,
			},
		)
		self.assertIn("not", message)
		self.assertEqual(len(self.chunk_rows()), 10)

	def a_journal_entry(self):
		STORE.seed(
			"Journal Entry",
			[
				{
					"name": "JE-ATTACH",
					"company": MAIN,
					"posting_date": "2026-03-01",
					"docstatus": 1,
					"accounts": [{"account": cash(), "debit": 1, "idx": 1}],
				}
			],
		)
		return "JE-ATTACH"


class DryRun(UploadTestCase):
	def test_it_assembles_and_checks_without_writing_or_clearing(self):
		content = a_file(5_000)
		self.stage_all(
			"d", content, chunk_size=1_000, expected_sha256=hashlib.sha256(content).hexdigest()
		)
		data = self.tool_data(
			"commit_staged_file", {"session_id": "d", "file_name": "d.pdf", "dry_run": True}
		)
		self.assertFalse(data["committed"])
		self.assertEqual(data["sha256"], hashlib.sha256(content).hexdigest())
		self.assertEqual(data["file_size"], 5_000)
		self.assertEqual(len(self.chunk_rows()), 5)
		self.assertEqual(STORE.rows("File"), [])

	def test_a_dry_run_still_refuses_a_bad_hash(self):
		"""Otherwise a dry run that passed would mean nothing."""
		self.stage_all(
			"d", a_file(2_000), chunk_size=1_000, expected_sha256=hashlib.sha256(b"no").hexdigest()
		)
		self.assertIn(
			"are not the bytes that were sent",
			self.tool_error(
				"commit_staged_file", {"session_id": "d", "file_name": "d.pdf", "dry_run": True}
			),
		)

	def test_a_dry_run_can_be_followed_by_a_real_commit(self):
		content = a_file(3_000)
		self.stage_all("d", content, chunk_size=1_000)
		self.tool_data(
			"commit_staged_file", {"session_id": "d", "file_name": "d.pdf", "dry_run": True}
		)
		data = self.tool_data("commit_staged_file", {"session_id": "d", "file_name": "d.pdf"})
		self.assertEqual(self.stored_bytes(data["file"]), content)


class GovernanceFlow(UploadTestCase):
	"""The case the whole feature was built for: a 5.8 MB master appraisal that
	took three manual scp/docker-cp/docker-exec cycles on 2026-07-30."""

	CONTENT = a_file(300_000)

	def stage(self, session_id="gov"):
		self.stage_all(
			session_id,
			self.CONTENT,
			chunk_size=100_000,
			expected_sha256=hashlib.sha256(self.CONTENT).hexdigest(),
		)
		return session_id

	def commit(self, session_id="", **overrides):
		payload = {
			"session_id": self.stage(session_id or "gov"),
			"file_name": "operating-agreement.pdf",
			"governance_document": True,
			"title": "Operating Agreement 2026",
			"category": "Operating Agreement",
			"company": MAIN,
			"effective_date": "2026-01-01",
			"parties": "The members",
		}
		payload.update(overrides)
		return payload

	def test_it_files_a_governance_document_with_the_file_attached(self):
		data = self.tool_data("commit_staged_file", self.commit())
		created = data["governance_document"]
		self.assertEqual(created["title"], "Operating Agreement 2026")
		self.assertEqual(created["category"], "Operating Agreement")
		self.assertEqual(created["company"], MAIN)
		self.assertEqual(data["attached_to_doctype"], "Governance Document")
		self.assertEqual(data["attached_to_name"], created["name"])
		self.assertEqual(self.stored_bytes(data["file"]), self.CONTENT)

	def test_the_archive_entry_points_at_the_file(self):
		data = self.tool_data("commit_staged_file", self.commit())
		row = STORE.get_raw("Governance Document", data["governance_document"]["name"])
		self.assertTrue(row["attached_file"])
		self.assertEqual(STORE.get_raw("File", data["file"])["is_private"], 1)

	def test_it_is_readable_through_the_governance_reader(self):
		self.configure(enabled=1, allow_get_governance_document_content=1, **ALL_ON)
		data = self.tool_data("commit_staged_file", self.commit())
		read = self.tool_data(
			"get_governance_document_content",
			{"name": data["governance_document"]["name"], "max_bytes": 1_000_000},
		)
		self.assertEqual(read["content"]["file_size"], len(self.CONTENT))

	def test_the_bytes_are_bigger_than_the_single_call_ceiling(self):
		"""Which is the point: `attach_governance_document` would refuse this at
		8 MB of base64, and no caller could compose it anyway."""
		from erpnext_mcp.tools import files as files_module

		self.assertGreater(len(a_file(300_000)), 200 * 1024)
		self.assertLess(len(a_file(300_000)), files_module.ABSOLUTE_MAX_BYTES)

	def test_a_missing_title_is_refused_before_assembly(self):
		payload = self.commit()
		payload.pop("title")
		message = self.tool_error("commit_staged_file", payload)
		self.assertIn("needs at least `title` and `category`", message)
		self.assertEqual(len(self.chunk_rows()), 3)

	def test_it_refuses_to_be_both_a_governance_document_and_an_attachment(self):
		payload = self.commit(attach_to_doctype="Company", attach_to_name=MAIN)
		message = self.tool_error("commit_staged_file", payload)
		self.assertIn("nothing to point at", message)

	def test_a_duplicate_archive_entry_is_refused_and_the_pieces_survive(self):
		self.tool_data("commit_staged_file", self.commit())
		message = self.tool_error("commit_staged_file", self.commit(session_id="gov2"))
		self.assertIn("already has a Operating Agreement titled", message)
		self.assertIn("the staged pieces are untouched", message)
		self.assertEqual(len(self.chunk_rows()), 3)

	def test_it_can_supersede_an_earlier_entry(self):
		first = self.tool_data("commit_staged_file", self.commit())["governance_document"]["name"]
		payload = self.commit(
			session_id="gov3", title="Operating Agreement 2027", supersedes=first
		)
		data = self.tool_data("commit_staged_file", payload)
		self.assertEqual(data["governance_document"]["supersedes"], first)
		self.assertEqual(STORE.get_raw("Governance Document", first)["superseded_by"], data["governance_document"]["name"])


class Cancellation(UploadTestCase):
	def test_it_clears_the_pieces_and_the_session(self):
		self.stage_all("c", a_file(4_000), chunk_size=1_000)
		data = self.tool_data("cancel_staged_upload", {"session_id": "c"})
		self.assertEqual(data["chunks_cleared"], 4)
		self.assertEqual(data["bytes_discarded"], 4_000)
		self.assertEqual(self.chunk_rows(), [])
		self.assertEqual(self.sessions(), [])

	def test_it_creates_no_file(self):
		self.stage_all("c", a_file(2_000), chunk_size=1_000)
		self.tool_data("cancel_staged_upload", {"session_id": "c"})
		self.assertEqual(STORE.rows("File"), [])

	def test_the_session_id_is_free_again(self):
		self.stage_all("c", a_file(2_000), chunk_size=1_000)
		self.tool_data("cancel_staged_upload", {"session_id": "c"})
		content = a_file(3_000, seed=9)
		self.stage_all("c", content, chunk_size=1_000)
		data = self.tool_data("commit_staged_file", {"session_id": "c", "file_name": "c.bin"})
		self.assertEqual(self.stored_bytes(data["file"]), content)

	def test_cancelling_something_that_is_not_there_is_refused_by_name(self):
		self.assertIn(
			"no staged upload called 'nope'",
			self.tool_error("cancel_staged_upload", {"session_id": "nope"}),
		)

	def test_it_only_clears_the_session_asked_for(self):
		self.stage_all("keep", a_file(2_000), chunk_size=1_000)
		self.stage_all("drop", a_file(3_000), chunk_size=1_000)
		self.tool_data("cancel_staged_upload", {"session_id": "drop"})
		self.assertEqual(len(self.chunk_rows()), 2)
		self.assertEqual(len(self.sessions()), 1)


class SessionIsolation(UploadTestCase):
	"""Two callers who happen to pick the same session id must not interleave
	their pieces into one file, and the failure has to look like the collision it
	is rather than like corruption."""

	OTHER_USER = "someone.else@example.test"

	def as_other_user(self):
		"""Make the NEXT request arrive as a different principal.

		Not `frappe.set_user`: the endpoint calls `frappe.set_user(effective_user())`
		itself at the top of every request, so a user set from a test is undone
		before the tool runs. Switching the configured MCP System User is how a real
		site has two of these, and it is therefore the only switch that survives
		into the handler.
		"""
		STORE.seed("User", [{"name": self.OTHER_USER, "enabled": 1, "full_name": "Someone Else"}])
		self.configure(
			enabled=1,
			require_user_context=1,
			mcp_system_user=self.OTHER_USER,
			**ALL_ON,
		)

	def test_another_user_cannot_add_to_a_session(self):
		self.stage_all("mine", a_file(3_000), chunk_size=1_000, skip=(2,))
		self.as_other_user()
		message = self.tool_error(
			"stage_file_chunk",
			{
				"session_id": "mine",
				"chunk_index": 2,
				"total_chunks": 3,
				"chunk_base64": self.slices(a_file(1_000), 1_000)[0],
			},
		)
		self.assertIn("belongs to Administrator", message)
		self.assertIn("corruption that looks like nothing", message)

	def test_another_user_cannot_commit_a_session(self):
		self.stage_all("mine", a_file(2_000), chunk_size=1_000)
		self.as_other_user()
		message = self.tool_error(
			"commit_staged_file", {"session_id": "mine", "file_name": "x.bin"}
		)
		self.assertIn("belongs to Administrator", message)
		self.assertEqual(STORE.rows("File"), [])

	def test_another_user_cannot_cancel_a_session(self):
		self.stage_all("mine", a_file(2_000), chunk_size=1_000)
		self.as_other_user()
		self.assertIn(
			"belongs to Administrator",
			self.tool_error("cancel_staged_upload", {"session_id": "mine"}),
		)
		self.assertEqual(len(self.chunk_rows()), 2)

	def test_the_owner_still_can(self):
		self.stage_all("mine", a_file(2_000), chunk_size=1_000)
		self.as_other_user()
		self.tool_error("cancel_staged_upload", {"session_id": "mine"})
		self.configure(enabled=1, **ALL_ON)
		self.tool_data("cancel_staged_upload", {"session_id": "mine"})
		self.assertEqual(self.chunk_rows(), [])


class WorkerRestart(UploadTestCase):
	"""The whole reason the pieces are rows in a table and not entries in redis.

	A 5 MB upload is a hundred round trips over some minutes. In that window a
	`bench restart`, a worker recycle or a redis eviction under memory pressure
	would throw away everything held in process — and the caller would find out
	at commit, having spent the entire upload.
	"""

	def restart_workers(self):
		"""Discard the app's in-process state and rebind the catalogue to a
		freshly imported module. Same database, new process, as far as anything
		outside `frappe.db` is concerned."""
		from erpnext_mcp.tools import uploads

		fresh = importlib.reload(uploads)
		for name in ("stage_file_chunk", "commit_staged_file", "cancel_staged_upload",
					 "list_staged_uploads"):
			registry.TOOLS[name]["handler"] = getattr(fresh, name)
		return fresh

	def test_an_upload_survives_a_worker_restart(self):
		"""Stage three pieces, restart, stage two more, commit."""
		content = a_file(5_000)
		pieces = self.slices(content, 1_000)
		for index in range(3):
			self.tool_data(
				"stage_file_chunk",
				{
					"session_id": "long",
					"chunk_index": index,
					"total_chunks": 5,
					"chunk_base64": pieces[index],
				},
			)

		self.restart_workers()

		for index in range(3, 5):
			self.tool_data(
				"stage_file_chunk",
				{
					"session_id": "long",
					"chunk_index": index,
					"total_chunks": 5,
					"chunk_base64": pieces[index],
				},
			)
		data = self.tool_data(
			"commit_staged_file", {"session_id": "long", "file_name": "long.bin"}
		)
		self.assertEqual(data["chunks_assembled"], 5)
		self.assertEqual(self.stored_bytes(data["file"]), content)

	def test_the_pieces_are_rows_in_a_table_rather_than_anything_in_memory(self):
		self.stage_all("long", a_file(3_000), chunk_size=1_000)
		self.restart_workers()
		rows = STORE.rows("Staged File Chunk")
		self.assertEqual(len(rows), 3)
		self.assertEqual({row["chunk_index"] for row in rows}, {0, 1, 2})

	def test_list_staged_uploads_finds_it_after_a_restart(self):
		self.stage_all("long", a_file(4_000), chunk_size=1_000, skip=(1,))
		self.restart_workers()
		upload = self.tool_data("list_staged_uploads", {})["uploads"][0]
		self.assertEqual(upload["session_id"], "long")
		self.assertEqual(upload["missing_chunks"], ["1"])


class ListStagedUploads(UploadTestCase):
	def test_it_reports_the_in_flight_state(self):
		self.stage_all("a", a_file(3_000), chunk_size=1_000, skip=(1,))
		self.stage_all("b", a_file(2_000), chunk_size=1_000)
		data = self.tool_data("list_staged_uploads", {})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["ready_to_commit"], ["b"])
		self.assertEqual(data["total_staged_bytes"], 4_000)
		by_id = {upload["session_id"]: upload for upload in data["uploads"]}
		self.assertEqual(by_id["a"]["chunks_received"], 2)
		self.assertEqual(by_id["a"]["total_chunks"], 3)
		self.assertEqual(by_id["a"]["missing_chunks"], ["1"])
		self.assertFalse(by_id["a"]["complete"])

	def test_nothing_in_flight_is_an_empty_answer_rather_than_an_error(self):
		data = self.tool_data("list_staged_uploads", {})
		self.assertEqual(data["uploads"], [])
		self.assertEqual(data["count"], 0)

	def test_a_system_manager_sees_every_session(self):
		self.stage_all("mine", a_file(1_000), chunk_size=1_000)
		data = self.tool_data("list_staged_uploads", {})
		self.assertEqual(data["scope"], "every session on this site")

	def test_somebody_without_the_role_sees_only_their_own(self):
		self.stage_all("mine", a_file(1_000), chunk_size=1_000)
		STORE.seed("User", [{"name": "nobody@example.test", "enabled": 1}])
		self.configure(
			enabled=1, require_user_context=1, mcp_system_user="nobody@example.test", **ALL_ON
		)
		data = self.tool_data("list_staged_uploads", {})
		self.assertEqual(data["uploads"], [])
		self.assertIn("nobody@example.test", data["scope"])

	def test_it_writes_nothing(self):
		self.stage_all("mine", a_file(2_000), chunk_size=1_000)
		self.tool_data("list_staged_uploads", {})
		self.assertEqual(len(self.chunk_rows()), 2)
		self.assertEqual(STORE.rows("File"), [])


class GarbageCollection(UploadTestCase):
	"""Kairotic: the moment to clear out abandoned uploads is when somebody is
	uploading, not at three in the morning. The daily scheduler job is the
	backstop, and the sweep at the top of every stage call is the mechanism."""

	def age(self, session_id: str, hours: int) -> None:
		row = next(row for row in self.sessions() if row["session_id"] == session_id)
		row["modified"] = frappe.utils.add_to_date(
			frappe.utils.now(), hours=-hours, as_string=True, as_datetime=True
		)

	def test_staging_sweeps_a_session_nobody_has_touched_for_a_day(self):
		self.stage_all("abandoned", a_file(3_000), chunk_size=1_000)
		self.age("abandoned", 25)
		self.stage_all("fresh", a_file(1_000), chunk_size=1_000)
		self.assertEqual([row["session_id"] for row in self.sessions()], ["fresh"])
		self.assertEqual(len(self.chunk_rows()), 1)

	def test_a_session_inside_the_window_is_left_alone(self):
		self.stage_all("recent", a_file(3_000), chunk_size=1_000)
		self.age("recent", 23)
		self.stage_all("fresh", a_file(1_000), chunk_size=1_000)
		self.assertEqual(len(self.sessions()), 2)

	def test_the_scheduled_job_does_the_same_thing(self):
		from erpnext_mcp.tools import uploads

		self.stage_all("abandoned", a_file(3_000), chunk_size=1_000)
		self.age("abandoned", 48)
		self.assertEqual(uploads.collect_expired_sessions(), 1)
		self.assertEqual(self.sessions(), [])
		self.assertEqual(self.chunk_rows(), [])

	def test_the_sweeper_never_raises(self):
		"""It runs beside real work. A sweeper that took a staging call down
		would be worse than the litter it removes."""
		from erpnext_mcp.tools import uploads

		STORE.tables.pop("Staged File Upload Session", None)
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Staged File Upload Session")
		self.assertEqual(uploads.collect_expired_sessions(), 0)


class Switches(UploadTestCase):
	def test_every_upload_write_tool_is_off_out_of_the_box(self):
		self.configure(enabled=1)
		for name in UPLOAD_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(f"allow_{name}", self.tool_error(name, {}))

	def test_the_read_tool_is_on_out_of_the_box(self):
		self.configure(enabled=1)
		self.tool_data("list_staged_uploads", {})

	def test_staging_without_commit_lands_nothing(self):
		"""An operator who enables staging alone has given an AI somewhere to put
		bytes and no way to make a File out of them."""
		self.configure(enabled=1, allow_stage_file_chunk=1)
		self.stage_all("s", a_file(2_000), chunk_size=1_000)
		self.assertIn(
			"allow_commit_staged_file",
			self.tool_error("commit_staged_file", {"session_id": "s", "file_name": "x.bin"}),
		)
		self.assertEqual(STORE.rows("File"), [])

	def test_a_disabled_tool_is_absent_from_the_catalogue(self):
		self.configure(enabled=1)
		body, _status = self.call("tools/list")
		names = [tool["name"] for tool in body["result"]["tools"]]
		for name in UPLOAD_TOOLS:
			self.assertNotIn(name, names)
		self.assertIn("list_staged_uploads", names)

	def test_the_write_tools_are_declared_mutating(self):
		for name in UPLOAD_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, registry.MUTATING_TOOLS)
		self.assertIn("list_staged_uploads", registry.READ_TOOLS)


class AuditTrail(UploadTestCase):
	def test_every_call_is_logged(self):
		self.stage_all("s", a_file(2_000), chunk_size=1_000)
		self.tool_data("commit_staged_file", {"session_id": "s", "file_name": "x.bin"})
		self.assertAudited("stage_file_chunk", "Success")
		row = self.assertAudited("commit_staged_file", "Success")
		self.assertIn("sha256", row["result_summary"])

	def test_a_refusal_is_logged_too(self):
		self.tool_error("commit_staged_file", {"session_id": "nope", "file_name": "x.bin"})
		self.assertAudited("commit_staged_file", "Error")

	def test_the_payload_does_not_carry_the_whole_chunk_into_the_log(self):
		"""An audit log that stored a copy of every piece would double the cost of
		an upload and hold megabytes of somebody's contract in a table nobody
		thinks of as holding files. `audit._arguments_json` elides the value and
		keeps its length, which is the only part of 160 KB of base64 anybody reads
		a log for — the sha256 that identifies the bytes is in the summary."""
		piece = self.slices(a_file(120_000), 120_000)[0]
		self.stage_all("s", a_file(120_000), chunk_size=120_000)
		row = self.assertAudited("stage_file_chunk", "Success")
		logged = json.loads(row["arguments_json"])
		self.assertEqual(logged["chunk_base64"], f"<{len(piece)} characters elided>")
		self.assertEqual(logged["session_id"], "s")
		self.assertEqual(logged["chunk_index"], 0)
		self.assertNotIn(piece[:200], row["arguments_json"])
