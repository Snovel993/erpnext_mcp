# SPDX-License-Identifier: MIT
"""`us_llc_farm` — a numbered chart of accounts for a US farming LLC.

WHY THIS EXISTS. A fresh ERPNext company is created from one of the bundled
"Standard" charts, which are unnumbered, use Indian-accounting labels
("Application of Funds", "Debtors", "Sundry Creditors") and have no idea what a
farm is. Every operator then spends an evening renaming things, and each one
arrives at a slightly different chart. This template is that evening, done once.

WHAT IT IS NOT. It is a starting point, not tax advice, and not a filing
position. Entity-level accounts live in the 3000s and are the part that differs
by entity type — a single-member LLC's Member Capital is not an S corporation's
Shareholder Distributions, and neither is a partnership's per-partner capital.
That is why the entity type is in the template key rather than a flag: the
sibling templates (`us_c_corp`, `us_s_corp`, `us_partnership`) will differ from
this one almost entirely in the 3000s, and pretending one chart covers all four
would put the wrong equity structure on somebody's return.

THE NUMBERING follows the convention a US bookkeeper expects: 1000s assets,
2000s liabilities, 3000s equity, 4000s income, 5000s cost of goods sold, 6000s
operating expenses, 7000s non-operating. Gaps are deliberate — the space between
1140 and 1200 is where the next bank account goes.

ACCOUNTS MARKED `optional` are ones a small operation will not need. They are
included rather than omitted because deleting a line from a proposed plan is
easier than knowing it was an option: `propose_clean_chart` lists them so a
reviewer can strike them before importing.
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
				_account("1120", "Checking - Primary", "Bank"),
				_account(
					"1130",
					"Checking - Payroll",
					"Bank",
					description=(
						"A second checking account funded only for payroll runs. Skip it "
						"if the operation runs everything through one checking account."
					),
					optional=True,
				),
				_account("1140", "Savings", "Bank"),
				_account("1200", "Accounts Receivable", "Receivable"),
				_group(
					"1300",
					"Inventory",
					[
						_account("1310", "Inventory - Crops in Storage", "Stock"),
						_account("1320", "Inventory - Supplies", "Stock"),
					],
					account_type="Stock",
				),
				_account(
					"1400",
					"Prepaid Expenses",
					"Current Asset",
					description=(
						"Insurance, rent and licences paid ahead of the period they cover. "
						"Amortise into the matching 6000-series expense as the period runs."
					),
				),
				_group(
					"1500",
					"Other Current Assets",
					[
						_account(
							"1510",
							"Employee Cash Advances",
							"Current Asset",
							description=(
								"Money handed to an employee that the business expects back or "
								"expects to recover from a future paycheque — an ASSET. Not to be "
								"confused with 2130 Employee Wage Advances, which is wages already "
								"earned and paid early, and is a liability offset."
							),
						),
						_account(
							"1520",
							"Startup Costs § 195 Deferred",
							"Current Asset",
							description=(
								"Organisational and start-up expenditure held for IRC § 195 "
								"treatment rather than expensed as incurred. Talk to the return "
								"preparer before clearing this account."
							),
						),
						_account("1530", "Earnest Money Deposits", "Current Asset"),
					],
				),
			],
		),
		_group(
			"1700",
			"Fixed Assets",
			[
				_account("1710", "Land", "Fixed Asset"),
				_account("1720", "Buildings", "Fixed Asset"),
				_group(
					"1730",
					"Machinery & Equipment",
					[
						_account("1731", "Tractors & Prime Movers", "Fixed Asset"),
						_account("1732", "Implements & Attachments", "Fixed Asset"),
						_account("1733", "Irrigation Systems", "Fixed Asset"),
						_account("1734", "Harvesting Equipment", "Fixed Asset"),
						_account(
							"1735",
							"Orchard-Specific",
							"Fixed Asset",
							description=(
								"Trellis, netting, wind machines, frost fans, platforms — capital "
								"that only makes sense on a tree-fruit block."
							),
						),
						_account("1736", "Utility Vehicles", "Fixed Asset"),
						_account("1737", "Storage & Handling", "Fixed Asset"),
					],
					account_type="Fixed Asset",
				),
				_account("1740", "Office Equipment", "Fixed Asset"),
				_account(
					"1750",
					"Software",
					"Fixed Asset",
					description=(
						"Capitalised software licences. Most operations expense software "
						"through 6520 Software Subscriptions instead; keep this only if "
						"something is genuinely being capitalised and depreciated."
					),
					optional=True,
				),
				_group(
					"1780",
					"Accumulated Depreciation",
					[
						_account("1781", "Accum Dep - Buildings", "Accumulated Depreciation"),
						_account("1782", "Accum Dep - Machinery", "Accumulated Depreciation"),
						_account("1783", "Accum Dep - Office", "Accumulated Depreciation"),
					],
					account_type="Accumulated Depreciation",
					description=(
						"Contra-asset. These carry credit balances and reduce the gross "
						"figures above; the matching charge is 6810 Depreciation Expense."
					),
				),
				_account(
					"1790",
					"Construction in Progress",
					"Capital Work in Progress",
					description=(
						"Capital spend on something not yet in service — a shed being built, "
						"a block being planted. Nothing depreciates from here; move the total "
						"to the right 1700-series account when the asset is placed in service."
					),
				),
			],
		),
		_group(
			"1800",
			"Investments",
			[],
			description=(
				"Deliberately empty. A holding place for marketable securities or an "
				"interest in another entity, so those do not end up filed under Other "
				"Current Assets when they appear."
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
						"Wages already earned and paid out ahead of the payroll run — a "
						"reduction of what will be paid. Contrast 1510 Employee Cash "
						"Advances, which is money the business expects back."
					),
				),
				_group(
					"2140",
					"Payroll Tax Withholdings",
					[
						_account("2141", "Federal Income Tax Withheld", "Payable"),
						_account("2142", "State Income Tax Withheld", "Payable"),
						_account("2143", "FICA Withheld", "Payable"),
						_account("2144", "Medicare Withheld", "Payable"),
						_account("2145", "Unemployment Insurance Payable", "Payable"),
					],
					account_type="Payable",
					description=(
						"Amounts withheld from employees and held on their behalf until "
						"remitted. This is not the employer's own payroll tax cost, which is "
						"an expense and belongs in 6120."
					),
				),
				_account("2150", "Sales Tax Payable", "Tax"),
				_account(
					"2160",
					"Credit Card Payable",
					"Credit Card",
					description=(
						"One account per card is usually easier to reconcile than one combined balance."
					),
				),
				_account("2170", "Accrued Interest Payable", "Payable"),
			],
		),
		_group(
			"2500",
			"Long-Term Liabilities",
			[
				_account("2510", "Notes Payable - Land", "Payable"),
				_account("2520", "Notes Payable - Equipment", "Payable"),
				_account(
					"2590",
					"Deferred Tax Liability",
					"Payable",
					description=(
						"Normally only used where the entity is taxed at entity level. A "
						"pass-through LLC often leaves this at zero all year."
					),
				),
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
			"Operating Income",
			[
				_account("4110", "Farm Sales", "Income Account"),
				_account("4120", "Custom Farming Services", "Income Account"),
				_account(
					"4130",
					"Government Program Income",
					"Income Account",
					description=(
						"Conservation, disaster and commodity program payments. Kept "
						"separate from farm sales because it is reported separately."
					),
				),
				_account("4140", "Crop Insurance Proceeds", "Income Account"),
			],
		),
		_group(
			"4200",
			"Investment Income",
			[
				_account("4210", "Interest Income", "Income Account"),
				_account("4220", "Dividend Income", "Income Account"),
				_account("4230", "Realized Capital Gains", "Income Account"),
			],
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
	"children": [
		_group(
			"5100",
			"COGS - Farm Operations",
			[
				_account("5110", "Seeds & Plants", "Cost of Goods Sold"),
				_account("5120", "Fertilizer & Soil Amendments", "Cost of Goods Sold"),
				_account("5130", "Chemicals & Sprays", "Cost of Goods Sold"),
				_account("5140", "Water & Irrigation", "Cost of Goods Sold"),
				_account(
					"5150",
					"Direct Farm Labor",
					"Cost of Goods Sold",
					description=(
						"Wages for work performed on the crop — picking, pruning, thinning, "
						"irrigating. Administrative and office wages belong in 6110, and "
						"keeping the two apart is what makes a cost-per-bin figure mean "
						"anything."
					),
				),
				_account("5190", "COGS Other", "Cost of Goods Sold"),
			],
		)
	],
}

# ── 6000 Operating Expenses ─────────────────────────────────────────────────
_OPEX = {
	"account_number": "6000",
	"account_name": "Operating Expenses",
	"root_type": "Expense",
	"is_group": True,
	"children": [
		_group(
			"6100",
			"Payroll & Benefits",
			[
				_account(
					"6110",
					"Wages - Administrative",
					"Expense Account",
					description="Office and management wages. Crop labour goes to 5150.",
				),
				_account(
					"6120",
					"Payroll Tax Expense",
					"Expense Account",
					description=(
						"The employer's own share. Amounts withheld from employees are not "
						"an expense — they are a liability in 2140."
					),
				),
				_account("6130", "Employee Benefits", "Expense Account"),
				_account("6140", "Workers Compensation Insurance", "Expense Account"),
			],
		),
		_group(
			"6200",
			"Occupancy",
			[
				_account("6210", "Rent", "Expense Account"),
				_account("6220", "Utilities", "Expense Account"),
				_account("6230", "Property Insurance", "Expense Account"),
				_account("6240", "Repairs & Maintenance", "Expense Account"),
			],
		),
		_group(
			"6300",
			"Vehicles & Fuel",
			[
				_account("6310", "Fuel & Lubricants", "Expense Account"),
				_account("6320", "Vehicle Insurance", "Expense Account"),
				_account("6330", "Vehicle Maintenance & Repairs", "Expense Account"),
				_account("6340", "Vehicle Registration", "Expense Account"),
			],
		),
		_group(
			"6400",
			"Professional Services",
			[
				_account("6410", "Legal Fees", "Expense Account"),
				_account("6420", "Accounting & Tax Preparation", "Expense Account"),
				_account("6430", "Consulting Fees", "Expense Account"),
				_account("6440", "Advisory & Management Fees", "Expense Account"),
			],
		),
		_group(
			"6500",
			"Office & Administrative",
			[
				_account("6510", "Office Supplies", "Expense Account"),
				_account("6520", "Software Subscriptions", "Expense Account"),
				_account("6530", "Telephone & Internet", "Expense Account"),
				_account("6540", "Postage & Delivery", "Expense Account"),
				_account("6550", "Bank Charges", "Expense Account"),
				_account("6560", "Licenses & Permits", "Expense Account"),
				_account("6570", "Dues & Subscriptions", "Expense Account"),
			],
		),
		_group(
			"6600",
			"Marketing & Sales",
			[
				_account("6610", "Marketing & Advertising", "Expense Account"),
				_account(
					"6620",
					"Meals & Entertainment",
					"Expense Account",
					description=(
						"Deductibility differs by category and by year. Keeping this out of "
						"6630 Travel is what lets the preparer apply the right limit."
					),
				),
				_account("6630", "Travel", "Expense Account"),
			],
		),
		_group(
			"6700",
			"Insurance",
			[
				_account("6710", "Crop Insurance", "Expense Account"),
				_account("6720", "Liability Insurance", "Expense Account"),
				_account("6730", "Umbrella Insurance", "Expense Account"),
			],
		),
		_group(
			"6800",
			"Depreciation & Amortization",
			[
				_account("6810", "Depreciation Expense", "Depreciation"),
				_account("6820", "Amortization Expense", "Depreciation"),
			],
		),
		_group(
			"6900",
			"Other Operating",
			[
				_account(
					"6910",
					"Contract Labor",
					"Expense Account",
					description=(
						"Payments to people who are not employees — the 1099 side. Employee "
						"wages never belong here."
					),
				),
				_account("6920", "Small Tools & Supplies", "Expense Account"),
				_account("6930", "Training & Education", "Expense Account"),
				_account("6990", "Miscellaneous Expense", "Expense Account"),
			],
		),
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
		_account("7300", "Realized Capital Losses", "Expense Account"),
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
			"A numbered chart of accounts for a US farming LLC, written with tree "
			"fruit in mind: crop labour separated from administrative wages so a "
			"cost per bin means something, orchard-specific capital broken out under "
			"machinery, and a live current-pay-period wage liability rather than a "
			"period-end accrual."
		),
		tree=TREE,
		notes=(
			"A starting point, not tax advice. Have the entity's return preparer "
			"look at the 3000s and at 1520 before anything is filed against them.",
			"The 3000-series equity accounts are LLC-shaped. A C corporation, an S "
			"corporation or a partnership needs a different structure there; those "
			"are separate templates rather than a flag on this one.",
			"2120 Current Pay Period - Due to Employees is meant to be updated "
			"continuously as work lands, not written up once at period end. Read its "
			"description before posting to it.",
			"Accounts marked optional can be struck from the plan before importing "
			"without breaking anything else in the tree.",
			"Numbering leaves gaps on purpose. Add the next bank account at 1150, "
			"the next expense category at the next free number in its group.",
		),
	)
)
