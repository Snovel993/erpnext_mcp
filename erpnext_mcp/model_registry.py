# SPDX-License-Identifier: MIT
"""Which ML model is deployed for which piecework activity. PURE FUNCTIONS.

Same contract `budget_engine.py` and `payroll_gl.py` keep: no database reads, no
side effects, everything arrives as a plain dict and everything returned is
derivable again from the same dict. `tools/ml_model.py` is the only place that
reads or writes an `ML Model` document.

WHAT AN `ML Model` RECORD IS. Volume Vision (the training side) already knows a
`TrainedModel` by its own `uuid`, `name`, `class_names_json` and
`metrics_json`. This app does not train anything and does not hold the weights
— it holds ONE FACT Volume Vision has no reason to know: which of its trained
models is the one an iOS app (BucketLog, Farm Ops) should be pulling right now
for a given company and a given piecework activity. `source_uuid` is the join
key back to Volume Vision's `TrainedModel.uuid`, which is also the key the iOS
offline cache is keyed by — so `get_active_model`'s manifest and a locally
cached model are talking about the same UUID without either side maintaining a
translation table.

`build_model_manifest` MATCHES VOLUME VISION'S OWN `to_dict()` SHAPE
(uuid/name/class_names/metadata) rather than inventing a second one, because an
iOS client that already parses one manifest shape from Volume Vision's sync
endpoint should not need a second parser for ERPNext's.
"""

from __future__ import annotations

import json
import re

STATUS_DRAFT = "Draft"
STATUS_ACTIVE = "Active"
STATUS_DEPRECATED = "Deprecated"
STATUS_ARCHIVED = "Archived"
STATUSES = (STATUS_DRAFT, STATUS_ACTIVE, STATUS_DEPRECATED, STATUS_ARCHIVED)

MODEL_KIND_CLASSIFICATION = "Classification"
MODEL_KIND_SEGMENTATION = "Segmentation"
MODEL_KIND_DETECTION = "Detection"
MODEL_KIND_OTHER = "Other"
MODEL_KINDS = (MODEL_KIND_CLASSIFICATION, MODEL_KIND_SEGMENTATION, MODEL_KIND_DETECTION, MODEL_KIND_OTHER)

MODEL_FORMAT_COREML = "CoreML"
MODEL_FORMAT_ONNX = "ONNX"
MODEL_FORMAT_TENSORFLOW = "TensorFlow"
MODEL_FORMAT_OTHER = "Other"
MODEL_FORMATS = (MODEL_FORMAT_COREML, MODEL_FORMAT_ONNX, MODEL_FORMAT_TENSORFLOW, MODEL_FORMAT_OTHER)
DEFAULT_MODEL_FORMAT = MODEL_FORMAT_COREML

#: `version` is compared and sorted as a string everywhere in this app (the
#: same way ERPNext compares one), so the only thing worth policing is that it
#: looks like a version at all — digits and dots, one to four segments: "3",
#: "3.2", "3.2.1". Not full semver (no pre-release/build suffix) because
#: Volume Vision's own `TrainedModel.version` is typically an integer or a
#: short dotted counter, not a package version.
_VERSION_PATTERN = re.compile(r"^\d+(\.\d+){0,3}$")
_UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _clean(value) -> str:
    return str(value or "").strip()


def _parse_json_value(raw, expected_type: type):
    """`(parsed, error)` — `raw` may already be `expected_type`, a JSON string
    of it, or empty. `error` is `None` on success; `parsed` is `None` on
    failure or when `raw` is empty."""
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, expected_type):
        return raw, None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None, "must be valid JSON"
        if not isinstance(parsed, expected_type):
            return None, f"must be a JSON {'array' if expected_type is list else 'object'}"
        return parsed, None
    return None, f"must be a JSON {'array' if expected_type is list else 'object'} or a {expected_type.__name__}"


def class_names_of(model_doc: dict) -> list:
    """`class_names` read as a list, or `[]` if absent or unparseable.

    Lenient by design — this is a read path (`build_model_manifest`), not a
    validation path. `validate_model_registration` is where an unparseable
    value becomes a reported error instead of a silently empty list.
    """
    parsed, _error = _parse_json_value((model_doc or {}).get("class_names"), list)
    return parsed if parsed is not None else []


def metrics_of(model_doc: dict) -> dict:
    """`metrics` read as a dict, or `{}` if absent or unparseable."""
    parsed, _error = _parse_json_value((model_doc or {}).get("metrics"), dict)
    return parsed if parsed is not None else {}


# ── validating a registration ────────────────────────────────────────────


def validate_model_registration(model_doc: dict) -> list:
    """Every reason `model_doc` cannot become (or remain) an ML Model record.

    Empty list means valid. Checks only what this module can check without a
    database: required fields, recognisable shapes for `version` and
    `source_uuid`, and that `class_names`/`metrics` are the JSON shape they
    claim to be. Uniqueness (one Active model per company + activity) needs a
    database read and lives in `check_model_conflicts` instead.
    """
    model_doc = model_doc or {}
    errors = []

    if not _clean(model_doc.get("model_name")):
        errors.append("model_name is required")

    version = _clean(model_doc.get("version"))
    if not version:
        errors.append("version is required")
    elif not _VERSION_PATTERN.match(version):
        errors.append(
            f"version {version!r} is not a recognized format — digits and dots only, e.g. '3.2' or '1'"
        )

    if not _clean(model_doc.get("company")):
        errors.append("company is required")

    if not _clean(model_doc.get("piecework_activity")):
        errors.append(
            "piecework_activity is required — it is the other half of the (company, "
            "piecework_activity) pair that determines which model is Active"
        )

    source_uuid = _clean(model_doc.get("source_uuid"))
    if source_uuid and not _UUID_PATTERN.match(source_uuid):
        errors.append(f"source_uuid {source_uuid!r} is not a well-formed UUID")

    model_kind = model_doc.get("model_kind")
    if model_kind and model_kind not in MODEL_KINDS:
        errors.append(f"model_kind must be one of {', '.join(MODEL_KINDS)}; got {model_kind!r}")

    model_format = model_doc.get("model_format")
    if model_format and model_format not in MODEL_FORMATS:
        errors.append(f"model_format must be one of {', '.join(MODEL_FORMATS)}; got {model_format!r}")

    status = model_doc.get("status")
    if status and status not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}; got {status!r}")

    _class_names, class_names_error = _parse_json_value(model_doc.get("class_names"), list)
    if class_names_error:
        errors.append(f"class_names {class_names_error}")

    _metrics, metrics_error = _parse_json_value(model_doc.get("metrics"), dict)
    if metrics_error:
        errors.append(f"metrics {metrics_error}")

    return errors


# ── building the manifest an iOS app pulls ───────────────────────────────


def build_model_manifest(model_doc: dict) -> dict:
    """The ERPNext record, reshaped to what Volume Vision's `to_dict()` returns.

    `uuid` is `source_uuid` when this model came from Volume Vision, falling
    back to the ERPNext docname for a model registered without one — an iOS
    cache keyed on `uuid` still gets a stable key either way, it is just not
    one Volume Vision itself would recognise.
    """
    model_doc = model_doc or {}
    return {
        "uuid": _clean(model_doc.get("source_uuid")) or _clean(model_doc.get("name")),
        "name": model_doc.get("model_name"),
        "class_names": class_names_of(model_doc),
        "metadata": {
            "version": model_doc.get("version"),
            "kind": model_doc.get("model_kind"),
            "format": model_doc.get("model_format") or DEFAULT_MODEL_FORMAT,
            "piecework_activity": model_doc.get("piecework_activity"),
            "taxonomy_schema": model_doc.get("taxonomy_schema"),
            "taxonomy_version": model_doc.get("taxonomy_version"),
            "metrics": metrics_of(model_doc),
            "file_size_bytes": model_doc.get("file_size_bytes"),
            "source_server": model_doc.get("source_server"),
            # v0.52.0. Whether get_model_file_chunk has something to serve. An
            # iOS app checks this before ever asking for a chunk rather than
            # discovering "not attached yet" on the first read call — and,
            # deliberately, before it would ever fall back to source_server.
            "downloadable": bool(_clean(model_doc.get("model_file"))),
            "deployment_targets": model_doc.get("deployment_targets"),
            "status": model_doc.get("status"),
            "deployed_at": model_doc.get("deployed_at"),
            "training_completed_at": model_doc.get("training_completed_at"),
        },
    }


# ── checking for a conflicting Active model ──────────────────────────────


def check_model_conflicts(candidate: dict, existing_active: dict | None) -> dict:
    """Would activating `candidate` collide with `existing_active`?

    `existing_active` is whatever the caller already found — the Active model,
    if any, for `candidate`'s (company, piecework_activity) pair — or `None`.
    This function makes no database read of its own; the read is
    `tools/ml_model.py`'s job precisely because it needs a database to do it.

    A candidate that reactivates ITSELF (same `name` as `existing_active`) is
    not a conflict: `activate_model` on an already-Active model is a no-op
    that should not report deprecating anything.
    """
    candidate = candidate or {}
    if not existing_active:
        return {"conflict": False, "supersedes": None}

    same_record = bool(candidate.get("name")) and candidate.get("name") == existing_active.get("name")
    if same_record:
        return {"conflict": False, "supersedes": None}

    same_company = _clean(candidate.get("company")) == _clean(existing_active.get("company"))
    same_activity = _clean(candidate.get("piecework_activity")) == _clean(existing_active.get("piecework_activity"))
    if same_company and same_activity:
        return {
            "conflict": True,
            "supersedes": existing_active.get("model_name") or existing_active.get("name"),
        }
    return {"conflict": False, "supersedes": None}
