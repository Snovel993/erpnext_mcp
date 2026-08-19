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
ONE COST CENTER IS A GUESS. THE HOURS ARE A RECORD.
────────────────────────────────────────────────────────────────────────────

v0.101.0. Until now every line of a payroll entry carried ONE cost center — the
one on the company's mapping — so a P&L by cost center said the whole fortnight's
wages happened wherever that mapping pointed. On a farm that is the only number
on the statement nobody can act on: labour is the largest cost a block carries
and the one the ledger knew least about.

The farm already records where the work happened. A Farm Task names its block
through the `location_doctype`/`location` pair, a `Field` names its Cost Center
(`link_field_to_cost_center`), and a Farm Task Assignment records the minutes.
So the split is not an estimate the accountant makes afterwards — it is the
foreman's own dispatch board, read back out.

THE SPLIT LANDS ON DEBITS AND ONLY ON DEBITS. Every component with a debit side
is an expense — gross pay is the wage expense and each employer component's debit
is a tax expense — and every credit is a liability: what is withheld, what is
owed, what is left to pay. A cost center on a liability is a dimension on a
balance sheet line, which answers no question anybody asks. So the debits split
and the credits keep the blanket cost center, and the entry still balances
because the split is exact.

EXACT MEANS LARGEST REMAINDER. Three blocks at a third of $1,000.00 is $333.33
three times and a cent unaccounted for, and an entry that is a cent out does not
post. The cent goes to the largest remainder, ties broken by position, so the
same run always produces the same entry.

WHAT WAS NOT ON A TASK IS NOT ON A BLOCK. An employee paid for eight hours who
was dispatched to two hours in Block 7 did not spend the day in Block 7, and
booking the whole wage there would overstate that block's labour by six hours.
The residual — paid time minus attributed time — goes to the blanket cost center,
which is the honest answer: this is the part the record does not place. Where a
slip carries no hours at all there is nothing to measure against, and the split
is the attributed time alone.

AN EMPLOYEE WITH NO TASK DATA POSTS EXACTLY AS BEFORE. That is the fallback and
it is also the whole compatibility story: a site that dispatches nothing produces
byte-identical entries to the ones it produced before this release.

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
CORE_COMPONENTS: tuple[str, ...] = tuple(row["component"] for row in COMPONENTS if row["party"] == "Employee")

#: The employer-side components. Each is self-balancing — one debit, one credit,
#: the same amount — so a mapping that has none of them still produces a valid
#: entry, just one that leaves the employer's own taxes off the books.
EMPLOYER_COMPONENTS: tuple[str, ...] = tuple(
	row["component"] for row in COMPONENTS if row["party"] == "Employer"
)

#: The components a cost center split has anything to say about: the ones with a
#: DEBIT side. Derived from `COMPONENTS` rather than listed, because the rule is
#: structural — in this table a debit is an expense and a credit is a liability,
#: for all eleven — and a hand-written list would go stale the first time a
#: twelfth component is added.
EXPENSE_COMPONENTS: tuple[str, ...] = tuple(row["component"] for row in COMPONENTS if "debit" in row["sides"])

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
				rows.append(
					{
						"component": getattr(row, "component", ""),
						"debit_account": getattr(row, "debit_account", ""),
						"credit_account": getattr(row, "credit_account", ""),
					}
				)

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
	rows = (
		mapping.items()
		if isinstance(mapping, dict)
		else [
			(row.get("component") if isinstance(row, dict) else getattr(row, "component", ""), None)
			for row in (mapping or [])
		]
	)
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
			missing.append(
				{
					"component": component,
					"side": side,
					"account_field": f"{side}_account",
					"amount": amount,
					"why": (f"{definition['label']} needs a {side} account. {definition['about']}"),
				}
			)

	unmapped_with_amounts = []
	if amounts:
		for component, amount in amounts.items():
			if abs(_as_float(amount)) < 0.005:
				continue
			if component not in index:
				unmapped_with_amounts.append(
					{
						"component": component,
						"amount": _money(amount),
					}
				)

	return {
		"complete": not missing,
		"missing": missing,
		"mapped": sorted(index),
		"unmapped_with_amounts": unmapped_with_amounts,
		"unrecognised": unrecognised_components(mapping),
		"required": required,
	}


# ── Splitting the expense across cost centers ─────────────────────────────


def cost_center_shares(
	block_minutes,
	paid_minutes: float = 0.0,
	fallback_cost_center: str = "",
) -> dict:
	"""One person's period as the proportions their wage should be split by.

	THE DENOMINATOR IS WHAT PAYROLL PAID FOR, not what the tasks add up to. A
	picker paid for eight hours with two hours of dispatched work in Block 7
	spent a quarter of the day in Block 7 and three quarters somewhere the
	record does not place; splitting by attributed time alone would book the
	whole wage to a block that saw two hours of it. So the shortfall becomes a
	share of its own on `fallback_cost_center` — the mapping's blanket cost
	center, or nothing at all where the mapping has none, which is the same
	un-dimensioned line this app posted before any of this existed.

	A row with minutes and no cost center is UNATTRIBUTED rather than dropped:
	a task on a block nobody pointed at a Cost Center is exactly as unplaced as
	a task nobody raised, and pretending otherwise would inflate every other
	block's share of the wage.

	Args:
		block_minutes: Rows of `{"cost_center", "minutes"}`, optionally with
			`block` and `task_count`, which are carried through for the report.
		paid_minutes: What payroll actually paid this person for, in minutes.
			Zero means "no hours on the slip", and then the attributed time is
			the whole of the split.
		fallback_cost_center: Where the unattributed remainder is booked.

	Returns:
		Dict with `shares` — the list `split_amount` takes, each row carrying
		`cost_center`, `minutes` and `share` — plus the minutes it was computed
		from, so a preview can show its own arithmetic.
	"""
	buckets: dict[str, dict] = {}
	order: list[str] = []
	unplaced = 0.0

	for row in block_minutes or []:
		row = row or {}
		minutes = max(_as_float(row.get("minutes")), 0.0)
		if minutes <= 0:
			continue
		center = str(row.get("cost_center") or "").strip()
		if not center:
			unplaced = round(unplaced + minutes, 2)
			continue
		if center not in buckets:
			buckets[center] = {"minutes": 0.0, "blocks": [], "task_count": 0}
			order.append(center)
		bucket = buckets[center]
		bucket["minutes"] = round(bucket["minutes"] + minutes, 2)
		bucket["task_count"] += int(_as_float(row.get("task_count"), 1.0)) or 1
		block = str(row.get("block") or "").strip()
		if block and block not in bucket["blocks"]:
			bucket["blocks"].append(block)

	attributed = round(sum(bucket["minutes"] for bucket in buckets.values()), 2)
	paid = max(_as_float(paid_minutes), 0.0)
	residual = round(paid - attributed, 2) if paid > 0 else 0.0
	if residual < 0.005:
		residual = 0.0

	# The fallback may BE one of the blocks' cost centers — a farm whose mapping
	# points at the same center a block does. Merging rather than appending keeps
	# the entry to one line there, and keeps `shares` a set of distinct centers
	# so the largest-remainder split cannot hand the same center two cents.
	if residual > 0:
		center = str(fallback_cost_center or "").strip()
		if center in buckets:
			buckets[center]["minutes"] = round(buckets[center]["minutes"] + residual, 2)
		else:
			buckets[center] = {"minutes": residual, "blocks": [], "task_count": 0}
			order.append(center)

	total = round(sum(bucket["minutes"] for bucket in buckets.values()), 2)
	shares: list[dict] = []
	if total > 0:
		rows = sorted(
			order,
			key=lambda center: (-buckets[center]["minutes"], center),
		)
		for center in rows:
			bucket = buckets[center]
			shares.append(
				{
					"cost_center": center,
					"minutes": round(bucket["minutes"], 2),
					"share": round(bucket["minutes"] / total, 6),
					"blocks": list(bucket["blocks"]),
					"task_count": bucket["task_count"],
				}
			)

	return {
		"shares": shares,
		"attributed_minutes": attributed,
		"paid_minutes": round(paid, 2),
		"unattributed_minutes": residual,
		"unplaced_minutes": unplaced,
		"total_minutes": total,
		"coverage": round(attributed / paid, 4) if paid > 0 else None,
		"fallback_cost_center": str(fallback_cost_center or ""),
	}


def split_amount(amount, shares) -> list[dict]:
	"""`amount` across `shares`, to the cent, summing back to exactly `amount`.

	LARGEST REMAINDER, not round-each-and-hope. A third of $1,000.00 three times
	is $333.33 three times and a cent left over, and a Journal Entry a cent out
	of balance does not post — so the cent is GIVEN to somebody rather than
	lost. It goes to the largest fractional remainder, ties broken by position,
	which makes the same run produce the same entry every time it is previewed.

	A negative amount — a correction booked as a negative slip — is split on its
	magnitude and given its sign back, so the reversal lands on the same cost
	centers in the same proportions as the posting it reverses.
	"""
	rows = [row for row in (shares or []) if _as_float((row or {}).get("share")) > 0]
	total_share = sum(_as_float(row.get("share")) for row in rows)
	if not rows or total_share <= 0:
		return []

	money = _money(amount)
	sign = -1 if money < 0 else 1
	cents = round(abs(money) * 100)

	raw = [_as_float(row.get("share")) / total_share * cents for row in rows]
	base = [int(value) for value in raw]
	# Each remainder is under one cent, so there are never more spare cents than
	# there are shares — the loop below cannot run off the end of `ranked`.
	spare = cents - sum(base)
	ranked = sorted(range(len(rows)), key=lambda index: (-(raw[index] - base[index]), index))
	for index in ranked[: max(spare, 0)]:
		base[index] += 1

	out = []
	for row, value in zip(rows, base, strict=True):
		if value == 0:
			continue
		out.append(
			{
				"cost_center": str(row.get("cost_center") or ""),
				"amount": round(sign * value / 100.0, 2),
			}
		)
	return out


def allocate_expense_amounts(amounts: dict, shares, include_employer: bool = True) -> dict:
	"""One slip's expense components, each split across cost centers.

	Returns `{component: {cost_center: amount}}`, covering only the components
	with a debit side and only where that component has an amount this period. A
	component absent from the result is one `journal_lines` should post whole,
	on the blanket cost center, exactly as it always has.
	"""
	out: dict[str, dict[str, float]] = {}
	for component in EXPENSE_COMPONENTS:
		definition = COMPONENT_INDEX[component]
		if not include_employer and definition["party"] == "Employer":
			continue
		amount = _money((amounts or {}).get(component))
		if abs(amount) < 0.005:
			continue
		pieces = split_amount(amount, shares)
		if not pieces:
			continue
		bucket: dict[str, float] = {}
		for piece in pieces:
			center = piece["cost_center"]
			bucket[center] = round(bucket.get(center, 0.0) + piece["amount"], 2)
		out[component] = bucket
	return out


def consolidate_expense_amounts(
	slips: list[dict],
	allocations: dict | None,
	include_employer: bool = True,
	fallback_cost_center: str = "",
) -> dict:
	"""A whole run's expense side, split per employee and then summed.

	EACH PERSON'S OWN SHARES, not the run's blended ones, and that is not
	fussiness. Employer taxes are not proportional to gross — FUTA stops at the
	first $7,000 and SUTA at whatever the state's base is — so blending the run
	into one set of proportions and splitting the totals by it would book the
	tax expense of the people who had capped out onto the blocks worked by the
	people who had not. Splitting each slip and summing the results costs one
	pass and is right by construction.

	A slip with no allocation is booked whole to `fallback_cost_center`, which is
	the blanket cost center the mapping already carried.
	"""
	default = [{"cost_center": str(fallback_cost_center or ""), "share": 1.0}]
	out: dict[str, dict[str, float]] = {}
	for slip in slips or []:
		employee = str((slip or {}).get("employee") or "")
		shares = (allocations or {}).get(employee) or default
		per_slip = allocate_expense_amounts(
			component_amounts(slip),
			shares,
			include_employer=include_employer,
		)
		for component, bucket in per_slip.items():
			target = out.setdefault(component, {})
			for center, amount in bucket.items():
				target[center] = round(target.get(center, 0.0) + amount, 2)
	return out


def cost_center_totals(expense_split: dict | None) -> list[dict]:
	"""Every cost center an entry's expense side landed on, and what it took.

	The line a P&L-by-cost-center reader wants off a posting result without
	adding the journal up themselves. Largest first, because the question is
	always which block carried the wage.
	"""
	totals: dict[str, float] = {}
	for bucket in (expense_split or {}).values():
		for center, amount in (bucket or {}).items():
			totals[center] = round(totals.get(center, 0.0) + amount, 2)
	return [
		{"cost_center": center, "amount": amount}
		for center, amount in sorted(totals.items(), key=lambda item: (-abs(item[1]), item[0]))
	]


# ── Building lines ────────────────────────────────────────────────────────


def _line_key(account: str, side: str, cost_center: str = "") -> tuple:
	return (account, side, cost_center)


def journal_lines(
	amounts: dict,
	mapping,
	cost_center: str = "",
	include_employer: bool = True,
	expense_split: dict | None = None,
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

	`expense_split` — `{component: {cost center: amount}}` from
	`allocate_expense_amounts` — breaks a component's DEBIT into one line per
	cost center. The merge key gains the cost center for that reason, which
	changes nothing when there is no split: every line then carries the same
	blanket cost center and merges exactly as it did before. The credits are
	never split; a withholding liability is not a block's cost.

	Args:
		amounts: Component amounts, from `component_amounts`.
		mapping: The company's account mapping.
		cost_center: The blanket cost center, on every line that is not split.
		include_employer: False leaves the employer's own taxes off.
		expense_split: Per-component cost center breakdown for the debits.

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
			skipped.append(
				{
					"component": component,
					"amount": amount,
					"why": f"{definition['label']} has no account mapped for this company.",
				}
			)
			continue

		placed = {}
		centers: list[dict] = []
		for side in definition["sides"]:
			account = entry.get(f"{side}_account")
			if not account:
				skipped.append(
					{
						"component": component,
						"amount": amount,
						"side": side,
						"why": f"{definition['label']} has no {side} account mapped.",
					}
				)
				continue

			pieces = [(cost_center, amount)]
			if side == "debit" and (expense_split or {}).get(component):
				split = [
					(center, value)
					for center, value in (expense_split[component] or {}).items()
					if abs(_as_float(value)) >= 0.005
				]
				if split:
					# Largest first: the question a split entry is read to answer
					# is which block carried the wage, and the answer should be
					# the top line rather than somewhere down the list.
					split.sort(key=lambda item: (-abs(item[1]), item[0]))
					pieces = split
					centers = [{"cost_center": center, "amount": _money(value)} for center, value in split]

			for center, piece in pieces:
				key = _line_key(account, side, center)
				if key not in merged:
					merged[key] = {
						"account": account,
						"side": side,
						"cost_center": center,
						"amount": 0.0,
						"components": [],
					}
					order.append(key)
				merged[key]["amount"] = round(merged[key]["amount"] + _money(piece), 2)
				if component not in merged[key]["components"]:
					merged[key]["components"].append(component)
			placed[side] = account

		if placed:
			row = {
				"component": component,
				"label": definition["label"],
				"party": definition["party"],
				"amount": amount,
				"debit_account": placed.get("debit", ""),
				"credit_account": placed.get("credit", ""),
			}
			if centers:
				row["cost_centers"] = centers
			breakdown.append(row)

	lines = []
	for key in order:
		row = merged[key]
		amount = round(row["amount"], 2)
		if abs(amount) < 0.005:
			continue
		line = {"account": row["account"]}
		line[row["side"]] = amount
		line["user_remark"] = ", ".join(COMPONENT_INDEX[name]["label"] for name in row["components"])
		if row["cost_center"]:
			line["cost_center"] = row["cost_center"]
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
	allocation=None,
) -> dict:
	"""One employee's slip as one Journal Entry structure.

	Returns the entry whether or not it balances, with `balanced` on it. A caller
	that writes an unbalanced entry has ignored a field that says so, which is a
	different and more findable failure than a function that raised somewhere
	inside a loop over forty people.

	`allocation` is this person's own cost center shares, from
	`cost_center_shares`. Absent or empty, the expense lands whole on
	`cost_center` — which is every entry this function built before v0.101.0.
	"""
	amounts = component_amounts(slip)
	expense_split = (
		allocate_expense_amounts(amounts, allocation, include_employer=include_employer)
		if allocation
		else None
	)
	lines, breakdown, unmapped = journal_lines(
		amounts,
		mapping,
		cost_center=cost_center,
		include_employer=include_employer,
		expense_split=expense_split,
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
		"cost_center": cost_center,
		"cost_center_split": cost_center_totals(expense_split),
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
	allocations: dict | None = None,
) -> dict:
	"""A whole run as ONE Journal Entry.

	The default mode, and the right one for most farms: a forty-person biweekly
	payroll is forty journal entries in per-employee mode and one here, and the
	general ledger does not want forty. Per-employee earns its keep where labour
	is costed by person or where a slip has to be reversed on its own; the run's
	own record keeps the per-person detail either way.

	AND IT IS THE MODE THE COST CENTER SPLIT MATTERS MOST IN. One entry per run
	was the reason a farm could not see labour by block at all; the split is
	computed per employee and summed, so the consolidated entry carries the same
	per-block totals the forty per-employee ones would have had between them.
	"""
	amounts = consolidate_amounts(slips)
	expense_split = (
		consolidate_expense_amounts(
			slips,
			allocations,
			include_employer=include_employer,
			fallback_cost_center=cost_center,
		)
		if allocations
		else None
	)
	lines, breakdown, unmapped = journal_lines(
		amounts,
		mapping,
		cost_center=cost_center,
		include_employer=include_employer,
		expense_split=expense_split,
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
		"cost_center": cost_center,
		"cost_center_split": cost_center_totals(expense_split),
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
	allocations: dict | None = None,
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
		allocations: Employee → the `shares` list from `cost_center_shares`, for
			splitting the expense side across the blocks the work was done on.
			Absent, or absent for one employee, the expense lands whole on
			`cost_center` — the behaviour of every release before v0.101.0.

	Returns:
		Dict with `journal_entries`, `totals`, `balanced`, `warnings`, `skipped`,
		`cost_center_split` and the validation verdict for the mapping.
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
				skipped.append(
					{
						"employee": slip.get("employee", ""),
						"employee_name": slip.get("employee_name", ""),
						"why": "every component is zero, so there is nothing to post.",
					}
				)
				warnings.extend(_slip_warnings(slip, amounts, include_employer))
				continue
			entries.append(
				build_journal_entry(
					slip,
					mapping,
					posting_date=posting_date,
					company=company,
					payroll_entry=payroll_entry,
					slip_name=(slip_names or {}).get(slip.get("employee", ""), ""),
					cost_center=cost_center,
					include_employer=include_employer,
					allocation=(allocations or {}).get(slip.get("employee", "")),
				)
			)
	else:
		payable = []
		for slip in slips:
			amounts = component_amounts(slip)
			if slip_is_empty(amounts):
				skipped.append(
					{
						"employee": slip.get("employee", ""),
						"employee_name": slip.get("employee_name", ""),
						"why": "every component is zero, so it adds nothing to the entry.",
					}
				)
				warnings.extend(_slip_warnings(slip, amounts, include_employer))
				continue
			payable.append(slip)
		if payable:
			entries.append(
				build_consolidated_journal_entry(
					payable,
					mapping,
					posting_date=posting_date,
					company=company,
					payroll_entry=payroll_entry,
					cost_center=cost_center,
					include_employer=include_employer,
					allocations=allocations,
				)
			)

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
		for entry in entries
		if not entry["balanced"]
	]

	# The run's own expense side by cost center, summed across however many
	# entries the mode produced. Identical in both modes for the same reason the
	# totals are: the split is computed per slip and the mode only decides how
	# many journal entries the same arithmetic is spread across.
	run_split: dict[str, float] = {}
	for entry in entries:
		for row in entry.get("cost_center_split") or []:
			center = row["cost_center"]
			run_split[center] = round(run_split.get(center, 0.0) + _as_float(row["amount"]), 2)

	return {
		"mode": mode,
		"company": company,
		"payroll_entry": payroll_entry,
		"posting_date": str(posting_date or ""),
		"cost_center": cost_center,
		"cost_center_split": [
			{"cost_center": center, "amount": amount}
			for center, amount in sorted(run_split.items(), key=lambda item: (-abs(item[1]), item[0]))
		],
		"split_by_cost_center": bool(allocations),
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
