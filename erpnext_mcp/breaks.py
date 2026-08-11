# SPDX-License-Identifier: MIT
"""Break entitlement and reconciliation — pure functions, no database.

Same contract as `bucket_bridge.py`, `payroll_integration.py` and `payroll_gl.py`:
everything arrives as a dict, nothing writes, nothing imports `frappe`.

The policy is a `Labor Break Policy` record read into a dict before it reaches
here. The events are `Farm Shift Compliance Event` rows — each one with at
least `event_type`, `event_datetime`, `break_kind`, `ended_at`,
`duration_minutes`, `applies_to` and `employee`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

# ── helpers ───────────────────────────────────────────────────────────────────


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _parse_dt(value) -> _dt.datetime | None:
    if isinstance(value, _dt.datetime):
        return value
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _hours_between(start, end) -> float:
    a, b = _parse_dt(start), _parse_dt(end)
    if a is None or b is None:
        return 0.0
    return max((b - a).total_seconds() / 3600, 0.0)


def _schedule_rows(policy: dict, key: str) -> list[dict]:
    return policy.get(key) or []


def _checked(value) -> bool:
    return value in (1, "1", True)


# ── 1. entitlement ──────────────────────────────────────────────────────────


def entitlement(hours_worked: float, policy: dict) -> dict:
    """How many rest and meal periods are owed for this many hours.

    Walks the schedule rows and picks the band whose range contains
    `hours_worked`. Returns the HIGHEST matching band — a 10-hour shift owes
    everything a 6-hour shift owes plus more.
    """
    rest = _best_band(hours_worked, _schedule_rows(policy, "rest_schedule"))
    meal = _best_band(hours_worked, _schedule_rows(policy, "meal_schedule"))
    return {
        "rest_periods": rest.get("periods_owed", 0) if rest else 0,
        "rest_minutes": (rest["periods_owed"] * rest["minutes_each"]) if rest else 0,
        "meal_periods": meal.get("periods_owed", 0) if meal else 0,
        "meal_minutes": (meal["periods_owed"] * meal["minutes_each"]) if meal else 0,
    }


def _best_band(hours: float, rows: list[dict]) -> dict | None:
    best = None
    for row in rows:
        lo = _as_float(row.get("hours_from"))
        hi = _as_float(row.get("hours_to"))
        if lo <= hours <= hi:
            if best is None or _as_float(row.get("periods_owed")) > _as_float(best.get("periods_owed")):
                best = row
        elif lo <= hours and hi < hours:
            if best is None or _as_float(row.get("periods_owed")) > _as_float(best.get("periods_owed")):
                best = row
    return best


# ── 2. heat_entitlement ─────────────────────────────────────────────────────


def heat_entitlement(hours_worked: float, weather_timeline: list[dict], policy: dict) -> dict:
    """How many heat cool-down periods are owed given the weather timeline.

    Walks the heat schedule rows and finds the highest heat index in the
    timeline. Returns the cool-down obligation for that band.
    """
    if not weather_timeline:
        return {"cool_down_periods": 0, "cool_down_minutes": 0, "concurrent_with_rest": False}

    max_hi = max(_as_float(r.get("heat_index_f")) for r in weather_timeline)
    heat_rows = _schedule_rows(policy, "heat_schedule")
    best = None
    for row in heat_rows:
        lo = _as_float(row.get("heat_index_from"))
        hi = _as_float(row.get("heat_index_to"))
        if lo <= max_hi <= hi:
            best = row
        elif lo <= max_hi and hi < max_hi:
            if best is None or _as_float(row.get("minutes_each")) > _as_float(
                (best or {}).get("minutes_each")
            ):
                best = row

    if not best:
        return {"cool_down_periods": 0, "cool_down_minutes": 0, "concurrent_with_rest": False}

    every = _as_float(best.get("every_hours"), 1.0)
    periods = max(int(hours_worked / every), 0) if every > 0 else 0
    return {
        "cool_down_periods": periods,
        "cool_down_minutes": periods * int(_as_float(best.get("minutes_each"))),
        "concurrent_with_rest": _checked(best.get("concurrent_with_rest")),
        "heat_index": max_hi,
    }


# ── 3. overlap_minutes ─────────────────────────────────────────────────────


def overlap_minutes(event: dict, segment_start, segment_end) -> float:
    """Minutes of overlap between a break event's window and a worker's segment.

    The event window is [event_datetime, event_datetime + duration_minutes].
    The segment is [segment_start, segment_end].
    """
    ev_start = _parse_dt(event.get("event_datetime"))
    dur = _as_float(event.get("duration_minutes"))
    if ev_start is None or dur <= 0:
        return 0.0
    ev_end_dt = _parse_dt(event.get("ended_at"))
    if ev_end_dt is not None:
        ev_end = ev_end_dt
    else:
        ev_end = ev_start + _dt.timedelta(minutes=dur)

    seg_s = _parse_dt(segment_start)
    seg_e = _parse_dt(segment_end)
    if seg_s is None or seg_e is None:
        return 0.0

    overlap_start = max(ev_start, seg_s)
    overlap_end = min(ev_end, seg_e)
    if overlap_start >= overlap_end:
        return 0.0
    return (overlap_end - overlap_start).total_seconds() / 60.0


# ── 4. worker_breaks ────────────────────────────────────────────────────────


def worker_breaks(segment: dict, events: list[dict], policy: dict) -> dict:
    """Break hours for one worker on one shift segment.

    `segment` has `joined_at` and `left_at` (or the shift's start/end).
    `events` are all compliance events on the shift with a `break_kind` set.
    `policy` is the Labor Break Policy dict.

    Returns paid_break_hours, unpaid_break_hours, rest/meal taken/owed, shortfall.
    """
    seg_start = segment.get("joined_at") or segment.get("start_datetime")
    seg_end = segment.get("left_at") or segment.get("end_datetime")
    hours_worked = _hours_between(seg_start, seg_end)

    ent = entitlement(hours_worked, policy)

    paid_minutes = 0.0
    unpaid_minutes = 0.0
    rest_taken = 0
    meal_taken = 0
    cool_down_minutes = 0.0

    employee = segment.get("employee")

    for ev in events:
        kind = ev.get("break_kind") or ""
        if not kind:
            continue
        applies = ev.get("applies_to") or "Crew"
        if applies == "Individual" and ev.get("employee") != employee:
            continue

        mins = overlap_minutes(ev, seg_start, seg_end)
        if mins <= 0:
            continue

        if kind == "Paid Rest":
            paid_minutes += mins
            rest_taken += 1
        elif kind == "Unpaid Meal":
            unpaid_minutes += mins
            meal_taken += 1
        elif kind == "Cool-Down":
            cool_down_minutes += mins

    # Cool-down concurrent logic: if the policy says cool-downs run concurrently
    # with rest, the cool-down minutes do NOT add to paid_minutes when a rest
    # period also covers that time. We count them as paid rest only when they
    # are NOT concurrent, or when there aren't enough rest periods to absorb them.
    heat_ent = heat_entitlement(hours_worked, segment.get("weather_timeline") or [], policy)
    if heat_ent.get("concurrent_with_rest") and rest_taken > 0:
        # Cool-downs absorbed by rest periods — already counted as Paid Rest
        pass
    else:
        paid_minutes += cool_down_minutes

    paid_break_hours = round(paid_minutes / 60, 4)
    unpaid_break_hours = round(unpaid_minutes / 60, 4)

    rest_owed = ent["rest_periods"]
    meal_owed = ent["meal_periods"]
    rest_short = max(rest_owed - rest_taken, 0)
    meal_short = max(meal_owed - meal_taken, 0)
    shortfall_minutes = rest_short * (ent["rest_minutes"] / max(rest_owed, 1)) + meal_short * (
        ent["meal_minutes"] / max(meal_owed, 1)
    )

    return {
        "paid_break_hours": paid_break_hours,
        "unpaid_break_hours": unpaid_break_hours,
        "rest_taken": rest_taken,
        "rest_owed": rest_owed,
        "meal_taken": meal_taken,
        "meal_owed": meal_owed,
        "shortfall_minutes": round(shortfall_minutes, 1),
    }


# ── 5. crew_reconciliation ─────────────────────────────────────────────────


def crew_reconciliation(shift: dict, crew: list[dict], events: list[dict], policy: dict) -> dict:
    """Which workers on this shift are short of their break entitlement."""
    break_events = [ev for ev in events if ev.get("break_kind")]
    workers_short = []
    for member in crew:
        seg = {
            "employee": member.get("employee"),
            "joined_at": member.get("joined_at") or shift.get("start_datetime"),
            "left_at": member.get("left_at") or shift.get("end_datetime"),
        }
        wb = worker_breaks(seg, break_events, policy)
        if wb["rest_taken"] < wb["rest_owed"] or wb["meal_taken"] < wb["meal_owed"]:
            workers_short.append(
                {
                    "employee": member.get("employee"),
                    "employee_name": member.get("employee_name") or member.get("employee"),
                    "rest_owed": wb["rest_owed"],
                    "rest_taken": wb["rest_taken"],
                    "meal_owed": wb["meal_owed"],
                    "meal_taken": wb["meal_taken"],
                    "shortfall_minutes": wb["shortfall_minutes"],
                }
            )
    return {"workers_short": workers_short}


# ── 6. next_break_due ──────────────────────────────────────────────────────


def next_break_due(
    now: Any,
    segment_start: Any,
    events: list[dict],
    policy: dict,
    heat_index: float | None = None,
) -> dict:
    """What break is due next, for the break coach.

    Returns the kind, when it is due, and how many minutes until then.
    """
    now_dt = _parse_dt(now)
    seg_start = _parse_dt(segment_start)
    if now_dt is None or seg_start is None:
        return {"due": None}

    hours_so_far = (now_dt - seg_start).total_seconds() / 3600
    ent = entitlement(hours_so_far + 1.0, policy)

    break_events = [ev for ev in events if ev.get("break_kind")]
    rest_taken = sum(1 for ev in break_events if ev.get("break_kind") == "Paid Rest")
    meal_taken = sum(1 for ev in break_events if ev.get("break_kind") == "Unpaid Meal")

    max_without = _as_float(policy.get("max_hours_without_rest")) if policy.get("max_hours_without_rest") else None

    # Check WA-style max hours without rest
    if max_without is not None:
        last_rest_dt = None
        for ev in break_events:
            if ev.get("break_kind") == "Paid Rest":
                dt = _parse_dt(ev.get("event_datetime"))
                if dt and (last_rest_dt is None or dt > last_rest_dt):
                    last_rest_dt = dt
        since = last_rest_dt or seg_start
        hours_since_rest = (now_dt - since).total_seconds() / 3600
        remaining = max_without - hours_since_rest
        if remaining <= 0.5:
            return {
                "due": "Paid Rest",
                "urgency": "overdue" if remaining <= 0 else "imminent",
                "minutes_until": round(remaining * 60, 0),
                "reason": f"WA: no more than {max_without:.0f} hours without a rest period",
            }

    # Check heat cool-down
    if heat_index is not None and heat_index >= 90:
        heat_rows = _schedule_rows(policy, "heat_schedule")
        for row in heat_rows:
            if _as_float(row.get("heat_index_from")) <= heat_index <= _as_float(row.get("heat_index_to")):
                every = _as_float(row.get("every_hours"), 2.0)
                last_cd_dt = None
                for ev in break_events:
                    if ev.get("break_kind") == "Cool-Down":
                        dt = _parse_dt(ev.get("event_datetime"))
                        if dt and (last_cd_dt is None or dt > last_cd_dt):
                            last_cd_dt = dt
                since = last_cd_dt or seg_start
                hours_since = (now_dt - since).total_seconds() / 3600
                if hours_since >= every * 0.75:
                    return {
                        "due": "Cool-Down",
                        "urgency": "overdue" if hours_since >= every else "soon",
                        "minutes_until": round(max(every - hours_since, 0) * 60, 0),
                        "minutes_each": int(_as_float(row.get("minutes_each"))),
                    }
                break

    # Check if rest or meal is due based on entitlement
    if rest_taken < ent["rest_periods"]:
        return {
            "due": "Paid Rest",
            "urgency": "upcoming",
            "rest_owed": ent["rest_periods"],
            "rest_taken": rest_taken,
        }
    if meal_taken < ent["meal_periods"]:
        return {
            "due": "Unpaid Meal",
            "urgency": "upcoming",
            "meal_owed": ent["meal_periods"],
            "meal_taken": meal_taken,
        }

    return {"due": None}
