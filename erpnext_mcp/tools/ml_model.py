# SPDX-License-Identifier: MIT
"""ML Model Registry — v0.43.0, plus v0.52.0's file serving. Nine tools, and
only five of them write.

WHAT THIS APP TRACKS, AND WHAT IT DOES NOT. Volume Vision (Flask/SQLAlchemy)
trains models and holds the weights; this app never sees a weight and never
computes a metric. What ERPNext adds is the one fact Volume Vision has no
reason to know: which trained model is DEPLOYED for which company and which
piecework activity — the record `get_active_model` is queried against by an
iOS app (BucketLog, Farm Ops) deciding what to pull. `source_uuid` is the join
key back to Volume Vision's `TrainedModel.uuid`, which is also the key an iOS
offline cache is keyed by, so a manifest this app hands back and a model
already cached on a phone are provably talking about the same trained model.

THE ARITHMETIC IS NOT HERE. Field validation, the manifest shape, and whether
activating one model conflicts with another live in
`erpnext_mcp/model_registry.py`, which is pure and reads no database — the
same split `budget_engine.py` and `payroll_gl.py` keep. This module is the only
place that reads or writes an `ML Model` document.

THE UNIQUENESS INVARIANT IS ENFORCED TWICE, DELIBERATELY. `activate_model`
here computes and REPORTS what it is about to supersede before saving, using
`model_registry.check_model_conflicts` against whatever this module found by
querying the database — that read is this module's job precisely because
`check_model_conflicts` cannot do it itself. The DocType controller
(`erpnext_mcp/erpnext_mcp/doctype/ml_model/ml_model.py`) separately guarantees
the invariant holds in the database regardless of which door a save came
through, the same way `Budget`'s controller refuses a duplicate account
independent of which tool built the document. Neither makes the other
redundant: this module's job is to tell a caller what changed, the
controller's job is to make sure the database cannot disagree.

WHO MAY WRITE. The same three roles the KPI and Budget frameworks use — System
Manager, Accounts Manager, Farm Manager — via `kpi_tools.require_kpi_role`.
Which model is live for a piecework activity determines what a scanning app
counts as a filled bucket, which is the same class of judgement that ends up
on a payroll run as a Budget's variance or a KPI's threshold does.

────────────────────────────────────────────────────────────────────────────
v0.52.0: ERPNEXT SERVES THE BINARY. IT DOES NOT ONLY POINT AT WHO HAS IT
────────────────────────────────────────────────────────────────────────────

Through v0.51.1 this module answered "which model" and nothing about "from
where" — an iOS app read `source_server` off the manifest and pulled the
`.mlmodelc` from Volume Vision directly. That made Volume Vision a second
thing a phone in an orchard has to reach, on a network path this app's own
farmops-api sidecar exists specifically to be the one door through (see
`farmops_api/wsgi.py`).

`attach_model_file` is the fix: an operator uploads the binary ONCE — straight
through this tool for something small, or through `tools/uploads.py`'s staged
chunks (`stage_file_chunk`/`commit_staged_file`, `attach_to_doctype="ML
Model"`) for something that is not — and the ML Model record owns it from then
on, as `model_file`, a plain Frappe Attach field. `get_model_file_chunk` is
the read half: an iOS app that already speaks farmops-api's `X-FarmOps-Token`
door reads the binary back through IT, in the same base64-chunked JSON shape
`stage_file_chunk` takes, rather than opening a second connection to a second
service with a second credential.

WHAT THIS DOES NOT DO. There is no on-demand proxy back to `source_server` when
a model has no `model_file` yet — `get_model_file_chunk` refuses by name and
says which tool fixes it. A pull-through cache that fetched from Volume Vision
the first time anybody asked would still make a phone's first request depend on
Volume Vision being reachable, which is the exact dependency this release
removes; an operator running `attach_model_file` once, deliberately, after a
training run is the one dependency it replaces it with.
"""

from __future__ import annotations

import base64
import json
import os

import frappe

from .. import compat, model_registry
from ..args import as_int, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import files as files_tools
from . import kpi as kpi_tools

DOCTYPE = "ML Model"

#: Most models one list call returns. See budget.py's RECORD_CAP for the same
#: reasoning: a cap that is reported beats a register that silently stops
#: somewhere nobody chose.
RECORD_CAP = 200

#: Raw bytes per piece `get_model_file_chunk` returns, before base64. 512 KB
#: matches FarmOpsKit's own upload slice (`FarmOpsConfig.uploadChunkBytes`,
#: see `tools/uploads.py`) so the read direction costs the client no new
#: chunking logic — it already drives one chunked transfer.
MODEL_CHUNK_BYTES = 512 * 1024

#: Fields `update_model` may edit. `status`, `company` and `deployed_at` are
#: deliberately absent — status moves only through activate_model/
#: deprecate_model, which is where the supersession bookkeeping lives, and
#: `company`/`piecework_activity` are the identity a caller is disambiguating
#: BY, not a field being renamed out from under an in-flight lookup.
_EDITABLE_FIELDS = (
	"model_name",
	"version",
	"source_uuid",
	"source_server",
	"model_kind",
	"model_format",
	"taxonomy_schema",
	"taxonomy_version",
	"file_size_bytes",
	"deployment_targets",
	"training_completed_at",
	"notes",
)


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


# ── resolving ─────────────────────────────────────────────────────────────


def _resolve(reference: str, company: str = "", version: str = ""):
	reference = str(reference or "").strip()
	if not reference:
		raise ToolError(
			"model is required — an ML Model docname (e.g. MLM-2026-0001), or a model_name to "
			"look up by. list_models has the register. Nothing was changed."
		)
	if frappe.db.exists(DOCTYPE, reference):
		return frappe.get_doc(DOCTYPE, reference)

	filters: dict = {"model_name": reference}
	if company:
		filters["company"] = resolve_company(company, required=False) or company
	if version:
		filters["version"] = str(version).strip()
	matches = frappe.db.get_all(DOCTYPE, filters=filters, pluck="name", limit=25)
	if len(matches) == 1:
		return frappe.get_doc(DOCTYPE, matches[0])
	if len(matches) > 1:
		raise ToolError(
			f"{reference!r} matches {len(matches)} ML Model records: "
			f"{', '.join(sorted(matches)[:10])}. Pass the docname, or narrow with version "
			"and/or company. Nothing was changed."
		)

	# A caller holding a manifest (get_active_model, or an iOS app's own cache)
	# has `source_uuid` under the name `uuid` and nothing else — see
	# `model_registry.build_model_manifest`. Tried last, after the docname and
	# model_name lookups, because a source_uuid is Volume Vision's identifier
	# and this app's own docname and model_name are what most callers pass.
	by_uuid = frappe.db.get_value(DOCTYPE, {"source_uuid": reference}, "name")
	if by_uuid:
		return frappe.get_doc(DOCTYPE, by_uuid)

	raise ToolError(
		f"no ML Model called {reference!r} on this site. list_models has the register. "
		"Nothing was changed."
	)


def _reference(args: dict) -> str:
	return as_str(args, "model") or as_str(args, "name") or as_str(args, "model_name", required=True)


# ── reading JSON arguments ───────────────────────────────────────────────


def _as_json_arg(args: dict, key: str, expected_type: type):
	"""`args[key]` as `expected_type` (list or dict) — accepts a native JSON
	value or a JSON string, since a model context sends both. `None` when the
	key is absent, so a caller can tell "not sent" from "sent empty"."""
	if key not in args or args[key] is None:
		return None
	raw = args[key]
	if isinstance(raw, expected_type):
		return raw
	if isinstance(raw, str):
		if raw.strip() == "":
			return expected_type()
		try:
			parsed = json.loads(raw)
		except (TypeError, ValueError):
			raise ToolError(f"{key} must be valid JSON. Nothing was changed.") from None
		if not isinstance(parsed, expected_type):
			raise ToolError(
				f"{key} must be a JSON {'array' if expected_type is list else 'object'}. "
				"Nothing was changed."
			)
		return parsed
	raise ToolError(
		f"{key} must be a JSON {'array' if expected_type is list else 'object'}. Nothing was changed."
	)


# ── reading a document ───────────────────────────────────────────────────


def _describe(doc) -> dict:
	return {
		"name": doc.name,
		"model_name": doc.model_name,
		"version": doc.version,
		"status": doc.status,
		"company": doc.company,
		"piecework_activity": doc.piecework_activity,
		"deployment_targets": doc.get("deployment_targets") or None,
		"source_uuid": doc.get("source_uuid") or None,
		"source_server": doc.get("source_server") or None,
		"model_kind": doc.get("model_kind") or None,
		"model_format": doc.get("model_format") or model_registry.DEFAULT_MODEL_FORMAT,
		"taxonomy_schema": doc.get("taxonomy_schema") or None,
		"taxonomy_version": doc.get("taxonomy_version") or None,
		"model_file": doc.get("model_file") or None,
		"class_names": model_registry.class_names_of(doc.as_dict()),
		"metrics": model_registry.metrics_of(doc.as_dict()),
		"file_size_bytes": doc.get("file_size_bytes") or None,
		"training_completed_at": str(doc.get("training_completed_at") or "") or None,
		"deployed_at": str(doc.get("deployed_at") or "") or None,
		"notes": doc.get("notes") or None,
	}


def _active_sibling(company: str, piecework_activity: str, exclude_name: str = ""):
	"""The Active ML Model for (company, piecework_activity), or `None`.

	The database read `model_registry.check_model_conflicts` cannot do itself —
	see the module docstring.
	"""
	filters = {
		"company": company,
		"piecework_activity": piecework_activity,
		"status": model_registry.STATUS_ACTIVE,
	}
	if exclude_name:
		filters["name"] = ("!=", exclude_name)
	name = frappe.db.get_value(DOCTYPE, filters, "name")
	if not name:
		return None
	return _describe(frappe.get_doc(DOCTYPE, name))


# ── 1. register_model ─────────────────────────────────────────────────────


def register_model(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Create an ML Model record from Volume Vision
	metadata: which trained model this is, what it predicts, and what it is
	used for. Starts life as Draft — activate_model is what makes it the model
	an iOS app pulls."""
	_require()
	actor = kpi_tools.require_kpi_role()

	company = resolve_company(as_str(args, "company"), required=True)
	model_name = as_str(args, "model_name", required=True)
	version = as_str(args, "version", required=True)
	piecework_activity = as_str(args, "piecework_activity", required=True)

	if frappe.db.exists(DOCTYPE, {"company": company, "model_name": model_name, "version": version}):
		raise ToolError(
			f"{model_name!r} version {version!r} is already registered for {company}. "
			"update_model edits the existing record; a genuinely different model wants a "
			"different name or version. Nothing was changed."
		)

	class_names = _as_json_arg(args, "class_names", list)
	metrics = _as_json_arg(args, "metrics", dict)
	status = as_str(args, "status") or model_registry.STATUS_DRAFT

	candidate = {
		"model_name": model_name,
		"version": version,
		"company": company,
		"piecework_activity": piecework_activity,
		"source_uuid": as_str(args, "source_uuid"),
		"model_kind": as_str(args, "model_kind"),
		"model_format": as_str(args, "model_format"),
		"status": status,
		"class_names": class_names,
		"metrics": metrics,
	}
	errors = model_registry.validate_model_registration(candidate)
	if errors:
		raise ToolError("; ".join(errors) + ". Nothing was changed.")

	doc = frappe.new_doc(DOCTYPE)
	doc.company = company
	doc.model_name = model_name
	doc.version = version
	doc.piecework_activity = piecework_activity
	doc.status = status
	doc.source_uuid = as_str(args, "source_uuid") or None
	doc.source_server = as_str(args, "source_server") or None
	doc.model_kind = as_str(args, "model_kind") or None
	doc.model_format = as_str(args, "model_format") or model_registry.DEFAULT_MODEL_FORMAT
	doc.taxonomy_schema = as_str(args, "taxonomy_schema") or None
	doc.taxonomy_version = as_str(args, "taxonomy_version") or None
	doc.deployment_targets = as_str(args, "deployment_targets") or None
	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes
	file_size_bytes = as_int(args, "file_size_bytes")
	if file_size_bytes is not None:
		doc.file_size_bytes = file_size_bytes
	training_completed_at = as_str(args, "training_completed_at")
	if training_completed_at:
		doc.training_completed_at = training_completed_at
	if class_names is not None:
		doc.class_names = json.dumps(class_names, default=str)
	if metrics is not None:
		doc.metrics = json.dumps(metrics, default=str)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = _describe(doc)
	return ToolResult(
		data={"actor": actor, "model": described},
		summary=f"registered {doc.model_name} v{doc.version} ({doc.name}) for {company}, status {status}",
		docstatus_delta="none → 0 (model registered)",
	)


# ── 2. update_model ────────────────────────────────────────────────────────


def update_model(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Edit metadata fields on an existing model.
	`status`, `company` and `piecework_activity` cannot be changed here —
	activate_model/deprecate_model own status, and a model's company and
	activity are its identity rather than an editable field."""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args), company=as_str(args, "company"), version=as_str(args, "version_hint"))
	before = _describe(doc)
	changed = []

	for field in _EDITABLE_FIELDS:
		if field not in args:
			continue
		if field == "file_size_bytes":
			value = as_int(args, field)
		else:
			value = as_str(args, field) or None
		if getattr(doc, field) != value:
			setattr(doc, field, value)
			changed.append(field)

	if "class_names" in args:
		class_names = _as_json_arg(args, "class_names", list)
		doc.class_names = json.dumps(class_names, default=str) if class_names is not None else None
		changed.append("class_names")

	if "metrics" in args:
		metrics = _as_json_arg(args, "metrics", dict)
		doc.metrics = json.dumps(metrics, default=str) if metrics is not None else None
		changed.append("metrics")

	if not changed:
		raise ToolError(
			f"nothing to update. Pass at least one of: {', '.join(_EDITABLE_FIELDS)}, class_names, "
			"metrics. Nothing was changed."
		)

	if "model_name" in changed or "version" in changed:
		clash = frappe.db.exists(
			DOCTYPE,
			{"company": doc.company, "model_name": doc.model_name, "version": doc.version, "name": ("!=", doc.name)},
		)
		if clash:
			raise ToolError(
				f"{doc.model_name!r} version {doc.version!r} is already registered for {doc.company} "
				f"as {clash}. Nothing was changed."
			)

	errors = model_registry.validate_model_registration(doc.as_dict())
	if errors:
		raise ToolError("; ".join(errors) + ". Nothing was changed.")

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = _describe(doc)
	return ToolResult(
		data={"actor": actor, "model": described, "changed_fields": changed, "previous": before},
		summary=f"updated {doc.name}: {', '.join(changed)}",
		docstatus_delta=f"{len(changed)} field(s) changed on {doc.name}",
	)


# ── 3. get_model ──────────────────────────────────────────────────────────


def get_model(args: dict) -> ToolResult:
	"""Read-only. One ML Model record in full."""
	_require()
	doc = _resolve(_reference(args), company=as_str(args, "company"), version=as_str(args, "version"))
	described = _describe(doc)
	return ToolResult(
		data=described,
		summary=f"{doc.model_name} v{doc.version} ({doc.name}): {doc.status} for {doc.company}",
	)


# ── 4. list_models ────────────────────────────────────────────────────────


def list_models(args: dict) -> ToolResult:
	"""Read-only. The model register: every ML Model matching the filters,
	newest first."""
	_require()
	limit = min(as_limit(args), RECORD_CAP)

	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	status = as_str(args, "status")
	if status:
		if status not in model_registry.STATUSES:
			raise ToolError(f"status must be one of {', '.join(model_registry.STATUSES)}; got {status!r}.")
		filters["status"] = status
	piecework_activity = as_str(args, "piecework_activity")
	if piecework_activity:
		filters["piecework_activity"] = piecework_activity

	found = frappe.db.get_all(
		DOCTYPE,
		filters=filters,
		fields=[
			"name", "model_name", "version", "status", "company", "piecework_activity",
			"source_uuid", "model_kind", "model_format", "deployed_at", "modified",
		],
		order_by="modified desc",
		limit=limit + 1,
	)
	truncated = len(found) > limit
	found = found[:limit]

	data = {"models": found, "count": len(found), "truncated": truncated, "limit": limit}
	summary = f"{len(found)} model(s)" + (f", truncated at {limit}" if truncated else "")
	return ToolResult(data=data, summary=summary)


# ── 5. activate_model ────────────────────────────────────────────────────


def activate_model(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Set status=Active and deployed_at=now. Whichever
	OTHER model was Active for the same (company, piecework_activity) auto-
	transitions to Deprecated — never more than one model is Active for one
	activity at one company. Activating an already-Active model is a no-op
	that still refreshes deployed_at."""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args), company=as_str(args, "company"), version=as_str(args, "version"))

	sibling = _active_sibling(doc.company, doc.piecework_activity, exclude_name=doc.name)
	conflict = model_registry.check_model_conflicts(
		{"name": doc.name, "company": doc.company, "piecework_activity": doc.piecework_activity},
		sibling,
	)

	was_active = doc.status == model_registry.STATUS_ACTIVE
	doc.status = model_registry.STATUS_ACTIVE
	doc.deployed_at = frappe.utils.now()
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = _describe(doc)
	data = {"actor": actor, "model": described, "superseded": conflict["supersedes"]}
	if conflict["supersedes"]:
		data["note"] = (
			f"{conflict['supersedes']} was Active for this (company, piecework_activity) pair and "
			"has been auto-deprecated. get_active_model for this pair now returns this record."
		)
	summary = f"activated {doc.model_name} v{doc.version} ({doc.name}) for {doc.company}/{doc.piecework_activity}"
	if was_active:
		summary += " (was already Active — deployed_at refreshed)"
	elif conflict["supersedes"]:
		summary += f", superseding {conflict['supersedes']}"
	return ToolResult(
		data=data,
		summary=summary,
		docstatus_delta=f"status → Active on {doc.name}" + (
			f"; {conflict['supersedes']} → Deprecated" if conflict["supersedes"] else ""
		),
	)


# ── 6. deprecate_model ────────────────────────────────────────────────────


def deprecate_model(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Set status=Deprecated. Nothing is deleted — a
	deprecated model keeps every field it had and simply stops being returned
	by get_active_model."""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args), company=as_str(args, "company"), version=as_str(args, "version"))
	if doc.status == model_registry.STATUS_DEPRECATED:
		raise ToolError(f"{doc.name} is already Deprecated. Nothing was changed.")

	previous_status = doc.status
	doc.status = model_registry.STATUS_DEPRECATED
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={"actor": actor, "name": doc.name, "status": doc.status, "previous_status": previous_status},
		summary=f"deprecated {doc.name} (was {previous_status})",
		docstatus_delta=f"status: {previous_status} → Deprecated on {doc.name}",
	)


# ── 7. get_active_model ──────────────────────────────────────────────────


def get_active_model(args: dict) -> ToolResult:
	"""Read-only. THE TOOL AN iOS APP QUERIES to find out which model to pull
	for one company and one piecework activity. Returns the full record and its
	manifest (uuid/name/class_names/metadata, matching Volume Vision's own
	`to_dict()` shape) when a model is Active; a clear "nothing deployed yet"
	result, not an error, when none is."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	piecework_activity = as_str(args, "piecework_activity", required=True)

	name = frappe.db.get_value(
		DOCTYPE,
		{"company": company, "piecework_activity": piecework_activity, "status": model_registry.STATUS_ACTIVE},
		"name",
	)
	if not name:
		return ToolResult(
			data={
				"company": company,
				"piecework_activity": piecework_activity,
				"active": False,
				"model": None,
				"manifest": None,
			},
			summary=f"no Active model for {company}/{piecework_activity}",
		)

	doc = frappe.get_doc(DOCTYPE, name)
	described = _describe(doc)
	manifest = model_registry.build_model_manifest(doc.as_dict())
	return ToolResult(
		data={
			"company": company,
			"piecework_activity": piecework_activity,
			"active": True,
			"model": described,
			"manifest": manifest,
		},
		summary=f"{doc.model_name} v{doc.version} ({doc.name}) is Active for {company}/{piecework_activity}",
	)


# ── 8. attach_model_file ─────────────────────────────────────────────────


def _attach_file_token(doc, file_token: str):
	if not frappe.db.exists("File", file_token):
		raise ToolError(f"no File named {file_token!r}. Nothing was changed.")
	file_doc = frappe.get_doc("File", file_token)
	if file_doc.get("is_folder"):
		raise ToolError(f"File {file_token!r} is a folder, not a model binary. Nothing was changed.")
	file_doc.attached_to_doctype = DOCTYPE
	file_doc.attached_to_name = doc.name
	file_doc.attached_to_field = "model_file"
	file_doc.flags.ignore_permissions = True
	file_doc.save(ignore_permissions=True)
	return file_doc


def _attach_file_content(doc, file_content: str, file_name: str):
	if not file_name:
		raise ToolError("file_name is required alongside file_content. Nothing was changed.")
	content = files_tools.decode_base64_content(file_content, tail="Nothing was changed.")
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": os.path.basename(str(file_name).replace("\\", "/")).strip() or "model",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"attached_to_field": "model_file",
			"is_private": 1,
			"content": content,
		}
	)
	file_doc.flags.ignore_permissions = True
	file_doc.insert(ignore_permissions=True)
	return file_doc


def attach_model_file(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Give an ML Model record the binary an iOS app
	pulls. This is the upload-once step v0.52.0's module docstring describes —
	after this call `get_model_file_chunk` can serve the model, and an iOS app
	never has to reach Volume Vision for it.

	TWO WAYS IN, EXACTLY ONE REQUIRED. `file_token` is a File docname already on
	this site — the shape `commit_staged_file` (`tools/uploads.py`) hands back
	after a large binary has gone up in pieces, which is the path any real
	`.mlmodelc` should take. `file_content` is base64 in the call itself, for
	something small enough to fit one argument; `decode_base64_content` refuses
	anything over this app's per-call ceiling rather than accepting a truncated
	model silently.

	RE-ATTACHING REPLACES `model_file` AND LEAVES THE OLD FILE ON THE SITE. The
	previous binary is not deleted — the same posture `generate_employee_badge_qr`
	takes with a badge `regenerate` supersedes — so a rollback to a previous
	version is `activate_model` on an older record plus a fresh attach, not a
	recovery from a bin.
	"""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args), company=as_str(args, "company"), version=as_str(args, "version"))

	file_token = as_str(args, "file_token")
	file_content = as_str(args, "file_content")

	if file_token and file_content:
		raise ToolError(
			"pass file_token (a File already on this site) or file_content (the bytes, base64), "
			"not both. Nothing was changed."
		)
	if not file_token and not file_content:
		raise ToolError(
			"one of file_token or file_content is required — there is no model binary to attach "
			"otherwise. Nothing was changed."
		)

	if file_token:
		file_doc = _attach_file_token(doc, file_token)
	else:
		file_doc = _attach_file_content(doc, file_content, as_str(args, "file_name"))

	doc.model_file = file_doc.file_url
	doc.file_size_bytes = int(file_doc.get("file_size") or 0)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = _describe(doc)
	return ToolResult(
		data={"actor": actor, "model": described, "file": file_doc.name, "file_size_bytes": doc.file_size_bytes},
		summary=f"attached {file_doc.get('file_name')} ({doc.file_size_bytes} bytes) to {doc.name}",
		docstatus_delta=f"model_file set on {doc.name}",
	)


# ── 9. get_model_file_chunk ──────────────────────────────────────────────


def get_model_file_chunk(args: dict) -> ToolResult:
	"""Read-only. One base64 slice of an ML Model's attached binary.

	THE SAME SHAPE `stage_file_chunk` TAKES, READ BACKWARDS. A compiled model
	can run to tens of megabytes; asking a caller — this app's own farmops-api
	sidecar included, see `farmops_api/wsgi.py`'s JSON-only promise — to hold
	the whole thing in one response is the same memory spike Sprint 6 built
	chunked staging to avoid on the way up. `chunk_index` counts from 0; a
	caller that does not know `total_chunks` yet asks for index 0 and reads it
	off the answer, the same contract `stage_file_chunk` documents for pieces
	going the other way.

	REFUSES BY NAME WHEN NOTHING IS ATTACHED YET, rather than reaching for
	`source_server` on the caller's behalf — see the module docstring on why
	there is no proxy here. `attach_model_file` is the fix, and the refusal
	says so.
	"""
	_require()
	doc = _resolve(_reference(args), company=as_str(args, "company"), version=as_str(args, "version"))

	model_file = doc.get("model_file")
	if not model_file:
		raise ToolError(
			f"{doc.name} has no model file attached yet. attach_model_file is what gives it one; "
			"until then this record is metadata only. Nothing was returned."
		)

	chunk_index = as_int(args, "chunk_index")
	if chunk_index is None or chunk_index < 0:
		raise ToolError("chunk_index is required and counts from 0. Nothing was returned.")
	requested = as_int(args, "chunk_bytes")
	chunk_bytes = MODEL_CHUNK_BYTES if requested is None else max(1, min(requested, MODEL_CHUNK_BYTES))

	file_name = frappe.db.get_value(
		"File",
		{"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name, "file_url": model_file},
		"name",
	)
	if not file_name:
		raise ToolError(
			f"{doc.name}'s model_file does not resolve to a File this site can read. "
			"attach_model_file again to fix it. Nothing was returned."
		)
	file_doc = frappe.get_doc("File", file_name)
	content = file_doc.get_content()
	total_bytes = len(content)
	total_chunks = max(1, -(-total_bytes // chunk_bytes))
	if chunk_index >= total_chunks:
		raise ToolError(
			f"chunk_index {chunk_index} is outside the {total_chunks} piece(s) this file has. "
			"Nothing was returned."
		)

	start = chunk_index * chunk_bytes
	piece = content[start : start + chunk_bytes]
	return ToolResult(
		data={
			"model": doc.name,
			"file_name": file_doc.get("file_name"),
			"chunk_index": chunk_index,
			"total_chunks": total_chunks,
			"chunk_bytes": len(piece),
			"total_bytes": total_bytes,
			"chunk_base64": base64.b64encode(piece).decode("ascii"),
			"is_last": chunk_index == total_chunks - 1,
		},
		summary=f"{doc.name} chunk {chunk_index + 1}/{total_chunks} ({len(piece)} bytes)",
	)
