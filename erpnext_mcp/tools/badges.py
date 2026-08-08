# SPDX-License-Identifier: MIT
"""Employee badges: minting one, printing a sheet of them, reading one back. v0.50.0.

WHAT WAS MISSING. `link_badge_to_employee` has RECORDED a badge since v0.44.0 —
an operator typed or scanned a string and this app remembered whose it was — and
nothing anywhere ISSUED one. The badges actually in workers' pockets were
printed by `farm_app` (Flask): a uuid4 `QRToken` that ERPNext has never heard
of, whose revocation lifecycle lives in a different application, and which
nobody can read off a scuffed card or say aloud over a radio at 6am when the
scanner will not focus. Of the two QR generators this app already had, one
issues an operator login credential and the other tags a valve.

So there was no answer to "print a badge for this hire", and — because a badge
ID could be any string at all — no answer to "who holds CF-0042?" either. These
three tools are those two answers.

────────────────────────────────────────────────────────────────────────────
THE IDENTIFIER IS MINTED HERE, AND THE OLD ONES KEEP WORKING
────────────────────────────────────────────────────────────────────────────

A minted badge is `<company abbreviation>-<sequence>` — `CF-0001` — and the
reason to prefer that over adopting `farm_app`'s uuid is that piece-rate
attribution is a payroll record. The identifier that decides who gets paid for a
bucket should live in the system that pays, with a retirement flag an HR manager
can flip, and should be legible to a human when the technology fails.

The cutover is incremental rather than a reprint day, and that is a genuine
property of the existing design rather than a workaround:
`bucket_bridge.resolve_badge_to_employee` matches a badge ID as an EXACT STRING
with no format assumption, so an old `farm_app` uuid mapped with
`link_badge_to_employee` and a new `CF-0042` minted here both resolve to the
same person for as long as the transition takes.

────────────────────────────────────────────────────────────────────────────
ONE ACTIVE BADGE PER PERSON, AND REISSUING RETIRES THE OLD ONE
────────────────────────────────────────────────────────────────────────────

`Bucket Log Badge Map.active` was always the retirement flag; nothing enforced
that a person had only one ticked. Two active cards for one picker is not a
harmless duplicate — it is a lost card that still resolves, which is precisely
the situation the flag exists for. So minting a replacement (`regenerate`)
unticks every other active row for that employee in the same call, and the
result names what it retired.

`generate_employee_badge_qr` is IDEMPOTENT WITHOUT `regenerate`: a second call
for somebody who already holds a live badge re-renders THAT badge's QR rather
than issuing a second one. Reprinting a card that went through a wash cycle is
the common request and it must not consume an identifier.
"""

from __future__ import annotations

import base64

import frappe

from .. import bucket_bridge, compat
from ..args import as_bool, as_str, resolve_company
from ..errors import ToolError
from ..render import qr
from ..result import ToolResult
from . import bucket_log
from . import employee as employee_tool

BADGE_DOCTYPE = bucket_log.BADGE_DOCTYPE
EMPLOYEE = "Employee"
FARM_SHIFT = "Farm Shift"

#: Cards one sheet call produces. The same hundred `generate_asset_qr_sheet`
#: takes, for the same reason: past that the answer is a multi-megabyte JSON
#: payload of base64 PNGs, and a crew of a hundred is already two Avery sheets.
SHEET_CAP = 100

#: Attempts at finding a free sequence number before giving up. Bounded because
#: the loop exists for a race — two foremen issuing a badge in the same second —
#: and a race that has not settled in fifty tries is a bug, not contention.
MINT_ATTEMPTS = 50

#: What a badge card carries, and the label printed above the QR. Kept here
#: rather than in the caller so the sheet and the single card agree.
_EMPLOYEE_FIELDS = ("name", "employee_name", "employee_number", "designation", "status", "company", "image")


def _require() -> None:
	compat.require_doctype(
		BADGE_DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _employee_row(employee: str) -> dict:
	"""One Employee's card fields, by whichever spelling of their name arrived."""
	name = employee_tool.resolve_employee(employee)
	wanted = compat.existing_fields(EMPLOYEE, list(_EMPLOYEE_FIELDS))
	row = frappe.db.get_value(EMPLOYEE, name, wanted, as_dict=True) or {}
	row = dict(row)
	row["name"] = name
	return row


def _initials(employee_name: str) -> str:
	"""The two letters that go in the photo box when there is no photograph.

	A badge with an empty rectangle where a face should be is a badge somebody
	has to turn over to identify. Initials are not identification and are not
	pretending to be — they are what a foreman glances at across a bin trailer.
	"""
	words = [word for word in str(employee_name or "").split() if word]
	if not words:
		return "?"
	if len(words) == 1:
		return words[0][:2].upper()
	return (words[0][:1] + words[-1][:1]).upper()


def _company_prefix(company: str) -> str:
	"""The badge prefix for a company: its abbreviation, or its initials.

	The abbreviation is what an operator already chose and what every docname on
	the site is suffixed with, so a badge that carries it needs no second
	convention learned. A Company with no `abbr` — possible on an install where
	it was cleared — falls back to the initials of its name, and a name with no
	letters at all is a refusal rather than a guess: a badge with no prefix is
	`-0001`, which is not an identifier anybody can read aloud.
	"""
	abbr = ""
	if compat.has_field("Company", "abbr"):
		abbr = str(frappe.db.get_value("Company", company, "abbr") or "")
	prefix = bucket_bridge.normalise_badge_prefix(abbr)
	if not prefix:
		from .. import abbr as abbr_module

		prefix = bucket_bridge.normalise_badge_prefix(abbr_module.derive_abbr(company))
	if not prefix:
		raise ToolError(
			f"company {company!r} has no abbreviation and no letters in its name, so a readable "
			"badge prefix cannot be derived from it. Set the Company's abbreviation, or pass "
			"badge_id explicitly. Nothing was changed."
		)
	return prefix


def _issued_badge_ids() -> list:
	"""Every badge ID on the site, active or not.

	SITE-WIDE RATHER THAN PER-COMPANY, because `badge_id` is the DOCNAME and
	docnames are unique across the site. Reading only one company's rows would
	mint a number that another entity's register already holds, and the insert
	would fail on a uniqueness error the operator did not cause.

	RETIRED ROWS COUNT. A lost card's number is never handed to the next picker
	— see `bucket_bridge.next_badge_sequence`.
	"""
	return frappe.db.get_all(BADGE_DOCTYPE, filters={}, pluck="badge_id", limit_page_length=0) or []


def _active_badges(employee: str) -> list:
	"""Every live badge row for one person, newest first."""
	return (
		frappe.db.get_all(
			BADGE_DOCTYPE,
			filters={"employee": employee, "active": 1},
			fields=["name", "badge_id", "company"],
			order_by="modified desc",
			limit_page_length=0,
		)
		or []
	)


def _mint(company: str, prefix: str) -> str:
	"""A badge ID nothing on this site holds yet.

	THE LOOP IS THE CONCURRENCY STORY. Two foremen issuing a badge in the same
	second read the same register and compute the same next number; the second
	insert would collide on the unique `badge_id`. Re-reading `frappe.db.exists`
	between the computation and the write closes most of that window, and the
	insert itself is the backstop — `badge_id` is unique in the doctype, so the
	worst case is a refusal rather than two pickers sharing an identifier.
	"""
	issued = list(_issued_badge_ids())
	for _ in range(MINT_ATTEMPTS):
		candidate = bucket_bridge.next_badge_id(issued, prefix)
		if not frappe.db.exists(BADGE_DOCTYPE, candidate):
			return candidate
		issued.append(candidate)
	raise ToolError(
		f"could not find a free badge ID under the prefix {prefix!r} after {MINT_ATTEMPTS} "
		"attempts. Something else is issuing badges at the same time, or the register is "
		"inconsistent. Nothing was changed."
	)


def _render(badge_id: str, error: str = "M") -> dict:
	"""The QR for one badge, with a missing encoder reported as a tool failure.

	`registry.py` gates all three of these tools on `_qr_available`, so a bench
	with neither `segno` nor `qrcode` loses them by name with the pip command to
	fix it. This is the direct-call path — a test, another tool — and turning the
	renderer's `RuntimeError` into a `ToolError` keeps that failure a sentence
	rather than a traceback.
	"""
	try:
		return qr.render(badge_id, error=error)
	except RuntimeError as exc:
		raise ToolError(str(exc)) from None


def _retire_others(employee: str, keep: str, reason: str) -> list:
	"""Untick every other live badge for this person. Returns what it retired.

	A REPLACEMENT CARD MUST KILL THE ONE IT REPLACES. Otherwise the lost badge
	somebody found in an orchard still resolves to its holder, and every bucket
	scanned with it is paid to them.
	"""
	retired = []
	for row in _active_badges(employee):
		if row["badge_id"] == keep:
			continue
		doc = frappe.get_doc(BADGE_DOCTYPE, row["name"])
		doc.active = 0
		doc.notes = ((doc.get("notes") or "") + f"\n{reason}").strip()
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		retired.append(row["badge_id"])
	return retired


def _choose_badge_id(row: dict, company: str, args: dict, explicit_badge: str = "") -> tuple:
	"""This employee's badge ID and whether it is a new one. READS ONLY.

	Split from `_record_badge` so the QR can be drawn BEFORE anything is
	written: a bench with no encoder must refuse before it has consumed an
	identifier and printed nothing, which is the failure order that leaves a
	register with a badge nobody holds.
	"""
	employee = row["name"]
	regenerate = as_bool(args, "regenerate", False)

	if explicit_badge:
		errors = bucket_bridge.validate_badge_id(explicit_badge)
		if errors:
			# Not quoted beyond `validate_badge_id`'s own bound — see
			# `bucket_bridge._display`.
			raise ToolError(f"that badge_id is not a usable badge: {'; '.join(errors)}. Nothing was changed.")
		holder = frappe.db.get_value(BADGE_DOCTYPE, explicit_badge, ["employee", "active"], as_dict=True)
		if holder and str(holder.get("employee")) != employee and holder.get("active"):
			raise ToolError(
				f"badge {explicit_badge!r} is already live for {holder['employee']}. Two people "
				"holding one badge ID is one person's piecework paid to the other. Retire it "
				"first with link_badge_to_employee(active=false), or let this tool mint a new "
				"ID. Nothing was changed."
			)
		return explicit_badge, not holder

	live = _active_badges(employee)
	if live and not regenerate:
		# The reprint case, and the reason this tool is idempotent: a card that
		# went through a wash cycle is reprinted, not reissued.
		return live[0]["badge_id"], False
	return _mint(company, _company_prefix(company)), True


def _record_badge(row: dict, company: str, badge_id: str, args: dict) -> dict:
	"""Make the register say this badge is this person's, and retire the rest.

	The upsert is `link_badge_to_employee`'s, not a second copy of it: that tool
	owns the reissue-to-somebody-else case AND the backfill of `employee` onto
	captures already synced against the badge, and a badge minted here would
	otherwise not claim what was picked before it was printed.
	"""
	employee = row["name"]
	notes = as_str(args, "notes")
	result = bucket_log.link_badge_to_employee(
		{
			"badge_id": badge_id,
			"employee": employee,
			"company": company,
			"active": True,
			**({"notes": notes} if notes else {}),
		}
	)
	retired = _retire_others(
		employee,
		badge_id,
		f"Retired {frappe.utils.today()}: replaced by badge {badge_id}.",
	)
	return {
		"retired": retired,
		"previous_employee": (result.data or {}).get("previous_employee"),
		"backfilled_entries": (result.data or {}).get("backfilled_entries") or [],
	}


def _card(row: dict, badge_id: str, company: str, rendered: dict) -> dict:
	"""One badge card's printable facts, in the order they go on the card."""
	return {
		"employee": row["name"],
		"employee_name": row.get("employee_name") or row["name"],
		"employee_number": row.get("employee_number") or None,
		"designation": row.get("designation") or None,
		"company": company,
		"badge_id": badge_id,
		# The photograph if the Employee record has one, and the initials that
		# go in its place when it does not. Both, always — a print template
		# should not have to ask this app a second question to lay out a card.
		"photo_url": row.get("image") or None,
		"photo_placeholder": _initials(row.get("employee_name") or row["name"]),
		"png_base64": base64.b64encode(rendered["png"]).decode("ascii"),
		"png_bytes": len(rendered["png"]),
		"modules": rendered["modules"],
		"pixels": rendered["pixels"],
	}


# ── 1. generate_employee_badge_qr ────────────────────────────────────────


def generate_employee_badge_qr(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Issue (or reprint) one employee's scanning badge:
	mint a readable badge ID if they have none, record it in the Bucket Log
	Badge Map register, and hand back the QR code that goes on the card.

	IDEMPOTENT WITHOUT `regenerate` — somebody who already holds a live badge
	gets THAT badge's QR back rather than a second identifier. `regenerate=true`
	is the lost-card path: it mints a new ID and RETIRES the old one in the same
	call, because a replacement that leaves its predecessor resolving is how a
	found badge keeps earning."""
	_require()
	actor = employee_tool.require_hr_role()

	row = _employee_row(as_str(args, "employee", required=True))
	company = resolve_company(as_str(args, "company") or str(row.get("company") or ""), required=True)
	employee_tool.require_company_scope(actor, company)

	if row.get("company") and str(row["company"]) != company:
		raise ToolError(
			f"{row['name']} is employed by {row['company']} and this call names {company}. A badge "
			"belongs to the entity that issued it and resolves only against that entity's "
			"captures. Nothing was changed."
		)

	status = str(row.get("status") or "Active")
	if status != "Active":
		raise ToolError(
			f"{row['name']} ({row.get('employee_name')}) has employment status {status}, not "
			"Active. A badge is what attributes piece work to somebody on the payroll — "
			"reactivate_employee first. Nothing was changed."
		)

	fmt = as_str(args, "format") or "png"
	if fmt not in ("png", "matrix"):
		raise ToolError("format must be 'png' or 'matrix'.")

	# The ID is settled and the QR drawn BEFORE the register is written, so a
	# bench that cannot draw one refuses before it has issued a badge nobody can
	# print.
	badge_id, created = _choose_badge_id(row, company, args, as_str(args, "badge_id"))
	rendered = _render(badge_id, as_str(args, "error_correction") or "M")
	recorded = _record_badge(row, company, badge_id, args)

	data = {
		"actor": actor,
		**_card(row, badge_id, company, rendered),
		"created": created,
		"reused": not created,
		"retired_badges": recorded["retired"],
		"previous_employee": recorded["previous_employee"],
		"backfilled_entries": recorded["backfilled_entries"],
		"scale": rendered["scale"],
		"border": rendered["border"],
		"error_correction": rendered["error_correction"],
		"encoder": rendered["encoder"],
	}
	if fmt == "matrix":
		data.pop("png_base64", None)
		data.pop("png_bytes", None)
		data["matrix"] = rendered["matrix"]

	verb = "issued" if created else "reprinted"
	summary = f"badge {badge_id} {verb} for {row.get('employee_name') or row['name']}"
	if recorded["retired"]:
		summary += f", retired {', '.join(recorded['retired'])}"
	return ToolResult(
		data=data,
		summary=summary,
		docstatus_delta=("none → 0 (badge issued)" if created else "badge reprinted"),
	)


# ── 2. generate_employee_badge_sheet ─────────────────────────────────────


def generate_employee_badge_sheet(args: dict) -> ToolResult:
	"""MUTATING (default OFF). A printable sheet of badge cards — name, photo (or
	initials), designation, badge ID and QR — for a crew at once. Issues a badge
	to anybody who has none and reuses the live one where there is one.

	ONE EMPLOYEE'S FAILURE DOES NOT LOSE THE SHEET. A name that resolves to
	nobody, somebody who has left, an entity this actor cannot reach — each is
	reported by name in `errors` and the other twenty-nine cards are still
	printed, the same posture `sync_bucket_entries` takes for a batch. A hiring
	day with one bad row in the list should not be a hiring day with no badges."""
	_require()
	actor = employee_tool.require_hr_role()

	names = args.get("employees") or args.get("employee_names")
	if isinstance(names, str):
		names = [part.strip() for part in names.split(",") if part.strip()]
	if not names or not isinstance(names, list):
		raise ToolError(
			"employees is required and must be a list of Employee docnames (or names, numbers "
			"or logins — each is resolved the way link_badge_to_employee resolves one). "
			"Nothing was changed."
		)
	if len(names) > SHEET_CAP:
		raise ToolError(
			f"{len(names)} employees is more than one sheet call produces ({SHEET_CAP}). Split "
			"the crew. Nothing was changed."
		)

	wanted_company = resolve_company(as_str(args, "company"), required=False)
	template = as_str(args, "template") or "badge_card_2x3"

	cards = []
	errors = []
	seen: set = set()
	for index, raw in enumerate(names):
		reference = str(raw or "").strip()
		if not reference:
			errors.append({"index": index, "employee": None, "error": "empty entry"})
			continue
		try:
			row = _employee_row(reference)
			if row["name"] in seen:
				errors.append(
					{
						"index": index,
						"employee": row["name"],
						"error": "named twice in this call — one card was printed",
					}
				)
				continue
			company = resolve_company(wanted_company or str(row.get("company") or ""), required=True)
			employee_tool.require_company_scope(actor, company)
			if row.get("company") and str(row["company"]) != company:
				raise ToolError(f"employed by {row['company']}, and this sheet is for {company}")
			status = str(row.get("status") or "Active")
			if status != "Active":
				raise ToolError(f"employment status is {status}, not Active")

			badge_id, created = _choose_badge_id(row, company, args)
			rendered = _render(badge_id)
			recorded = _record_badge(row, company, badge_id, args)
			seen.add(row["name"])
			cards.append(
				{
					**_card(row, badge_id, company, rendered),
					"created": created,
					"reused": not created,
					"retired_badges": recorded["retired"],
				}
			)
		except ToolError as exc:
			errors.append({"index": index, "employee": reference, "error": str(exc)})

	minted = sum(1 for card in cards if card["created"])
	summary = f"{len(cards)} badge card(s) for {template}, {minted} newly issued"
	if errors:
		summary += f", {len(errors)} skipped"
	return ToolResult(
		data={
			"actor": actor,
			"template": template,
			"card_count": len(cards),
			"issued_count": minted,
			"error_count": len(errors),
			"cards": cards,
			"errors": errors,
			"encoder": qr.encoder_name(),
		},
		summary=summary,
		docstatus_delta=(f"{minted} Bucket Log Badge Map record(s) created" if minted else ""),
	)


# ── 3. resolve_badge ─────────────────────────────────────────────────────


def resolve_badge(args: dict) -> ToolResult:
	"""Read-only. Who holds this badge — the call between a scan and a name.

	THE READ THAT DID NOT EXIST. `get_employee` could answer "does this person
	have a badge?"; nothing could answer "whose badge is this?", which is the
	question a crew clock asks on every scan (`add_worker_to_shift` takes an
	Employee docname and a scanner produces a badge string) and the question a
	capture loop needs to put a picker's NAME on screen instead of a code.

	IT REFUSES RATHER THAN ANSWERING EMPTY. An unknown badge, a retired one and
	one belonging to somebody who has left each get their own sentence, because
	from the foreman's side those are three different situations with three
	different fixes and "not found" would collapse them into one.

	`shift` IS OPTIONAL AND ANSWERS THE SECOND HALF OF THE QUESTION. Given one,
	the result carries `on_shift` and `joined_at` — whether this person is
	actually clocked in right now — which is what turns a badge scan at a bin
	trailer from an identification into an admission."""
	_require()
	badge_id = as_str(args, "badge_id") or as_str(args, "badge", required=True)
	wanted_company = resolve_company(as_str(args, "company"), required=False)

	shape = bucket_bridge.validate_badge_id(badge_id)
	if shape:
		# The scan is NOT quoted here beyond what `validate_badge_id` already
		# allows itself: this is the branch a `generate_mobile_login_qr` payload
		# takes, and a refusal that echoed it would put an api_secret in a
		# ValidationError, in the audit row's arguments and on a screen.
		raise ToolError(
			f"that scan is not a badge ID: {'; '.join(shape)}. Nothing that was scanned here "
			"identifies a worker."
		)

	row = frappe.db.get_value(
		BADGE_DOCTYPE, badge_id, ["name", "badge_id", "company", "employee", "active", "notes"], as_dict=True
	)
	# A badge belonging to an entity the caller named a different one for is
	# reported as unknown rather than as "wrong company": confirming that a
	# badge exists somewhere else on the site is a fact this read has no reason
	# to hand to somebody holding a card.
	if not row or (wanted_company and str(row.get("company")) != wanted_company):
		raise ToolError(
			f"no badge {badge_id!r} in this company's register. Nothing was issued with that ID "
			"— it may be another site's card, or not a badge at all."
		)
	if not row.get("active"):
		raise ToolError(
			f"badge {badge_id!r} was retired and no longer resolves to anybody. It was last held "
			f"by {row.get('employee')}; a reissued card has its own ID."
		)

	person = str(row.get("employee") or "")
	wanted = compat.existing_fields(EMPLOYEE, list(_EMPLOYEE_FIELDS))
	employee = (frappe.db.get_value(EMPLOYEE, person, wanted, as_dict=True) if person else None) or {}
	if not employee:
		raise ToolError(
			f"badge {badge_id!r} maps to Employee {person!r}, which is not on this site. The "
			"register is inconsistent — remap it with link_badge_to_employee."
		)
	status = str(employee.get("status") or "Active")
	if status != "Active":
		raise ToolError(
			f"badge {badge_id!r} belongs to {person} ({employee.get('employee_name')}), whose "
			f"employment status is {status}, not Active. Retire the badge, or reactivate them."
		)

	data = {
		"badge_id": row["badge_id"],
		"company": row.get("company"),
		"active": True,
		"employee": person,
		"employee_name": employee.get("employee_name") or person,
		"employee_number": employee.get("employee_number") or None,
		"designation": employee.get("designation") or None,
		"status": status,
		"photo_url": employee.get("image") or None,
		"photo_placeholder": _initials(employee.get("employee_name") or person),
		"notes": row.get("notes") or None,
	}

	shift = as_str(args, "shift")
	if shift:
		data.update(_shift_membership(shift, person))

	summary = f"badge {row['badge_id']} → {person} ({data['employee_name']})"
	if shift:
		summary += ", on shift" if data.get("on_shift") else f", NOT on shift {shift}"
	return ToolResult(data=data, summary=summary)


def _shift_membership(shift: str, employee: str) -> dict:
	"""Whether this person is currently on that shift's crew.

	`on_shift` is False rather than an error for a shift that does not exist or
	has closed, and `shift_state` says which — a crew clock asking "may this
	badge log a bucket against the shift I have open" wants an answer it can put
	on a screen, and a phone holding a stale shift name is an ordinary thing.
	"""
	if not compat.doctype_exists(FARM_SHIFT):
		return {"shift": shift, "on_shift": False, "shift_state": "no Farm Shift doctype on this site"}
	if not frappe.db.exists(FARM_SHIFT, shift):
		return {"shift": shift, "on_shift": False, "shift_state": "no such shift"}

	doc = frappe.get_doc(FARM_SHIFT, shift)
	if doc.get("end_datetime"):
		state = "closed"
	else:
		state = "open"
	for entry in doc.get("crew") or []:
		if str(entry.get("employee")) == employee:
			return {
				"shift": shift,
				"on_shift": state == "open" and not entry.get("left_at"),
				"shift_state": state,
				"joined_at": str(entry.get("joined_at") or "") or None,
				"left_at": str(entry.get("left_at") or "") or None,
			}
	return {"shift": shift, "on_shift": False, "shift_state": state, "joined_at": None, "left_at": None}
