# SPDX-License-Identifier: MIT
"""Payroll → Journal Entry. PURE FUNCTIONS.

No database reads, no side effects, fully testable with deterministic inputs.
Same contract as `payroll_calc.py` and `payroll_integration.py`: everything
arrives as an argument.

v0.40.0. THE GAP THIS CLOSES. v0.30.0 computed a payroll slip, v0.35.0 fed it
the hours off the shift register, and v0.36.0 drew the tax forms — and none of
it reached the ledger. A completed payroll run left Farm Payroll Slips holding
gross pay, withholding and net pay, and a general ledger that had never heard
of any of it. The wages were the largest number on the farm's income statement
and the only one somebody had to key in twice.

────────────────────────────────────────────────────────────────────────────
A COMPONENT IS THE UNIT, AND THE ACCOUNT NAMES ARE NOT IN THIS FILE
────────────────────────────────────────────────────────────────────────────

Eleven components — the things a payroll slip is made of — and each one knows
which SIDE it lands on and nothing about which account. "Gross Pay is a debit"
is a fact about double-entry bookkeeping and belongs in code. "Gross Pay is a
debit to `5300 - Field Labor - ETC`" is a fact about one farm's chart of
accounts and belongs in a record, which is `Farm Payroll Account Mapping`.

A shipped default account name would be worse than no default: it would be
right for the site it was written on and silently wrong everywhere else, and
"silently wrong" in a chart of accounts means a year of wages in the wrong
expense line that nobody notices until the tax preparer asks.

────────────────────────────────────────────────────────────────────────────
WHY THE EMPLOYER SIDE IS FIVE COMPONENTS AND NOT ONE
────────────────────────────────────────────────────────────────────────────

Employee withholding is money the farm is HOLDING — gross pay is already the
expense, and the withheld amounts are liabilities carved out of it. Employer
taxes are money the farm OWES ON TOP, so each of them is an expense AND a
liability: two lines, both from the one component.

They stay five separate components because they are remitted to five different
places on five different schedules. Employer Social Security and Medicare go to
the IRS on the 941 with the withheld amounts; FUTA goes to the IRS on the 940
annually; state unemployment goes to the state agency quarterly; and Paid Leave
Oregon, workers' compensation, WA PFML and L&I go elsewhere again. A single
"payroll tax payable" account would make every one of those reconciliations a
spreadsheet exercise. A farm that genuinely wants them in one account can point
five components at one account, which is a decision it has made rather than one
this app made for it.

`State Employer Other` is the eleventh, and it is the one the specification for
this release did not name. It exists because `state_withholding.py` computes
employer amounts that are NOT unemployment insurance — Paid Leave Oregon's
employer share, Oregon workers' compensation, Washington PFML's employer share
and Washington L&I's employer rate — and a mapping with nowhere to put them
would drop real money out of the books quietly. Dropping it loudly is not an
option either; it is a cost the farm has incurred.

────────────────────────────────────────────────────────────────────────────
IT BUILDS ENTRIES. IT DOES NOT POST THEM, AND IT CANNOT.
────────────────────────────────────────────────────────────────────────────

Every function here returns a dict. Nothing in this module can write anything,
which is why the balance check is worth doing here as well as in ERPNext: an
imbalance found in a pure function is an error message, and the same imbalance
found on submit is a broken draft somebody has to work out the provenance of.

The tools that use this create DRAFT Journal Entries and stop. Posting payroll
is a statement about money leaving the farm, and it wants the same human in
front of it that `submit_journal_entry` wants.
"""
from __future__ import annotations

#: Rounding tolerance for the double-entry check. The same half-cent
#: `tools/mutate.py` uses, and for the same reason: below the smallest tracked
#: amount and above float noise.
BALANCE_TOLERANCE = 0.005

#: The eleven components, in the order they appear on a journal entry and in the
#: mapping form. Each carries:
#:
#:   sides    — which account(s) the component needs. "debit", "credit", or both.
#:   keys     — the slip keys its amount is read from, in preference order.
#:   party    — Employee (withheld out of gross) or Employer (owed on top).
#:   label    — what it is called in a result and on a line's remark.
#:
#: `sides` is the validation rule as well as the posting rule: a component with
#: a nonzero amount and no account for one of its sides is a mapping that cannot
#: produce a balanced entry, and that is refused before anything is written.
COMPONENTS: tuple[dict, ...] = (
	{
		"component": "Gross Pay",
		"label": "Gross pay",
		"sides": ("debit",),
		"keys": ("gross_pay",),
		"party": "Employee",
		"about": "Total earnings before any deduction — the wage expense itself.",
	},
	{
		"component": "Federal Tax",
		"label": "Federal income tax withheld",
		"sides": ("credit",),
		"keys": ("federal_withholding", "federal_income_tax"),
		"party": "Employee",
		"about": "Withheld from the worker and owed to the IRS on the 941.",
	},
	{
		"component": "SS Employee",
		"label": "Social Security withheld (employee)",
		"sides": ("credit",),
		"keys": ("social_security", "social_security_employee"),
		"party": "Employee",
		"about": "The worker's 6.2% share, withheld and owed to the IRS.",
	},
	{
		"component": "SS Employer",
		"label": "Social Security (employer share)",
		"sides": ("debit", "credit"),
		"keys": ("social_security_employer",),
		"party": "Employer",
		"about": "The farm's matching 6.2%. An expense and a liability, not a deduction.",
	},
	{
		"component": "Medicare Employee",
		"label": "Medicare withheld (employee)",
		"sides": ("credit",),
		"keys": ("medicare", "medicare_employee"),
		"party": "Employee",
		"about": "The worker's 1.45% share plus any Additional Medicare, withheld.",
	},
	{
		"component": "Medicare Employer",
		"label": "Medicare (employer share)",
		"sides": ("debit", "credit"),
		"keys": ("medicare_employer",),
		"party": "Employer",
		"about": "The farm's matching 1.45%. Additional Medicare has no employer share.",
	},
	{
		"component": "FUTA",
		"label": "Federal unemployment (FUTA)",
		"sides": ("debit", "credit"),
		"keys": ("futa", "futa_employer"),
		"party": "Employer",
		"about": "Employer-only, on the first $7,000 per worker. Filed annually on the 940.",
	},
	{
		"component": "SUTA",
		"label": "State unemployment (SUTA)",
		"sides": ("debit", "credit"),
		"keys": ("state_unemployment", "suta"),
		"party": "Employer",
		"about": "Employer-only state unemployment insurance, at this employer's own rate.",
	},
	{
		"component": "State Tax",
		"label": "State tax withheld (employee)",
		"sides": ("credit",),
		"keys": ("state_withholding",),
		"party": "Employee",
		"about": (
			"Everything withheld from the worker by a state — Oregon income tax, the "
			"statewide transit tax, the employee share of Paid Leave Oregon, WA PFML, "
			"WA Cares and the WA L&I employee rate."
		),
	},
	{
		"component": "State Employer Other",
		"label": "Other state employer taxes",
		"sides": ("debit", "credit"),
		"keys": ("state_employer_other",),
		"party": "Employer",
		"about": (
			"State employer taxes that are not unemployment: Paid Leave Oregon's "
			"employer share, Oregon workers' compensation, WA PFML's employer share "
			"and the WA L&I employer rate."
		),
	},
	{
		"component": "Net Pay",
		"label": "Net pay",
		"sides": ("credit",),
		"keys": ("net_pay",),
		"party": "Employee",
		"about": "What the worker is actually paid — a payroll clearing, bank or cash account.",
	},
)

#: Component name → its definition. The lookup every other function uses.
COMPONENT_INDEX: dict[str, dict] = {row["component"]: row for row in COMPONENTS}

#: Component names in posting order.
COMPONENT_NAMES: tuple[str, ...] = tuple(row["component"] for row in COMPONENTS)

#: The employee-side components. Together they are the whole of gross pay:
#: the debit is gross, and the credits are every deduction plus what is left.
#: An entry missing one of these does not balance, which is why they are the
#: set `validate_mapping` insists on even where an amount happens to be zero.
CORE_COMPONENTS: tuple[str, ...] = tuple(
	row["component"] for row in COMPONENTS if row["party"] == "Employee"
)

#: The employer-side components. Each is self-balancing — one debit, one credit,
#: the same amount — so a mapping that has none of them still produces a valid
#: entry, just one that leaves the employer's own taxes off the books.
EMPLOYER_COMPONENTS: tuple[str, ...] = tuple(
	row["component"] for row in COMPONENTS if row["party"] == "Employer"
)

#: Posting modes. `per_employee` writes one Journal Entry per slip;
#: `consolidated` writes one for the whole run.
MODES = ("consolidated", "per_employee")


def _as_float(value, default: float = 0.0) -> float:
	try:
		if value is None or value == "":
			return default
		return float(value)
	except (TypeError, ValueError):
		return default


def _money(value: float) -> float:
	return round(_as_float(value) + 0.0, 2)


# ── Reading a slip ────────────────────────────────────────────────────────


def component_amounts(slip: dict) -> dict[str, float]:
	"""Every component's amount for one slip, keyed by component name.

	Each component names the slip keys it can be read from in preference order,
	so a slip written by `payroll_calc` (which says `social_security`) and one
	read back off a Farm Payroll Slip row (same field) and one hand-built by a
	test (which might say `social_security_employee`) all resolve to the same
	component. A key that is absent is 0.0 — not an error, because a Washington
	worker genuinely has no state income tax and a first-period worker genuinely
	has no wage-base exhaustion.
	"""
	amounts = {}
	for row in COMPONENTS:
		value = 0.0
		for key in row["keys"]:
			if key in slip and slip[key] not in (None, ""):
				value = _as_float(slip[key])
				break
		amounts[row["component"]] = _money(value)
	return amounts


def slip_is_empty(amounts: dict) -> bool:
	"""Whether a slip has nothing to post at all.

	A worker with no hours in the period produces a slip of zeros, and a zero
	journal entry is noise in the ledger rather than a record of anything. The
	slip is still reported — see `build_payroll_journal_entries`'s `skipped` —
	because a zero slip somebody did not expect is worth looking at.
	"""
	return all(abs(_as_float(value)) < 0.005 for value in (amounts or {}).values())


def employer_taxes_recorded(slip: dict) -> bool:
	"""Whether this slip carries the employer-side figures at all.

	Slips written before v0.40.0 have no employer tax fields — the engine
	computed them and the child row had nowhere to keep them. Posting such a run
	is not wrong, it is INCOMPLETE, and the difference matters enough to be
	reported rather than inferred from four zeros: an employer with no employer
	taxes is a real thing (a household employer below every threshold), and an
	entry that quietly left them out is not.
	"""
	for component in EMPLOYER_COMPONENTS:
		for key in COMPONENT_INDEX[component]["keys"]:
			if key in slip and slip[key] not in (None, ""):
				return True
	return False


# ── Reading a mapping ─────────────────────────────────────────────────────


def mapping_index(mapping) -> dict[str, dict]:
	"""The account mapping as {component: {"debit_account", "credit_account"}}.

	Accepts the three shapes a caller has: the child rows off a Farm Payroll
	Account Mapping document, a plain list of dicts, or an already-keyed dict.
	A row naming a component this app does not have is kept out rather than
	raising — an unknown component is reported by `validate_mapping` as
	`unrecognised`, which is a thing somebody can fix, while an exception in the
	middle of a payroll posting is not.
	"""
	index: dict[str, dict] = {}
	rows: list[dict] = []

	if isinstance(mapping, dict):
		for key, value in mapping.items():
			if isinstance(value, dict):
				row = dict(value)
				row.setdefault("component", key)
				rows.append(row)
			elif isinstance(value, str):
				# {"Gross Pay": "5300 - Field Labor"} — one account, on whichever
				# side the component takes. Only unambiguous for a one-sided
				# component, so a two-sided one gets it on both and is corrected
				# by validation if that is not what was meant.
				rows.append({"component": key, "account": value})
	else:
		for row in mapping or []:
			if isinstance(row, dict):
				rows.append(dict(row))
			else:
				rows.append({
					"component": getattr(row, "component", ""),
					"debit_account": getattr(row, "debit_account", ""),
					"credit_account": getattr(row, "credit_account", ""),
				})

	for row in rows:
		component = str(row.get("component") or "").strip()
		if component not in COMPONENT_INDEX:
			continue
		single = str(row.get("account") or "").strip()
		debit = str(row.get("debit_account") or "").strip() or single
		credit = str(row.get("credit_account") or "").strip() or single
		index[component] = {
			"debit_account": debit,
			"credit_account": credit,
		}
	return index


def unrecognised_components(mapping) -> list[str]:
	"""Component names in a mapping that this app has never heard of.

	A typo in a component name produces a row that maps nothing and refuses
	nothing, so it is named rather than dropped.
	"""
	found = []
	rows = mapping.items() if isinstance(mapping, dict) else [
		(row.get("component") if isinstance(row, dict) else getattr(row, "component", ""), None)
		for row in (mapping or [])
	]
	for component, _value in rows:
		name = str(component or "").strip()
		if name and name not in COMPONENT_INDEX:
			found.append(name)
	return sorted(set(found))


def validate_mapping(
	mapping,
	amounts: dict | None = None,
	include_employer: bool = True,
) -> dict:
	"""Whether this mapping can produce a balanced entry for these amounts.

	Two different questions, answered together:

	COULD IT BALANCE. The six employee-side components are required whatever the
	amounts are, because they are the two sides of gross pay. A mapping missing
	`Net Pay` cannot post a payroll however small the payroll is.

	IS IT COMPLETE FOR THIS RUN. An employer-side component is required only
	where the run actually has an amount for it. A farm with no state
	unemployment liability this period should not have to invent a SUTA account
	to post its payroll, and a farm with one should not be allowed to post
	without naming where it goes.

	Args:
		mapping: Child rows, a list of dicts, or a keyed dict.
		amounts: Component totals for the run. None means "check the structure
			only", which is what `get_payroll_account_mapping` asks for.
		include_employer: False drops the employer components entirely — a run
			posting only the wage half.

	Returns:
		Dict with `complete`, `missing` (component + side + why), `mapped`,
		`unmapped_with_amounts`, and `unrecognised`.
	"""
	index = mapping_index(mapping)
	missing = []
	required = list(CORE_COMPONENTS)
	if include_employer:
		required += list(EMPLOYER_COMPONENTS)

	for component in COMPONENT_NAMES:
		if component not in required:
			continue
		definition = COMPONENT_INDEX[component]
		amount = None if amounts is None else _as_float((amounts or {}).get(component))
		optional = definition["party"] == "Employer"
		if optional and (amounts is None or abs(amount or 0.0) < 0.005):
			# Nothing to post for it — this run has no amount, or no run was
			# named at all. Not a gap either way: an employer-side component is
			# required by the money, and asking a farm with no state
			# unemployment liability to invent a SUTA account before it can run
			# payroll would be a refusal about nothing.
			continue

		entry = index.get(component) or {}
		for side in definition["sides"]:
			account = entry.get(f"{side}_account")
			if account:
				continue
			missing.append({
				"component": component,
				"side": side,
				"account_field": f"{side}_account",
				"amount": amount,
				"why": (
					f"{definition['label']} needs a {side} account. {definition['about']}"
				),
			})

	unmapped_with_amounts = []
	if amounts:
		for component, amount in amounts.items():
			if abs(_as_float(amount)) < 0.005:
				continue
			if component not in index:
				unmapped_with_amounts.append({
					"component": component,
					"amount": _money(amount),
				})

	return {
		"complete": not missing,
		"missing": missing,
		"mapped": sorted(index),
		"unmapped_with_amounts": unmapped_with_amounts,
		"unrecognised": unrecognised_components(mapping),
		"required": required,
	}


# ── Building lines ────────────────────────────────────────────────────────


def _line_key(account: str, side: str) -> tuple:
	return (account, side)


def journal_lines(
	amounts: dict,
	mapping,
	cost_center: str = "",
	include_employer: bool = True,
) -> tuple[list[dict], list[dict], list[dict]]:
	"""Component amounts and a mapping in; Journal Entry Account rows out.

	LINES ON THE SAME ACCOUNT AND THE SAME SIDE ARE MERGED, and that is
	deliberate rather than tidiness. The ordinary mapping sends employee Social
	Security and employee Medicare to one FICA payable account and employer
	Social Security and employer Medicare to one payroll tax expense; leaving
	them as separate lines would produce a six-line employer section where the
	books want two, and every one of those lines would reconcile to the same
	balance anyway. What each merged line was BUILT FROM survives on the line's
	`user_remark` and in the returned breakdown, so nothing is lost — the
	arithmetic is just done here instead of by whoever reads the entry.

	A component whose amount is zero produces no line. ERPNext refuses a row
	with a zero on both sides, and a zero line is not a fact about anything.

	Returns:
		(lines, breakdown, skipped) — the Journal Entry Account rows, one row per
		component that contributed with the accounts it went to, and the
		components that had an amount and nowhere to put it.
	"""
	index = mapping_index(mapping)
	merged: dict[tuple, dict] = {}
	order: list[tuple] = []
	breakdown: list[dict] = []
	skipped: list[dict] = []

	for component in COMPONENT_NAMES:
		definition = COMPONENT_INDEX[component]
		if not include_employer and definition["party"] == "Employer":
			continue
		amount = _money((amounts or {}).get(component))
		if abs(amount) < 0.005:
			continue

		entry = index.get(component)
		if not entry:
			skipped.append({
				"component": component,
				"amount": amount,
				"why": f"{definition['label']} has no account mapped for this company.",
			})
			continue

		placed = {}
		for side in definition["sides"]:
			account = entry.get(f"{side}_account")
			if not account:
				skipped.append({
					"component": component,
					"amount": amount,
					"side": side,
					"why": f"{definition['label']} has no {side} account mapped.",
				})
				continue
			key = _line_key(account, side)
			if key not in merged:
				merged[key] = {
					"account": account,
					"side": side,
					"amount": 0.0,
					"components": [],
				}
				order.append(key)
			merged[key]["amount"] = round(merged[key]["amount"] + amount, 2)
			merged[key]["components"].append(component)
			placed[side] = account

		if placed:
			breakdown.append({
				"component": component,
				"label": definition["label"],
				"party": definition["party"],
				"amount": amount,
				"debit_account": placed.get("debit", ""),
				"credit_account": placed.get("credit", ""),
			})

	lines = []
	for key in order:
		row = merged[key]
		amount = round(row["amount"], 2)
		if abs(amount) < 0.005:
			continue
		line = {"account": row["account"]}
		line[row["side"]] = amount
		line["user_remark"] = ", ".join(
			COMPONENT_INDEX[name]["label"] for name in row["components"]
		)
		if cost_center:
			line["cost_center"] = cost_center
		lines.append(line)

	return lines, breakdown, skipped


def check_balance(lines: list[dict]) -> dict:
	"""Total debits against total credits, to the half cent."""
	total_debit = round(sum(_as_float(line.get("debit")) for line in lines or []), 2)
	total_credit = round(sum(_as_float(line.get("credit")) for line in lines or []), 2)
	difference = round(total_debit - total_credit, 2)
	return {
		"total_debit": total_debit,
		"total_credit": total_credit,
		"difference": difference,
		"balanced": abs(difference) <= BALANCE_TOLERANCE,
	}


# ── Building entries ──────────────────────────────────────────────────────


def _period_text(slip: dict) -> str:
	start = str(slip.get("pay_period_start") or "").strip()
	end = str(slip.get("pay_period_end") or "").strip()
	if start and end:
		return f"{start} to {end}"
	return start or end or ""


def slip_remark(slip: dict, payroll_entry: str = "", slip_name: str = "") -> str:
	"""What one employee's entry says about where it came from.

	Every entry names the Farm Payroll Slip behind it, because the question
	somebody asks of a payroll journal entry six months later is "which run, and
	whose hours" — and an entry that answers it is the difference between a
	reconciliation and an investigation.
	"""
	employee = str(slip.get("employee_name") or slip.get("employee") or "").strip()
	docname = str(slip.get("employee") or "").strip()
	who = f"{employee} ({docname})" if employee and docname and employee != docname else (employee or docname)
	period = _period_text(slip)

	parts = ["Payroll"]
	if payroll_entry:
		parts.append(str(payroll_entry))
	if who:
		parts.append(f"— {who}")
	text = " ".join(parts)
	if period:
		text += f", {period}"
	text += (
		f". Gross {_money(slip.get('gross_pay')):.2f}, "
		f"deductions {_money(slip.get('total_deductions')):.2f}, "
		f"net {_money(slip.get('net_pay')):.2f}."
	)
	if slip_name:
		text += f" Farm Payroll Slip {slip_name}."
	else:
		text += " Source: Farm Payroll Slip."
	return text


def run_remark(slips: list[dict], payroll_entry: str = "", totals: dict | None = None) -> str:
	"""What a consolidated entry says about the run behind it."""
	period = ""
	for slip in slips or []:
		period = _period_text(slip)
		if period:
			break
	count = len(slips or [])
	gross = (totals or {}).get("Gross Pay")
	if gross is None:
		gross = sum(_money(slip.get("gross_pay")) for slip in slips or [])
	net = (totals or {}).get("Net Pay")
	if net is None:
		net = sum(_money(slip.get("net_pay")) for slip in slips or [])

	parts = ["Payroll"]
	if payroll_entry:
		parts.append(str(payroll_entry))
	text = " ".join(parts)
	if period:
		text += f", {period}"
	text += (
		f". {count} employee(s), gross {_money(gross):.2f}, net {_money(net):.2f}. "
		"Consolidated from the run's Farm Payroll Slips."
	)
	return text


def build_journal_entry(
	slip: dict,
	mapping,
	posting_date: str = "",
	company: str = "",
	payroll_entry: str = "",
	slip_name: str = "",
	cost_center: str = "",
	include_employer: bool = True,
) -> dict:
	"""One employee's slip as one Journal Entry structure.

	Returns the entry whether or not it balances, with `balanced` on it. A caller
	that writes an unbalanced entry has ignored a field that says so, which is a
	different and more findable failure than a function that raised somewhere
	inside a loop over forty people.
	"""
	amounts = component_amounts(slip)
	lines, breakdown, unmapped = journal_lines(
		amounts, mapping, cost_center=cost_center, include_employer=include_employer,
	)
	balance = check_balance(lines)

	entry = {
		"employee": slip.get("employee", ""),
		"employee_name": slip.get("employee_name", ""),
		"company": company or slip.get("company", ""),
		"posting_date": str(posting_date or slip.get("pay_period_end") or ""),
		"user_remark": slip_remark(slip, payroll_entry, slip_name),
		"accounts": lines,
		"line_count": len(lines),
		"components": breakdown,
		"amounts": amounts,
		"unmapped_components": unmapped,
		"payroll_entry": payroll_entry,
		"payroll_slip": slip_name,
		"employer_taxes_recorded": employer_taxes_recorded(slip),
		"warnings": _slip_warnings(slip, amounts, include_employer),
	}
	entry.update(balance)
	return entry


def _slip_warnings(slip: dict, amounts: dict, include_employer: bool) -> list[str]:
	"""What is worth saying about this slip before it becomes an entry."""
	warnings = []
	who = str(slip.get("employee_name") or slip.get("employee") or "this slip")
	gross = _money(amounts.get("Gross Pay"))

	if abs(gross) < 0.005:
		warnings.append(
			f"{who} has zero gross pay. Nothing about that is refused — a salaried "
			"employee on unpaid leave and a picker with no shifts both produce one — "
			"but a zero in a run nobody expected zeros in is worth opening."
		)
	if gross < 0:
		warnings.append(
			f"{who} has NEGATIVE gross pay ({gross}). A correction booked as a negative "
			"slip posts as a reversed entry; check that is what was meant."
		)
	if include_employer and gross > 0 and not employer_taxes_recorded(slip):
		warnings.append(
			f"{who} carries no employer tax figures, so none are posted. Slips written "
			"before v0.40.0 did not store them — the engine computed them and the record "
			"had nowhere to keep them. Re-running the period with run_payroll_for_period "
			"records them; posting as-is books the wages and leaves the employer's own "
			"taxes off the ledger."
		)
	if slip.get("minimum_wage_check") is False:
		warnings.append(
			f"{who} did not meet minimum wage in at least one state. The slip posts as "
			"computed — this module changes no amount — but the underlying pay is a wage "
			"claim waiting to happen."
		)
	makeup = _as_float(slip.get("minimum_wage_makeup"))
	if makeup > 0:
		warnings.append(
			f"{who} was paid ${_money(makeup)} of minimum wage makeup: what the work earned "
			"did not reach what the hours are owed, and gross was raised to the floor. The "
			"posting is correct and the whole of gross is wages — this is not a warning "
			"about the entry. It is a warning about the rate that produced it."
		)
	return warnings


def consolidate_amounts(slips: list[dict]) -> dict[str, float]:
	"""Every component summed across a whole run."""
	totals = {component: 0.0 for component in COMPONENT_NAMES}
	for slip in slips or []:
		for component, amount in component_amounts(slip).items():
			totals[component] = round(totals[component] + _as_float(amount), 2)
	return {component: _money(amount) for component, amount in totals.items()}


def build_consolidated_journal_entry(
	slips: list[dict],
	mapping,
	posting_date: str = "",
	company: str = "",
	payroll_entry: str = "",
	cost_center: str = "",
	include_employer: bool = True,
) -> dict:
	"""A whole run as ONE Journal Entry.

	The default mode, and the right one for most farms: a forty-person biweekly
	payroll is forty journal entries in per-employee mode and one here, and the
	general ledger does not want forty. Per-employee earns its keep where labour
	is costed by person or where a slip has to be reversed on its own; the run's
	own record keeps the per-person detail either way.
	"""
	amounts = consolidate_amounts(slips)
	lines, breakdown, unmapped = journal_lines(
		amounts, mapping, cost_center=cost_center, include_employer=include_employer,
	)
	balance = check_balance(lines)

	period_end = ""
	for slip in slips or []:
		period_end = str(slip.get("pay_period_end") or "")
		if period_end:
			break

	warnings = []
	for slip in slips or []:
		warnings.extend(_slip_warnings(slip, component_amounts(slip), include_employer))

	entry = {
		"employee": "",
		"employee_name": "",
		"company": company,
		"posting_date": str(posting_date or period_end or ""),
		"user_remark": run_remark(slips, payroll_entry, amounts),
		"accounts": lines,
		"line_count": len(lines),
		"components": breakdown,
		"amounts": amounts,
		"unmapped_components": unmapped,
		"payroll_entry": payroll_entry,
		"payroll_slip": "",
		"employee_count": len(slips or []),
		"employees": [
			{
				"employee": slip.get("employee", ""),
				"employee_name": slip.get("employee_name", ""),
				"gross_pay": _money(slip.get("gross_pay")),
				"net_pay": _money(slip.get("net_pay")),
			}
			for slip in slips or []
		],
		"employer_taxes_recorded": any(employer_taxes_recorded(slip) for slip in slips or []),
		"warnings": warnings,
	}
	entry.update(balance)
	return entry


def build_payroll_journal_entries(
	slips: list[dict],
	mapping,
	posting_date: str = "",
	company: str = "",
	payroll_entry: str = "",
	mode: str = "consolidated",
	cost_center: str = "",
	include_employer: bool = True,
	slip_names: dict | None = None,
) -> dict:
	"""The whole run, as the Journal Entries it should become.

	The one entry point both `preview_payroll_gl` and `post_payroll_to_gl` call,
	so the thing previewed and the thing written cannot drift — the only
	difference between the two tools is whether anything is inserted afterwards.

	Args:
		slips: Slip dicts, from a Farm Payroll Entry's child rows or from
			`payroll_integration.run_integrated_payroll` directly.
		mapping: The company's account mapping.
		posting_date: Defaults to each slip's pay period end.
		company: The company every entry is booked in.
		payroll_entry: The Farm Payroll Entry docname, for the remarks.
		mode: `consolidated` (one entry) or `per_employee` (one each).
		cost_center: Set on every line where given.
		include_employer: False posts the wage half only.
		slip_names: Employee → Farm Payroll Slip child docname, for the remarks.

	Returns:
		Dict with `journal_entries`, `totals`, `balanced`, `warnings`, `skipped`
		and the validation verdict for the mapping.
	"""
	mode = (mode or "consolidated").strip().lower().replace("-", "_").replace(" ", "_")
	if mode in ("per_employee", "peremployee", "employee", "individual"):
		mode = "per_employee"
	elif mode in ("consolidated", "combined", "single", "one"):
		mode = "consolidated"
	else:
		mode = "consolidated"

	slips = list(slips or [])
	totals = consolidate_amounts(slips)
	validation = validate_mapping(mapping, totals, include_employer=include_employer)

	entries: list[dict] = []
	skipped: list[dict] = []
	warnings: list[str] = []

	if mode == "per_employee":
		for slip in slips:
			amounts = component_amounts(slip)
			if slip_is_empty(amounts):
				skipped.append({
					"employee": slip.get("employee", ""),
					"employee_name": slip.get("employee_name", ""),
					"why": "every component is zero, so there is nothing to post.",
				})
				warnings.extend(_slip_warnings(slip, amounts, include_employer))
				continue
			entries.append(build_journal_entry(
				slip,
				mapping,
				posting_date=posting_date,
				company=company,
				payroll_entry=payroll_entry,
				slip_name=(slip_names or {}).get(slip.get("employee", ""), ""),
				cost_center=cost_center,
				include_employer=include_employer,
			))
	else:
		payable = []
		for slip in slips:
			amounts = component_amounts(slip)
			if slip_is_empty(amounts):
				skipped.append({
					"employee": slip.get("employee", ""),
					"employee_name": slip.get("employee_name", ""),
					"why": "every component is zero, so it adds nothing to the entry.",
				})
				warnings.extend(_slip_warnings(slip, amounts, include_employer))
				continue
			payable.append(slip)
		if payable:
			entries.append(build_consolidated_journal_entry(
				payable,
				mapping,
				posting_date=posting_date,
				company=company,
				payroll_entry=payroll_entry,
				cost_center=cost_center,
				include_employer=include_employer,
			))

	for entry in entries:
		warnings.extend(entry.get("warnings") or [])

	# Deduplicated in order: a run of forty pre-v0.40.0 slips produces forty
	# copies of the same sentence about employer taxes, and forty copies of one
	# sentence is how somebody stops reading the warnings.
	seen = set()
	unique_warnings = []
	for warning in warnings:
		if warning in seen:
			continue
		seen.add(warning)
		unique_warnings.append(warning)

	total_debit = round(sum(entry["total_debit"] for entry in entries), 2)
	total_credit = round(sum(entry["total_credit"] for entry in entries), 2)
	unbalanced = [
		{
			"employee": entry.get("employee", ""),
			"employee_name": entry.get("employee_name", ""),
			"difference": entry["difference"],
		}
		for entry in entries if not entry["balanced"]
	]

	return {
		"mode": mode,
		"company": company,
		"payroll_entry": payroll_entry,
		"posting_date": str(posting_date or ""),
		"cost_center": cost_center,
		"include_employer": include_employer,
		"journal_entries": entries,
		"entry_count": len(entries),
		"totals": totals,
		"total_debit": total_debit,
		"total_credit": total_credit,
		"difference": round(total_debit - total_credit, 2),
		"balanced": not unbalanced,
		"unbalanced": unbalanced,
		"mapping": validation,
		"skipped": skipped,
		"warnings": unique_warnings,
	}


def describe_components() -> list[dict]:
	"""The eleven components as a caller reads them, for the mapping tools."""
	return [
		{
			"component": row["component"],
			"label": row["label"],
			"party": row["party"],
			"sides": list(row["sides"]),
			"about": row["about"],
			"required": row["component"] in CORE_COMPONENTS,
		}
		for row in COMPONENTS
	]
