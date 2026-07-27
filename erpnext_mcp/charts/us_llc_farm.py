# SPDX-License-Identifier: MIT
"""`us_llc_farm` — a numbered chart of accounts for a US farming LLC.

WHY THIS EXISTS. A fresh ERPNext company is created from one of the bundled
"Standard" charts, which are unnumbered, use Indian-accounting labels
("Application of Funds", "Debtors", "Sundry Creditors") and have no idea what a
farm is. Every operator then spends an evening renaming things, and each one
arrives at a slightly different chart. This template is that evening, done once.

SMALL ON PURPOSE. Roughly seventy accounts, most groups only one level deep.
A chart with a line for every conceivable expense is a chart where nobody can
find the right line, so the operating expenses are nine buckets rather than
thirty-five, and a sub-account gets added when a real transaction needs one.
Numbering leaves gaps for exactly that — the next bank account is 1130, the next
operating expense is the next free number in the 6000s.

TWO BUSINESSES, ONE LEDGER. The farm and the investment book are kept
structurally apart so "how did trading do this year" is a report filter, not a
reconstruction:

    assets       1800-1849   Investments & Trading
    income       4200-4249   Investment & Trading Income
    expense      7300        Realized Capital Losses & Options Losses
    equity       3500        Unrealized Gain/Loss on Investments

Filter a P&L or trial balance to those four ranges and you have the trading
segment; exclude them and you have the farm. Nothing else in the chart reaches
into those numbers, which is what keeps the split true over time.

WHAT IT IS NOT. A starting point, not tax advice, and not a filing position.
Entity-level accounts live in the 3000s and are the part that differs by entity
type — a single-member LLC's Member Capital is not an S corporation's
Shareholder Distributions, and neither is a partnership's per-partner capital.
That is why the entity type is in the template key rather than a flag: the
sibling templates (`us_c_corp`, `us_s_corp`, `us_partnership`) will differ from
this one almost entirely in the 3000s, and pretending one chart covers all four
would put the wrong equity structure on somebody's return.
"""

from .base import ChartTemplate, register


def _group(number, name, children, account_type="", description=""):
	node = {
		"account_number": number,
		"account_name": name,
		"is_group": True,
		"children": children,
	}
	if account_type:
		node["account_type"] = account_type
	if description:
		node["description"] = description
	return node


def _account(number, name, account_type="", description="", optional=False):
	node = {"account_number": number, "account_name": name, "is_group": False}
	if account_type:
		node["account_type"] = account_type
	if description:
		node["description"] = description
	if optional:
		node["optional"] = True
	return node


# ── 1000 Assets ─────────────────────────────────────────────────────────────
_ASSETS = {
	"account_number": "1000",
	"account_name": "Assets",
	"root_type": "Asset",
	"is_group": True,
	"children": [
		_group(
			"1100",
			"Current Assets",
			[
				_account("1110", "Cash on Hand", "Cash"),
				_account("1120", "Checking - Wells Fargo Primary", "Bank"),
				_account("1140", "Savings", "Bank"),
				_account("1200", "Accounts Receivable", "Receivable"),
				_account(
					"1300",
					"Inventory - Crops & Supplies",
					"Stock",
					description=(
						"Harvested crop held for sale and unused inputs on hand. One "
						"account rather than two: split it only when the two are being "
						"counted and valued separately."
					),
				),
				_account(
					"1400",
					"Prepaid Expenses",
					"Current Asset",
					description=(
						"Insurance, subscriptions and licences paid ahead of the period "
						"they cover, amortised into the matching 6000-series expense as "
						"the period runs. Property tax prepayments have their own account "
						"at 1420."
					),
				),
				_account(
					"1420",
					"Prepaid Property Tax",
					"Current Asset",
					description=(
						"Property tax paid to the county (or equivalent taxing authority) "
						"ahead of schedule, amortising into 6650 Property & Business Taxes "
						"as time passes. Separate from 1400 so the balance answers 'how far "
						"ahead are we on property tax' at a glance, which a combined "
						"prepaid account cannot."
					),
				),
				_account(
					"1510",
					"Employee Cash Advances",
					"Current Asset",
					description=(
						"Money handed to an employee that the business expects back — an "
						"ASSET. Not to be confused with 2130 Employee Wage Advances, which "
						"is wages already earned, paid early, and recovered from the next "
						"paycheque."
					),
				),
			],
		),
		_group(
			"1700",
			"Fixed Assets",
			[
				_account("1710", "Land", "Fixed Asset"),
				_account("1720", "Buildings", "Fixed Asset"),
				_account(
					"1730",
					"Machinery & Equipment",
					"Fixed Asset",
					description=(
						"Tractors, implements, irrigation, harvest equipment and "
						"orchard-specific capital (trellis, netting, wind machines, frost "
						"fans, platforms). Break it into sub-accounts when the depreciation "
						"schedules diverge enough to be worth tracking apart."
					),
				),
				_account("1740", "Vehicles", "Fixed Asset"),
				_account("1750", "Software & IP", "Fixed Asset"),
				_account(
					"1780",
					"Accumulated Depreciation",
					"Accumulated Depreciation",
					description=(
						"Contra-asset. Carries a credit balance and reduces the gross "
						"figures above; the matching charge is 6700 Depreciation & "
						"Amortization."
					),
				),
			],
		),
		_group(
			"1800",
			"Investments & Trading",
			[
				_account(
					"1810",
					"Marketable Securities - Stocks & ETFs",
					description=(
						"Equity positions at cost. Mark-to-market movement goes to 3500 "
						"Unrealized Gain/Loss on Investments until a position closes, at "
						"which point the result lands in 4230 or 7300."
					),
				),
				_account(
					"1820",
					"Marketable Securities - Bonds & Fixed Income",
					description="Debt positions at cost. Coupon income goes to 4210 Interest Income.",
				),
				_account(
					"1830",
					"Brokerage Cash & Money Market",
					"Bank",
					description=(
						"Uninvested cash sitting at the broker. Typed as Bank so it "
						"reconciles like any other cash account — this is the account a "
						"linked brokerage feed posts against."
					),
				),
				_account(
					"1840",
					"Options Contracts Open",
					description=(
						"Open option positions, carried separately so a covered-call "
						"programme's live exposure is visible without unpicking it from the "
						"underlying equity. Premium received goes to 4240; a loss on close "
						"goes to 7300."
					),
				),
			],
			description=(
				"The investment book. Everything in 1800-1849 belongs to the trading "
				"segment, paired with income in 4200-4249, losses in 7300 and unrealised "
				"movement in 3500. Filter a report to those ranges and you have the "
				"trading business on its own."
			),
		),
	],
}

# ── 2000 Liabilities ────────────────────────────────────────────────────────
_LIABILITIES = {
	"account_number": "2000",
	"account_name": "Liabilities",
	"root_type": "Liability",
	"is_group": True,
	"children": [
		_group(
			"2100",
			"Current Liabilities",
			[
				_account("2110", "Accounts Payable", "Payable"),
				_account(
					"2120",
					"Current Pay Period - Due to Employees",
					"Payable",
					description=(
						"LIVE BALANCE, NOT A PERIOD-END ACCRUAL. This account holds what is "
						"owed to employees right now for work already performed in the "
						"current pay period, and it is designed to be updated continuously "
						"as that work lands: bucket picks posted as they are recorded (piece "
						"rate), hours posted as they accumulate. The balance is therefore "
						"meant to be readable at any moment as real-time wage exposure, not "
						"only at a period boundary. It flushes to zero when payroll is "
						"processed.\n\n"
						"Do NOT book period-end adjusting entries here. An accrual dropped in "
						"at month end double-counts against the continuous postings and "
						"destroys the one property this account exists for. If a separate "
						"period-end accrual is genuinely needed, open a sibling account for "
						"it and leave this one alone."
					),
				),
				_account(
					"2130",
					"Employee Wage Advances",
					"Payable",
					description=(
						"Wages already earned and paid out ahead of the normal payday, "
						"recovered against the next paycheque — a reduction of what will be "
						"paid. Contrast 1510 Employee Cash Advances, which is money the "
						"business expects back rather than wages brought forward."
					),
				),
				_group(
					"2140",
					"Payroll Tax Withholdings",
					[
						_account("2141", "Federal & FICA Withheld", "Payable"),
						_account("2142", "State Withheld", "Payable"),
					],
					account_type="Payable",
					description=(
						"Amounts withheld from employees and held on their behalf until "
						"remitted. This is not the employer's own payroll tax cost, which is "
						"an expense and belongs in 6150."
					),
				),
				_account(
					"2150",
					"Sales Tax Payable",
					"Tax",
					description=(
						"Sales tax collected from customers and owed onward. Not an expense "
						"— the business is a collection agent for it."
					),
				),
				_account(
					"2160",
					"Credit Card Payable",
					"Credit Card",
					description=(
						"One account per card is usually easier to reconcile than one combined balance."
					),
				),
				_account(
					"2170",
					"Property Tax Payable",
					"Payable",
					description=(
						"Accrued property tax owed at any point in time. The county bills "
						"once or twice a year, but the obligation accrues monthly, so "
						"accruing it here keeps the balance sheet honest between bills. "
						"Cleared when the bill is paid; the charge goes to 6650 Property & "
						"Business Taxes."
					),
				),
			],
		),
		_group(
			"2500",
			"Long-Term Liabilities",
			[
				_account(
					"2510",
					"Notes Payable",
					"Payable",
					description=(
						"Land, equipment and operating notes. Give each note its own "
						"sub-account once there is more than one, so a payoff schedule can "
						"be read off the ledger."
					),
				)
			],
		),
	],
}

# ── 3000 Equity ─────────────────────────────────────────────────────────────
# The part that differs by entity type. See the module docstring.
_EQUITY = {
	"account_number": "3000",
	"account_name": "Equity",
	"root_type": "Equity",
	"is_group": True,
	"children": [
		_account(
			"3100",
			"Member Capital",
			"Equity",
			description=(
				"Contributions in. On a multi-member LLC, give each member their own "
				"sub-account under here rather than tracking members in a memo field."
			),
		),
		_account(
			"3200",
			"Member Distributions",
			"Equity",
			description=(
				"Cash and property out to members. A contra-equity account: it carries a "
				"debit balance and reduces equity. Distributions are not an expense and "
				"must never be booked in the 6000s."
			),
		),
		_account(
			"3300",
			"Opening Balance Equity",
			"Equity",
			description=(
				"The plug used when balances are first loaded into ERPNext. It should be "
				"cleared to Member Capital or Retained Earnings once the opening trial "
				"balance is agreed, and should read zero at every year end afterwards. A "
				"non-zero balance here at close is a sign the conversion was never "
				"finished."
			),
		),
		_account(
			"3350",
			"Prior Period Adjustments",
			"Equity",
			description=(
				"Corrections to a closed year that must not be run through the current "
				"year's income statement."
			),
		),
		_account("3400", "Retained Earnings", "Equity"),
		_account(
			"3500",
			"Unrealized Gain/Loss on Investments",
			"Equity",
			description=(
				"Mark-to-market bucket for open positions in 1810/1820/1840. Movement "
				"sits here — outside the income statement — until a position actually "
				"closes, at which point it moves to 4230 Realized Capital Gains or 7300 "
				"Realized Capital Losses & Options Losses. Part of the trading segment."
			),
		),
	],
}

# ── 4000 Income ─────────────────────────────────────────────────────────────
_INCOME = {
	"account_number": "4000",
	"account_name": "Income",
	"root_type": "Income",
	"is_group": True,
	"children": [
		_group(
			"4100",
			"Farm Operations",
			[
				_account("4110", "Farm Sales", "Income Account"),
				_account("4120", "Custom Services", "Income Account"),
				_account(
					"4130",
					"Government Programs",
					"Income Account",
					description=(
						"USDA program payments, EQIP and other conservation cost-share, and "
						"disaster relief. Kept out of farm sales because it is reported "
						"separately and is not revenue from a crop."
					),
				),
				_account("4140", "Crop Insurance Proceeds", "Income Account"),
			],
		),
		_group(
			"4200",
			"Investment & Trading Income",
			[
				_account("4210", "Interest Income", "Income Account"),
				_account("4220", "Dividend Income", "Income Account"),
				_account("4230", "Realized Capital Gains", "Income Account"),
				_account(
					"4240",
					"Options Premium Income",
					"Income Account",
					description=(
						"Premium collected on written contracts. A loss on closing one goes "
						"to 7300 rather than being netted here, so the gross premium taken "
						"in stays visible."
					),
				),
			],
			description="The income half of the trading segment. See 1800.",
		),
		_group(
			"4300",
			"Other Income",
			[
				_account("4310", "Gain on Sale of Fixed Assets", "Income Account"),
				_account("4320", "Miscellaneous Income", "Income Account"),
			],
		),
	],
}

# ── 5000 Cost of Goods Sold ─────────────────────────────────────────────────
_COGS = {
	"account_number": "5000",
	"account_name": "Cost of Goods Sold",
	"root_type": "Expense",
	"is_group": True,
	"description": (
		"Direct cost of growing the crop. Note that these are typed Expense Account "
		"rather than ERPNext's Cost of Goods Sold type: if the Stock module is used "
		"against 1300, the Item or Item Group default expense account has to be set "
		"by hand, because ERPNext looks for the Cost of Goods Sold type when it "
		"picks one automatically."
	),
	"children": [
		_account("5110", "Seeds, Fertilizer, Chemicals", "Expense Account"),
		_account("5120", "Water & Irrigation", "Expense Account"),
		_account(
			"5150",
			"Direct Farm Labor",
			"Expense Account",
			description=(
				"Wages for work performed on the crop — picking, pruning, thinning, "
				"irrigating. Administrative and management wages belong in 6100, and "
				"keeping the two apart is what makes a cost per bin mean anything."
			),
		),
	],
}

# ── 6000 Operating Expenses ─────────────────────────────────────────────────
# Deliberately flat: nine buckets, no sub-groups. Add a sub-account under one
# of these when a real transaction needs the detail, not in advance.
_OPEX = {
	"account_number": "6000",
	"account_name": "Operating Expenses",
	"root_type": "Expense",
	"is_group": True,
	"children": [
		_account(
			"6100",
			"Payroll & Benefits",
			"Expense Account",
			description=(
				"Administrative and management wages, health cover and other benefits. "
				"Crop labour goes to 5150; the employer's payroll tax goes to 6150."
			),
		),
		_account(
			"6150",
			"Employer Payroll Tax Expense",
			"Expense Account",
			description=(
				"The employer's own share of payroll taxes on wages paid — FICA (6.2%), "
				"Medicare (1.45%), FUTA, SUTA, and workers comp premiums where treated "
				"as such. Separated from 6100 so wage cost and true cost of employment "
				"can be read apart.\n\n"
				"Does NOT include amounts withheld from employees. Those are the "
				"employees' money held on their behalf and go to the 2140 Payroll Tax "
				"Withholdings liability group, never to expense."
			),
		),
		_account("6200", "Occupancy & Utilities", "Expense Account"),
		_account("6300", "Vehicles & Fuel", "Expense Account"),
		_account(
			"6400",
			"Professional Services",
			"Expense Account",
			description="Legal, accounting and tax preparation, consulting, advisory and management fees.",
		),
		_account("6500", "Office & Administrative", "Expense Account"),
		_account(
			"6600",
			"Insurance",
			"Expense Account",
			description=(
				"Crop, liability and umbrella cover. Vehicle insurance goes to 6300 and "
				"workers comp to 6150, so each sits with the cost it belongs to."
			),
		),
		_account(
			"6650",
			"Property & Business Taxes",
			"Expense Account",
			description=(
				"Recurring taxes and fees the LLC owes regardless of income: property tax "
				"on land, buildings and equipment; vehicle registration and weight tax on "
				"farm trucks and tractors; LLC annual filing fees; state franchise or "
				"minimum tax; business licences.\n\n"
				"Does NOT include federal or state income tax (this is a pass-through "
				"entity, so those are the members'), sales tax collected from customers "
				"(2150 Sales Tax Payable — the business is only a collection agent), or "
				"employer payroll taxes (6150)."
			),
		),
		_account("6700", "Depreciation & Amortization", "Depreciation"),
		_account("6800", "Repairs & Maintenance", "Expense Account"),
		_account("6900", "Miscellaneous", "Expense Account"),
	],
}

# ── 7000 Non-Operating Expenses ─────────────────────────────────────────────
_NON_OPERATING = {
	"account_number": "7000",
	"account_name": "Non-Operating Expenses",
	"root_type": "Expense",
	"is_group": True,
	"children": [
		_account("7100", "Interest Expense", "Expense Account"),
		_account("7200", "Loss on Sale of Fixed Assets", "Expense Account"),
		_account(
			"7300",
			"Realized Capital Losses & Options Losses",
			"Expense Account",
			description=(
				"The loss side of the trading segment: closed positions from 1810/1820 "
				"and losses on closing contracts from 1840. Gains go to 4230 and premium "
				"to 4240 rather than being netted here, so gross activity on both sides "
				"stays visible."
			),
		),
	],
}


TREE = [_ASSETS, _LIABILITIES, _EQUITY, _INCOME, _COGS, _OPEX, _NON_OPERATING]


register(
	ChartTemplate(
		key="us_llc_farm",
		title="US farming LLC",
		entity_type="LLC",
		jurisdiction="US",
		summary=(
			"A compact numbered chart of accounts for a US farming LLC that also runs "
			"an investment book. Crop labour is separated from administrative wages so "
			"a cost per bin means something; the trading segment has its own asset, "
			"income, loss and unrealised-gain accounts so it can be reported on "
			"without being untangled; and the current-pay-period wage liability is a "
			"live balance rather than a period-end accrual."
		),
		tree=TREE,
		notes=(
			"A starting point, not tax advice. Have the entity's return preparer look "
			"at the 3000s before anything is filed against them.",
			"THE TRADING SEGMENT is assets 1800-1849, income 4200-4249, losses 7300 "
			"and unrealised movement 3500. Filter a P&L or trial balance to those four "
			"ranges to see the investment book on its own; exclude them to see the "
			"farm. Nothing else in the chart reaches into those numbers.",
			"2120 Current Pay Period - Due to Employees is meant to be updated "
			"continuously as work lands, not written up once at period end. Read its "
			"description before posting to it.",
			"The 3000-series equity accounts are LLC-shaped. A C corporation, an S "
			"corporation or a partnership needs a different structure there; those are "
			"separate templates rather than a flag on this one.",
			"Operating expenses are nine flat buckets on purpose. A chart with a line "
			"for every conceivable cost is one where nobody finds the right line — add "
			"a sub-account when a real transaction needs it.",
			"Numbering leaves gaps on purpose. The next bank account is 1130, the next "
			"operating expense the next free number in its decade.",
		),
	)
)
