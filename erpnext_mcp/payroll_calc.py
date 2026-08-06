# SPDX-License-Identifier: MIT
"""Payroll calculation engine — PURE FUNCTIONS.

No database reads, no side effects, fully testable with deterministic inputs.

v0.30.0. Ties the federal withholding engine (v0.28.0) and the state tax
engines (v0.29.0) into actual payroll: gross pay from hours and piece units,
overtime at 1.5x, break pay at the average piece-rate hourly, minimum wage
checks, and the full deduction stack.

MINIMUM WAGE (2025):
  Oregon: $14.70/hr standard, $13.70 non-urban, $15.95 Portland metro
  Washington: $16.66/hr

OVERTIME (both states, ag workers, fully phased):
  Oregon HB 4002: 40 hrs/wk threshold
  Washington SB 5172: 40 hrs/wk threshold
  1.5x the regular rate
"""
from __future__ import annotations

from .state_withholding import calculate_state_withholding
from .withholding import PERIODS_PER_YEAR, calculate_federal_withholding

MINIMUM_WAGE_RATES = {
	"OR": {"standard": 14.70, "non_urban": 13.70, "portland_metro": 15.95},
	"WA": {"standard": 16.66},
}

OT_MULTIPLIER = 1.5


def calculate_gross_pay(
	pay_type: str,
	base_rate: float,
	hours: float,
	overtime_hours: float,
	piece_units: float,
	break_hours: float = 0.0,
) -> dict:
	"""Calculate gross pay from raw inputs.

	For piece rate workers: gross = (units * rate) + break_pay + OT_pay.
	For hourly workers: gross = (regular_hours * rate) + (OT_hours * rate * 1.5).
	For salary: gross = base_rate (the periodic salary amount).

	Returns a dict with gross_pay and the components that built it.
	"""
	regular_hours = max(hours - overtime_hours, 0)

	if pay_type == "Piece Rate":
		piece_earnings = piece_units * base_rate
		piece_hours = hours - break_hours
		bp = calculate_break_pay(piece_earnings, piece_hours, break_hours)
		effective_rate = (piece_earnings / piece_hours) if piece_hours > 0 else 0.0
		ot = calculate_overtime(0, overtime_hours, effective_rate)
		gross = piece_earnings + bp + ot
		return {
			"gross_pay": round(gross, 2),
			"piece_earnings": round(piece_earnings, 2),
			"break_pay": round(bp, 2),
			"overtime_pay": round(ot, 2),
			"effective_hourly_rate": round(effective_rate, 2),
			"regular_hours": round(regular_hours, 2),
			"overtime_hours": round(overtime_hours, 2),
			"piece_units": piece_units,
			"piece_rate": base_rate,
			"pay_type": "Piece Rate",
		}

	elif pay_type == "Hourly":
		regular_pay = regular_hours * base_rate
		ot = calculate_overtime(0, overtime_hours, base_rate)
		gross = regular_pay + ot
		return {
			"gross_pay": round(gross, 2),
			"regular_pay": round(regular_pay, 2),
			"overtime_pay": round(ot, 2),
			"effective_hourly_rate": round(base_rate, 2),
			"regular_hours": round(regular_hours, 2),
			"overtime_hours": round(overtime_hours, 2),
			"pay_type": "Hourly",
		}

	else:  # Salary
		return {
			"gross_pay": round(base_rate, 2),
			"effective_hourly_rate": round(base_rate / hours, 2) if hours > 0 else 0.0,
			"regular_hours": round(hours, 2),
			"overtime_hours": 0.0,
			"pay_type": "Salary",
		}


def calculate_break_pay(
	piece_earnings: float,
	piece_hours: float,
	break_hours: float = 0.0,
) -> float:
	"""Break pay at the average piece-rate hourly.

	WA WAC 296-131-020 and OR similar: rest breaks for piece-rate workers
	are paid at the average hourly rate earned during the piece-rate period.
	"""
	if piece_hours <= 0 or break_hours <= 0:
		return 0.0
	avg_rate = piece_earnings / piece_hours
	return round(avg_rate * break_hours, 2)


def calculate_overtime(
	regular_pay: float,
	overtime_hours: float,
	effective_rate: float,
) -> float:
	"""Overtime pay at 1.5x the effective rate.

	Both OR (HB 4002) and WA (SB 5172) use a 40-hour threshold for ag
	workers, fully phased. The caller is responsible for splitting hours
	into regular and overtime at the 40-hour boundary.
	"""
	if overtime_hours <= 0:
		return 0.0
	return round(overtime_hours * effective_rate * OT_MULTIPLIER, 2)


def check_minimum_wage(
	gross_pay: float,
	total_hours: float,
	state: str,
	min_wage_rates: dict | None = None,
	region: str = "standard",
) -> dict:
	"""Check whether piece-rate or other pay meets minimum wage.

	Returns:
		Dict with meets_minimum_wage (bool), effective_hourly_rate, and
		the applicable minimum_wage for the state and region.
	"""
	rates = min_wage_rates or MINIMUM_WAGE_RATES
	state_rates = rates.get(state, {})
	min_wage = state_rates.get(region, state_rates.get("standard", 0.0))

	if total_hours <= 0:
		return {
			"meets_minimum_wage": True,
			"effective_hourly_rate": 0.0,
			"minimum_wage": min_wage,
			"state": state,
			"region": region,
		}

	effective_rate = gross_pay / total_hours
	return {
		"meets_minimum_wage": effective_rate >= min_wage,
		"effective_hourly_rate": round(effective_rate, 2),
		"minimum_wage": min_wage,
		"state": state,
		"region": region,
	}


def calculate_full_payroll(
	employee_data: dict,
	shifts: list[dict],
	salary_structure: dict,
	tax_config: dict,
) -> dict:
	"""Orchestrate a complete payroll slip for one employee.

	Aggregates shifts by state, calculates gross pay, runs the federal
	engine, FICA, and the appropriate state engine based on work_state.

	Args:
		employee_data: Dict with employee, employee_name, etc.
		shifts: List of shift dicts with work_state, hours, overtime_hours,
			piece_units, break_hours.
		salary_structure: Dict with pay_type, base_rate, name (docname).
		tax_config: Dict with w4_data, fica_config, federal_tax_table,
			pay_frequency, ytd_gross, ytd_ss_withheld, and per-state
			state_configs keyed by state code (e.g. {"OR": {...}, "WA": {...}})
			and state_tax_tables keyed by state code.

	Returns:
		Complete slip dict with gross, all deductions, and net.
	"""
	pay_type = salary_structure.get("pay_type", "Hourly")
	base_rate = float(salary_structure.get("base_rate", 0))
	pay_frequency = tax_config.get("pay_frequency", "Biweekly")

	# ── Aggregate shift data ──────────────────────────────────────────
	total_hours = 0.0
	regular_hours = 0.0
	overtime_hours = 0.0
	piece_units = 0.0
	break_hours = 0.0
	state_hours = {}

	for shift in shifts:
		h = float(shift.get("hours", 0))
		ot = float(shift.get("overtime_hours", 0))
		total_hours += h
		overtime_hours += ot
		regular_hours += max(h - ot, 0)
		piece_units += float(shift.get("piece_units", 0))
		break_hours += float(shift.get("break_hours", 0))

		ws = shift.get("work_state", "")
		if ws:
			entry = state_hours.setdefault(ws, {"hours": 0.0, "gross": 0.0})
			entry["hours"] += h

	# ── Gross pay ─────────────────────────────────────────────────────
	gross_result = calculate_gross_pay(
		pay_type, base_rate, total_hours, overtime_hours, piece_units, break_hours,
	)
	gross_pay = gross_result["gross_pay"]
	effective_rate = gross_result.get("effective_hourly_rate", 0.0)

	# ── Determine primary work state ─────────────────────────────────
	primary_state = ""
	if state_hours:
		primary_state = max(state_hours, key=lambda s: state_hours[s]["hours"])

	# ── Minimum wage check ────────────────────────────────────────────
	min_wage_result = check_minimum_wage(gross_pay, total_hours, primary_state)

	# ── Federal taxes ─────────────────────────────────────────────────
	w4_data = tax_config.get("w4_data", {})
	fica_config = tax_config.get("fica_config", {})
	federal_tax_table = tax_config.get("federal_tax_table", [])
	ytd_gross = float(tax_config.get("ytd_gross", 0))
	ytd_ss_withheld = float(tax_config.get("ytd_ss_withheld", 0))

	federal = calculate_federal_withholding(
		gross_pay, pay_frequency, w4_data, ytd_gross, ytd_ss_withheld,
		fica_config, federal_tax_table,
	)

	# ── State taxes — per-state allocation for cross-state workers ────
	state_configs = tax_config.get("state_configs", {})
	state_tax_tables = tax_config.get("state_tax_tables", {})
	filing_status = w4_data.get("filing_status", "Single")

	state_results = {}
	total_state_employee = 0.0
	total_state_employer = 0.0
	total_state_suta = 0.0

	if len(state_hours) <= 1:
		# Single-state: apply to full gross
		if primary_state and primary_state in state_configs:
			state_result = calculate_state_withholding(
				gross_pay, pay_frequency, primary_state, filing_status,
				state_configs[primary_state],
				state_tax_tables.get(primary_state),
				ytd_gross,
			)
			state_results[primary_state] = state_result
			ee_key = f"total_{primary_state.lower()}_employee"
			er_key = f"total_{primary_state.lower()}_employer"
			total_state_employee += state_result.get(ee_key, 0.0)
			total_state_employer += state_result.get(er_key, 0.0)
			total_state_suta += state_result.get("suta", 0.0)
	else:
		# Cross-state: allocate gross by hours proportion
		for ws, info in state_hours.items():
			if total_hours <= 0:
				continue
			proportion = info["hours"] / total_hours
			state_gross = round(gross_pay * proportion, 2)
			info["gross"] = state_gross

			if ws in state_configs:
				state_result = calculate_state_withholding(
					state_gross, pay_frequency, ws, filing_status,
					state_configs[ws],
					state_tax_tables.get(ws),
					ytd_gross,
				)
				state_results[ws] = state_result
				ee_key = f"total_{ws.lower()}_employee"
				er_key = f"total_{ws.lower()}_employer"
				total_state_employee += state_result.get(ee_key, 0.0)
				total_state_employer += state_result.get(er_key, 0.0)
				total_state_suta += state_result.get("suta", 0.0)

	total_state_employee = round(total_state_employee, 2)
	total_state_employer = round(total_state_employer, 2)
	total_state_suta = round(total_state_suta, 2)

	# ── Aggregate deductions ──────────────────────────────────────────
	federal_withholding = federal["federal_income_tax"]
	social_security = federal["social_security_employee"]
	medicare = federal["medicare_employee"] + federal["additional_medicare"]

	total_deductions = round(
		federal_withholding + social_security + medicare + total_state_employee, 2,
	)
	net_pay = round(gross_pay - total_deductions, 2)

	# ── Employer taxes ────────────────────────────────────────────────
	#
	# v0.40.0. COMPUTED SINCE v0.28.0 AND REPORTED ONLY IN `federal_detail`
	# UNTIL NOW, which meant the slip written to the database carried the
	# employee's deductions and none of what the employer owed on top of them.
	# The GL posting needs them as figures rather than as a nested breakdown,
	# and so does anybody asking what a worker actually costs — the employer
	# share of FICA alone is 7.65% of gross that never appeared on a slip.
	#
	# Nothing here is a new charge and no total moves: these are the same
	# numbers `calculate_federal_withholding` and the state engines already
	# returned, lifted to the top level where they can be stored.
	social_security_employer = federal["social_security_employer"]
	medicare_employer = federal["medicare_employer"]
	futa = federal["futa_employer"]
	state_employer_other = round(total_state_employer - total_state_suta, 2)
	total_employer_taxes = round(
		social_security_employer + medicare_employer + futa + total_state_employer, 2,
	)

	return {
		"employee": employee_data.get("employee", ""),
		"employee_name": employee_data.get("employee_name", ""),
		"salary_structure": salary_structure.get("name", ""),
		"pay_type": pay_type,
		"work_state": primary_state,
		"total_hours": round(total_hours, 2),
		"regular_hours": round(regular_hours, 2),
		"overtime_hours": round(overtime_hours, 2),
		"piece_units": round(piece_units, 2),
		"piece_rate": base_rate if pay_type == "Piece Rate" else 0,
		"gross_pay": gross_pay,
		"federal_withholding": federal_withholding,
		"state_withholding": total_state_employee,
		"social_security": social_security,
		"medicare": medicare,
		"state_taxes_detail": state_results,
		"total_deductions": total_deductions,
		"net_pay": net_pay,
		"social_security_employer": social_security_employer,
		"medicare_employer": medicare_employer,
		"futa": futa,
		"state_unemployment": total_state_suta,
		"state_employer_other": state_employer_other,
		"state_employer_taxes": total_state_employer,
		"total_employer_taxes": total_employer_taxes,
		"total_cost_of_employment": round(gross_pay + total_employer_taxes, 2),
		"minimum_wage_check": min_wage_result["meets_minimum_wage"],
		"effective_hourly_rate": effective_rate,
		"gross_detail": gross_result,
		"federal_detail": federal,
		"state_hours": state_hours,
	}
