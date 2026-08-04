# SPDX-License-Identifier: MIT
"""The trip, rather than the five tasks it happened to contain.

v0.20.1. A WORKER DOES NOT GO TO A TASK, THEY GO SOMEWHERE. They drive to the
north block, walk five cabins, close five task assignments and drive back. The
dispatch board records that as five completions with five timestamps, and every
question anybody actually asks about the morning — how long was the trip, was
the drive worth it, how many places did we send somebody to twice in one day —
has to be reconstructed by guessing which completions belonged together from how
close their timestamps are.

Guessing from timestamps is wrong in both directions and there is no threshold
that fixes it. Two cabins closed forty minutes apart on one unhurried walk look
like two trips; two closed a minute apart from opposite ends of the property
look like one. The handset is the only thing that KNOWS: it is there, it saw the
worker arrive, and it can mint an identifier at the moment they do. So it does,
and every completion filed before they leave carries it. `visit_id` on Farm Task
Assignment is that identifier and this module is the rollup.

────────────────────────────────────────────────────────────────────────────
WHY A GROUPING AND NOT A `FARM VISIT` DOCTYPE
────────────────────────────────────────────────────────────────────────────

A visit has no facts of its own. Every field below is derived from the
completions in it — when it started is the first one, where it was is where
their tasks are, how long it took is the span. A doctype would be a row that
must be created before the completions, kept in step with them, and repaired
when one is amended; and a client that is offline when the visit starts cannot
create one to attach to. A Data column on the completion, minted by the thing
that was present, is the whole record — and the rollup is a read.

That is the same argument this codebase makes about Farm Task Assignment being
a doctype rather than a child table, run the other way. The assignment holds
facts nothing else holds; a visit holds none.

────────────────────────────────────────────────────────────────────────────
ONE-TASK VISITS ARE RETURNED, AND THAT IS DELIBERATE
────────────────────────────────────────────────────────────────────────────

A trip to one cabin is a trip. Somebody drove out, did one job and drove back,
and that is frequently the most interesting row in the report — it is what a
question about wasted travel is looking for. Filtering to "visits worth showing"
would mean the report answers that question by hiding its evidence.

The count is reported separately (`single_task_visits`) so a caller that wants
only the multi-task trips can filter on the way out and knows exactly what it
dropped.

────────────────────────────────────────────────────────────────────────────
A COMPLETION WITH NO `visit_id` IS NOT IN ANY VISIT
────────────────────────────────────────────────────────────────────────────

Not in a synthetic one-per-completion visit, and not in an "unassigned" bucket
pretending to be a trip. Everything filed before v0.20.1 has the column blank,
as does everything filed by a client that does not mint one, and a report that
invented visits for those would put fabricated trips beside real ones with
nothing to tell them apart. `ungrouped_completions` reports how many were
skipped, which is the honest version of the same information.
"""

from __future__ import annotations

import frappe

from .. import compat
from ..args import as_date, as_limit, as_str, resolve_company
from ..result import ToolResult
from . import employee as employee_tool

DOCTYPE = "Farm Task Assignment"
FARM_TASK = "Farm Task"
COMPLETED = "Completed"

#: Most completions one read walks. A visit rollup is read to answer a question
#: about a period or a worker, not to be exported — the same posture, and the
#: same ceiling, `list_shifts` takes.
RECORD_CAP = 500

#: Most completions one read pulls out of the database before grouping. Higher
#: than the visit ceiling because completions are what is stored and visits are
#: what is asked for: a day of five-task trips is five rows per row of answer.
SCAN_CAP = 5000

_FIELDS = (
	"name",
	"task",
	"task_name",
	"assigned_to",
	"assigned_to_name",
	"company",
	"completed_at",
	"actual_duration_minutes",
	"farm_location_gps",
	"visit_id",
)


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate`.",
	)


def _minutes(first: str, last: str) -> int:
	"""Wall-clock minutes from the first completion to the last, never negative.

	THE VISIT IS LONGER THAN THIS AND THE FIELD DOES NOT PRETEND OTHERWISE. It
	measures first completion to last, so it excludes the drive out, the walk to
	the first cabin, and everything after the last task was filed. A one-task
	visit measures zero — which is right: one completion is one instant, and the
	only honest thing to say about how long that trip took is that this record
	cannot say.
	"""
	try:
		delta = frappe.utils.time_diff_in_seconds(last, first)
	except Exception:
		return 0
	return max(0, int((delta or 0) // 60))


def _sole(values):
	"""The one distinct value, or None where the rows disagree or say nothing.

	Two workers who happened to use one visit_id, or completions spanning two
	companies, is a real thing a client can produce and it is not this tool's to
	adjudicate. Reporting None beside the full list is the answer that does not
	pick a winner — `visit["companies"]` is always there to be read.
	"""
	distinct = [value for value in dict.fromkeys(values) if value]
	return distinct[0] if len(distinct) == 1 else None


# ── list_visits ─────────────────────────────────────────────────────────────
def list_visits(args: dict) -> ToolResult:
	"""Completed task assignments grouped into the trips their handsets recorded."""
	_require()
	actor = employee_tool.require_hr_role()
	limit = min(as_limit(args), RECORD_CAP)

	if not compat.has_field(DOCTYPE, "visit_id"):
		return ToolResult(
			data={"visits": [], "count": 0, "limit": limit, "ungrouped_completions": 0},
			summary=(
				"this site's Farm Task Assignment has no `visit_id` column yet, so no completion "
				"can be in a visit. It ships with erpnext_mcp v0.20.1 — run `bench migrate`."
			),
		)

	filters = {"state": COMPLETED}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		from .. import roles

		allowed = roles.companies_for(actor) or []
		if allowed:
			filters["company"] = ("in", allowed)

	worker = as_str(args, "worker") or as_str(args, "employee")
	if worker:
		filters["assigned_to"] = employee_tool.resolve_employee(worker)

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["completed_at"] = ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"])
	elif from_date:
		filters["completed_at"] = (">=", f"{from_date} 00:00:00")
	elif to_date:
		filters["completed_at"] = ("<=", f"{to_date} 23:59:59")

	rows = (
		frappe.db.get_all(
			DOCTYPE,
			filters=filters,
			fields=compat.existing_fields(DOCTYPE, _FIELDS),
			order_by="completed_at asc",
			limit=SCAN_CAP,
		)
		or []
	)

	grouped, ungrouped = {}, 0
	for row in rows:
		visit = str(row.get("visit_id") or "").strip()
		if not visit:
			ungrouped += 1
			continue
		grouped.setdefault(visit, []).append(row)

	described = [_describe(visit, members) for visit, members in grouped.items()]

	# THE LOCATION FILTER IS APPLIED TO THE VISIT, NOT TO THE COMPLETION. A trip
	# that touched the north block is a trip to the north block even where it
	# also touched somewhere else on the way, and dropping the other tasks out of
	# it would report a visit that is missing half its work.
	location = as_str(args, "location")
	if location:
		wanted = location.strip().casefold()
		described = [
			entry
			for entry in described
			if any(wanted == str(place or "").strip().casefold() for place in entry["locations"])
		]

	described.sort(key=lambda entry: (entry["first_completion_datetime"] or "", entry["visit_id"]))
	truncated = len(described) > limit
	described = described[:limit]

	singles = len([entry for entry in described if entry["total_tasks"] == 1])
	data = {
		"company": company,
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"visits": described,
		"total_tasks": sum(entry["total_tasks"] for entry in described),
		"single_task_visits": singles,
		"ungrouped_completions": ungrouped,
	}
	if ungrouped:
		data["ungrouped_note"] = (
			f"{ungrouped} completion(s) in this selection carry no visit_id and are in no visit "
			"here. Everything filed before v0.20.1 is in that group, as is anything filed by a "
			"client that does not mint one. They are NOT missing work — they are work whose trip "
			"nobody recorded, and inventing a one-task visit for each would put fabricated trips "
			"beside real ones with nothing to tell them apart."
		)
	if singles:
		data["single_task_note"] = (
			f"{singles} of these visit(s) contain one task. That is a real trip and it is "
			"reported as one — somebody drove out, did one job and drove back, which is exactly "
			"what a question about wasted travel is looking for. Filter on total_tasks if the "
			"question is about multi-stop rounds."
		)
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} visit(s) matched and this is the first {limit}, earliest first. "
			"Narrow by company, worker or period before relying on the totals above."
		)
	if len(rows) >= SCAN_CAP:
		data["scan_note"] = (
			f"This read hit its {SCAN_CAP}-completion ceiling, so the visits at the far end of "
			"the period may be incomplete — a trip whose last task fell outside the slice is "
			"reported short. Narrow the period."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} visit(s)"
			+ (f" for {company}" if company else "")
			+ f" covering {data['total_tasks']} completed task(s)"
			+ (f"; {ungrouped} completion(s) carry no visit_id" if ungrouped else "")
		),
	)


def _describe(visit: str, members: list) -> dict:
	"""One visit, entirely derived from the completions in it."""
	members = sorted(members, key=lambda row: str(row.get("completed_at") or ""))
	stamps = [str(row.get("completed_at") or "") for row in members if row.get("completed_at")]
	first, last = (stamps[0], stamps[-1]) if stamps else ("", "")

	tasks = [str(row.get("task") or "") for row in members if row.get("task")]
	places = _locations(tasks)
	companies = [str(row.get("company") or "") for row in members]
	workers = [str(row.get("assigned_to") or "") for row in members]

	return {
		"visit_id": visit,
		"first_completion_datetime": first or None,
		"last_completion_datetime": last or None,
		"duration_minutes": _minutes(first, last) if first and last else 0,
		"location": _sole(places),
		"locations": places,
		# What the handsets recorded for where they were standing, which is a
		# different fact from which record the task is about — see
		# Farm Task Assignment.farm_location_gps. Both are reported because a
		# visit whose tasks name one cabin and whose phones name three
		# coordinates is worth being able to see.
		"farm_location_gps": [
			place
			for place in dict.fromkeys(str(row.get("farm_location_gps") or "") for row in members)
			if place
		],
		"company": _sole(companies),
		"companies": [value for value in dict.fromkeys(companies) if value],
		"completing_user": _sole(workers),
		"completing_user_name": _sole([str(row.get("assigned_to_name") or "") for row in members]),
		"completing_users": [value for value in dict.fromkeys(workers) if value],
		"task_assignment_names": [str(row.get("name")) for row in members],
		"task_names": [str(row.get("task_name") or "") for row in members],
		"total_tasks": len(members),
		"total_evidence_files": _evidence_count([str(row.get("name")) for row in members]),
		"logged_duration_minutes": sum(int(row.get("actual_duration_minutes") or 0) for row in members),
	}


def _locations(tasks: list) -> list:
	"""The places the visit's tasks are about, distinct, in the order first seen."""
	if not tasks:
		return []
	rows = (
		frappe.db.get_all(
			FARM_TASK,
			filters={"name": ("in", tasks)},
			fields=["name", "location"],
			limit=len(tasks),
		)
		or []
	)
	by_task = {str(row["name"]): str(row.get("location") or "") for row in rows}
	return [place for place in dict.fromkeys(by_task.get(task, "") for task in tasks) if place]


def _evidence_count(assignments: list) -> int:
	"""Distinct evidence FILES across the visit, not evidence rows.

	COUNTED BY FILE, because a signature captured once and filed against three
	cabins on one walk is one photograph of one signature — counting the rows
	would report three and make the trip look better evidenced than it was. A
	row with no File docname (an older completion carrying only a `file_url`)
	falls back to the URL, which is the only identity it has.
	"""
	if not assignments:
		return 0
	rows = (
		frappe.db.get_all(
			"Farm Task Evidence",
			filters={"parent": ("in", assignments), "parenttype": DOCTYPE},
			fields=["file", "file_url"],
			limit=SCAN_CAP,
		)
		or []
	)
	seen = {str(row.get("file") or "").strip() or str(row.get("file_url") or "").strip() for row in rows}
	seen.discard("")
	return len(seen)
