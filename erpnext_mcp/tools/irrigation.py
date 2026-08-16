# SPDX-License-Identifier: MIT
"""How long the water actually ran, from the valve log that already exists.

THE MEASUREMENT IS NOT A NEW RECORD. Every open and every close a worker logs
through `log_asset_state_change` is already an Asset State Log row with a
timestamp on it, and a run is two of those rows: the one that left a valve
`open` and the next one that did not. Summing the pairs is arithmetic over a
register that has been filling up since v0.25.0 — so a farm that has been
scanning valve tags all season can ask what its water did last month without
having recorded anything for the purpose.

That is also why this is a tool and not a doctype. An Irrigation Run table would
be a second account of the same events, written by the same call, and the two
would disagree the first time somebody corrected a log row.

WHAT A "ZONE" IS HERE. The Asset Register is a tree — a valve's `location` is
the zone it sits under, a zone's is the block, a block's is the ranch — so
"runtime for zone 3" is "runtime for every valve below the zone-3 asset". The
root can be any node: pass a valve and the answer is that valve; pass a block
and it is every valve on it. The tree is walked with `asset_tags._descendants`,
which is the same walk the closing cascade uses, so the set of valves a main
shuts and the set of valves this counts are the same set by construction.

THE FOUR THINGS THAT MAKE THIS HARDER THAN SUBTRACTING TIMESTAMPS:

  * A RUN THAT STARTED BEFORE THE WINDOW. Water opened on 28 June and closed on
    2 July is four days of July irrigation, and a query that read only July's
    rows would see a close with no open and drop it. So the last event before
    the window is read for every valve, and where it left the valve open the
    window opens with the water already running. This is the difference between
    "the report is a bit low" and "the report is wrong in exactly the months
    that matter", because a valve is most likely to be open across a boundary
    during the heaviest irrigation.
  * A RUN THAT ENDED AFTER THE WINDOW, which is the same problem at the other
    edge and the one that quietly shortens a month. Water still on at midnight
    on 30 June and shut on 1 July ran for all of those June hours; it is not
    "currently running" when the report is written in August. So the first event
    AFTER the window is read too, and a run it ends is a finished run clipped to
    the window's last second rather than an open-ended one.
  * A RUN THAT HAS NOT ENDED AT ALL. A valve open right now has real minutes on
    it and no closing row anywhere. Those minutes are counted, to the end of the
    window or to now, whichever is earlier — and reported SEPARATELY as
    `open_run_minutes`, because a total that mixes finished runs with a
    still-running one is a number that changes when you ask it twice.
  * A CLOSE THAT NOBODY PERFORMED. The cascade writes a close on every valve
    below a main that was shut. Those rows are real closes — the water did
    stop — so they end runs like any other. `cascaded_closes` counts them, so a
    reading that looks short can be traced to the main somebody shut rather
    than to a valve nobody logged.

WATER, AND WHY THE RATE IS AN ARGUMENT. Minutes are what the log can prove.
Gallons are minutes times a flow rate, and the flow rate lives on the Irrigation
Zone record (`flow_rate_gpm`), which this app does NOT link to the asset tree —
an asset's parent is another asset, not a zone. Rather than guess at a match by
name, the rate is taken from an explicit `flow_rate_gpm`, or from an
`irrigation_zone` the caller names, and the answer says which
(`flow_rate_source`). With neither, the runtime comes back without gallons and
says so. A volume computed from a rate nobody confirmed is worse than no volume:
it is the number that ends up in a water report.
"""

from __future__ import annotations

import frappe

from .. import compat, timezones
from ..args import as_date, as_float, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import asset_tags

ASSET_REGISTER = asset_tags.ASSET_REGISTER
ASSET_STATE_LOG = asset_tags.ASSET_STATE_LOG
IRRIGATION_ZONE = "Irrigation Zone"

#: The state that means water is moving. A valve is `open`, `closed` or
#: `winterized` (see `asset_tags._STATE_DEFINITIONS`), and only the first of
#: those passes water — so a run starts at any event whose `to_state` is this
#: and ends at the next event whose `to_state` is anything else.
RUNNING = "open"

#: The pair of actions that make a type a valve, for the purposes of this file.
#:
#: TESTED ON THE ACTION NAMES AND NOT ON THE STATES. Three of the shipped types
#: reach a state called `open` — a Storage and a Cold Storage are "open for the
#: season" — and a rule that counted anything reaching `open` would report a
#: packing shed's season as irrigation runtime. Opening and closing a VALVE is
#: what `open_valve`/`close_valve` name, so a type that defines both is one this
#: measurement applies to and a type that does not is not.
_VALVE_ACTIONS = ("open_valve", "close_valve")


def _valve_types() -> tuple[str, ...]:
	"""Which asset types have a valve's state machine.

	Read off `_STATE_DEFINITIONS` rather than listed here, so a second valve type
	added there starts being counted without an edit in this file.
	"""
	return tuple(
		asset_type
		for asset_type, defn in asset_tags._STATE_DEFINITIONS.items()
		if all(action in defn["actions"] for action in _VALVE_ACTIONS)
	)


#: How far back the window reaches when the caller names no dates. A month is
#: the period a water report is written for, and an unbounded default would walk
#: every event since the register was created to answer "how is this zone doing".
DEFAULT_WINDOW_DAYS = 30

#: Most state-log rows read for one valve. A valve toggled more than this in one
#: window is an automation writing rows, not a person opening a gate, and the
#: cap is reported (`truncated`) rather than quietly shortening the runtime.
EVENT_CAP = 2000

#: Most individual runs itemised in the answer. The totals are computed from
#: everything; `runs` is the readable list under it.
RUN_CAP = 200


def _require() -> None:
	compat.require_doctype(
		ASSET_STATE_LOG,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _now() -> str:
	return str(frappe.utils.now())


def _window(args: dict) -> tuple[str, str]:
	"""The `[from, to]` datetimes to measure over, as a closed interval of days.

	Dates rather than datetimes on the ARGUMENT, because a water report is
	written for days; the bounds are widened to the whole of each day here, so
	`to_date` naming today includes what has run this morning rather than
	stopping at midnight.
	"""
	# THE DAY BOUNDARIES ARE THE SITE'S, not the requested display zone's. A
	# `to_date` of 2026-07-31 means that day as the farm lived it, and the column
	# being filtered is stored in the site zone — so the bounds are built in the
	# same zone the comparison happens in. Rendering the result in Denver moves
	# the wall clock on each timestamp; it must not move which day was measured.
	to_date = as_date(args, "to_date") or str(frappe.utils.today())
	from_date = as_date(args, "from_date") or str(frappe.utils.add_days(to_date, -DEFAULT_WINDOW_DAYS))
	if from_date > to_date:
		raise ToolError(f"from_date {from_date!r} is after to_date {to_date!r}. Nothing was measured.")
	return f"{from_date} 00:00:00", f"{to_date} 23:59:59"


def _minutes(later: str, earlier: str) -> float:
	try:
		return max(0.0, float(frappe.utils.time_diff_in_seconds(later, earlier)) / 60.0)
	except Exception:  # pragma: no cover - an unparseable stored timestamp
		return 0.0


def _stamp(row: dict) -> str:
	"""When the event happened. `performed_at` where there is one, else the row's
	own creation — the same fallback `list_asset_state_history` reports on."""
	return str(row.get("performed_at") or row.get("creation") or "")


def _valves(root: dict) -> list[dict]:
	"""The root and every asset below it that has a valve's state machine."""
	types = set(_valve_types())
	found = []
	if (root.get("asset_type") or "") in types and not root.get("retired_at"):
		found.append(root)
	descendants, _ = asset_tags._descendants(root["name"])
	found.extend(
		child
		for child in descendants
		if (child.get("asset_type") or "") in types and not child.get("retired_at")
	)
	return found


def _events(asset_name: str, opened: str, closed: str) -> tuple[list[dict], bool]:
	"""This valve's state changes inside the window, oldest first.

	Ordered on `creation` and not on `performed_at`, then re-sorted in Python on
	whichever timestamp the row actually carries. The column the database can
	order on cheaply is the one that is always set; the column the arithmetic
	needs is the one the worker's handset stamped, and on a queued completion
	filed hours later those two disagree. Sorting the page in memory is a page of
	rows, and getting this wrong is a negative run.
	"""
	fields = compat.existing_fields(
		ASSET_STATE_LOG,
		(
			"name",
			"action",
			"from_state",
			"to_state",
			"performed_by",
			"performed_at",
			"cascaded_from",
			"creation",
		),
	)
	column = "performed_at" if compat.has_field(ASSET_STATE_LOG, "performed_at") else "creation"
	rows = (
		frappe.db.get_all(
			ASSET_STATE_LOG,
			filters={"asset_name": asset_name, column: ("between", [opened, closed])},
			fields=fields,
			order_by="creation asc",
			limit=EVENT_CAP + 1,
		)
		or []
	)
	truncated = len(rows) > EVENT_CAP
	rows = sorted((dict(row) for row in rows[:EVENT_CAP]), key=_stamp)
	return rows, truncated


def _closed_after(asset_name: str, closed: str) -> str:
	"""Did a run left open at the window's end ever end, and when.

	THE MIRROR OF `_open_before`, AND IT DECIDES WHICH TOTAL THE MINUTES LAND IN.
	A valve open at midnight on 30 June and shut on 1 July ran for all of those
	June hours, and June's report is written in August — the water is not
	"currently running", it ran and stopped. Without this lookahead the run has
	no closing row inside the window, gets called still-open, and its minutes go
	to `open_run_minutes` instead of `runtime_minutes`: every month whose last
	night was irrigated reads short in the headline figure, which is the figure
	somebody copies into a water report.

	Returns the timestamp the run actually ended at, or `""` when nothing has
	ended it yet — which is the one case where the water really is still moving.
	"""
	column = "performed_at" if compat.has_field(ASSET_STATE_LOG, "performed_at") else "creation"
	rows = (
		frappe.db.get_all(
			ASSET_STATE_LOG,
			filters={"asset_name": asset_name, column: (">", closed)},
			fields=compat.existing_fields(ASSET_STATE_LOG, ("name", "to_state", "performed_at", "creation")),
			order_by=f"{column} asc",
			limit=EVENT_CAP,
		)
		or []
	)
	for row in rows:
		if str(dict(row).get("to_state") or "") != RUNNING:
			return _stamp(dict(row))
	return ""


def _open_before(asset_name: str, opened: str) -> str:
	"""Was the water already running when the window began, and since when.

	Returns the timestamp of the event that left this valve open, or `""`. See
	the module docstring: without this, every run that crosses the start of the
	window loses all of the minutes inside it.
	"""
	column = "performed_at" if compat.has_field(ASSET_STATE_LOG, "performed_at") else "creation"
	rows = (
		frappe.db.get_all(
			ASSET_STATE_LOG,
			filters={"asset_name": asset_name, column: ("<", opened)},
			fields=compat.existing_fields(ASSET_STATE_LOG, ("name", "to_state", "performed_at", "creation")),
			order_by=f"{column} desc",
			limit=1,
		)
		or []
	)
	if not rows:
		return ""
	last = dict(rows[0])
	return _stamp(last) if str(last.get("to_state") or "") == RUNNING else ""


def _runs_for(valve: dict, opened: str, closed: str, now: str) -> dict:
	"""Pair this valve's events into runs and add up the minutes."""
	events, truncated = _events(valve["name"], opened, closed)
	carried = _open_before(valve["name"], opened)

	runs: list[dict] = []
	# `started` is the timestamp water began moving, and it is seeded from BEFORE
	# the window when the valve was already open — clamped to the window's own
	# start so June's minutes are not billed to July.
	started = opened if carried else ""
	started_before = bool(carried)

	for event in events:
		to_state = str(event.get("to_state") or "")
		when = _stamp(event)
		if to_state == RUNNING:
			# An open on an already-open valve is not a second run. It happens —
			# a worker re-scans a gate they already opened — and treating it as
			# one would double every minute between the two rows.
			if not started:
				started, started_before = when, False
			continue
		if not started:
			continue
		runs.append(
			{
				"valve": valve["name"],
				"opened_at": started,
				"closed_at": when,
				"minutes": round(_minutes(when, started), 1),
				"open_at_window_start": started_before,
				"closed_after_window": False,
				"still_open": False,
				"closed_by_cascade": bool(event.get("cascaded_from")),
				"cascaded_from": event.get("cascaded_from") or None,
			}
		)
		started, started_before = "", False

	# A run still open at the window's last event either ended after the window —
	# in which case it is a FINISHED run, clipped to the window, and its minutes
	# belong in the total that does not move — or it has never ended, which is
	# the only case where the water is genuinely still moving. See `_closed_after`.
	still_open = False
	open_minutes = 0.0
	if started:
		ended_later = _closed_after(valve["name"], closed)
		if ended_later:
			runs.append(
				{
					"valve": valve["name"],
					"opened_at": started,
					"closed_at": closed,
					"minutes": round(_minutes(closed, started), 1),
					"open_at_window_start": started_before,
					# The run's own close is reported as well as the boundary the
					# minutes were counted to, so a reader can see both the water's
					# actual stop and why this window was billed less than that.
					"closed_after_window": True,
					"actual_closed_at": ended_later,
					"still_open": False,
					"closed_by_cascade": False,
					"cascaded_from": None,
				}
			)
		else:
			still_open = True
			# The end of an unfinished run is the window's end or now, whichever
			# came first: a window that closed last week must not keep accruing
			# minutes, and one that ends today must not count minutes that have
			# not happened.
			edge = min(closed, now) if now else closed
			open_minutes = round(_minutes(edge, started), 1)
			runs.append(
				{
					"valve": valve["name"],
					"opened_at": started,
					"closed_at": None,
					"minutes": open_minutes,
					"open_at_window_start": started_before,
					"closed_after_window": False,
					"still_open": True,
					"closed_by_cascade": False,
					"cascaded_from": None,
				}
			)

	completed = [run for run in runs if not run["still_open"]]
	return {
		"asset_name": valve["name"],
		"asset_type": valve.get("asset_type") or None,
		"run_count": len(completed),
		"runtime_minutes": round(sum(run["minutes"] for run in completed), 1),
		"open_run_minutes": open_minutes,
		"still_open": still_open,
		"open_since": started or None,
		"open_at_window_start": bool(carried),
		"cascaded_closes": len([run for run in completed if run["closed_by_cascade"]]),
		"events_truncated": truncated,
		"runs": runs,
	}


def _rate(args: dict, company: str = "") -> dict:
	"""The gallons-per-minute to price the runtime at, and where it came from.

	Three answers, all of them reported. An explicit `flow_rate_gpm` wins because
	somebody measured it; an `irrigation_zone` is read off that zone's own record
	along with its acreage; and neither is a perfectly good answer that returns
	minutes and says why there are no gallons.
	"""
	explicit = args.get("flow_rate_gpm")
	if explicit is not None:
		rate = as_float(explicit, "flow_rate_gpm")
		if rate <= 0:
			raise ToolError("flow_rate_gpm must be greater than zero. Nothing was measured.")
		return {
			"flow_rate_gpm": round(rate, 2),
			"flow_rate_source": "the flow_rate_gpm argument",
			"irrigation_zone": None,
			"area_acres": None,
		}

	zone = as_str(args, "irrigation_zone")
	if not zone:
		return {
			"flow_rate_gpm": None,
			"flow_rate_source": (
				"NOT PRICED. Runtime is minutes; gallons are minutes times a flow rate, and "
				"nothing on the Asset Register carries one — an asset's parent is another "
				"asset, not an Irrigation Zone. Pass `irrigation_zone` to read the rate off "
				"that zone's record, or `flow_rate_gpm` to state it outright."
			),
			"irrigation_zone": None,
			"area_acres": None,
		}

	if not compat.doctype_exists(IRRIGATION_ZONE):
		raise ToolError(
			f"this site has no {IRRIGATION_ZONE} doctype, so {zone!r} cannot be read for a flow "
			"rate. Pass flow_rate_gpm instead. Nothing was measured."
		)
	fields = compat.existing_fields(
		IRRIGATION_ZONE, ("name", "zone_name", "flow_rate_gpm", "area_acres", "owning_entity")
	)
	row = frappe.db.get_value(IRRIGATION_ZONE, zone, fields, as_dict=True)
	if not row:
		raise ToolError(
			f"no {IRRIGATION_ZONE} record called {zone!r}. list_irrigation_zones has the "
			"register. Nothing was measured."
		)
	row = dict(row)
	if company and row.get("owning_entity") and row["owning_entity"] != company:
		raise ToolError(
			f"Irrigation zone {zone!r} belongs to {row['owning_entity']!r}, not {company!r}. "
			"Nothing was measured."
		)

	rate = float(row.get("flow_rate_gpm") or 0)
	if rate <= 0:
		return {
			"flow_rate_gpm": None,
			"flow_rate_source": (
				f"NOT PRICED. {IRRIGATION_ZONE} {zone!r} has no flow_rate_gpm on it, so the "
				"runtime below is minutes only. Set it with update_irrigation_zone, or pass "
				"flow_rate_gpm here."
			),
			"irrigation_zone": row.get("name"),
			"area_acres": round(float(row.get("area_acres") or 0), 3) or None,
		}
	return {
		"flow_rate_gpm": round(rate, 2),
		"flow_rate_source": f"{IRRIGATION_ZONE} {row.get('name')!r} (flow_rate_gpm)",
		"irrigation_zone": row.get("name"),
		"area_acres": round(float(row.get("area_acres") or 0), 3) or None,
	}


# ── get_irrigation_runtime ──────────────────────────────────────────────────
def get_irrigation_runtime(args: dict) -> ToolResult:
	"""How many minutes the valves under one asset ran, from the state log."""
	asset_tags._require()
	_require()

	company = resolve_company(as_str(args, "company"))
	root = asset_tags.asset_row(as_str(args, "asset", required=True) or "", company or "")
	opened, closed = _window(args)
	now = _now()

	valves = _valves(root)
	if not valves:
		raise ToolError(
			f"{root['name']} has no valve under it — neither it nor anything below it in the "
			f"register is one of: {', '.join(_valve_types())}. Runtime is measured from valve "
			"open/close events, so there is nothing here to measure. get_asset_detail shows "
			"what this asset's children actually are."
		)

	measured = [_runs_for(valve, opened, closed, now) for valve in valves]
	rate = _rate(args, company or "")

	# WHICH six o'clock the water went on. A runtime report is read against a
	# wall clock — "it ran from six to nine thirty" — and the naive columns this
	# is summed from cannot say which zone that is. Every timestamp gets its
	# offset-bearing twin; the MINUTES are unaffected, because a duration is the
	# difference between two instants and both were already in one zone.
	clock = timezones.Renderer(args)
	for entry in measured:
		clock.add(entry, "open_since")
		for run in entry["runs"]:
			clock.add(run, "opened_at", "closed_at", "actual_closed_at")

	total = round(sum(entry["runtime_minutes"] for entry in measured), 1)
	open_minutes = round(sum(entry["open_run_minutes"] for entry in measured), 1)
	runs = sorted(
		(run for entry in measured for run in entry["runs"]),
		key=lambda run: str(run.get("opened_at") or ""),
		reverse=True,
	)

	data = {
		"asset": root["name"],
		"asset_type": root.get("asset_type") or None,
		"company": root.get("company") or None,
		"from": opened,
		"to": closed,
		"from_local": clock(opened),
		"to_local": clock(closed),
		"measured_at": now,
		"measured_at_local": clock(now),
		"valve_count": len(valves),
		"valves": measured,
		"run_count": sum(entry["run_count"] for entry in measured),
		# THE TWO TOTALS ARE KEPT APART ON PURPOSE. `runtime_minutes` is finished
		# runs — it does not change if you ask again in an hour. `open_run_minutes`
		# is the water running right now, which does. A caller writing a water
		# report wants the first; a caller drawing a screen wants both.
		"runtime_minutes": total,
		"runtime_hours": round(total / 60.0, 2),
		"open_run_minutes": open_minutes,
		"total_minutes_including_open": round(total + open_minutes, 1),
		"valves_open_now": [entry["asset_name"] for entry in measured if entry["still_open"]],
		"cascaded_closes": sum(entry["cascaded_closes"] for entry in measured),
		"events_truncated": any(entry["events_truncated"] for entry in measured),
		"runs": runs[:RUN_CAP],
		"runs_truncated": len(runs) > RUN_CAP,
		**rate,
		**clock.block(),
	}

	if rate["flow_rate_gpm"]:
		gallons = round(total * rate["flow_rate_gpm"], 1)
		data["gallons"] = gallons
		data["acre_inches"] = round(gallons / 27154.0, 3) if gallons else 0.0
		if rate["area_acres"]:
			data["gallons_per_acre"] = round(gallons / rate["area_acres"], 1)
			data["inches_applied"] = round(gallons / 27154.0 / rate["area_acres"], 3)

	volume = f", {data['gallons']:,.0f} gal" if data.get("gallons") else ""
	return ToolResult(
		data=data,
		summary=(
			f"{root['name']}: {len(valves)} valve(s), {data['run_count']} run(s), "
			f"{data['runtime_hours']} h{volume}"
			+ (f", {len(data['valves_open_now'])} open now" if data["valves_open_now"] else "")
		),
	)
