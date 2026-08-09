# SPDX-License-Identifier: MIT
"""ML Model file serving — v0.52.0. Models come from ERPNext, not Volume Vision.

`test_model_registry.py` owns the pure engine (`model_registry.py`) and the
registration count. This file is the impure half `tools/ml_model.py` itself
does not have coverage for at the tool layer yet — `attach_model_file` and
`get_model_file_chunk`, the pair that lets an ML Model record own its binary
and serve it back in the same base64-chunked shape `stage_file_chunk` already
takes uploads in.

THREE CLAIMS.

1. `AttachingAModelFile` — exactly one of `file_token` or `file_content` is
   accepted, a base64 attach records the right size and URL, a `file_token`
   re-parents an already-staged File rather than copying it, and re-attaching
   replaces `model_file` without deleting what it replaces.

2. `DownloadingAModelFile` — `get_model_file_chunk` splits the attached bytes
   into the requested pieces and reassembles byte-identical, refuses a model
   with nothing attached BY NAME rather than reaching for `source_server`, and
   resolves a caller's `source_uuid` the same way `get_active_model`'s own
   manifest hands one back.

3. `ServedOverTheMobileSurface` — `api/mobile.py`'s `get_active_model` and
   `get_model_file_chunk` wrappers scope to the caller's own company the same
   way every other docname argument on that surface does.
"""

import base64
import unittest

import frappe

from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.tools import ml_model as ml_model_tools

from .fixtures import MAIN, OTHER, SeededTestCase
from .test_api_mobile import MobileAPITestCase

DOCTYPE = "ML Model"

ON = {
	f"allow_{name}": 1
	for name in ("register_model", "activate_model", "attach_model_file")
}


def _model(**overrides):
	base = {
		"model_name": "Cherry Fill Detection",
		"version": "3.2",
		"company": MAIN,
		"piecework_activity": "bucket_fill_detection",
	}
	base.update(overrides)
	return base


class MLModelToolTestCase(SeededTestCase):
	"""A site with the model tools switched on."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)

	def register(self, **overrides):
		return self.tool_data("register_model", _model(**overrides))["model"]["name"]

	def staged_file(self, file_name="cherry.mlmodelc.zip", content=b"not-really-a-coreml-model"):
		"""A File on the site with nobody's name on it — what `commit_staged_file`
		(`tools/uploads.py`) would have produced for a binary too large for one
		tool call. Built directly rather than through the staging tools: those are
		Sprint 6's own and are exercised in `test_uploads.py`; this file owes them
		only their output shape, a File docname."""
		doc = frappe.get_doc({"doctype": "File", "file_name": file_name, "is_private": 1, "content": content})
		doc.insert(ignore_permissions=True)
		return doc.name


# ── 1. Attaching ─────────────────────────────────────────────────────────


class AttachingAModelFile(MLModelToolTestCase):
	def test_base64_content_attaches_and_records_size(self):
		model = self.register()
		content = b"\x00\x01coreml-bytes\x02\x03"
		result = self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(content).decode(), "file_name": "cherry.mlmodelc.zip"},
		)
		self.assertEqual(result["file_size_bytes"], len(content))
		described = result["model"]
		self.assertTrue(described["model_file"])
		self.assertEqual(described["file_size_bytes"], len(content))

	def test_a_file_token_is_reparented_rather_than_copied(self):
		model = self.register()
		token = self.staged_file()
		self.assertFalse(frappe.db.get_value("File", token, "attached_to_name"))

		result = self.tool_data("attach_model_file", {"model": model, "file_token": token})
		self.assertEqual(result["file"], token)

		row = frappe.db.get_value(
			"File", token, ["attached_to_doctype", "attached_to_name"], as_dict=True
		)
		self.assertEqual(row["attached_to_doctype"], DOCTYPE)
		self.assertEqual(row["attached_to_name"], model)

	def test_exactly_one_of_file_token_or_file_content_is_required(self):
		model = self.register()
		error = self.tool_error("attach_model_file", {"model": model})
		self.assertIn("file_token or file_content is required", error)

		error = self.tool_error(
			"attach_model_file",
			{"model": model, "file_token": "whatever", "file_content": base64.b64encode(b"x").decode()},
		)
		self.assertIn("not both", error)

	def test_reattaching_replaces_model_file_and_leaves_the_old_file(self):
		model = self.register()
		first_token = self.staged_file("v1.mlmodelc.zip", b"version one")
		self.tool_data("attach_model_file", {"model": model, "file_token": first_token})
		first_url = self.tool_data("get_model", {"model": model})["model_file"]

		second_token = self.staged_file("v2.mlmodelc.zip", b"version two, longer")
		self.tool_data("attach_model_file", {"model": model, "file_token": second_token})
		described = self.tool_data("get_model", {"model": model})
		self.assertNotEqual(described["model_file"], first_url)
		self.assertEqual(described["file_size_bytes"], len(b"version two, longer"))
		# The old File is not deleted — see the module docstring.
		self.assertTrue(frappe.db.exists("File", first_token))


# ── 2. Downloading ──────────────────────────────────────────────────────


class DownloadingAModelFile(MLModelToolTestCase):
	def test_refuses_when_nothing_is_attached(self):
		model = self.register()
		error = self.tool_error("get_model_file_chunk", {"model": model, "chunk_index": 0})
		self.assertIn("attach_model_file", error)

	def test_a_small_file_is_one_chunk(self):
		model = self.register()
		content = b"small model bytes"
		self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(content).decode(), "file_name": "m.mlmodelc"},
		)
		chunk = self.tool_data("get_model_file_chunk", {"model": model, "chunk_index": 0})
		self.assertEqual(chunk["total_chunks"], 1)
		self.assertTrue(chunk["is_last"])
		self.assertEqual(base64.b64decode(chunk["chunk_base64"]), content)
		self.assertEqual(chunk["total_bytes"], len(content))

	def test_a_large_file_reassembles_byte_identical_across_pieces(self):
		model = self.register()
		content = bytes(range(256)) * 10  # 2560 bytes
		token = self.staged_file("big.mlmodelc.zip", content)
		self.tool_data("attach_model_file", {"model": model, "file_token": token})

		chunk_bytes = 300
		pieces = []
		total_chunks = None
		index = 0
		while total_chunks is None or index < total_chunks:
			chunk = self.tool_data(
				"get_model_file_chunk",
				{"model": model, "chunk_index": index, "chunk_bytes": chunk_bytes},
			)
			total_chunks = chunk["total_chunks"]
			pieces.append(base64.b64decode(chunk["chunk_base64"]))
			self.assertEqual(chunk["is_last"], index == total_chunks - 1)
			index += 1
		self.assertEqual(b"".join(pieces), content)
		self.assertGreater(total_chunks, 1)

	def test_an_out_of_range_chunk_index_is_refused(self):
		model = self.register()
		self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(b"abc").decode(), "file_name": "m.mlmodelc"},
		)
		error = self.tool_error("get_model_file_chunk", {"model": model, "chunk_index": 5})
		self.assertIn("outside the", error)

	def test_a_model_resolves_by_its_source_uuid_the_same_way_the_manifest_hands_one_back(self):
		uuid = "4b6f6e1a-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
		model = self.register(source_uuid=uuid)
		self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(b"abc").decode(), "file_name": "m.mlmodelc"},
		)
		chunk = self.tool_data("get_model_file_chunk", {"model": uuid, "chunk_index": 0})
		self.assertEqual(chunk["model"], model)

	def test_the_manifest_reports_downloadable_only_once_a_file_is_attached(self):
		model = self.register(piecework_activity="bucket_fill_detection")
		self.tool_data("activate_model", {"model": model})
		before = self.tool_data("get_active_model", {"company": MAIN, "piecework_activity": "bucket_fill_detection"})
		self.assertFalse(before["manifest"]["metadata"]["downloadable"])

		self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(b"abc").decode(), "file_name": "m.mlmodelc"},
		)
		after = self.tool_data("get_active_model", {"company": MAIN, "piecework_activity": "bucket_fill_detection"})
		self.assertTrue(after["manifest"]["metadata"]["downloadable"])


# ── 3. Over the mobile surface ──────────────────────────────────────────


class ServedOverTheMobileSurface(MobileAPITestCase):
	"""`api/mobile.py`'s `get_active_model` / `get_model_file_chunk`, in-process."""

	ACTIVITY = "bucket_fill_detection"

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **ON)
		frappe.local.session.user = "Administrator"
		self.model = ml_model_tools.register_model(_model(piecework_activity=self.ACTIVITY)).data["model"]["name"]
		ml_model_tools.activate_model({"model": self.model})
		content = b"model bytes for the mobile surface"
		token = self.staged_file(content)
		ml_model_tools.attach_model_file({"model": self.model, "file_token": token})

	def staged_file(self, content):
		doc = frappe.get_doc({"doctype": "File", "file_name": "m.mlmodelc.zip", "is_private": 1, "content": content})
		doc.insert(ignore_permissions=True)
		return doc.name

	def test_the_worker_reads_the_active_model_for_their_own_company(self):
		self.be()
		result = mobile_api.get_active_model(user="ana@example.test", company=MAIN, piecework_activity=self.ACTIVITY)
		self.assertTrue(result["active"])
		self.assertTrue(result["manifest"]["metadata"]["downloadable"])

	def test_the_worker_reads_the_binary_back_through_this_surface(self):
		self.be()
		chunk = mobile_api.get_model_file_chunk(user="ana@example.test", model=self.model, chunk_index=0)
		self.assertEqual(chunk["total_chunks"], 1)
		self.assertEqual(base64.b64decode(chunk["chunk_base64"]), b"model bytes for the mobile surface")

	def test_a_model_belonging_to_another_entity_reads_as_not_found(self):
		theirs = ml_model_tools.register_model(
			_model(company=OTHER, piecework_activity="theirs", version="1")
		).data["model"]["name"]
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.get_model_file_chunk(user="ana@example.test", model=theirs, chunk_index=0)


if __name__ == "__main__":
	unittest.main()
