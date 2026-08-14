# SPDX-License-Identifier: MIT
"""ML Model file serving — v0.52.0. Models come from ERPNext, not Volume Vision.

`test_model_registry.py` owns the pure engine (`model_registry.py`) and the
registration count. This file is the impure half `tools/ml_model.py` itself
does not have coverage for at the tool layer yet — `attach_model_file` and
`get_model_file_chunk`, the pair that lets an ML Model record own its binary
and serve it back in the same base64-chunked shape `stage_file_chunk` already
takes uploads in.

EIGHT CLAIMS.

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

4. `AttachingABundle` — v0.59.0. A zip is read as a bundle on its magic number
   rather than its name, its manifest's `class_names` replace the record's and
   say so in `manifest_source`, a bundle naming a different `source_uuid` is
   refused with nothing written, a corrupt zip never reaches the record, and a
   raw `.mlmodel` behaves exactly as it did in v0.52.0.

5. `PullingFromVolumeVision` — v0.59.0. `pull_model_from_vv` asks the `/bundle`
   endpoint first, falls back to the original `/download` when that endpoint is
   not there and REPORTS the fallback, refuses a server that is not http(s),
   and never leaves a half-applied record behind when the manifest belongs to
   another trained model.

6. `MigratingAModelFormat` — v0.68.0. A record from each of the three shapes a
   running site actually holds (see `MigrationTestCase`) is brought to the
   current manifest schema, METADATA ONLY — the attached binary is not read,
   replaced or re-uploaded, and `get_model_file_chunk` serves the same bytes
   afterwards. A record whose labels were typed gets a manifest that SAYS so
   rather than one claiming they came out of training, and `is_bundle` stays
   false so no client unpacks a raw model. A record with no labels anywhere,
   and one with a `model_format` nothing recognises, are refused by name.

7. `ValidatingAModelBundle` — v0.68.0. Every issue at once rather than the
   first one, split error/warning, over all three layers: the manifest against
   the schema, the record against its own manifest, and the FILE against both —
   including the two that a pure function cannot see, a manifest claiming
   `bundle` over bytes that are not a zip and a zip whose manifest nobody read.

8. `ListingModelsNeedingMigration` — v0.68.0. The register: outdated records
   only by default, the blocked ones counted separately from the ones
   `migrate_model_format` can fix on its own, and — the claim that keeps the
   list meaningful — a model attached today is never in it.
"""

import base64
import io
import json
import sys
import types
import unittest
import zipfile

import frappe

from erpnext_mcp import model_registry
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.services import volume_vision
from erpnext_mcp.tools import ml_model as ml_model_tools

from .fixtures import MAIN, OTHER, SeededTestCase
from .test_api_mobile import MobileAPITestCase

DOCTYPE = "ML Model"

ON = {
	f"allow_{name}": 1
	for name in (
		"register_model",
		"activate_model",
		"attach_model_file",
		"pull_model_from_vv",
		"migrate_model_format",
	)
}

UUID = "4b6f6e1a-2c3d-4e5f-8a9b-0c1d2e3f4a5b"

MANIFEST = {
	"uuid": UUID,
	"name": "Cherry Fill Detection",
	"version": "3.2",
	"class_names": ["background", "cherry", "bucket", "lip"],
	"class_roles": {"cherry": "fill", "bucket": "container"},
	"piecework_activity": "bucket_fill_detection",
	"model_kind": "Segmentation",
	"metrics": {"mAP50": 0.91},
	"preprocessing": {"input_size": [640, 640], "normalization": "0-1"},
	"training_completed_at": "2026-08-01 14:22:00",
}


def bundle_bytes(manifest=MANIFEST, model_payload=b"\x00\x01coreml-weights\x02\x03"):
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		if manifest is not None:
			archive.writestr("manifest.json", json.dumps(manifest))
		archive.writestr("model.mlmodel", model_payload)
	return buffer.getvalue()


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


# ── 4. Attaching a bundle ───────────────────────────────────────────────


class AttachingABundle(MLModelToolTestCase):
	"""v0.59.0. The zip decides what happens, and the manifest decides the labels."""

	def attach(self, content, file_name="cherry.bundle.zip", **extra):
		model = extra.pop("model", None) or self.model
		args = {"model": model, "file_token": self.staged_file(file_name, content)}
		args.update(extra)
		return self.tool_data("attach_model_file", args)

	def setUp(self):
		super().setUp()
		self.model = self.register(source_uuid=UUID, class_names=["cherry", "bucket"])

	def test_a_bundles_manifest_replaces_the_records_class_names_and_records_where_they_came_from(self):
		result = self.attach(bundle_bytes())
		self.assertTrue(result["is_bundle"])
		self.assertEqual(result["model"]["class_names"], MANIFEST["class_names"])
		self.assertEqual(result["model"]["metrics"], MANIFEST["metrics"])
		self.assertEqual(result["previous"]["class_names"], ["cherry", "bucket"])
		self.assertTrue(result["manifest_source"].startswith("class_names source: bundle manifest"))
		self.assertIn(UUID, result["manifest_source"])
		self.assertTrue(any("class_names on this record were" in w for w in result["warnings"]))
		self.assertEqual(result["bundle"]["model_entry"], "model.mlmodel")

	def test_the_whole_zip_is_stored_and_not_the_extracted_model(self):
		content = bundle_bytes()
		self.attach(content)
		chunk = self.tool_data("get_model_file_chunk", {"model": self.model, "chunk_index": 0})
		self.assertEqual(chunk["total_bytes"], len(content))
		self.assertTrue(chunk["is_bundle"])
		self.assertEqual(base64.b64decode(chunk["chunk_base64"])[:4], b"PK\x03\x04")

	def test_a_bundle_is_recognised_by_its_bytes_and_not_its_file_name(self):
		result = self.attach(bundle_bytes(), file_name="cherry.mlmodel")
		self.assertTrue(result["is_bundle"])
		self.assertEqual(result["model"]["class_names"], MANIFEST["class_names"])

	def test_a_raw_model_keeps_the_v052_behaviour_and_says_its_labels_are_unverified(self):
		result = self.attach(b"\x00\x01coreml-weights\x02\x03", file_name="cherry.mlmodel")
		self.assertFalse(result["is_bundle"])
		self.assertIsNone(result["bundle"])
		self.assertEqual(result["model"]["class_names"], ["cherry", "bucket"])
		self.assertIn("no bundle manifest", result["manifest_source"])
		self.assertTrue(any("raw model file" in w for w in result["warnings"]))

	def test_a_corrupt_zip_never_reaches_the_record(self):
		error = self.tool_error(
			"attach_model_file",
			{"model": self.model, "file_token": self.staged_file("cherry.bundle.zip", bundle_bytes()[:40])},
		)
		self.assertIn("truncated", error)
		self.assertFalse(self.tool_data("get_model", {"model": self.model})["model_file"])

	def test_a_zip_with_no_manifest_is_refused_rather_than_stored_as_a_bundle(self):
		error = self.tool_error(
			"attach_model_file",
			{
				"model": self.model,
				"file_token": self.staged_file("cherry.bundle.zip", bundle_bytes(manifest=None)),
			},
		)
		self.assertIn("manifest.json", error)

	def test_a_bundle_naming_a_different_trained_model_is_refused_with_nothing_written(self):
		other = dict(MANIFEST, uuid="11111111-2222-3333-4444-555555555555")
		error = self.tool_error(
			"attach_model_file",
			{"model": self.model, "file_token": self.staged_file("other.bundle.zip", bundle_bytes(other))},
		)
		self.assertIn("different trained model", error)
		self.assertIn("force=true", error)
		described = self.tool_data("get_model", {"model": self.model})
		self.assertFalse(described["model_file"])
		self.assertEqual(described["class_names"], ["cherry", "bucket"])

	def test_force_attaches_the_mismatched_bundle_and_leaves_source_uuid_alone(self):
		other = dict(MANIFEST, uuid="11111111-2222-3333-4444-555555555555")
		result = self.attach(bundle_bytes(other), file_name="other.bundle.zip", force=True)
		self.assertEqual(result["model"]["source_uuid"], UUID)
		self.assertTrue(any("force=true" in w for w in result["warnings"]))

	def test_an_iso_8601_training_date_is_converted_rather_than_failing_the_save(self):
		"""THE v0.59.0 FIELD BUG. Volume Vision writes `2026-07-08T02:38:43Z`,
		which is what every JSON producer writes and what no MariaDB DATETIME
		accepts: the save came back `OperationalError (1292, "Incorrect datetime
		value")` with the model already downloaded and nothing to show for it.
		The standalone double now refuses the same string the server refuses, so
		this test fails at the attach if the conversion is ever removed."""
		result = self.attach(bundle_bytes(dict(MANIFEST, training_completed_at="2026-07-08T02:38:43Z")))
		self.assertEqual(result["model"]["training_completed_at"], "2026-07-08 02:38:43")
		self.assertEqual(
			frappe.db.get_value(DOCTYPE, self.model, "training_completed_at"), "2026-07-08 02:38:43"
		)

	def test_a_training_date_with_an_offset_is_stored_as_the_same_instant_in_utc(self):
		result = self.attach(bundle_bytes(dict(MANIFEST, training_completed_at="2026-07-08T04:38:43+02:00")))
		self.assertEqual(result["model"]["training_completed_at"], "2026-07-08 02:38:43")

	def test_an_unreadable_training_date_warns_and_the_rest_of_the_bundle_still_lands(self):
		result = self.attach(bundle_bytes(dict(MANIFEST, training_completed_at="whenever")))
		self.assertIsNone(result["model"]["training_completed_at"])
		self.assertEqual(result["model"]["class_names"], MANIFEST["class_names"])
		self.assertTrue(any("not a timestamp" in w for w in result["warnings"]))

	def test_the_manifests_preprocessing_reaches_an_ios_client_through_get_active_model(self):
		self.attach(bundle_bytes())
		self.tool_data("activate_model", {"model": self.model})
		manifest = self.tool_data(
			"get_active_model", {"company": MAIN, "piecework_activity": "bucket_fill_detection"}
		)["manifest"]
		self.assertEqual(manifest["class_names"], MANIFEST["class_names"])
		self.assertTrue(manifest["metadata"]["bundle"]["is_bundle"])
		self.assertEqual(manifest["metadata"]["bundle"]["preprocessing"], MANIFEST["preprocessing"])
		self.assertEqual(manifest["metadata"]["bundle"]["class_roles"], MANIFEST["class_roles"])

	def test_a_bundle_larger_than_one_chunk_reports_the_pieces_it_actually_has(self):
		content = bundle_bytes(model_payload=bytes(range(256)) * 12)
		self.attach(content)
		chunk = self.tool_data(
			"get_model_file_chunk", {"model": self.model, "chunk_index": 0, "chunk_bytes": 512}
		)
		self.assertEqual(chunk["total_chunks"], -(-len(content) // 512))
		self.assertGreater(chunk["total_chunks"], 1)
		self.assertFalse(chunk["is_last"])


# ── 5. Pulling from Volume Vision ───────────────────────────────────────


class FakeVolumeVision:
	"""Volume Vision over HTTP, as `services/volume_vision.py` sees it.

	Installed as a `requests` module the same way `test_weather.py` does it —
	`_get` imports `requests` inside the call so that a bench missing the package
	loses one tool rather than the app, which means the import has to be
	satisfied at call time rather than patched at module scope.
	"""

	def __init__(self, bundle_status=200, download_status=200, bundle=None, raw=b"raw-coreml-bytes"):
		self.bundle_status = bundle_status
		self.download_status = download_status
		self.bundle = bundle if bundle is not None else bundle_bytes()
		self.raw = raw
		self.requested = []
		#: The real server sends `filename=x.mlmodel` with no quotes around it,
		#: which is what this defaults to. Both spellings are legal and both
		#: turn up, so the parser is tested against the one in the wild.
		self.quote_filename = False

	def get(self, url, timeout=None, allow_redirects=None, stream=None):
		self.requested.append(url)
		if url.endswith("/bundle"):
			body = self.bundle if self.bundle_status == 200 else b"not found"
			return self._response(self.bundle_status, body, "cherry_fill_v1.bundle.zip")
		body = self.raw if self.download_status == 200 else b"not found"
		return self._response(self.download_status, body, "cherry_fill_v1.mlmodel")

	def _response(self, status, body, file_name):
		quoted = f'"{file_name}"' if self.quote_filename else file_name
		return types.SimpleNamespace(
			status_code=status,
			content=body,
			headers={
				"Content-Length": str(len(body)),
				"Content-Disposition": f"attachment; filename={quoted}",
				volume_vision.HEADER_VERSION: "3.2",
			},
			text=body.decode("utf-8", "replace"),
		)


class PullingFromVolumeVision(MLModelToolTestCase):
	SERVER = "http://umbrel.local:5101"

	def setUp(self):
		super().setUp()
		self.model = self.register(source_uuid=UUID, source_server=self.SERVER, class_names=["cherry"])
		self.vv = self.install(FakeVolumeVision())

	def install(self, vv):
		module = types.ModuleType("requests")
		# Dispatched through the instance rather than bound once, so a test can
		# swap `vv.get` for one call without reinstalling the module.
		module.get = lambda *args, **kwargs: vv.get(*args, **kwargs)
		previous = sys.modules.get("requests")
		sys.modules["requests"] = module

		def restore():
			if previous is None:
				sys.modules.pop("requests", None)
			else:
				sys.modules["requests"] = previous

		self.addCleanup(restore)
		return vv

	def test_the_bundle_endpoint_is_asked_first_and_its_manifest_becomes_the_record(self):
		result = self.tool_data("pull_model_from_vv", {"model": self.model})
		self.assertEqual(self.vv.requested, [f"{self.SERVER}/training/models/{UUID}/bundle"])
		self.assertEqual(result["source"]["endpoint"], "bundle")
		self.assertFalse(result["source"]["fell_back"])
		self.assertTrue(result["is_bundle"])
		self.assertEqual(result["model"]["class_names"], MANIFEST["class_names"])
		self.assertEqual(result["previous"]["class_names"], ["cherry"])
		self.assertIn(UUID, result["model"]["manifest_source"])
		self.assertEqual(result["file_size_bytes"], len(bundle_bytes()))

	def test_the_port_comes_from_the_record_and_is_never_assumed(self):
		"""And a source_server typed as a bare host:port is read as http rather than refused."""
		other = self.register(
			model_name="Second Camera", version="1", source_uuid=UUID, source_server="umbrel.local:5199"
		)
		self.tool_data("pull_model_from_vv", {"model": other})
		self.assertEqual(self.vv.requested, [f"http://umbrel.local:5199/training/models/{UUID}/bundle"])

	def test_a_server_passed_in_the_call_beats_the_records_own(self):
		self.tool_data(
			"pull_model_from_vv", {"model": self.model, "source_server": "http://laptop.local:8000/vv"}
		)
		self.assertEqual(self.vv.requested, [f"http://laptop.local:8000/vv/training/models/{UUID}/bundle"])

	def test_a_volume_vision_without_the_bundle_endpoint_falls_back_and_says_so(self):
		self.vv.bundle_status = 404
		result = self.tool_data("pull_model_from_vv", {"model": self.model})
		self.assertEqual(
			self.vv.requested,
			[
				f"{self.SERVER}/training/models/{UUID}/bundle",
				f"{self.SERVER}/training/models/{UUID}/download",
			],
		)
		self.assertEqual(result["source"]["endpoint"], "download")
		self.assertTrue(result["source"]["fell_back"])
		self.assertFalse(result["is_bundle"])
		self.assertEqual(result["model"]["class_names"], ["cherry"])
		self.assertIn("no bundle manifest", result["model"]["manifest_source"])
		self.assertTrue(any("does not serve bundles yet" in w for w in result["warnings"]))

	def test_the_fallback_can_be_refused_rather_than_taken_silently(self):
		self.vv.bundle_status = 404
		error = self.tool_error("pull_model_from_vv", {"model": self.model, "allow_raw_fallback": False})
		self.assertIn("does not serve bundles yet", error)
		self.assertFalse(self.tool_data("get_model", {"model": self.model})["model_file"])

	def test_a_server_error_is_not_read_as_a_missing_endpoint(self):
		self.vv.bundle_status = 500
		error = self.tool_error("pull_model_from_vv", {"model": self.model})
		self.assertIn("500", error)
		self.assertEqual(len(self.vv.requested), 1)

	def test_a_record_is_found_by_the_uuid_alone(self):
		result = self.tool_data("pull_model_from_vv", {"source_uuid": UUID})
		self.assertEqual(result["model"]["name"], self.model)

	def test_a_record_with_no_server_says_which_tool_sets_one(self):
		bare = self.register(model_name="Bare Model", version="1", source_uuid=UUID)
		error = self.tool_error("pull_model_from_vv", {"model": bare})
		self.assertIn("update_model", error)
		self.assertEqual(self.vv.requested, [])

	def test_a_record_with_no_uuid_says_so_before_fetching_anything(self):
		bare = self.register(model_name="No UUID", version="1", source_server=self.SERVER)
		error = self.tool_error("pull_model_from_vv", {"model": bare})
		self.assertIn("source_uuid", error)
		self.assertEqual(self.vv.requested, [])

	def test_a_source_server_that_is_not_http_is_refused(self):
		error = self.tool_error(
			"pull_model_from_vv", {"model": self.model, "source_server": "file:///etc/passwd"}
		)
		self.assertIn("only http and https", error)
		self.assertEqual(self.vv.requested, [])

	def test_a_source_server_carrying_credentials_is_refused(self):
		error = self.tool_error(
			"pull_model_from_vv", {"model": self.model, "source_server": "http://user:pw@umbrel.local:5101"}
		)
		self.assertIn("credentials", error)
		self.assertEqual(self.vv.requested, [])

	def test_a_bundle_for_another_trained_model_is_refused_with_nothing_attached(self):
		self.vv.bundle = bundle_bytes(dict(MANIFEST, uuid="11111111-2222-3333-4444-555555555555"))
		error = self.tool_error("pull_model_from_vv", {"model": self.model})
		self.assertIn("different trained model", error)
		described = self.tool_data("get_model", {"model": self.model})
		self.assertFalse(described["model_file"])
		self.assertEqual(described["class_names"], ["cherry"])

	def test_the_file_is_stored_under_the_name_volume_vision_gave_it(self):
		"""Quoted or not — the running server sends `filename=x.mlmodel` bare."""
		for quote in (False, True):
			with self.subTest(quoted=quote):
				self.vv.quote_filename = quote
				result = self.tool_data("pull_model_from_vv", {"model": self.model})
				self.assertEqual(
					frappe.db.get_value("File", result["file"], "file_name"), "cherry_fill_v1.bundle.zip"
				)

	def test_a_server_that_names_no_file_still_stores_one_named_for_the_uuid(self):
		self.vv.get = lambda url, **kwargs: types.SimpleNamespace(
			status_code=200, content=bundle_bytes(), headers={}, text=""
		)
		result = self.tool_data("pull_model_from_vv", {"model": self.model})
		self.assertEqual(
			frappe.db.get_value("File", result["file"], "file_name"), f"{UUID}.bundle.zip"
		)

	def test_the_pulled_bundle_is_what_get_model_file_chunk_serves(self):
		self.tool_data("pull_model_from_vv", {"model": self.model})
		chunk = self.tool_data("get_model_file_chunk", {"model": self.model, "chunk_index": 0})
		self.assertEqual(base64.b64decode(chunk["chunk_base64"]), bundle_bytes())
		self.assertTrue(chunk["is_bundle"])


# ── 6. Migrating a record that predates the format ──────────────────────


class MigrationTestCase(MLModelToolTestCase):
	"""Records in each of the three shapes a running site actually holds.

	`legacy()` is what v0.43.0/v0.52.0 wrote: labels typed onto the record and
	no manifest anywhere. `v059()` is what v0.59.x wrote: a manifest stored
	exactly as Volume Vision's exporter produced it, which is what the stored
	shape was BEFORE this release started normalizing on the way in — written
	here with `frappe.db.set_value` for that reason, because there is no longer
	a code path in this app that produces it.
	"""

	def legacy(self, class_names=("cherry", "bucket"), **overrides):
		return self.register(class_names=list(class_names), **overrides)

	def v059(self, manifest=None, **overrides):
		name = self.register(source_uuid=UUID, class_names=list(MANIFEST["class_names"]), **overrides)
		frappe.db.set_value(DOCTYPE, name, "bundle_manifest", json.dumps(manifest or MANIFEST))
		frappe.db.set_value(DOCTYPE, name, "manifest_source", model_registry.MANIFEST_SOURCE_BUNDLE)
		return name

	def current(self, **overrides):
		"""A record whose bundle came in through this release's own attach path."""
		name = self.register(source_uuid=UUID, **overrides)
		self.tool_data(
			"attach_model_file", {"model": name, "file_token": self.staged_file("c.zip", bundle_bytes())}
		)
		return name


class MigratingAModelFormat(MigrationTestCase):
	def test_a_pre_v059_record_gets_a_manifest_built_from_its_own_fields(self):
		model = self.legacy()
		result = self.tool_data("migrate_model_format", {"model": model})
		self.assertTrue(result["migrated"])
		manifest = result["manifest"]
		self.assertEqual(manifest["schema_version"], "1.0")
		self.assertEqual(manifest["manifest_origin"], "record")
		self.assertEqual(manifest["class_names"], ["cherry", "bucket"])
		self.assertEqual(manifest["userDefined"]["class_names"], "cherry,bucket")
		self.assertEqual(manifest["name"], "Cherry Fill Detection")
		self.assertEqual(manifest["piecework_activity"], "bucket_fill_detection")

	def test_it_never_claims_typed_labels_came_out_of_training(self):
		"""THE ONE THING MIGRATION MUST NOT DO. `manifest_source` exists so that
		"these labels came out of a training run" and "somebody typed these" stay
		distinguishable on the record. A migration that produced a manifest and
		then said it came from a bundle would erase exactly that."""
		model = self.legacy()
		result = self.tool_data("migrate_model_format", {"model": model})
		source = result["model"]["manifest_source"]
		self.assertIn("migrate_model_format", source)
		self.assertNotIn("bundle manifest from VV training", source)
		self.assertTrue(any("unverified against the weights" in w for w in result["warnings"]))

	def test_a_migrated_record_never_tells_a_client_to_unpack_a_raw_model(self):
		model = self.legacy()
		self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(b"\x00raw-weights").decode(), "file_name": "c.mlmodel"},
		)
		result = self.tool_data("migrate_model_format", {"model": model})
		self.assertFalse(result["model"]["is_bundle"])
		self.tool_data("activate_model", {"model": model})
		manifest = self.tool_data(
			"get_active_model", {"company": MAIN, "piecework_activity": "bucket_fill_detection"}
		)["manifest"]
		self.assertFalse(manifest["metadata"]["bundle"]["is_bundle"])
		self.assertEqual(manifest["metadata"]["bundle"]["manifest_origin"], "record")
		self.assertEqual(manifest["metadata"]["bundle"]["user_defined"]["class_names"], "cherry,bucket")

	def test_a_v059_record_keeps_its_bundle_provenance_and_every_key_the_exporter_wrote(self):
		model = self.v059()
		result = self.tool_data("migrate_model_format", {"model": model})
		manifest = result["manifest"]
		self.assertEqual(manifest["manifest_origin"], "bundle")
		for key, value in MANIFEST.items():
			with self.subTest(key=key):
				self.assertEqual(manifest[key], value)
		self.assertIn("bundle manifest from VV training", result["model"]["manifest_source"])
		self.assertTrue(result["model"]["is_bundle"])

	def test_it_moves_metadata_only_and_never_touches_the_binary(self):
		model = self.legacy()
		self.tool_data(
			"attach_model_file", {"model": model, "file_token": self.staged_file("c.mlmodel", b"weights")}
		)
		before = self.tool_data("get_model", {"model": model})
		files_before = len(frappe.db.get_all("File", filters={"attached_to_name": model}, pluck="name"))

		self.tool_data("migrate_model_format", {"model": model})

		after = self.tool_data("get_model", {"model": model})
		self.assertEqual(after["model_file"], before["model_file"])
		self.assertEqual(after["file_size_bytes"], before["file_size_bytes"])
		self.assertEqual(
			len(frappe.db.get_all("File", filters={"attached_to_name": model}, pluck="name")), files_before
		)
		chunk = self.tool_data("get_model_file_chunk", {"model": model, "chunk_index": 0})
		self.assertEqual(base64.b64decode(chunk["chunk_base64"]), b"weights")

	def test_a_record_with_no_class_names_anywhere_is_refused_by_name(self):
		model = self.register(version="9")
		error = self.tool_error("migrate_model_format", {"model": model})
		self.assertIn("no class_names anywhere", error)
		self.assertIn("pull_model_from_vv", error)
		self.assertIsNone(self.tool_data("get_model", {"model": model})["bundle_manifest"])

	def test_an_unrecognised_model_format_is_refused_rather_than_defaulted(self):
		model = self.legacy()
		frappe.db.set_value(DOCTYPE, model, "model_format", "PyTorch")
		error = self.tool_error("migrate_model_format", {"model": model})
		self.assertIn("PyTorch", error)
		self.assertIn("update_model", error)

	def test_an_already_current_record_is_a_no_op_and_not_an_error(self):
		model = self.current()
		result = self.tool_data("migrate_model_format", {"model": model})
		self.assertFalse(result["migrated"])
		self.assertTrue(result["already_current"])
		self.assertEqual(result["changed_fields"], [])

	def test_force_rewrites_a_record_that_needed_nothing(self):
		model = self.current()
		result = self.tool_data("migrate_model_format", {"model": model, "force": True})
		self.assertTrue(result["migrated"])
		self.assertIn("bundle_manifest", result["changed_fields"])

	def test_dry_run_computes_everything_and_saves_nothing(self):
		model = self.legacy()
		result = self.tool_data("migrate_model_format", {"model": model, "dry_run": True})
		self.assertFalse(result["migrated"])
		self.assertTrue(result["dry_run"])
		self.assertEqual(result["manifest"]["manifest_origin"], "record")
		self.assertIn("bundle_manifest", result["changed_fields"])
		self.assertIsNone(self.tool_data("get_model", {"model": model})["bundle_manifest"])
		self.assertIsNone(self.tool_data("get_model", {"model": model})["manifest_source"])

	def test_migrating_twice_changes_nothing_the_second_time(self):
		model = self.legacy()
		first = self.tool_data("migrate_model_format", {"model": model})["manifest"]
		second = self.tool_data("migrate_model_format", {"model": model})
		self.assertFalse(second["migrated"])
		self.assertTrue(second["already_current"])
		self.assertEqual(self.tool_data("get_model", {"model": model})["bundle_manifest"], first)

	def test_the_switch_is_off_by_default_and_the_refusal_names_the_tool(self):
		model = self.legacy()
		self.configure(enabled=1, **{**ON, "allow_migrate_model_format": 0})
		error = self.tool_error("migrate_model_format", {"model": model})
		self.assertIn("migrate_model_format", error)


# ── 7. Validating one record ────────────────────────────────────────────


class ValidatingAModelBundle(MigrationTestCase):
	def codes(self, result, severity=None):
		return [
			issue["code"]
			for issue in result["issues"]
			if severity is None or issue["severity"] == severity
		]

	def test_a_record_whose_bundle_came_in_through_this_release_passes(self):
		result = self.tool_data("validate_model_bundle", {"model": self.current()})
		self.assertTrue(result["valid"])
		self.assertEqual(self.codes(result, "error"), [])
		self.assertFalse(result["needs_migration"])
		self.assertTrue(result["checks"]["payload_is_bundle"])
		self.assertTrue(result["checks"]["payload_matches_manifest_origin"])
		self.assertEqual(result["checks"]["bundle_model_entry"], "model.mlmodel")

	def test_a_legacy_record_fails_and_names_the_missing_manifest(self):
		result = self.tool_data("validate_model_bundle", {"model": self.legacy()})
		self.assertFalse(result["valid"])
		self.assertIn("no_manifest", self.codes(result, "error"))
		self.assertTrue(result["needs_migration"])
		self.assertTrue(result["can_migrate"])
		self.assertTrue(any("migrate_model_format" in i["message"] for i in result["issues"]))

	def test_a_v059_record_fails_on_the_schema_and_lists_every_reason_at_once(self):
		result = self.tool_data("validate_model_bundle", {"model": self.v059()})
		self.assertFalse(result["valid"])
		self.assertEqual(set(self.codes(result, "error")), {"schema"})
		messages = " ".join(i["message"] for i in result["issues"])
		self.assertIn("schema_version", messages)
		self.assertIn("userDefined", messages)

	def test_a_manifest_claiming_a_bundle_over_raw_bytes_is_an_error(self):
		"""THE FAILURE THIS CHECK EXISTS FOR: a client reading the record would
		try to unzip a `.mlmodel` and fail on the handset, hours later."""
		model = self.legacy()
		self.tool_data(
			"attach_model_file",
			{"model": model, "file_content": base64.b64encode(b"\x00raw").decode(), "file_name": "c.mlmodel"},
		)
		self.tool_data("migrate_model_format", {"model": model})
		manifest = self.tool_data("get_model", {"model": model})["bundle_manifest"]
		manifest["manifest_origin"] = "bundle"
		frappe.db.set_value(DOCTYPE, model, "bundle_manifest", json.dumps(manifest))

		result = self.tool_data("validate_model_bundle", {"model": model})
		self.assertFalse(result["valid"])
		self.assertIn("payload_not_a_bundle", self.codes(result, "error"))
		self.assertFalse(result["checks"]["payload_matches_manifest_origin"])

	def test_a_stored_zip_nobody_read_the_manifest_of_is_an_error(self):
		"""What a v0.52.0 attach could leave behind: a zip stored as if it were a
		raw model, its manifest never opened, and then migrated — so the record
		has labels nobody checked and a payload a client cannot compile."""
		model = self.current()
		manifest = self.tool_data("get_model", {"model": model})["bundle_manifest"]
		manifest["manifest_origin"] = "record"
		frappe.db.set_value(DOCTYPE, model, "bundle_manifest", json.dumps(manifest))

		result = self.tool_data("validate_model_bundle", {"model": model})
		self.assertIn("payload_is_an_unread_bundle", self.codes(result, "error"))
		self.assertTrue(any("attach_model_file" in i["message"] for i in result["issues"]))

	def test_a_model_file_that_resolves_to_nothing_is_an_error(self):
		model = self.current()
		frappe.db.set_value(DOCTYPE, model, "model_file", "/private/files/gone.zip")
		result = self.tool_data("validate_model_bundle", {"model": model})
		self.assertFalse(result["valid"])
		self.assertIn("file_missing", self.codes(result, "error"))
		self.assertFalse(result["checks"]["file_resolves"])

	def test_a_record_with_nothing_attached_is_a_warning_and_still_passes(self):
		model = self.legacy()
		self.tool_data("migrate_model_format", {"model": model})
		result = self.tool_data("validate_model_bundle", {"model": model})
		self.assertTrue(result["valid"])
		self.assertIn("no_model_file", self.codes(result, "warning"))
		self.assertIn("labels_unverified", self.codes(result, "warning"))

	def test_class_names_disagreeing_with_the_manifest_is_an_error(self):
		model = self.current()
		frappe.db.set_value(DOCTYPE, model, "class_names", json.dumps(["something", "else"]))
		result = self.tool_data("validate_model_bundle", {"model": model})
		self.assertFalse(result["valid"])
		self.assertIn("class_names_disagree", self.codes(result, "error"))

	def test_check_payload_false_skips_the_byte_reads(self):
		result = self.tool_data("validate_model_bundle", {"model": self.current(), "check_payload": False})
		self.assertTrue(result["valid"])
		self.assertFalse(result["payload_checked"])
		self.assertIsNone(result["checks"]["payload_is_bundle"])
		self.assertTrue(result["checks"]["file_resolves"])

	def test_it_corrects_nothing_it_finds(self):
		model = self.legacy()
		before = self.tool_data("get_model", {"model": model})
		self.tool_data("validate_model_bundle", {"model": model})
		self.assertEqual(self.tool_data("get_model", {"model": model}), before)


# ── 8. The register of what has not been migrated ───────────────────────


class ListingModelsNeedingMigration(MigrationTestCase):
	def names(self, result):
		return {row["name"] for row in result["models"]}

	def test_it_finds_the_outdated_records_and_leaves_out_the_current_one(self):
		legacy = self.legacy()
		old_bundle = self.v059(version="2")
		current = self.current(version="4")

		result = self.tool_data("list_models_needing_migration", {"company": MAIN})
		self.assertEqual(self.names(result), {legacy, old_bundle})
		self.assertNotIn(current, self.names(result))
		self.assertEqual(result["count"], 2)
		self.assertEqual(result["scanned"], 3)
		self.assertEqual(result["already_current"], 1)
		self.assertEqual(result["ready_to_migrate"], 2)

	def test_each_row_carries_its_own_reasons(self):
		self.legacy()
		row = self.tool_data("list_models_needing_migration", {"company": MAIN})["models"][0]
		self.assertTrue(any("no bundle_manifest at all" in reason for reason in row["reasons"]))
		self.assertEqual(row["manifest_origin"], "record")
		self.assertFalse(row["has_manifest"])
		self.assertTrue(row["can_migrate"])

	def test_a_record_with_no_labels_anywhere_is_counted_as_blocked(self):
		self.register(version="9")
		result = self.tool_data("list_models_needing_migration", {"company": MAIN})
		self.assertEqual(result["blocked"], 1)
		self.assertEqual(result["ready_to_migrate"], 0)
		self.assertFalse(result["models"][0]["can_migrate"])
		self.assertTrue(any("update_model" in b for b in result["models"][0]["blockers"]))

	def test_include_current_returns_everything_and_the_counts_still_split(self):
		self.legacy()
		self.current(version="4")
		result = self.tool_data(
			"list_models_needing_migration", {"company": MAIN, "include_current": True}
		)
		self.assertEqual(len(result["models"]), 2)
		self.assertEqual(result["count"], 1)
		self.assertEqual(result["already_current"], 1)

	def test_it_filters_by_company_and_by_status(self):
		mine = self.legacy()
		self.legacy(company=OTHER, piecework_activity="theirs", version="2")
		self.assertEqual(self.names(self.tool_data("list_models_needing_migration", {"company": MAIN})), {mine})

		self.tool_data("activate_model", {"model": mine})
		self.assertEqual(
			self.names(
				self.tool_data("list_models_needing_migration", {"company": MAIN, "status": "Draft"})
			),
			set(),
		)
		self.assertEqual(
			self.names(
				self.tool_data("list_models_needing_migration", {"company": MAIN, "status": "Active"})
			),
			{mine},
		)

	def test_an_unknown_status_is_refused_with_the_choices_listed(self):
		error = self.tool_error("list_models_needing_migration", {"status": "Retired"})
		self.assertIn("Deprecated", error)

	def test_a_model_attached_today_is_not_in_the_register(self):
		"""v0.68.0 normalizes on the way in, so this is a register of records that
		predate the format rather than a queue that refills."""
		model = self.register(source_uuid=UUID, version="5")
		self.tool_data(
			"attach_model_file", {"model": model, "file_token": self.staged_file("c.zip", bundle_bytes())}
		)
		listed = self.names(self.tool_data("list_models_needing_migration", {"company": MAIN}))
		self.assertNotIn(model, listed)

	def test_migrating_everything_it_lists_empties_it(self):
		self.legacy()
		self.v059(version="2")
		result = self.tool_data("list_models_needing_migration", {"company": MAIN})
		for row in result["models"]:
			self.tool_data("migrate_model_format", {"model": row["name"]})
		after = self.tool_data("list_models_needing_migration", {"company": MAIN})
		self.assertEqual(after["count"], 0)
		self.assertEqual(after["already_current"], 2)


if __name__ == "__main__":
	unittest.main()
