# SPDX-License-Identifier: MIT
"""The register of handsets a break horn is delivered to, and the two admin reads.

FOUR FUNCTIONS, TWO OF THEM TOOLS. `register_push_token` and
`unregister_push_token` are reached only from the mobile surface — a phone
enrols ITSELF, and there is no version of "enrol somebody else's handset" that
is a thing a model should be able to do. `list_push_tokens` and `send_test_push`
are the catalogue's half: the register an operator reads when a worker says the
tone never reaches them, and the one send that proves the pipeline end to end
without waiting for a real break.

THE UPSERT IS THE WHOLE OF THE REGISTRATION. iOS calls `register_push_token` on
every login and every launch, which is many times a day per handset, and Apple
hands the app a new device token whenever it feels like it. So the contract is:
find the row for this device, write whatever token it is presenting now, stamp
`last_used_at`, and reactivate it if a previous logout had retired it. A register
that appended would have a season's worth of dead rows per phone by August, and
each one of them would be a wasted request on every crew push.

WHY REACTIVATION IS SILENT AND DEACTIVATION IS NOT. Logging back in IS the
consent to be pushed to — the app asks for notification permission at the OS
level and the server never sees the answer, so a handset that is calling this
method is by definition one the worker has signed into. Logging out is a
deliberate act and `unregister_push_token` records it by clearing the flag
rather than by deleting the row, so a phone that went quiet still has a row
saying when and why.
"""

import frappe

from .. import compat
from ..args import as_bool, as_limit, as_str
from ..erpnext_mcp.doctype.mobile_push_token import mobile_push_token as token_doctype
from ..errors import ToolError
from ..result import ToolResult
from ..services import push as push_service
from . import employee as employee_tool

PUSH_TOKEN = "Mobile Push Token"
EMPLOYEE = "Employee"

#: What a read hands back. `token` IS INCLUDED and deliberately so: it is a
#: routing address rather than a credential, it authorises nobody to do anything
#: without this farm's own signing key, and an operator debugging a handset that
#: does not buzz needs to see whether the string on the row is the one the phone
#: thinks it registered. The MOBILE wrappers do not echo it back — see
#: `api/mobile.register_push_token`, where `guard.strip_secrets` would remove it
#: anyway and the handset has no use for it.
_FIELDS = (
	"name",
	"user",
	"employee",
	"employee_name",
	"platform",
	"device_id",
	"device_key",
	"token",
	"is_active",
	"app_version",
	"device_model",
	"registered_at",
	"last_used_at",
	"last_error",
)


def _require() -> None:
	compat.require_doctype(
		PUSH_TOKEN,
		"which ships with erpnext_mcp — run `bench migrate` to create it",
	)


def _existing(platform: str, device_id: str):
	"""The row for this device, by its composite key. None if this is a new phone."""
	key = token_doctype.device_key(platform, device_id)
	rows = frappe.db.get_all(PUSH_TOKEN, filters={"device_key": key}, fields=["name"], limit=1)
	return rows[0]["name"] if rows else None


def _normalised(args: dict) -> tuple:
	"""platform and device_id, validated the same way for both write paths.

	Shared rather than duplicated because the two methods must agree exactly on
	what a device key is: a logout that normalised differently from the login
	would clear no row and leave a handset receiving break horns after the worker
	signed out of it.
	"""
	platform = (as_str(args, "platform") or "ios").strip().lower()
	if platform not in token_doctype.PLATFORMS:
		raise ToolError(f"platform must be one of {', '.join(token_doctype.PLATFORMS)}. Got {platform!r}.")
	device_id = as_str(args, "device_id", required=True).strip()
	if len(device_id) > token_doctype.MAX_DEVICE_ID:
		raise ToolError(
			f"device_id is {len(device_id)} characters; the maximum is "
			f"{token_doctype.MAX_DEVICE_ID}. Send the handset's own identifier "
			"(`identifierForVendor` on iOS), not a description of it."
		)
	return platform, device_id


# ── 1. register_push_token (mobile only) ────────────────────────────────────


def register_push_token(args: dict) -> ToolResult:
	"""Store — or refresh — one handset's device token. Idempotent per device.

	`user` and `employee` are NOT taken from the body by the wrapper that calls
	this: a phone enrols itself, and a body that could name somebody else would
	be a way to have another worker's break horns delivered to your own handset.
	The mobile endpoint resolves both from the caller's own login and passes them
	in.
	"""
	_require()
	platform, device_id = _normalised(args)
	token = as_str(args, "token", required=True).strip()
	user = as_str(args, "user", required=True).strip()
	employee = as_str(args, "employee") or None

	if employee:
		employee = employee_tool.resolve_employee(employee)

	values = {
		"user": user,
		"employee": employee,
		"platform": platform,
		"device_id": device_id,
		"token": token,
		"is_active": 1,
		"last_used_at": frappe.utils.now(),
	}
	for key in ("app_version", "device_model"):
		value = as_str(args, key)
		if value:
			values[key] = value

	name = _existing(platform, device_id)
	if name:
		doc = frappe.get_doc(PUSH_TOKEN, name)
		previous_token = str(doc.get("token") or "")
		for key, value in values.items():
			# `employee` is the one field a re-registration may not blank. A
			# launch that arrived before the worker's Employee record was linked
			# would otherwise erase the link the previous launch established, and
			# an employee-less token is a token no crew push will ever reach.
			if key == "employee" and not value:
				continue
			doc.set(key, value)
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		created = False
		rotated = bool(previous_token and previous_token != token)
	else:
		doc = frappe.get_doc({"doctype": PUSH_TOKEN, **values})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		created = True
		rotated = False

	row = frappe.db.get_all(PUSH_TOKEN, filters={"name": doc.name}, fields=list(_FIELDS), limit=1)
	return ToolResult(
		data={
			"push_token": (row[0] if row else {"name": doc.name}),
			"created": created,
			"token_rotated": rotated,
			"apns": {"configured": push_service.apns_config()["configured"]},
		},
		summary=(
			f"{'registered' if created else 'refreshed'} {platform} push token for {user} "
			f"on device {device_id}" + (" (token rotated)" if rotated else "")
		),
		docstatus_delta="none → 0 (draft)" if created else "0 → 0 (amended)",
	)


# ── 2. unregister_push_token (mobile only) ──────────────────────────────────


def unregister_push_token(args: dict) -> ToolResult:
	"""Retire one handset's token on logout. A soft delete, and never a hard one.

	A device this app has never seen is NOT an error. A phone that logs out
	before it ever finished registering is a normal thing to happen on a bad
	signal, and a refusal there would be an error dialog on a screen the worker
	is already leaving.
	"""
	_require()
	platform, device_id = _normalised(args)
	user = as_str(args, "user").strip()

	name = _existing(platform, device_id)
	if not name:
		return ToolResult(
			data={"deactivated": False, "found": False, "platform": platform, "device_id": device_id},
			summary=f"no {platform} push token on file for device {device_id}; nothing to retire",
		)

	doc = frappe.get_doc(PUSH_TOKEN, name)
	# THE OWNERSHIP CHECK. A device key is guessable in principle, and without
	# this a caller could retire a colleague's handset and take them off every
	# crew push for the rest of the season. The mobile wrapper always passes the
	# caller's own login; a mismatch is refused rather than silently ignored.
	if user and str(doc.get("user") or "") and str(doc.get("user")) != user:
		raise ToolError(
			f"device {device_id} is registered to another account. A handset is retired by "
			"the login that registered it. Nothing was changed."
		)

	already = not compat.checked(doc.get("is_active"))
	doc.is_active = 0
	doc.last_error = "unregistered"
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={
			"deactivated": True,
			"found": True,
			"was_already_inactive": already,
			"push_token": doc.name,
			"platform": platform,
			"device_id": device_id,
		},
		summary=f"retired {platform} push token {doc.name} for device {device_id}",
		docstatus_delta="0 → 0 (amended)",
	)


# ── 3. list_push_tokens ─────────────────────────────────────────────────────


def list_push_tokens(args: dict) -> ToolResult:
	"""Every handset on file, filtered. The read behind "why is their phone quiet".

	Reports `apns` alongside the rows, because the commonest answer to that
	question is not on any row: a site with no p8 key configured has a perfect
	register and sends nothing, and a list that showed twenty healthy tokens
	without saying so would send an operator looking in the wrong place.
	"""
	_require()
	employee_tool.require_shift_role()

	filters = {}
	employee = as_str(args, "employee")
	if employee:
		filters["employee"] = employee_tool.resolve_employee(employee)
	user = as_str(args, "user")
	if user:
		filters["user"] = user
	platform = as_str(args, "platform")
	if platform:
		filters["platform"] = platform.strip().lower()
	is_active = as_bool(args, "is_active")
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0

	rows = (
		frappe.db.get_all(
			PUSH_TOKEN,
			filters=filters or None,
			fields=list(_FIELDS),
			order_by="modified desc",
			limit_page_length=as_limit(args),
		)
		or []
	)

	config = push_service.apns_config()
	active = sum(1 for row in rows if compat.checked(row.get("is_active")))
	return ToolResult(
		data={
			"push_tokens": rows,
			"count": len(rows),
			"active": active,
			"inactive": len(rows) - active,
			"filters": filters,
			"apns": {
				"configured": config["configured"],
				"environment": config["environment"],
				"topic": config["topic"],
				"requires": "" if config["configured"] else push_service.APNS_REQUIREMENTS,
			},
		},
		summary=f"{len(rows)} push token(s), {active} active",
	)


# ── 4. send_test_push ───────────────────────────────────────────────────────


def send_test_push(args: dict) -> ToolResult:
	"""One push to one worker's handsets, to prove the pipeline before a real break.

	MUTATING because it makes a phone in somebody's pocket ring — which is the
	sort of thing an operator should have to switch on deliberately, and the
	reason this is not a read despite writing almost nothing.

	AN UNCONFIGURED SITE IS REPORTED RATHER THAN REFUSED. "This site has no p8
	key" is precisely the answer somebody running this tool is trying to find
	out, and raising it as an error would put it in a place where the arguments
	that produced it are harder to read.
	"""
	_require()
	employee_tool.require_shift_role()

	employee = employee_tool.resolve_employee(as_str(args, "employee", required=True))
	message = as_str(args, "message") or "Test notification from the farm office."

	tokens = push_service.active_tokens_for_employees([employee])
	payload = {
		"aps": {
			"alert": {"title": "FarmOps test", "body": message},
			"sound": push_service.SOUND_BREAK_START,
			"interruption-level": push_service.INTERRUPTION_LEVEL,
			"category": push_service.CATEGORY_TEST,
		},
		"phase": "test",
	}
	report = push_service.send_push(tokens, payload)

	return ToolResult(
		data={
			"employee": employee,
			"message": message,
			"tokens": len(tokens),
			"devices": [
				{"platform": row.get("platform"), "device_id": row.get("device_id")} for row in tokens
			],
			"result": report,
			"apns": {"configured": push_service.apns_config()["configured"]},
		},
		summary=(
			f"test push to {employee}: {report['sent']} sent, {report['failed']} failed, "
			f"{report['skipped']} skipped ({report['reason']})"
		),
		docstatus_delta="0 → 0 (amended)",
	)
