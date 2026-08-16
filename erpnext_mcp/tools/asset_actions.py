# SPDX-License-Identifier: MIT
"""What a handset should offer for the thing that was just scanned.

A worker scans a tag. What comes up next depends entirely on what the tag is
attached to: a valve is turned on or off, a sprayer is mixed and then started, a
tractor is checked out and walked around before it moves, an implement is hung
off the back of one. Until now a scan answered with the STATE MACHINE — the
transitions legal from the asset's current state — and that is a strictly
smaller thing than the menu a screen has to draw. "Pre-trip inspection" is not a
state change. Neither is "log hours", or "record a calibration".

So this module is the menu, per asset type, and `state_actions` remains the
subset of it that is a legal transition right this second.

EVERY ENTRY SAYS WHETHER IT IS BUILT, and that is the point of the file rather
than a caveat about it. The alternative shapes were both worse: publishing only
the finished actions gives iOS no way to lay out a screen it will need next
month and no reason to expect one, and publishing all of them undifferentiated
gives a worker a button that fails after they have walked to the machine. So
`implemented` is on every row, `method` names the endpoint that does the work
where there is one, and `note` says what is missing where there is not — which
is also the to-do list, written where the next person to open this file will see
it.

WHAT `available` MEANS, AND WHY IT IS NOT `implemented`. A `close_valve` on a
valve that is already closed is built and unavailable; `log_hours` on any
tractor is unbuilt and therefore never available. Both come back with
`available: false` and different reasons, because a screen greys out the first
and hides or badges the second, and it cannot tell them apart from a single
flag.

THE ACTIONS THAT EXIST ELSEWHERE ARE NAMED RATHER THAN REBUILT. A pre-trip
inspection is an Inspection Session against the asset — that machinery has
templates, evidence and a submitted state, and a second half-copy of it hanging
off the sprayer would be the worse of two. Attaching an implement to a tractor
is `parent_asset`, which is the register tree the whole app already walks. Those
entries carry `implemented: true` and point at the method that does it, so the
handset calls the real thing.
"""

from __future__ import annotations

from . import asset_tags

#: What each menu entry is, so a client can group them without parsing labels:
#:
#:   state_change — `log_asset_state_change`, validated against the state machine
#:   inspection   — an Inspection Session against this asset
#:   task         — raises a Farm Task
#:   record       — files a record of something that happened
KIND_STATE = "state_change"
KIND_INSPECTION = "inspection"
KIND_TASK = "task"
KIND_RECORD = "record"

#: Labels are the WORKER'S words, not the state machine's. `open_valve` is the
#: action name a tool takes and `Turn On` is what goes on a button in an orchard,
#: and the day somebody renames the button the action name must not move with it.
#: Only the actions whose own name would read badly on a screen are listed; the
#: rest are title-cased from the action name by `_label`.
_LABELS = {
	"open_valve": "Turn On",
	"close_valve": "Turn Off",
	"fill_tank": "Mix / Load Tank",
	"start_spray": "Start Spraying",
	"end_spray": "End Spraying",
	"clean_tank": "Clean Tank",
	"check_out": "Check Out",
	"check_in": "Check In",
	"put_in_service": "Return to Service",
	"take_out_of_service": "Take Out of Service",
	"attach": "Attach to Tractor",
	"detach": "Detach",
	"flag_repair": "Flag for Repair",
	"clear_repair": "Repair Done",
}


def _label(action: str) -> str:
	return _LABELS.get(action) or action.replace("_", " ").title()


def _state(action: str) -> dict:
	"""A menu row backed by the state machine. Built, by definition."""
	return {
		"action": action,
		"label": _label(action),
		"kind": KIND_STATE,
		"method": "log_asset_state_change",
		"implemented": True,
	}


def _todo(action: str, label: str, kind: str, note: str) -> dict:
	"""A menu row a screen may lay out and must not yet call."""
	return {
		"action": action,
		"label": label,
		"kind": kind,
		"method": None,
		"implemented": False,
		"note": note,
	}


def _elsewhere(action: str, label: str, kind: str, method: str, note: str) -> dict:
	"""A menu row whose work is done by an endpoint that already exists."""
	return {
		"action": action,
		"label": label,
		"kind": kind,
		"method": method,
		"implemented": True,
		"note": note,
	}


#: Raised against any asset, so it is appended to every type's menu rather than
#: repeated in each. `report_issue` is already in `universal_scan`'s button
#: vocabulary; it appears here too because this menu is meant to be the whole
#: list of what can be done at the machine, and a client that renders only this
#: must not lose the one action that works on everything.
_UNIVERSAL = (
	_elsewhere(
		"report_issue",
		"Report a Problem",
		KIND_TASK,
		"report_asset_issue",
		"Raises a Farm Task against this asset, with the skill filled in from its type.",
	),
)

_INSPECTION_NOTE = (
	"Runs the existing Inspection Session machinery against this asset — call "
	"list_inspection_templates for the templates this site has, then start_inspection with "
	"location_doctype='Asset Register' and location set to this asset. The checklist, the "
	"evidence and the submitted state are that doctype's, not a second copy living on the asset."
)

#: The menu, per asset type. Types absent from this table get `_UNIVERSAL` plus
#: whatever their state machine offers, which is the right answer for a Block or
#: a Water Source: they have states and no equipment workflow.
_MENU: dict[str, tuple[dict, ...]] = {
	"Irrigation Valve": (
		_state("open_valve"),
		_state("close_valve"),
		_state("winterize"),
		_state("reopen"),
	),
	"Sprayer": (
		_state("fill_tank"),
		_state("start_spray"),
		_state("end_spray"),
		_state("clean_tank"),
		_elsewhere(
			"pre_use_inspection",
			"Pre-Use Inspection",
			KIND_INSPECTION,
			"start_inspection",
			_INSPECTION_NOTE,
		),
		_todo(
			"set_rates",
			"Set Application Rates",
			KIND_RECORD,
			"NOT BUILT. The tank mix and the rate it goes out at are recorded on the SPRAY TASK "
			"rather than on the sprayer — `Farm Task.materials_used`, filed through "
			"complete_task_via_mobile, which is what draws the stock down and computes the REI and "
			"PHI windows. Rates stored on the machine would be a default nobody re-reads, and the "
			"record that matters is what went on which block on which day. Building this means "
			"deciding whether a sprayer carries a standing rate that a task pre-fills from.",
		),
		# v0.78.0. WAS `_todo`, AND THE NOTE THAT SAT HERE WAS RIGHT ABOUT THE
		# DESIGN AND WRONG ABOUT THE CONCLUSION. It said REI is a property of the
		# block rather than of the machine — it is — and concluded that a sprayer
		# therefore has no REI action. But the moment a window OPENS is the moment
		# a sprayer finishes, and the person holding the phone is standing at the
		# sprayer. So the action lives on this menu and the RECORD it writes names
		# blocks: one `Spray REI` per block, keyed on the block, readable by
		# `get_active_rei` without going near this machine. See `tools/spray_rei.py`.
		_elsewhere(
			"record_spray",
			"Record Spray & Start REI",
			KIND_RECORD,
			"record_spray_application",
			"Files what went out over which blocks, opens a restricted-entry window per block from "
			"the longest REI in the tank, ends the spray on this machine, and stamps each block's "
			"last_spray_date. The window is a `Spray REI` record keyed on the BLOCK — a timer "
			"against the sprayer would say nothing about which field a worker may re-enter — and "
			"get_active_rei answers for one block, list_active_reis for the whole farm.",
		),
		_elsewhere(
			"rei_status",
			"Restricted-Entry Status",
			KIND_RECORD,
			"list_active_reis",
			"What this sprayer has closed and until when. A scan of the machine returns the same "
			"list in `status.applied_reis` without a second call; this entry is the button for a "
			"screen opened from somewhere other than a scan.",
		),
	),
	"Tractor": (
		_state("check_out"),
		_state("check_in"),
		_state("take_out_of_service"),
		_state("put_in_service"),
		_state("start_maintenance"),
		_state("end_maintenance"),
		_elsewhere(
			"pre_trip_inspection",
			"Pre-Trip Inspection",
			KIND_INSPECTION,
			"start_inspection",
			_INSPECTION_NOTE,
		),
		# v0.78.0. BUILT, AND BUILT THE WAY THE OLD NOTE HERE SAID IT WOULD HAVE
		# TO BE: `Asset State Log.engine_hours` is the series and
		# `Asset Register.current_hours` is a cache of it, so hours-since-service
		# and hours-this-season are arithmetic rather than a single overwritten
		# number. IT IS NOT A CALL OF ITS OWN — the moment somebody reads an hour
		# meter is the moment they are sitting in the machine checking it out or
		# bringing it back, so the reading rides on those two state changes.
		_elsewhere(
			"log_hours",
			"Log Engine Hours",
			KIND_RECORD,
			"log_asset_state_change",
			"Send `engine_hours` with a check_out or a check_in and the reading is filed against "
			"that event; a check-in with a reading at both ends also records the hours run that "
			"session. A reading below the last one on record is refused as a typo unless "
			"allow_meter_reset=true says the instrument was swapped. get_engine_hours_summary is "
			"the series read back — total, this season, since the last service.",
		),
		_elsewhere(
			"service_due",
			"Service Status",
			KIND_RECORD,
			"check_maintenance_due",
			"Whether this machine is due on hours, on the calendar, or neither — and by how much. "
			"A scan returns the same answer in `status.maintenance` without a second call. Set the "
			"schedule with update_registered_asset(service_interval_hours=…, "
			"service_interval_days=…) and close one out with record_service.",
		),
	),
	"Implement": (
		_state("attach"),
		_state("detach"),
		_state("flag_repair"),
		_state("clear_repair"),
		_elsewhere(
			"set_tractor",
			"Set Which Tractor",
			KIND_RECORD,
			"update_registered_asset",
			"WHICH tractor an implement is on is the register tree, not a state: call "
			"update_registered_asset with parent_asset set to the tractor's docname. `attach` "
			"records THAT it is hitched and when; this records what to. Send both — the state "
			"change is what the timeline shows, the parent is what a later scan of the tractor "
			"resolves through.",
		),
		_todo(
			"calibration_record",
			"Record Calibration",
			KIND_RECORD,
			"NOT BUILT as its own record. The nearest thing that exists today is an Inspection "
			"Session with a calibration template, which gives it a checklist, evidence and a "
			"submitted state for nothing — create the template with create_inspection_template and "
			"this entry becomes a `start_inspection` call. A dedicated Calibration doctype should "
			"wait until somebody needs a figure OUT of it (drift over time, last-calibrated "
			"alerts), because that is what would justify its own columns.",
		),
	),
}

#: Vehicle shares Tractor's menu for the reason it shares Tractor's state
#: machine: a pickup is checked out, walked around and driven, and a near-copy
#: of six rows would drift the first time one of them changed.
_MENU["Vehicle"] = _MENU["Tractor"]


def menu_for(asset_type: str, current_state: str = "") -> list[dict]:
	"""The full action menu for one asset type, marked up for its current state.

	`current_state` is optional: a caller that only wants to know what a TYPE can
	ever do (a settings screen, a print-out of the workflows) omits it and gets
	every row with `available` absent. A caller holding a real asset passes it
	and gets `available` on every row, which is what a screen greys out on.
	"""
	asset_type = str(asset_type or "") or "General"
	defn = asset_tags._STATE_DEFINITIONS.get(asset_type)
	rows = list(_MENU.get(asset_type, ()))

	# A type with a state machine but no hand-written menu — Block, Water Source,
	# Housing Unit, General — gets its transitions listed rather than an empty
	# menu. The table above exists for types whose menu is MORE than their state
	# machine, not to decide which types have one.
	if defn and not rows:
		rows = [_state(action) for action in defn["actions"]]
	rows = rows + list(_UNIVERSAL)

	if not current_state:
		return [dict(row) for row in rows]

	legal = {entry["action"]: entry for entry in asset_tags._actions_for(asset_type, current_state)}
	out = []
	for row in rows:
		row = dict(row)
		if row["kind"] != KIND_STATE:
			# Not a transition, so the state machine has no opinion on it. An
			# inspection can be started on a valve whatever the valve is doing.
			row["available"] = bool(row["implemented"])
			if not row["implemented"]:
				row["unavailable_reason"] = "not implemented on this server yet"
		elif row["action"] in legal:
			row["available"] = True
			row["from_state"] = legal[row["action"]]["from_state"]
			row["to_state"] = legal[row["action"]]["to_state"]
		else:
			row["available"] = False
			row["unavailable_reason"] = f"not a legal move from {current_state!r}"
		out.append(row)
	return out
