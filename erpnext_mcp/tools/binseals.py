# SPDX-License-Identifier: MIT
"""Closing a bin in the field, and answering for it at the pack line.

FOUR TOOLS THAT ARE ONE QUESTION. `seal_bin` writes the record a checker's phone
makes when it closes a bin; `get_bin_seal` and `list_bin_seals` read the
register; `trace_bin` answers the only question a packing house ever asks, which
is **whose fruit is in this bin**.

────────────────────────────────────────────────────────────────────────────
WHY THE CHAIN BREAKS WITHOUT THIS
────────────────────────────────────────────────────────────────────────────

A bin leaves the orchard on a trailer carrying a tag and nothing else. Everything
anybody wants to know about it afterwards is about the hour before it was closed:

    a residue detection      → which block, and was that block inside a
                               re-entry interval when it was picked
    a piece-rate dispute     → whose buckets, and how many each
    a food-safety hold       → which OTHER bins the same crew filled that
                               afternoon, because a hygiene failure is about
                               people rather than about one bin
    a heat-exposure question → which shift, and therefore which weather
                               timeline and which break log

Every one of those is a join from a tag to a shift to a crew, and NONE of them is
reconstructable once the crew has gone home. The buckets are tipped and mixed;
the badge scans exist only on the handset; the tag is the single surviving
identifier and it points at nothing. So the record is written at the moment of
sealing, in the field, by the person who closed the bin — which is also the only
moment anybody knows the answer.

────────────────────────────────────────────────────────────────────────────
THE TAG IS NOT UNIQUE AND IS NOT MADE SO
────────────────────────────────────────────────────────────────────────────

Bin tags are reused — between seasons, between growers, between the two hundred
bins a packing house owns and lends out. A uniqueness constraint on `bin_tag`
would refuse the SECOND TRUE RECORD rather than the first false one: the second
seal is a real bin that was really closed, and the app would be throwing it away
to protect an assumption about somebody else's stickers.

So `trace_bin` answers with EVERY seal carrying the tag, newest first, and says
how many it found. A packing house holding a bin under a tag that traces to three
seals has a real problem and now knows it has one; the alternative was a
confident answer about the wrong afternoon.

WHAT IS UNIQUE IS `client_event_id`, which is the handset's own identifier for
one sealing action. That is what makes a retry safe — a phone that sealed a bin
and did not hear the answer sends the same call again, and without it the orchard
gets two records of one bin and the packing house a doubled count.

────────────────────────────────────────────────────────────────────────────
THE TWO COUNTS ARE ALLOWED TO DISAGREE
────────────────────────────────────────────────────────────────────────────

`bucket_count` is what the checker's tally read when the bin was closed. The
contributors' `buckets_contributed` is what the badge scans attributed. They are
two different measurements and this app never reconciles them, because a bucket
tipped by somebody whose badge did not scan is IN THE BIN and NOT IN THE ROWS —
and that is precisely the fact a piece-rate dispute turns on. Balancing the two
would delete it.

What the reads do instead is name the difference. `unattributed_buckets` is on
every answer, and a bin where it is large is a bin where the scanning broke down,
which is a thing worth knowing on the day rather than at settlement.
"""

from __future__ import annotations

import frappe

from .. import compat, geo, shifts
from ..args import as_date, as_int, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import employee as employee_tool
from .bucket_log import BADGE_DOCTYPE

DOCTYPE = "Bin Seal"
CONTRIBUTOR_DOCTYPE = "Bin Seal Contributor"
BUCKET_SESSION = "Bucket Log Session"

#: Most seals one read returns. A bin register is read to answer a question about
#: a tag, a shift or a block, not to be exported — the same argument
#: `tools/shifts.RECORD_CAP` makes, and the same number.
RECORD_CAP = 500

#: Most contributors one seal may carry. Sixty is the same figure `start_shift`
#: caps a crew at, and for the same reason: past it the caller has handed over a
#: company roster rather than the people who tipped into one bin.
CONTRIBUTOR_CAP = 60

#: What a bin tag looks like when there was no tag to scan. The handset generates
#: it; this app only ever recognises the shape, so a seal can be told apart from
#: one whose QR code actually read.
MANUAL_PREFIX = "MANUAL-"


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


# ── writing ─────────────────────────────────────────────────────────────────


def seal_bin(args: dict) -> ToolResult:
	"""Close one bin, with the names of the people whose buckets are in it.

	THE ONLY MOMENT THE ANSWER EXISTS. See the module docstring: after the trailer
	leaves, a bin is a tag. This is what turns it into a record.

	IDEMPOTENT ON `client_event_id`. A phone that sealed a bin and did not hear
	the answer sends the same call again — over a funnel, in a canyon, on a
	battery about to die, this is the ordinary case rather than the exception —
	and a second record of one bin is a doubled count at the pack line and a
	doubled piece rate on somebody's cheque. The retry gets the SAME seal back
	with `already_sealed` set, which is the same shape `claim_task` and
	`sync_bucket_entries` use for the same reason.

	CONTRIBUTORS ARRIVE AS EMPLOYEE DOCNAMES OR AS BADGES, and both are resolved
	here rather than on the handset. A badge that resolves to nobody is REPORTED
	AND KEPT — it goes on `unresolved_badges` and the bin is still sealed —
	because a bin refused over a badge that was never registered is a bin with no
	record at all, and the trace is worth more with a gap in it than not at all.
	"""
	_require()
	actor = employee_tool.require_shift_role()

	bin_tag = as_str(args, "bin_tag", required=True).strip()
	if not bin_tag:
		raise ToolError(
			"bin_tag is required. It is the only identifier that leaves the orchard on the "
			"trailer, and a seal without one is a record nothing at the pack line can find. "
			f"Where the code would not scan, the handset sends {MANUAL_PREFIX}XXXX. "
			"Nothing was created."
		)

	bucket_count = as_int(args, "bucket_count")
	if bucket_count is None:
		raise ToolError(
			"bucket_count is required — it is what the checker's tally read when the bin was "
			"closed. Zero is a legitimate answer for a bin sealed empty and is not the same as "
			"omitting it. Nothing was created."
		)
	if bucket_count < 0:
		raise ToolError(
			f"bucket_count cannot be {bucket_count}. It is a piece-rate figure before it is a "
			"traceability one, and a negative one propagates into a settlement. Nothing was "
			"created."
		)

	client_event_id = as_str(args, "client_event_id").strip()
	if client_event_id:
		existing = frappe.db.get_value(DOCTYPE, {"client_event_id": client_event_id}, "name")
		if existing:
			described = describe(existing, with_contributors=True)
			return ToolResult(
				data={
					**described,
					"actor": actor,
					"already_sealed": True,
					"note": (
						f"{existing} already carries client_event_id {client_event_id!r}, so this "
						"call was a retry and nothing was written a second time. A duplicate seal "
						"is a doubled count at the pack line and a doubled piece rate on "
						"somebody's cheque."
					),
				},
				summary=f"{bin_tag} was already sealed as {existing}",
			)

	shift = as_str(args, "shift") or as_str(args, "farm_shift")
	shift_row = {}
	if shift:
		shift_row = dict(
			frappe.db.get_value(shifts.DOCTYPE, shift, ["name", "company", "start_datetime"], as_dict=True)
			or {}
		)
		if not shift_row:
			raise ToolError(
				f"no Farm Shift called {shift!r} on this site. list_shifts has the open ones. "
				"Nothing was created."
			)

	company = resolve_company(
		as_str(args, "company") or str(shift_row.get("company") or ""), required=False
	)
	if company:
		employee_tool.require_company_scope(actor, company)

	sealed_by = as_str(args, "sealed_by")
	if sealed_by:
		sealed_by = employee_tool.resolve_employee(sealed_by)

	bucket_session = as_str(args, "bucket_session") or as_str(args, "session")
	if bucket_session and compat.doctype_exists(BUCKET_SESSION):
		resolved = (
			bucket_session
			if frappe.db.exists(BUCKET_SESSION, bucket_session)
			else frappe.db.get_value(BUCKET_SESSION, {"session_uuid": bucket_session}, "name")
		)
		if not resolved:
			raise ToolError(
				f"no Bucket Log Session matching {bucket_session!r} on this site — a docname or a "
				"session_uuid is accepted. list_bucket_sessions has the register. Nothing was "
				"created."
			)
		bucket_session = resolved

	field = as_str(args, "field")
	if field and compat.doctype_exists("Field") and not frappe.db.exists("Field", field):
		raise ToolError(
			f"no Field called {field!r} on this site. `field` is a Link because a residue trace "
			"runs from a bin to a block to a spray application, and a typed block name breaks "
			"that chain at its weakest point — pass the crew's own words as `block` instead. "
			"list_fields has the register. Nothing was created."
		)

	latitude, longitude = geo.coordinates(args, prefix="gps_", required=False)
	if latitude is None:
		latitude, longitude = geo.coordinates(args, required=False)

	contributors, unresolved = _contributors(args.get("contributors"), company)

	doc = frappe.new_doc(DOCTYPE)
	doc.bin_tag = bin_tag
	doc.bucket_count = bucket_count
	doc.company = company or None
	doc.sealed_at = _sealed_at(args, shift_row)
	doc.sealed_by = sealed_by or None
	doc.shift = shift_row.get("name") or None
	doc.field = field or None
	doc.block = as_str(args, "block") or None
	doc.crop = as_str(args, "crop") or None
	doc.bucket_session = bucket_session or None
	doc.gps_lat = latitude
	doc.gps_lon = longitude
	doc.gps_accuracy_meters = args.get("gps_accuracy_meters") or args.get("accuracy_meters") or None
	doc.h3_hex = as_str(args, "h3_hex") or as_str(args, "h3_cell") or None
	doc.client_event_id = client_event_id or None
	doc.nostr_event_id = as_str(args, "nostr_event_id") or None
	doc.notes = as_str(args, "notes") or None
	source = as_str(args, "source")
	if source:
		doc.source = source
	for entry in contributors:
		doc.append("contributors", entry)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = describe(doc.name, with_contributors=True, row=dict(doc.as_dict()))
	data = {
		**described,
		"actor": actor,
		"already_sealed": False,
		"note": (
			f"{doc.name} is the record of bin {bin_tag}. From here a tag at the packing house "
			"traces back to "
			+ (f"{len(contributors)} named contributor(s), " if contributors else "no contributors, ")
			+ (f"{doc.shift}, " if doc.shift else "no shift, ")
			+ (f"and {doc.field}." if doc.field else "and no Field on the register.")
		),
	}
	if unresolved:
		data["unresolved_badges"] = unresolved
		data["unresolved_note"] = (
			f"{len(unresolved)} scanned badge(s) resolved to nobody on this site: "
			f"{', '.join(unresolved)}. The bin WAS sealed — a record with a gap in it is worth "
			"more than no record, and a bin refused over an unregistered badge is a bin nothing "
			"can trace at all. link_badge_to_employee registers a card; this seal can then be "
			"corrected on the Desk."
		)
	if not contributors:
		data["no_contributors_note"] = (
			"This bin names nobody. It is sealed and traceable to a block and a shift, and it is "
			"not traceable to a person — which is the half a piece-rate dispute and a hygiene "
			"hold both need. Contributors are the badges scanned since the last seal; a checker "
			"whose phone is not scanning is the usual cause."
		)
	if not (doc.field or doc.block):
		data["no_block_note"] = (
			"This bin names no Field and no block. A residue detection at the packing house "
			"traces to a shift and stops there — the block is what a spray record is filed "
			"against."
			+ (
				" The GPS fix on this seal can still place it: find_fields_containing_point."
				if latitude is not None
				else " There is no GPS fix on it either, so nothing can place it after the fact."
			)
		)
	return ToolResult(
		data=data,
		summary=(
			f"sealed bin {bin_tag} with {bucket_count} bucket(s) from {len(contributors)} "
			f"worker(s)" + (f" on {doc.shift}" if doc.shift else "")
		),
		docstatus_delta="none → 0 (created)",
	)


def _sealed_at(args: dict, shift_row: dict) -> str:
	"""When the bin was closed, defaulting to NOW rather than to the shift's start.

	The opposite of `start_shift`'s default and right for the same reason
	`add_worker_to_shift`'s is: a bin is closed at a moment somebody performed,
	and stamping it with the shift's start would file every bin of the day at six
	in the morning.
	"""
	given = as_str(args, "sealed_at") or as_str(args, "when")
	return given or frappe.utils.now()


def _contributors(raw, company: str) -> tuple:
	"""The rows, and the badges that resolved to nobody.

	ACCEPTS THREE SHAPES because three callers send three. A list of Employee
	docnames is what an MCP caller writes; a list of badge strings is what the
	tally screen has in hand; a list of dicts is what the handset sends when it
	has counted per badge, and it is the only one that can carry
	`buckets_contributed` and the scan window. All three land on one row shape.

	DUPLICATES ARE MERGED RATHER THAN REFUSED. One worker who tipped into the bin
	twice is one row with the buckets added up and the scan window widened —
	which is what the controller's own duplicate check would otherwise refuse the
	whole seal over, and refusing a bin because somebody came back with a second
	bucket would be absurd.
	"""
	entries = raw if isinstance(raw, (list, tuple)) else ([raw] if raw else [])
	if len(entries) > CONTRIBUTOR_CAP:
		raise ToolError(
			f"{len(entries)} contributors on one bin. The cap is {CONTRIBUTOR_CAP}, which is the "
			"same figure a crew is capped at — past it, this is a company roster rather than the "
			"people who tipped into one bin. Nothing was created."
		)

	merged: dict = {}
	unresolved: list = []
	badge_cache: dict = {}
	for entry in entries:
		if isinstance(entry, dict):
			given = str(entry.get("employee") or entry.get("badge_id") or entry.get("badge") or "").strip()
			buckets = entry.get("buckets_contributed") or entry.get("buckets") or 0
			first = str(entry.get("first_scan_at") or entry.get("scanned_at") or "").strip()
			last = str(entry.get("last_scan_at") or entry.get("scanned_at") or "").strip()
		else:
			given = str(entry or "").strip()
			buckets, first, last = 0, "", ""
		if not given:
			continue

		employee, badge = _resolve_contributor(given, company, badge_cache)
		if not employee:
			if given not in unresolved:
				unresolved.append(given)
			continue

		row = merged.setdefault(
			employee,
			{
				"employee": employee,
				"employee_name": str(frappe.db.get_value("Employee", employee, "employee_name") or "")
				or employee,
				"badge_id": badge or None,
				"buckets_contributed": 0,
				"first_scan_at": None,
				"last_scan_at": None,
			},
		)
		try:
			row["buckets_contributed"] += int(buckets or 0)
		except (TypeError, ValueError):
			pass
		if first and (row["first_scan_at"] is None or first < row["first_scan_at"]):
			row["first_scan_at"] = first
		if last and (row["last_scan_at"] is None or last > row["last_scan_at"]):
			row["last_scan_at"] = last
	return list(merged.values()), unresolved


def _resolve_contributor(given: str, company: str, cache: dict) -> tuple:
	"""`(employee, badge_id)` for whatever the caller named, or `("", "")`.

	AN EMPLOYEE DOCNAME WINS OVER A BADGE with the same string, which cannot
	actually collide on this site — badges are printed short and Employee
	docnames are `HR-EMP-…` — but the order is stated rather than left to
	whichever query happens to run first.

	A badge belonging to another entity resolves to NOBODY rather than to its
	owner, which is `resolve_badge`'s own posture: a checker on one farm scanning
	a card issued by another has scanned a card this site cannot vouch for, and
	attributing buckets on it would put somebody else's worker on this bin.
	"""
	key = (given, company)
	if key in cache:
		return cache[key]

	answer = ("", "")
	if compat.doctype_exists("Employee") and frappe.db.exists("Employee", given):
		answer = (given, "")
	elif compat.doctype_exists(BADGE_DOCTYPE):
		row = (
			frappe.db.get_value(
				BADGE_DOCTYPE, given, ["employee", "company", "active"], as_dict=True
			)
			or {}
		)
		if row and (not company or str(row.get("company") or "") == company) and row.get("employee"):
			answer = (str(row["employee"]), given)
	cache[key] = answer
	return answer


# ── reading ─────────────────────────────────────────────────────────────────

FIELDS = (
	"name",
	"bin_tag",
	"bucket_count",
	"company",
	"sealed_at",
	"sealed_by",
	"sealed_by_name",
	"shift",
	"field",
	"block",
	"crop",
	"bucket_session",
	"gps_lat",
	"gps_lon",
	"gps_accuracy_meters",
	"h3_hex",
	"source",
	"client_event_id",
	"nostr_event_id",
	"notes",
)


def describe(name: str, with_contributors: bool = False, row: dict | None = None) -> dict:
	"""One seal in the shape every tool here reports it.

	THE CONTRIBUTORS ARE READ THROUGH THE PARENT DOCUMENT and never by filtering
	the child doctype on `parent`. That filter works on a bench and returns
	nothing in the standalone suite, so a tool written the other way is a tool
	whose whole answer is untested — this app has been caught by exactly that
	before.
	"""
	if row is None:
		row = dict(frappe.db.get_value(DOCTYPE, name, list(FIELDS), as_dict=True) or {})
	if not row:
		return {}
	out = {
		"name": str(row.get("name") or name),
		"bin_tag": row.get("bin_tag"),
		"bucket_count": row.get("bucket_count") or 0,
		"company": row.get("company"),
		"sealed_at": str(row.get("sealed_at") or "") or None,
		"sealed_by": row.get("sealed_by") or None,
		"sealed_by_name": row.get("sealed_by_name") or None,
		"shift": row.get("shift") or None,
		"field": row.get("field") or None,
		"block": row.get("block") or None,
		"crop": row.get("crop") or None,
		"bucket_session": row.get("bucket_session") or None,
		"gps_lat": row.get("gps_lat"),
		"gps_lon": row.get("gps_lon"),
		"gps_accuracy_meters": row.get("gps_accuracy_meters"),
		"h3_hex": row.get("h3_hex") or None,
		"source": row.get("source") or None,
		"client_event_id": row.get("client_event_id") or None,
		"nostr_event_id": row.get("nostr_event_id") or None,
		"notes": row.get("notes") or None,
		"manual_tag": str(row.get("bin_tag") or "").startswith(MANUAL_PREFIX),
	}
	if not with_contributors:
		return out

	rows = row.get("contributors")
	if rows is None:
		try:
			rows = frappe.get_doc(DOCTYPE, out["name"]).get("contributors") or []
		except Exception:  # pragma: no cover - a seal deleted between the two reads
			rows = []
	contributors = [
		{
			"employee": entry.get("employee"),
			"employee_name": entry.get("employee_name") or entry.get("employee"),
			"badge_id": entry.get("badge_id") or None,
			"buckets_contributed": entry.get("buckets_contributed") or 0,
			"first_scan_at": str(entry.get("first_scan_at") or "") or None,
			"last_scan_at": str(entry.get("last_scan_at") or "") or None,
		}
		for entry in rows
	]
	attributed = sum(int(entry["buckets_contributed"] or 0) for entry in contributors)
	out["contributors"] = contributors
	out["contributor_count"] = len(contributors)
	out["buckets_attributed"] = attributed
	# NEVER RECONCILED, ALWAYS REPORTED. See the module docstring: a bucket
	# tipped by somebody whose badge did not scan is in the bin and not in the
	# rows, and that difference is the fact a piece-rate dispute turns on.
	out["unattributed_buckets"] = int(out["bucket_count"] or 0) - attributed
	return out


def get_bin_seal(args: dict) -> ToolResult:
	"""One seal in full, with everybody whose buckets are in it. Read-only."""
	_require()
	actor = employee_tool.require_shift_role()
	name = as_str(args, "name") or as_str(args, "bin_seal") or as_str(args, "seal", required=True)
	described = describe(name, with_contributors=True)
	if not described:
		raise ToolError(
			f"no Bin Seal called {name!r} on this site. list_bin_seals has the register, and "
			"trace_bin takes the TAG rather than the docname — which is what a packing house has."
		)
	employee_tool.require_company_scope(actor, str(described.get("company") or ""))
	described["actor"] = actor
	if described["unattributed_buckets"]:
		described["attribution_note"] = _attribution_note(described)
	return ToolResult(
		data=described,
		summary=(
			f"{described['name']}: bin {described['bin_tag']}, {described['bucket_count']} bucket(s), "
			f"{described['contributor_count']} contributor(s)"
		),
	)


def _attribution_note(described: dict) -> str:
	gap = described["unattributed_buckets"]
	if gap > 0:
		return (
			f"{gap} of this bin's {described['bucket_count']} bucket(s) are attributed to nobody. "
			"The tally and the badge scans are two different measurements and this app never "
			"reconciles them: a bucket tipped by somebody whose badge did not scan is in the bin "
			"and not in the rows, and that is exactly the fact a piece-rate dispute turns on. A "
			"large gap is a checker whose scanning broke down, which is worth knowing on the day."
		)
	return (
		f"The contributor rows account for {described['buckets_attributed']} bucket(s) against a "
		f"tally of {described['bucket_count']} — {abs(gap)} MORE than were counted into the bin. "
		"Either the tally was short or a scan was counted twice; both are worth resolving before "
		"this feeds a settlement."
	)


def list_bin_seals(args: dict) -> ToolResult:
	"""The bin register — by shift, block, tag or day. Newest first. Read-only."""
	_require()
	actor = employee_tool.require_shift_role()
	limit = min(as_limit(args), RECORD_CAP)

	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	for key, column in (
		("shift", "shift"),
		("farm_shift", "shift"),
		("field", "field"),
		("block", "block"),
		("bin_tag", "bin_tag"),
		("bucket_session", "bucket_session"),
		("sealed_by", "sealed_by"),
	):
		value = as_str(args, key)
		if value:
			filters[column] = value
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["sealed_at"] = ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"])
	elif from_date:
		filters["sealed_at"] = (">=", f"{from_date} 00:00:00")
	elif to_date:
		filters["sealed_at"] = ("<=", f"{to_date} 23:59:59")

	rows = (
		frappe.db.get_all(
			DOCTYPE,
			filters=filters,
			fields=compat.existing_fields(DOCTYPE, FIELDS),
			order_by="sealed_at desc",
			limit=limit + 1,
		)
		or []
	)
	truncated = len(rows) > limit
	rows = rows[:limit]
	seals = [describe(str(row["name"]), row=dict(row)) for row in rows]

	data = {
		"seals": seals,
		"count": len(seals),
		"limit": limit,
		"truncated": truncated,
		"total_buckets": sum(int(entry["bucket_count"] or 0) for entry in seals),
		"filters": {key: value for key, value in filters.items() if not isinstance(value, tuple)},
		"actor": actor,
		"note": (
			"Contributors are NOT on this register — one bin's crew is a child table and forty "
			"bins would be forty reads of it. get_bin_seal has one seal in full, and trace_bin "
			"answers from the TAG, which is what a packing house holds."
		),
	}
	if truncated:
		data["truncated_note"] = (
			f"More than {limit} seals matched. Narrow by shift, field or date — a bin register "
			"is read to answer a question about a block or an afternoon, not to be exported."
		)
	return ToolResult(data=data, summary=f"{len(seals)} bin seal(s), {data['total_buckets']} bucket(s)")


def trace_bin(args: dict) -> ToolResult:
	"""Given a bin tag at the packing house, who put buckets into it.

	THE ACCOUNTABILITY CHAIN, AND THE ONE READ THIS WHOLE FEATURE EXISTS FOR.
	Everything else here is a register; this is the question. It takes the TAG —
	the only thing that travels with the bin — rather than a docname, because
	nobody at a pack line has a docname.

	IT ANSWERS WITH EVERY SEAL CARRYING THE TAG, NEWEST FIRST, AND SAYS HOW MANY.
	Bin tags are reused between seasons and between growers; a tool that answered
	with one seal would be answering confidently about possibly the wrong
	afternoon. `matches` is on the answer whether it is one or five, so a caller
	never has to infer it from the length of a list.
	"""
	_require()
	actor = employee_tool.require_shift_role()
	tag = as_str(args, "bin_tag") or as_str(args, "tag") or as_str(args, "bin", required=True)
	tag = tag.strip()

	filters: dict = {"bin_tag": tag}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company

	rows = (
		frappe.db.get_all(
			DOCTYPE,
			filters=filters,
			fields=["name", "sealed_at"],
			order_by="sealed_at desc",
			limit=RECORD_CAP,
		)
		or []
	)
	if not rows:
		raise ToolError(
			f"no bin sealed under the tag {tag!r} on this site. A bin that reached the packing "
			"house with no seal behind it is a break in the chain rather than a lookup failure: "
			"nothing records which block it came from or whose buckets are in it, and nothing can "
			"reconstruct either now. list_bin_seals(shift=…) shows what the crew did seal that "
			"day, which is usually how a mis-keyed tag is found."
		)

	seals = [describe(str(row["name"]), with_contributors=True) for row in rows]
	seals = [entry for entry in seals if not company or str(entry.get("company") or "") == company]

	answer = dict(seals[0])
	answer.update(
		{
			"bin_tag": tag,
			"matches": len(seals),
			"actor": actor,
			"seals": [entry["name"] for entry in seals],
		}
	)
	if answer.get("unattributed_buckets"):
		answer["attribution_note"] = _attribution_note(answer)
	if len(seals) > 1:
		answer["ambiguous"] = [
			{
				"name": entry["name"],
				"sealed_at": entry["sealed_at"],
				"shift": entry["shift"],
				"field": entry["field"],
				"bucket_count": entry["bucket_count"],
				"contributor_count": entry["contributor_count"],
			}
			for entry in seals
		]
		answer["ambiguity_note"] = (
			f"{len(seals)} seals carry the tag {tag!r}. Bin tags are reused between seasons and "
			"between growers, so this app does not make them unique — refusing the second seal "
			"would throw away a true record of a bin that was really closed. The NEWEST is "
			"returned above and every match is in `ambiguous`; the sealing date and the block "
			"are what tell them apart."
		)
	else:
		answer["note"] = (
			f"Bin {tag} was sealed at {answer['sealed_at']} by "
			f"{answer.get('sealed_by_name') or 'an unnamed checker'} with "
			f"{answer['bucket_count']} bucket(s) from {answer['contributor_count']} worker(s)"
			+ (f", picked on {answer['shift']}" if answer.get("shift") else "")
			+ (f" in {answer['field']}." if answer.get("field") else ".")
		)
	return ToolResult(
		data=answer,
		summary=(
			f"{tag}: {answer['contributor_count']} contributor(s), {answer['bucket_count']} "
			f"bucket(s)" + (f" — {len(seals)} seals carry this tag" if len(seals) > 1 else "")
		),
	)
