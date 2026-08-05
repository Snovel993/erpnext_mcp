# SPDX-License-Identifier: MIT
"""Universal Asset Tags: scan it, see its history, log what happened.

Every reportable asset on the farm — a valve, a sprayer, a cabin, a cold storage
unit — gets a durable ID tag (QR and optional NFC) so a worker can scan it and
see what it is, what has happened to it, and what is due. The tag is the
docname, and the docname is the printable ID.

v0.25.0 adds state-change actions: a worker scans an asset and picks an action
(open a valve, fill a sprayer tank, winterize a cabin). The state machine
validates transitions, updates the asset's current_state, and logs the event.

WHY THE HISTORY IS CROSS-DOCTYPE. An irrigation valve's history includes water
tests, inspection sessions, compliance alerts, and farm tasks — all of them
pointing at the same asset name through different link fields. Pulling from one
doctype would be a timeline with gaps; pulling from all of them is the timeline
a worker standing in front of the valve actually needs.
"""

import base64
import json

import frappe

from .. import compat, settings
from ..args import as_bool, as_date, as_float, as_int, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..render import qr
from ..result import ToolResult

ASSET_REGISTER = "Asset Register"
ASSET_STATE_LOG = "Asset State Log"

REGISTER_CAP = 500

_ASSET_FIELDS = (
    "name",
    "asset_type",
    "company",
    "location",
    "qr_url",
    "nfc_uid",
    "current_state",
    "last_scan_at",
    "last_scan_by",
    "retired_at",
    "description",
    "gps_latitude",
    "gps_longitude",
    "creation",
    "owner",
)

ASSET_TYPES = (
    "Housing Unit",
    "Irrigation Zone",
    "Irrigation Valve",
    "Sprayer",
    "Tractor",
    "Block",
    "Water Source",
    "Storage",
    "Cold Storage",
    "General",
)

_HISTORY_DOCTYPES = (
    ("Farm Task", "asset_register", "name,task_type,status,priority,assigned_to,creation"),
    ("Housing Inspection", "housing_unit", "name,unit,inspector,inspection_date,creation"),
    ("Detector Test", "housing_unit", "name,unit,test_type,result,test_date,creation"),
    ("Water Test", "water_source", "name,source_name,test_type,result,test_date,creation"),
    ("Inspection Session", "location", "name,template,status,creation"),
    ("Compliance Alert", "asset_register", "name,alert_type,severity,status,creation"),
    ("Asset State Log", "asset_name", "name,action,from_state,to_state,performed_by,performed_at,creation"),
)


def _require() -> None:
    compat.require_doctype(
        ASSET_REGISTER,
        "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
    )


def _company(args: dict, required: bool = False) -> str | None:
    requested = as_str(args, "company")
    return resolve_company(requested, required=required)


def _parse_state(value) -> dict | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _describe_asset(row: dict) -> dict:
    return {
        "name": row.get("name"),
        "asset_type": row.get("asset_type") or None,
        "company": row.get("company") or None,
        "location": row.get("location") or None,
        "qr_url": row.get("qr_url") or None,
        "nfc_uid": row.get("nfc_uid") or None,
        "current_state": _parse_state(row.get("current_state")),
        "last_scan_at": str(row.get("last_scan_at") or "") or None,
        "last_scan_by": row.get("last_scan_by") or None,
        "retired": bool(row.get("retired_at")),
        "retired_at": str(row.get("retired_at") or "") or None,
        "description": row.get("description") or None,
        "gps_latitude": round(float(row.get("gps_latitude") or 0), 7) or None,
        "gps_longitude": round(float(row.get("gps_longitude") or 0), 7) or None,
    }


def asset_row(asset_name: str, company: str = "") -> dict:
    """One Asset Register record as a dict, from its docname."""
    asset_name = (asset_name or "").strip()
    if not asset_name:
        raise ToolError("asset_name is required (an Asset Register docname, e.g. 'MC-Valve-05')")
    fields = compat.existing_fields(ASSET_REGISTER, _ASSET_FIELDS)

    if frappe.db.exists(ASSET_REGISTER, asset_name):
        row = dict(frappe.db.get_value(ASSET_REGISTER, asset_name, fields, as_dict=True) or {})
        if company and row.get("company") and row["company"] != company:
            raise ToolError(f"Asset {asset_name!r} belongs to {row['company']!r}, not {company!r}")
        return row

    filters = {"name": ("like", f"%{asset_name}%")}
    if company:
        filters["company"] = company
    matches = frappe.db.get_all(ASSET_REGISTER, filters=filters, fields=fields, limit=25)
    if len(matches) == 1:
        return dict(matches[0])
    if len(matches) > 1:
        names = ", ".join(sorted(str(m.get("name")) for m in matches))
        raise ToolError(f"{asset_name!r} matches {len(matches)} assets: {names}. Pass the exact docname.")
    raise ToolError(f"no Asset Register record called {asset_name!r}. list_assets has the register.")


def _asset_history(asset_name: str, limit: int = 50) -> list:
    """Chronological history from all doctypes that reference this asset."""
    events = []
    for doctype, link_field, field_str in _HISTORY_DOCTYPES:
        if not compat.doctype_exists(doctype):
            continue
        if not compat.has_field(doctype, link_field):
            continue
        fields_wanted = [f.strip() for f in field_str.split(",")]
        fields_available = compat.existing_fields(doctype, fields_wanted)
        if not fields_available:
            continue
        try:
            rows = frappe.db.get_all(
                doctype,
                filters={link_field: asset_name},
                fields=fields_available,
                order_by="creation desc",
                limit=limit,
            )
        except Exception:
            continue
        for row in rows or []:
            event = {"doctype": doctype, "docname": row.get("name")}
            for field in fields_available:
                if field != "name":
                    event[field] = str(row.get(field) or "") or None
            event["timestamp"] = str(row.get("creation") or "")
            events.append(event)

    events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    return events[:limit]


# ── list_assets ────────────────────────────────────────────────────────────
def list_assets(args: dict) -> ToolResult:
    """The asset register: every tagged asset with its type, location and scan status."""
    _require()
    company = _company(args)
    limit = as_limit(args)

    filters = {}
    if company:
        filters["company"] = company
    asset_type = as_str(args, "asset_type")
    if asset_type:
        filters["asset_type"] = asset_type
    location = as_str(args, "location")
    if location:
        filters["location"] = location

    retired = as_bool(args, "retired")
    if retired is True:
        filters["retired_at"] = ("is", "set")
    elif retired is False or retired is None:
        filters["retired_at"] = ("is", "not set")

    rows = frappe.db.get_all(
        ASSET_REGISTER,
        filters=filters,
        fields=compat.existing_fields(ASSET_REGISTER, _ASSET_FIELDS),
        order_by="asset_type asc, name asc",
        limit=min(limit, REGISTER_CAP),
    )
    assets = [_describe_asset(dict(row)) for row in rows]

    by_type: dict = {}
    for asset in assets:
        key = asset["asset_type"] or "(unrecorded)"
        by_type[key] = by_type.get(key, 0) + 1

    return ToolResult(
        data={
            "company": company,
            "asset_count": len(assets),
            "by_asset_type": dict(sorted(by_type.items())),
            "assets": assets,
        },
        summary=f"{len(assets)} asset(s)" + (f" for {company}" if company else ""),
    )


# ── get_asset_detail ───────────────────────────────────────────────────────
def get_asset_detail(args: dict) -> ToolResult:
    """One asset in full: current state, open tasks, history timeline."""
    _require()
    company = _company(args)
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name, company or "")
    described = _describe_asset(row)

    history = _asset_history(row["name"], limit=as_limit(args))

    open_tasks = []
    if compat.doctype_exists("Farm Task") and compat.has_field("Farm Task", "asset_register"):
        try:
            tasks = frappe.db.get_all(
                "Farm Task",
                filters={
                    "asset_register": row["name"],
                    "status": ("in", ["Open", "In Progress", "Dispatched", "Claimed"]),
                },
                fields=compat.existing_fields(
                    "Farm Task", ["name", "task_type", "status", "priority", "assigned_to", "creation"]
                ),
                order_by="creation desc",
                limit=50,
            )
            open_tasks = [dict(t) for t in tasks or []]
        except Exception:
            pass

    children = frappe.db.get_all(
        ASSET_REGISTER,
        filters={"location": row["name"]},
        fields=compat.existing_fields(ASSET_REGISTER, ("name", "asset_type", "retired_at")),
        order_by="name asc",
        limit=REGISTER_CAP,
    )
    child_list = [
        {"name": c.get("name"), "asset_type": c.get("asset_type"), "retired": bool(c.get("retired_at"))}
        for c in children or []
    ]

    return ToolResult(
        data={
            **described,
            "open_tasks": open_tasks,
            "open_task_count": len(open_tasks),
            "children": child_list,
            "child_count": len(child_list),
            "history": history,
            "history_count": len(history),
        },
        summary=f"{row['name']}: {described['asset_type'] or 'untyped'}"
        + (f", {len(open_tasks)} open task(s)" if open_tasks else ""),
    )


# ── get_asset_history ──────────────────────────────────────────────────────
def get_asset_history(args: dict) -> ToolResult:
    """Chronological history of all events/tasks/inspections for one asset."""
    _require()
    company = _company(args)
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name, company or "")
    limit = as_limit(args)

    history = _asset_history(row["name"], limit=limit)

    return ToolResult(
        data={
            "asset_name": row["name"],
            "asset_type": row.get("asset_type"),
            "event_count": len(history),
            "events": history,
        },
        summary=f"{row['name']}: {len(history)} event(s)",
    )


# ── scan_asset ─────────────────────────────────────────────────────────────
def scan_asset(args: dict) -> ToolResult:
    """Record a scan event, return asset detail + open tasks + due compliance items.

    Updates last_scan_at and last_scan_by on the asset record. If GPS coordinates
    are provided, updates the asset's position too.
    """
    _require()
    name = as_str(args, "asset_name", required=True)
    scanned_by = as_str(args, "scanned_by")

    row = asset_row(name)
    doc = frappe.get_doc(ASSET_REGISTER, row["name"])

    doc.last_scan_at = frappe.utils.now()
    if scanned_by:
        doc.last_scan_by = scanned_by

    gps_lat = args.get("gps_lat") or args.get("gps_latitude")
    gps_lon = args.get("gps_lon") or args.get("gps_longitude")
    if gps_lat is not None and gps_lon is not None:
        try:
            doc.gps_latitude = float(gps_lat)
            doc.gps_longitude = float(gps_lon)
        except (TypeError, ValueError):
            pass

    doc.save(ignore_permissions=True)
    described = _describe_asset(dict(doc.as_dict()))

    open_tasks = []
    if compat.doctype_exists("Farm Task") and compat.has_field("Farm Task", "asset_register"):
        try:
            tasks = frappe.db.get_all(
                "Farm Task",
                filters={
                    "asset_register": doc.name,
                    "status": ("in", ["Open", "In Progress", "Dispatched", "Claimed"]),
                },
                fields=compat.existing_fields(
                    "Farm Task", ["name", "task_type", "status", "priority", "creation"]
                ),
                order_by="creation desc",
                limit=20,
            )
            open_tasks = [dict(t) for t in tasks or []]
        except Exception:
            pass

    due_compliance = []
    if compat.doctype_exists("Compliance Alert") and compat.has_field("Compliance Alert", "asset_register"):
        try:
            alerts = frappe.db.get_all(
                "Compliance Alert",
                filters={
                    "asset_register": doc.name,
                    "status": ("in", ["Open", "Overdue"]),
                },
                fields=compat.existing_fields(
                    "Compliance Alert", ["name", "alert_type", "severity", "status", "creation"]
                ),
                order_by="creation desc",
                limit=20,
            )
            due_compliance = [dict(a) for a in alerts or []]
        except Exception:
            pass

    return ToolResult(
        data={
            **described,
            "scan_recorded": True,
            "open_tasks": open_tasks,
            "open_task_count": len(open_tasks),
            "due_compliance": due_compliance,
            "due_compliance_count": len(due_compliance),
        },
        summary=f"scanned {doc.name}" + (f" by {scanned_by}" if scanned_by else ""),
        docstatus_delta="0 → 0 (updated)",
    )


# ── create_asset ───────────────────────────────────────────────────────────
def register_asset(args: dict) -> ToolResult:
    """Register a new asset with its tag ID, type, and location."""
    _require()
    company = _company(args, required=True)
    name = as_str(args, "name", required=True)

    if frappe.db.exists(ASSET_REGISTER, name):
        raise ToolError(
            f"Asset Register already has a record called {name!r}. The docname IS the "
            "printable tag ID, and two tags with the same string is two tags that resolve "
            "to the same record. Nothing was created."
        )

    asset_type = as_str(args, "asset_type", required=True)
    if asset_type not in ASSET_TYPES:
        from ..args import as_choice

        asset_type = as_choice(ASSET_REGISTER, "asset_type", asset_type, "asset_type")

    location = as_str(args, "location")
    if location:
        if not frappe.db.exists(ASSET_REGISTER, location):
            raise ToolError(
                f"Location {location!r} does not exist in Asset Register. The parent must "
                "be registered first. Nothing was created."
            )

    doc = frappe.new_doc(ASSET_REGISTER)
    doc.__newname = name
    doc.asset_type = asset_type
    doc.company = company
    doc.location = location or None
    doc.description = as_str(args, "description")
    doc.nfc_uid = as_str(args, "nfc_uid")

    lat = args.get("gps_latitude")
    lon = args.get("gps_longitude")
    if lat is not None:
        doc.gps_latitude = as_float(lat, "gps_latitude")
    if lon is not None:
        doc.gps_longitude = as_float(lon, "gps_longitude")

    doc.insert(ignore_permissions=True)
    described = _describe_asset(dict(doc.as_dict()))

    return ToolResult(
        data=described,
        summary=f"registered asset {doc.name} ({asset_type})",
        docstatus_delta="none → 0 (created)",
    )


# ── update_asset ───────────────────────────────────────────────────────────
def update_registered_asset(args: dict) -> ToolResult:
    """Update asset fields. Cannot rename — the docname is the tag ID."""
    _require()
    company = _company(args)
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name, company or "")

    doc = frappe.get_doc(ASSET_REGISTER, row["name"])
    changes = {}

    for key in ("description", "nfc_uid"):
        if key in args:
            _stage(changes, doc, key, as_str(args, key))
    if "asset_type" in args:
        value = as_str(args, "asset_type")
        if value:
            from ..args import as_choice

            value = as_choice(ASSET_REGISTER, "asset_type", value, "asset_type")
        _stage(changes, doc, "asset_type", value)
    if "location" in args:
        location = as_str(args, "location")
        if location and location == row["name"]:
            raise ToolError("An asset cannot be its own parent. Nothing was changed.")
        if location and not frappe.db.exists(ASSET_REGISTER, location):
            raise ToolError(f"Location {location!r} does not exist in Asset Register. Nothing was changed.")
        _stage(changes, doc, "location", location or None)
    for key in ("gps_latitude", "gps_longitude"):
        if key in args:
            _stage(changes, doc, key, as_float(args.get(key), key))
    if "current_state" in args:
        state = args.get("current_state")
        if state and isinstance(state, str):
            try:
                json.loads(state)
            except (json.JSONDecodeError, ValueError):
                raise ToolError("current_state must be valid JSON. Nothing was changed.")
        elif state and isinstance(state, dict):
            state = json.dumps(state)
        _stage(changes, doc, "current_state", state or None)

    if not changes:
        raise ToolError(
            "nothing to change. Pass at least one of: asset_type, location, description, "
            "nfc_uid, gps_latitude, gps_longitude, current_state."
        )

    doc.save(ignore_permissions=True)
    described = _describe_asset(dict(doc.as_dict()))

    return ToolResult(
        data={
            **described,
            "changed": {key: [before, after] for key, (before, after) in changes.items()},
        },
        summary=f"{doc.name}: {len(changes)} field(s) changed",
        docstatus_delta="0 → 0 (updated)",
    )


def _stage(changes: dict, doc, field: str, wanted) -> None:
    before = doc.get(field)
    before = "" if before is None else before
    if str(before) == str(wanted if wanted is not None else ""):
        return
    changes[field] = [before or None, wanted or None]
    doc.set(field, wanted if wanted != "" else None)


# ── retire_asset ───────────────────────────────────────────────────────────
def retire_asset(args: dict) -> ToolResult:
    """Soft-retire an asset: set retired_at, preserve all history."""
    _require()
    company = _company(args)
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name, company or "")

    if row.get("retired_at"):
        raise ToolError(
            f"{row['name']} was already retired on {row['retired_at']}. Nothing was changed."
        )

    doc = frappe.get_doc(ASSET_REGISTER, row["name"])
    doc.retired_at = as_date(args, "retired_at") or frappe.utils.today()
    reason = as_str(args, "reason")
    if reason:
        existing_desc = doc.description or ""
        doc.description = f"{existing_desc}\n\nRetired: {reason}".strip()
    doc.save(ignore_permissions=True)

    described = _describe_asset(dict(doc.as_dict()))
    return ToolResult(
        data={**described, "reason": reason or None},
        summary=f"retired {doc.name}" + (f": {reason}" if reason else ""),
        docstatus_delta="0 → 0 (updated)",
    )


# ── bulk_create_assets ─────────────────────────────────────────────────────
def bulk_create_assets(args: dict) -> ToolResult:
    """Bulk registration for initial rollout. Each item needs name, asset_type."""
    _require()
    company = _company(args, required=True)
    assets_list = args.get("assets")
    if not assets_list or not isinstance(assets_list, list):
        raise ToolError("assets is required and must be a list of asset objects.")
    if len(assets_list) > REGISTER_CAP:
        raise ToolError(f"bulk_create_assets accepts at most {REGISTER_CAP} assets per call.")

    created = []
    errors = []
    for index, item in enumerate(assets_list):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": "each asset must be an object"})
            continue
        name = str(item.get("name") or "").strip()
        asset_type = str(item.get("asset_type") or "").strip()
        if not name or not asset_type:
            errors.append({"index": index, "name": name, "error": "name and asset_type are required"})
            continue
        if frappe.db.exists(ASSET_REGISTER, name):
            errors.append({"index": index, "name": name, "error": f"{name} already exists"})
            continue

        doc = frappe.new_doc(ASSET_REGISTER)
        doc.__newname = name
        doc.asset_type = asset_type
        doc.company = company
        doc.location = str(item.get("location") or "").strip() or None
        doc.description = str(item.get("description") or "").strip() or None
        doc.nfc_uid = str(item.get("nfc_uid") or "").strip() or None
        if item.get("gps_latitude") is not None:
            try:
                doc.gps_latitude = float(item["gps_latitude"])
            except (TypeError, ValueError):
                pass
        if item.get("gps_longitude") is not None:
            try:
                doc.gps_longitude = float(item["gps_longitude"])
            except (TypeError, ValueError):
                pass
        try:
            doc.insert(ignore_permissions=True)
            created.append(_describe_asset(dict(doc.as_dict())))
        except Exception as exc:
            errors.append({"index": index, "name": name, "error": str(exc)})

    return ToolResult(
        data={
            "company": company,
            "created_count": len(created),
            "error_count": len(errors),
            "created": created,
            "errors": errors,
        },
        summary=f"bulk created {len(created)} asset(s), {len(errors)} error(s)",
        docstatus_delta="none → 0 (created)" if created else "",
    )


# ── generate_asset_qr ─────────────────────────────────────────────────────
def generate_asset_qr(args: dict) -> ToolResult:
    """Generate a QR code for one asset's tag ID."""
    _require()
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name)
    url = row.get("qr_url") or f"/scan/{row['name']}"

    fmt = as_str(args, "format") or "png"
    if fmt not in ("png", "matrix"):
        raise ToolError("format must be 'png' or 'matrix'.")

    rendered = qr.render(url)
    data = {
        "asset_name": row["name"],
        "qr_url": url,
        "modules": rendered["modules"],
        "pixels": rendered["pixels"],
        "scale": rendered["scale"],
        "border": rendered["border"],
        "error_correction": rendered["error_correction"],
        "encoder": rendered["encoder"],
    }
    if fmt == "png":
        data["png_base64"] = base64.b64encode(rendered["png"]).decode("ascii")
        data["png_bytes"] = len(rendered["png"])
    else:
        data["matrix"] = rendered["matrix"]

    return ToolResult(
        data=data,
        summary=f"QR code for {row['name']} ({rendered['modules']}×{rendered['modules']} modules)",
    )


# ── generate_asset_qr_sheet ───────────────────────────────────────────────
def generate_asset_qr_sheet(args: dict) -> ToolResult:
    """Bulk QR sheet: one QR per asset, for printing on Avery labels."""
    _require()
    names = args.get("asset_names")
    if not names or not isinstance(names, list):
        raise ToolError("asset_names is required and must be a list of asset docnames.")
    if len(names) > 100:
        raise ToolError("generate_asset_qr_sheet accepts at most 100 assets per call.")

    template = as_str(args, "template") or "avery_5160"

    labels = []
    errors = []
    for name in names:
        name = str(name or "").strip()
        if not name:
            continue
        if not frappe.db.exists(ASSET_REGISTER, name):
            errors.append({"asset_name": name, "error": "not found"})
            continue
        url_val = frappe.db.get_value(ASSET_REGISTER, name, "qr_url") or f"/scan/{name}"
        try:
            rendered = qr.render(url_val)
            labels.append({
                "asset_name": name,
                "qr_url": url_val,
                "png_base64": base64.b64encode(rendered["png"]).decode("ascii"),
                "modules": rendered["modules"],
            })
        except Exception as exc:
            errors.append({"asset_name": name, "error": str(exc)})

    return ToolResult(
        data={
            "template": template,
            "label_count": len(labels),
            "error_count": len(errors),
            "labels": labels,
            "errors": errors,
        },
        summary=f"{len(labels)} label(s) generated for {template}",
    )


# ══════════════════════════════════════════════════════════════════════════════
# v0.25.0 — state-change actions
# ══════════════════════════════════════════════════════════════════════════════

_STATE_DEFINITIONS: dict[str, dict] = {
    "Irrigation Valve": {
        "default": "closed",
        "actions": {
            "open_valve": {"from": ["closed"], "to": "open"},
            "close_valve": {"from": ["open"], "to": "closed"},
            "winterize": {"from": ["closed"], "to": "winterized"},
            "reopen": {"from": ["winterized"], "to": "closed"},
        },
    },
    "Housing Unit": {
        "default": "vacant",
        "actions": {
            "mark_occupied": {"from": ["vacant"], "to": "occupied"},
            "mark_vacant": {"from": ["occupied", "uninhabitable"], "to": "vacant"},
            "mark_uninhabitable": {"from": ["vacant", "occupied"], "to": "uninhabitable"},
            "winterize": {"from": ["vacant"], "to": "winterized"},
            "reopen": {"from": ["winterized"], "to": "vacant"},
        },
    },
    "Sprayer": {
        "default": "empty",
        "actions": {
            "fill_tank": {"from": ["empty", "cleaned"], "to": "loaded"},
            "start_spray": {"from": ["loaded"], "to": "in_use"},
            "end_spray": {"from": ["in_use"], "to": "empty"},
            "clean_tank": {"from": ["empty"], "to": "cleaned"},
        },
    },
    "Tractor": {
        "default": "in_service",
        "actions": {
            "put_in_service": {"from": ["out_of_service", "maintenance"], "to": "in_service"},
            "take_out_of_service": {"from": ["in_service"], "to": "out_of_service"},
            "start_maintenance": {"from": ["in_service", "out_of_service"], "to": "maintenance"},
            "end_maintenance": {"from": ["maintenance"], "to": "in_service"},
        },
    },
    "Water Source": {
        "default": "active",
        "actions": {
            "activate": {"from": ["inactive"], "to": "active"},
            "deactivate": {"from": ["active", "treated"], "to": "inactive"},
            "log_treatment": {"from": ["active", "contaminated"], "to": "treated"},
            "mark_contaminated": {"from": ["active", "treated"], "to": "contaminated"},
            "clear_contamination": {"from": ["contaminated"], "to": "active"},
        },
    },
    "Storage": {
        "default": "closed",
        "actions": {
            "open_for_season": {"from": ["closed", "off_season"], "to": "open"},
            "close_for_season": {"from": ["open", "active_season"], "to": "off_season"},
            "log_temperature": {"from": ["open", "active_season"], "to": "active_season"},
        },
    },
    "Cold Storage": {
        "default": "closed",
        "actions": {
            "open_for_season": {"from": ["closed", "off_season"], "to": "open"},
            "close_for_season": {"from": ["open", "active_season"], "to": "off_season"},
            "log_temperature": {"from": ["open", "active_season"], "to": "active_season"},
        },
    },
    "Block": {
        "default": "active",
        "actions": {
            "activate": {"from": ["dormant", "fallow"], "to": "active"},
            "set_dormant": {"from": ["active"], "to": "dormant"},
            "set_fallow": {"from": ["active", "dormant"], "to": "fallow"},
        },
    },
    "Irrigation Zone": {
        "default": "active",
        "actions": {
            "activate": {"from": ["winterized", "offline"], "to": "active"},
            "winterize": {"from": ["active"], "to": "winterized"},
            "take_offline": {"from": ["active", "winterized"], "to": "offline"},
        },
    },
    "General": {
        "default": "active",
        "actions": {
            "activate": {"from": ["inactive", "needs_repair"], "to": "active"},
            "deactivate": {"from": ["active"], "to": "inactive"},
            "flag_repair": {"from": ["active", "inactive"], "to": "needs_repair"},
            "clear_repair": {"from": ["needs_repair"], "to": "active"},
        },
    },
}


def _current_state_value(state_json) -> str:
    parsed = _parse_state(state_json)
    if parsed and isinstance(parsed, dict):
        return str(parsed.get("state") or "")
    return ""


def _actions_for(asset_type: str, current: str) -> list[dict]:
    defn = _STATE_DEFINITIONS.get(asset_type)
    if not defn:
        return []
    result = []
    effective = current or defn["default"]
    for action_name, rule in defn["actions"].items():
        if effective in rule["from"]:
            result.append({
                "action": action_name,
                "from_state": effective,
                "to_state": rule["to"],
            })
    return result


# ── get_available_actions ─────────────────────────────────────────────────
def get_available_actions(args: dict) -> ToolResult:
    """What can be done to this asset right now, given its type and current state."""
    _require()
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name)
    asset_type = row.get("asset_type") or "General"
    current = _current_state_value(row.get("current_state"))

    defn = _STATE_DEFINITIONS.get(asset_type, _STATE_DEFINITIONS["General"])
    effective = current or defn["default"]
    actions = _actions_for(asset_type, effective)

    all_states = set()
    for rule in defn["actions"].values():
        all_states.update(rule["from"])
        all_states.add(rule["to"])

    return ToolResult(
        data={
            "asset_name": row["name"],
            "asset_type": asset_type,
            "current_state": effective,
            "available_actions": actions,
            "all_states": sorted(all_states),
        },
        summary=f"{row['name']}: {len(actions)} available action(s) from {effective!r}",
    )


# ── log_asset_state_change ───────────────────────────────────────────────
def log_asset_state_change(args: dict) -> ToolResult:
    """Perform a state-change action on an asset.

    Validates the transition, updates current_state on the Asset Register record,
    and writes an Asset State Log entry.
    """
    _require()
    compat.require_doctype(
        ASSET_STATE_LOG,
        "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
    )

    name = as_str(args, "asset_name", required=True)
    action = as_str(args, "action", required=True)
    row = asset_row(name)
    asset_type = row.get("asset_type") or "General"
    current = _current_state_value(row.get("current_state"))

    defn = _STATE_DEFINITIONS.get(asset_type, _STATE_DEFINITIONS["General"])
    effective = current or defn["default"]

    action_def = defn["actions"].get(action)
    if not action_def:
        valid = sorted(defn["actions"].keys())
        raise ToolError(
            f"{action!r} is not a valid action for asset type {asset_type!r}. "
            f"Valid actions: {', '.join(valid)}."
        )

    if effective not in action_def["from"]:
        available = _actions_for(asset_type, effective)
        available_names = [a["action"] for a in available]
        raise ToolError(
            f"Cannot {action!r} from state {effective!r}. "
            f"Available actions in this state: {', '.join(available_names) or 'none'}."
        )

    to_state = action_def["to"]

    doc = frappe.get_doc(ASSET_REGISTER, row["name"])
    doc.current_state = json.dumps({"state": to_state})
    doc.save(ignore_permissions=True)

    log = frappe.new_doc(ASSET_STATE_LOG)
    log.asset_name = row["name"]
    log.asset_type = asset_type
    log.action = action
    log.from_state = effective
    log.to_state = to_state
    log.performed_by = as_str(args, "performed_by") or (frappe.session.user if hasattr(frappe, "session") else None)
    log.performed_at = frappe.utils.now()
    log.notes = as_str(args, "notes")

    gps_lat = args.get("gps_lat") or args.get("gps_latitude")
    gps_lon = args.get("gps_lon") or args.get("gps_longitude")
    if gps_lat is not None:
        try:
            log.gps_latitude = float(gps_lat)
        except (TypeError, ValueError):
            pass
    if gps_lon is not None:
        try:
            log.gps_longitude = float(gps_lon)
        except (TypeError, ValueError):
            pass

    photo = as_str(args, "photo_file_token")
    if photo:
        log.photo = photo

    log.insert(ignore_permissions=True)

    return ToolResult(
        data={
            "asset_name": row["name"],
            "asset_type": asset_type,
            "action": action,
            "from_state": effective,
            "to_state": to_state,
            "log_name": log.name,
            "performed_by": log.performed_by,
            "performed_at": str(log.performed_at or ""),
        },
        summary=f"{row['name']}: {action} ({effective} → {to_state})",
        docstatus_delta="0 → 0 (updated)",
    )


# ── list_asset_state_history ─────────────────────────────────────────────
def list_asset_state_history(args: dict) -> ToolResult:
    """Chronological state-change log for one asset."""
    _require()
    name = as_str(args, "asset_name", required=True)
    row = asset_row(name)
    limit = as_limit(args)

    if not compat.doctype_exists(ASSET_STATE_LOG):
        return ToolResult(
            data={"asset_name": row["name"], "event_count": 0, "events": []},
            summary=f"{row['name']}: no state log (Asset State Log doctype not installed)",
        )

    fields = compat.existing_fields(
        ASSET_STATE_LOG,
        ("name", "action", "from_state", "to_state", "performed_by", "performed_at",
         "notes", "gps_latitude", "gps_longitude", "photo", "asset_type", "creation"),
    )

    rows = frappe.db.get_all(
        ASSET_STATE_LOG,
        filters={"asset_name": row["name"]},
        fields=fields,
        order_by="creation desc",
        limit=limit,
    )

    events = []
    for r in rows or []:
        events.append({
            "log_name": r.get("name"),
            "action": r.get("action"),
            "from_state": r.get("from_state"),
            "to_state": r.get("to_state"),
            "performed_by": r.get("performed_by") or None,
            "performed_at": str(r.get("performed_at") or r.get("creation") or ""),
            "notes": r.get("notes") or None,
            "gps_latitude": round(float(r.get("gps_latitude") or 0), 7) or None,
            "gps_longitude": round(float(r.get("gps_longitude") or 0), 7) or None,
            "photo": r.get("photo") or None,
        })

    return ToolResult(
        data={
            "asset_name": row["name"],
            "asset_type": row.get("asset_type"),
            "event_count": len(events),
            "events": events,
        },
        summary=f"{row['name']}: {len(events)} state change(s)",
    )
