# SPDX-License-Identifier: MIT
"""Compliance metadata on the doctypes where the work actually happens.

THIS FILE IS THE ONE PLACE THIS APP BREAKS ITS OWN RULE, AND THE RULE IS WORTH
RESTATING BEFORE THE EXCEPTION. `hooks.py` promises that installing erpnext_mcp
adds no field to any doctype it did not create, so an operator who removes the
app gets their site back exactly as it was. v0.7.0's asset tooling keeps its cost
split in an `Asset Cost Profile` beside ERPNext's Asset for precisely that
reason: a doctype of ours goes with the app, a field on theirs does not.

Sprint 7 adds fields to Spray Log, to Employee and to Bucket Log Entry — three
doctypes belonging to other apps — and it does so **on purpose**, because the
alternative is worse in a way that matters more than the promise. v0.19.3 adds a
fourth target, `Attendance`, for the same reason and with the same argument: the
shift-close bridge writes payroll rows, and a row that cannot say which shift it
came from is a row nobody can reach the working conditions through.

WHY. Compliance is a lens on operational data, not a duplicate set of records.
Every spray IS an EPA and Worker Protection Standard record; every hire IS an
I-9 record; every bucket IS an FSMA traceability record. The bolt-on version of
this feature is a "Spray Compliance Log" doctype somebody fills in after doing
the spraying, and it fails the only test that matters:

    Does removing the feature break OPERATIONS, or only break COMPLIANCE
    REPORTING?
      breaks operations too  → compliance is woven in correctly
      only breaks reporting  → it is a shadow layer; refactor

A shadow log drifts from reality the first busy week of harvest, and an auditor
who finds two records of one spray that disagree has found something far worse
than a missing field. So the applicator's name, the EPA registration number, the
restricted-entry interval and the pre-harvest interval go **on the spray record**,
where the person doing the spraying already is, and where leaving them blank
stops the spray being recorded at all.

WHAT THAT COSTS, SAID PLAINLY. Uninstalling erpnext_mcp from a site where these
have been filled in drops the columns and the data in them. `before_uninstall`
says so by name. That is a real cost and it is the right trade: an app that
refuses to touch anybody else's doctype cannot make compliance fundamental to
operations, it can only make it adjacent to them.

CUSTOM FIELD, NOT A DOCTYPE EDIT. Every field here is a `Custom Field` row,
which is Frappe's supported way for one app to extend another's doctype. The
target app's repository is untouched, its own migrations keep working, and a
version of farm_precision_ag that later adds `epa_reg_number` itself finds this
one already there and is not confused by it — `_existing` matches on the field
being present at all, not on who put it there.

GRACEFUL DEGRADATION IS THE DEFAULT, NOT A FEATURE FLAG. A site without
farm_precision_ag has no Spray Log, and the installer skips it by name and says
why. A site with Frappe HR but not farm_hr still gets the Employee fields,
because the Employee register is the same register either way. Nothing here
fails because a target is absent; a target that is absent is reported.

IT RUNS ON EVERY MIGRATE AND IS A NO-OP ON THE SECOND. `_existing` asks this
site's own meta whether the field is there before writing anything, so `bench
migrate` three times running creates the fields once. That is asserted by a test.

THE REQUIRED FIELDS ARE REQUIRED, AND THAT HAS A CONSEQUENCE. Frappe enforces
`reqd` on save, not retroactively, so existing records keep their rows and keep
being readable. But re-saving one without filling the new field in is refused —
which is exactly the intended behaviour ("this spray record was never compliant
and now you cannot pretend otherwise") and is also a surprise if nobody said it
first. So the installer counts the existing rows that would fail and reports the
number per field. That count is the operation's compliance backlog, stated in
rows, and it is the most useful thing this installer produces on a site with
history.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

from . import compat, settings

CUSTOM_FIELD = "Custom Field"

#: The settings switch. Unlike every other switch in this app it defaults ON,
#: because this is an installer that adds columns to a schema rather than a tool
#: that writes somebody's data — and a compliance field that arrives only when an
#: operator remembers to tick a box is a compliance field that is missing on the
#: sites that needed it most. An operator who does not want their Spray Log
#: touched turns it off and the fields are never added.
SWITCH = "install_compliance_fields"

#: How many existing rows to count when reporting the backlog for a newly
#: required field. Past this the answer is "lots" and counting further is a table
#: scan nobody reads.
BACKLOG_CAP = 10000


@dataclass(frozen=True)
class ComplianceField:
	"""One column, and the sentence explaining why a regulator wants it.

	`framework` and `why` are not documentation of the code — they are the
	content of `docs/compliance_fields.md`, generated from this table by
	`describe()`, so the doc and the schema cannot drift. A field nobody can say
	the framework for does not belong here.

	`operational` is the woven-in claim, per field: what BREAKS in the day-to-day
	work if this is missing, not what breaks in the report. A field whose
	`operational` reads "nothing" is a shadow field and should be somewhere else.
	"""

	fieldname: str
	label: str
	fieldtype: str
	framework: str
	why: str
	operational: str
	reqd: bool = False
	options: str = ""
	description: str = ""
	insert_after: str = ""

	def as_custom_field(self, doctype: str) -> dict:
		"""The `Custom Field` row this becomes, minus the fields Frappe fills in."""
		row = {
			"dt": doctype,
			"fieldname": self.fieldname,
			"label": self.label,
			"fieldtype": self.fieldtype,
			"reqd": 1 if self.reqd else 0,
			"description": self.description or self.why,
			"module": "ERPNext MCP",
		}
		if self.options:
			row["options"] = self.options
		if self.insert_after:
			row["insert_after"] = self.insert_after
		return row

	def describe(self, doctype: str) -> dict:
		return {
			"doctype": doctype,
			"fieldname": self.fieldname,
			"label": self.label,
			"fieldtype": self.fieldtype,
			"required": bool(self.reqd),
			"options": [line for line in self.options.split("\n") if line] if self.options else [],
			"framework": self.framework,
			"why": self.why,
			"breaks_operationally": self.operational,
		}


@dataclass(frozen=True)
class Target:
	"""One doctype this installer touches, or checks and leaves alone.

	`mode` is "extend" for a doctype belonging to another app, which gets Custom
	Fields; and "verify" for a doctype this app already ships, whose compliance
	columns are declared in its own JSON and should simply be *there*. The second
	kind writes nothing at all: a Housing Unit missing `fsma_worker_facility` is a
	migration that did not finish, and quietly adding a Custom Field over the top
	of it would hide a real problem behind a duplicate column.
	"""

	doctype: str
	owner_app: str
	purpose: str
	fields: tuple = ()
	mode: str = "extend"
	#: What the operator does about it when the doctype is not on this site.
	absent_note: str = ""


# ── Spray Log — farm_precision_ag ───────────────────────────────────────────
#
# The Worker Protection Standard (40 CFR 170) is the reason this doctype carries
# more required fields than any other. A spray record without a restricted-entry
# interval cannot answer the only question that matters the morning after — can
# the crew go in — and a crew sent into a block inside its REI is a human injury,
# not a paperwork finding.
_SPRAY_FIELDS = (
	ComplianceField(
		fieldname="applicator_name",
		label="Applicator",
		fieldtype="Data",
		reqd=True,
		framework="EPA WPS 40 CFR 170.309(f); ORS 634 / OAR 603-057",
		why=(
			"Federal and Oregon pesticide records must name the person who made the "
			"application. Oregon additionally ties the record to a licensed applicator."
		),
		operational=(
			"Nobody can be asked what the tank actually held, whether the nozzles were "
			"the high- or low-volume set, or why a block was skipped. The applicator is "
			"the only person who knows what happened in the field that day."
		),
	),
	ComplianceField(
		fieldname="epa_reg_number",
		label="EPA Registration Number",
		fieldtype="Data",
		reqd=True,
		framework="FIFRA; EPA WPS 40 CFR 170.309(f)(3)",
		why=(
			"The registration number identifies the product as registered for this crop "
			"and this use. It is the number a residue detection is traced back through."
		),
		operational=(
			"The label is the law: without the registration number nothing downstream "
			"can check the product against the crop, the rate or the buyer's maximum "
			"residue limit, so a load can be rejected at the packing house with no way "
			"to find out which block it came from."
		),
	),
	ComplianceField(
		fieldname="rei_hours",
		label="REI (hours)",
		fieldtype="Int",
		reqd=True,
		framework="EPA WPS 40 CFR 170.407 — restricted-entry interval",
		why=(
			"The interval during which workers may not enter the treated area without "
			"PPE. Posting and notification obligations run off it."
		),
		operational=(
			"THE crew-scheduling number. Without it nobody knows when the block can be "
			"picked, thinned or irrigated, and the crew boss guesses. This is the field "
			"that makes the compliance record and the work order the same record."
		),
	),
	ComplianceField(
		fieldname="phi_hours",
		label="PHI (hours)",
		fieldtype="Int",
		reqd=True,
		framework="FIFRA label; FDA tolerances 40 CFR 180",
		why=(
			"The pre-harvest interval: how long after application the fruit may not be "
			"picked. Violating it is a residue violation on a shipped load."
		),
		operational=(
			"Harvest scheduling. A block sprayed inside its PHI cannot be picked, and "
			"the pick date is planned off this number weeks in advance."
		),
	),
	ComplianceField(
		fieldname="weather_temp_f",
		label="Temperature (°F)",
		fieldtype="Float",
		framework="EPA WPS 40 CFR 170.309; label temperature restrictions",
		why=(
			"Many labels restrict application above a stated temperature, and an "
			"inversion is the usual cause of an off-target drift complaint."
		),
		operational=(
			"Efficacy. Half the products in a tank behave differently at 90°F, and the "
			"reason a spray did not work is read out of this column the following week."
		),
	),
	ComplianceField(
		fieldname="weather_wind_mph",
		label="Wind Speed (mph)",
		fieldtype="Float",
		framework="EPA label drift restrictions; ODA drift investigations",
		why=(
			"Nearly every label sets a maximum wind speed. It is the first thing an "
			"Oregon Department of Agriculture drift investigation asks for."
		),
		operational=(
			"Whether to spray at all that morning, and the defence when a neighbour "
			"complains. Without it a drift complaint is unanswerable."
		),
	),
	ComplianceField(
		fieldname="wind_direction",
		label="Wind Direction",
		fieldtype="Data",
		framework="EPA label drift restrictions; ODA drift investigations",
		why=(
			"Direction is what turns a wind speed into a statement about where the "
			"spray went, and about which neighbouring property was downwind."
		),
		operational=(
			"Which end of the block to start at, and which rows to leave for a calmer "
			"day. A drift complaint from upwind answers itself."
		),
	),
	ComplianceField(
		fieldname="target_pest",
		label="Target Pest",
		fieldtype="Data",
		framework="FIFRA label use; IPM records for GAP / GlobalGAP",
		why=(
			"A product applied for a pest not on its label is an off-label application. "
			"Food safety audits ask for the IPM justification for every application."
		),
		operational=(
			"The IPM loop. The threshold that triggered the spray and the assessment of "
			"whether it worked both key off the target pest; without it the next "
			"application is chosen blind."
		),
	),
)


# ── Employee — farm_hr, or Frappe HR ────────────────────────────────────────
#
# These five are the difference between a payroll register and an employment
# record that survives an I-9 audit. Three are required because a person whose
# work authorisation, tax withholding or governing jurisdiction is unknown cannot
# be lawfully paid, and payroll is an operation.
_EMPLOYEE_FIELDS = (
	ComplianceField(
		fieldname="i9_status",
		label="I-9 Status",
		fieldtype="Select",
		reqd=True,
		options="\nVerified\nPending\nExpired\nN-A",
		framework="IRCA 8 USC 1324a; Form I-9",
		why=(
			"Employment eligibility must be verified within three business days of hire "
			"and re-verified when a document expires. ICE fines are per form."
		),
		operational=(
			"Whether this person may be put on a crew at all. Expired means they cannot "
			"lawfully work tomorrow, which is a scheduling fact before it is a filing "
			"fact — and it is what the Sprint 7 alert engine blocks employment on."
		),
	),
	ComplianceField(
		fieldname="w4_status",
		label="W-4 Status",
		fieldtype="Select",
		reqd=True,
		options="\nOn-File\nMissing\nRequires-Update",
		framework="IRC §3402; Form W-4",
		why=(
			"Withholding must follow a signed W-4. Missing means the employer withholds "
			"at the default single rate and owes an explanation if asked."
		),
		operational=(
			"Payroll cannot compute a net cheque without it. Missing is not a reporting "
			"gap, it is a cheque that comes out at the wrong number."
		),
	),
	ComplianceField(
		fieldname="jurisdiction",
		label="Wage-Law Jurisdiction",
		fieldtype="Data",
		reqd=True,
		framework="FLSA; ORS 653 (Oregon); RCW 49.46 (Washington)",
		why=(
			"Wage law follows the location where the work is performed, not where the "
			"employer sits. Oregon and Washington differ on overtime for agricultural "
			"labour, on rest breaks and on minimum wage regions."
		),
		operational=(
			"The minimum wage and the overtime rule used to compute this person's pay. "
			"A crew that crossed the river to a Washington block is paid under a "
			"different rule that day, and this is the field that says so."
		),
		description=(
			"OR, WA, CA or another two-letter state code. Wage law follows the work "
			"location — a crew tagged here as OR that spent the week on a Washington "
			"block is being paid under the wrong rule."
		),
	),
	ComplianceField(
		fieldname="flc_license_status",
		label="FLC License Status",
		fieldtype="Data",
		framework="MSPA 29 USC 1801; ORS 658.405 farm labor contractor licensing",
		why=(
			"Anyone recruiting, supervising or transporting agricultural workers for a "
			"fee needs a farm labor contractor licence, federally and in Oregon. Using "
			"an unlicensed contractor is the grower's violation as well as theirs."
		),
		operational=(
			"Whether this person may lawfully run a crew or drive the bus. An expired "
			"licence takes a crew boss off the schedule that morning."
		),
	),
	ComplianceField(
		fieldname="flc_license_expiration",
		label="FLC License Expiration",
		fieldtype="Date",
		framework="MSPA 29 USC 1801; ORS 658.405",
		why="A licence is only a defence while it is current. The expiration date is the fact.",
		operational=(
			"Feeds the renewal alert. A crew boss whose licence lapses mid-harvest is a "
			"crew with nobody who can lawfully supervise it."
		),
	),
)


# ── Bucket Log Entry — the BucketLog bridge ─────────────────────────────────
#
# Traceability is a chain, and a chain is only as good as its weakest link. The
# FSMA Food Traceability Rule (21 CFR 1 Subpart S) wants a Traceability Lot Code
# that survives from the field to the shipment; these five columns are the links.
# All are audited rather than assumed, because the BucketLog bridge doctype is
# written by whichever version of the iPad app is in the field this season.
_BUCKET_FIELDS = (
	ComplianceField(
		fieldname="picker_id",
		label="Picker",
		fieldtype="Data",
		framework="FSMA 21 CFR 1 Subpart S; GAP worker hygiene traceback",
		why=(
			"A worker health or hygiene investigation traces from a lot back to the "
			"people who handled it. Without the picker the trace stops at the crew."
		),
		operational=(
			"Piecework pay. Every bucket is somebody's money, and an unattributed "
			"bucket is a payroll dispute at the end of the week."
		),
	),
	ComplianceField(
		fieldname="crew_id",
		label="Crew",
		fieldtype="Data",
		framework="FSMA Subpart S; MSPA crew records",
		why=(
			"The crew is the unit a hygiene training record, a field sanitation "
			"inspection and a wage-law jurisdiction all attach to."
		),
		operational=(
			"Who to pay, who to send where tomorrow, and which crew boss answers for "
			"the block. Harvest is organised by crew, not by picker."
		),
	),
	ComplianceField(
		fieldname="block_id",
		label="Block",
		fieldtype="Data",
		framework="FSMA Subpart S critical tracking event; spray REI/PHI linkage",
		why=(
			"The block is where the lot came from, and it is the join to the spray "
			"record — which is how a residue question becomes an answerable question."
		),
		operational=(
			"Yield by block, cost by block, and the REI check that says whether the "
			"block could lawfully be picked at all."
		),
	),
	ComplianceField(
		fieldname="bin_id",
		label="Bin",
		fieldtype="Data",
		framework="FSMA Subpart S — commingling / transformation event",
		why=(
			"A bin is where buckets from several pickers become one lot. It is the "
			"transformation event the rule asks to be recorded."
		),
		operational=(
			"What actually goes on the truck. The bin is the physical unit the packing "
			"house receives and pays against."
		),
	),
	ComplianceField(
		fieldname="shipment_id",
		label="Shipment",
		fieldtype="Data",
		framework="FSMA Subpart S — shipping event; buyer traceback exercises",
		why=(
			"The shipping event closes the chain. A buyer's mock recall is timed, and "
			"an operation that cannot answer in four hours fails the audit."
		),
		operational=(
			"Getting paid. The shipment is what the invoice is raised against, and an "
			"unlinked bin is fruit that left the farm with no receivable behind it."
		),
	),
)


# ── Attendance — hrms ───────────────────────────────────────────────────────
#
# ONE COLUMN, AND IT IS A BRIDGE RATHER THAN A COMPLIANCE FACT IN ITSELF. v0.19.3
# makes the Farm Shift the anchor for exposure-based compliance, and closing a
# shift writes one submitted Attendance per crew member for the span that person
# was actually present. Without a column pointing back at the shift those rows
# are indistinguishable from a hand-keyed day, with two consequences: nobody
# reading an attendance register can get from a day to the water breaks, the
# weather and the supervisor's signature that describe it; and the bridge cannot
# tell its own rows from somebody else's, so re-closing an amended shift would
# duplicate them.
#
# It sits here rather than in `shifts.py` because this file is where every column
# this app grafts onto another app's doctype is declared, and — the part that
# matters — where `before_uninstall` goes looking to warn that removing the app
# drops it.
_ATTENDANCE_FIELDS = (
	ComplianceField(
		fieldname="farm_shift",
		label="Farm Shift",
		fieldtype="Link",
		options="Farm Shift",
		framework="OAR 437-004-1131; FSMA 21 CFR 112.161(b); ORS 653 wage records",
		why=(
			"An attendance row says somebody was at work. The shift says what the "
			"conditions were, what breaks were called, who supervised and who signed. "
			"A heat-illness investigation and a wage claim both start from the day and "
			"need the second, and this link is the only way from one to the other."
		),
		operational=(
			"Payroll reconciliation. A shift-formed day and a hand-keyed day look "
			"identical without it, so nobody can tell which rows a re-closed shift "
			"already wrote — and the bridge, unable to tell either, would pay somebody "
			"twice for one afternoon."
		),
	),
)


# ── Asset — ERPNext ─────────────────────────────────────────────────────────
#
# v0.19.5, AND THE FIRST TARGET IN THIS FILE THAT IS NOT ABOUT A REGULATOR. The
# framework line on each field below says so plainly: this is managerial
# accounting, and it is here rather than in a doctype of ours for exactly the
# argument the module docstring makes about Spray Log.
#
# WHY NOT AN `Asset Capex Profile` BESIDE THE ASSET. v0.7.0 put the cost split in
# an `Asset Cost Profile` precisely so this app would touch nobody else's schema,
# and the obvious move would be to put `capex_type` there too. It fails the test
# this file is built on. The maintenance/growth call is made ONCE, by the person
# raising the purchase, at the moment they know why they are buying the thing —
# the old pump failed, or the new block needs a pump it never had. A profile row
# written afterwards by somebody reconciling the quarter is a person reconstructing
# an intention from an invoice, and they will get it wrong in the direction that
# makes the quarter look better. Six months later nobody alive can say which it
# was.
#
# And it breaks operations, not only reporting. `capex_type` is what a replacement
# budget is built from: an operation that cannot separate "what we spend to stay
# where we are" from "what we spend to get bigger" cannot plan either one, and the
# first thing that happens is that growth is funded out of the maintenance the
# orchard needed.
#
# `capex_type` IS NOT `reqd`, AND THAT IS DELIBERATE. Frappe enforces `reqd` on
# save rather than retroactively, so marking it required would leave every
# existing Asset readable and unsaveable — a farm with two hundred assets would
# find that editing a location on a tractor bought in 2019 now demands a capex
# classification nobody present can make. The gate is in `create_asset` instead,
# where the person raising the purchase is standing, and `backfill_asset_capex_type`
# is how the history gets classified in bulk.
_ASSET_FIELDS = (
	ComplianceField(
		fieldname="capex_type",
		label="Capex Type",
		fieldtype="Select",
		options="\nMaintenance\nGrowth\nMixed",
		framework="Managerial accounting — Sustainable CF/Acre (v0.19.5); lender maintenance-capex covenants",
		why=(
			"Maintenance capex replaces productive capacity that wore out; growth capex "
			"adds capacity that was never there. Sustainable cash flow is what is left "
			"after the first is funded, and an operation that cannot tell them apart "
			"reports growth spending as if it were keeping the orchard whole."
		),
		operational=(
			"The replacement budget. 'What we spend to stay where we are' and 'what we "
			"spend to get bigger' are two different plans, and an operation that cannot "
			"separate them funds the second out of the first — which is deferred "
			"maintenance with a better name."
		),
		description=(
			"Maintenance replaces existing productive capacity (a failed irrigation pump, "
			"a worn-out tractor, a replant in kind). Growth adds capacity that was not "
			"there (a new block, a new zone, a second sprayer). Mixed is split across the "
			"two portion fields, which must sum to the gross purchase amount."
		),
	),
	ComplianceField(
		fieldname="maintenance_portion",
		label="Maintenance Portion",
		fieldtype="Currency",
		framework="Managerial accounting — Sustainable CF/Acre (v0.19.5)",
		why=(
			"A single purchase is often both — a bigger tractor replacing a smaller one "
			"is the old machine's capacity as maintenance and the difference as growth. "
			"Recording only the total forces the whole amount into one bucket and the "
			"KPI reads whichever the person picked."
		),
		operational=(
			"What a replacement reserve is sized against. The maintenance half of a mixed "
			"purchase is the recurring number; the growth half happens once."
		),
	),
	ComplianceField(
		fieldname="growth_portion",
		label="Growth Portion",
		fieldtype="Currency",
		framework="Managerial accounting — Sustainable CF/Acre (v0.19.5)",
		why=(
			"The other half of the split, stored rather than derived. A portion computed "
			"as 'the total minus the other one' cannot disagree with the total, which "
			"sounds like a virtue and means a transposed figure is silently absorbed "
			"instead of refused."
		),
		operational=(
			"What the expansion actually cost, separable from what keeping the existing "
			"ground going cost. It is the number a return-on-new-planting calculation "
			"starts from."
		),
	),
	ComplianceField(
		fieldname="capex_justification",
		label="Capex Justification",
		fieldtype="Small Text",
		framework="Managerial accounting — Sustainable CF/Acre (v0.19.5)",
		why=(
			"Required for Growth and Mixed by `create_asset`: what capacity does this "
			"add? Classifying a purchase as growth takes it out of the maintenance "
			"figure, which raises sustainable cash flow — the one direction in which "
			"a misclassification flatters the operation, and therefore the one that "
			"needs a sentence behind it."
		),
		operational=(
			"The reason the purchase was made, in the words of whoever made it, on the "
			"record it was made against. It is what next year's planning reads to find "
			"out whether the new capacity did what it was bought to do."
		),
	),
)


#: Every doctype this installer knows about, in the order it reports them.
#:
#: The last two are `verify` targets: Housing Unit and Field are this app's own
#: doctypes and already carry their compliance columns in their shipped JSON.
#: They are listed so the report answers "is the whole compliance surface
#: present" rather than "did the three custom-field targets work", which is a
#: different and less useful question.
TARGETS = (
	Target(
		doctype="Spray Log",
		owner_app="farm_precision_ag",
		purpose=(
			"Pesticide application records under FIFRA, the EPA Worker Protection "
			"Standard and Oregon's ORS 634. Every spray is a compliance event; these "
			"are the columns that make it one."
		),
		fields=_SPRAY_FIELDS,
		absent_note=(
			"farm_precision_ag is not installed on this site, so there is no Spray Log "
			"to extend. Install it and re-run `bench migrate` — nothing else is needed."
		),
	),
	Target(
		doctype="Employee",
		owner_app="farm_hr / hrms",
		purpose=(
			"Employment eligibility, tax withholding, the wage law that governs this "
			"person's pay, and farm labor contractor licensing. Every hire is a "
			"compliance event."
		),
		fields=_EMPLOYEE_FIELDS,
		absent_note=(
			"No HR app on this site, so there is no Employee register to extend. "
			"Install farm_hr or Frappe HR and re-run `bench migrate`."
		),
	),
	Target(
		doctype="Bucket Log Entry",
		owner_app="the BucketLog bridge",
		purpose=(
			"Harvest chain of custody: bucket → picker → crew → block → bin → shipment. "
			"The FSMA Food Traceability Rule's critical tracking events, in the record "
			"the iPad already writes."
		),
		fields=_BUCKET_FIELDS,
		absent_note=(
			"The BucketLog bridge doctype is not on this site. The columns are audited "
			"rather than assumed because the bridge is written by whichever version of "
			"the iPad app is in the field this season."
		),
	),
	Target(
		doctype="Attendance",
		owner_app="hrms",
		purpose=(
			"The one-way bridge from a closed Farm Shift to the payroll register. A shift "
			"close writes one submitted Attendance per crew member for that person's own "
			"span, and this column is what says which shift it came from — so farm_hr has "
			"one canonical answer to 'when was Ana at work' and an investigator reading "
			"that day can get to the conditions she worked in."
		),
		fields=_ATTENDANCE_FIELDS,
		absent_note=(
			"No HR app on this site, so there is no Attendance register to extend. The "
			"shift's own crew table still carries every joined_at and left_at — nothing "
			"about the compliance record depends on the bridge. Install Frappe HR and "
			"re-run `bench migrate`, and the next shift close writes the rows."
		),
	),
	Target(
		doctype="Asset",
		owner_app="erpnext",
		purpose=(
			"The maintenance-versus-growth split every sustainable cash flow figure is "
			"read through. Maintenance capex replaces what wore out and growth capex buys "
			"capacity that was never there; an operation that cannot tell them apart "
			"cannot say whether a good year was earned or borrowed from the orchard."
		),
		fields=_ASSET_FIELDS,
		absent_note=(
			"This site has no Asset doctype, which means ERPNext's asset module is not "
			"present. get_sustainable_cf_per_acre still computes — it reports a "
			"maintenance capex of zero and says why in its warnings, which is the honest "
			"answer for a site that records no fixed assets at all."
		),
	),
	Target(
		doctype="Housing Unit",
		owner_app="erpnext_mcp",
		mode="verify",
		purpose=(
			"FSMA Produce Safety Rule Subpart L worker facilities, and the habitability "
			"and detector-test dates Oregon's agricultural labor housing rules turn on. "
			"Shipped as declared fields in v0.12.0, verified here."
		),
		fields=(
			ComplianceField(
				fieldname="fsma_worker_facility",
				label="FSMA Worker Facility",
				fieldtype="Check",
				framework="FSMA Produce Safety Rule 21 CFR 112 Subpart L",
				why=(
					"Which of fifty buildings are subject to the worker facility "
					"sanitation requirements. Without the flag every building is either "
					"in scope or none is."
				),
				operational=(
					"Which buildings get walked on the sanitation round, and which need "
					"supplies restocked before a crew arrives."
				),
			),
			ComplianceField(
				fieldname="last_habitability_inspection",
				label="Last Habitability Inspection",
				fieldtype="Date",
				framework="OAR 437-004-1120 agricultural labor housing; 29 CFR 1910.142",
				why="Annual habitability inspection is the cadence a camp is walked on.",
				operational=(
					"Whether a cabin can be assigned. An uninspected unit is one nobody "
					"has confirmed has running water this season."
				),
			),
			ComplianceField(
				fieldname="smoke_detector_last_test",
				label="Smoke Detector Last Test",
				fieldtype="Date",
				framework="OAR 437-004-1120; ORS 479 smoke alarm requirements",
				why="A detector nobody has tested is a detector nobody knows works.",
				operational="Somebody sleeps there tonight.",
			),
			ComplianceField(
				fieldname="co_detector_last_test",
				label="CO Detector Last Test",
				fieldtype="Date",
				framework="OAR 437-004-1120; ORS 690 carbon monoxide alarms",
				why=(
					"Required wherever there is a fuel-burning appliance, which on a camp "
					"cabin usually means a propane heater."
				),
				operational="Somebody sleeps there tonight.",
			),
		),
	),
	Target(
		doctype="Field",
		owner_app="erpnext_mcp",
		mode="verify",
		purpose=(
			"Food safety zoning, the agricultural water and spray dates the Produce "
			"Safety Rule turns on, and — from v0.19.5 — the dates that say when this "
			"block was actually earning. Shipped as declared fields in v0.12.0 and "
			"v0.19.5, verified here."
		),
		fields=(
			ComplianceField(
				fieldname="productive_from_date",
				label="Productive From",
				fieldtype="Date",
				framework="Managerial accounting — Sustainable CF/Acre (v0.19.5)",
				why=(
					"The denominator of every per-acre metric is what is PRODUCTIVE, not "
					"what is owned. Without this date a pre-yield block counts as earning "
					"ground and every per-acre figure is understated by however much of the "
					"farm is still coming into bearing."
				),
				operational=(
					"When a block starts being budgeted as a crop rather than as capital "
					"under construction. It is what a picking plan, a bin forecast and a "
					"crew estimate all key off."
				),
			),
			ComplianceField(
				fieldname="productive_through_date",
				label="Productive Through",
				fieldtype="Date",
				framework="Managerial accounting — Sustainable CF/Acre (v0.19.5)",
				why=(
					"A block pulled in July earned for half the year. Null means still "
					"productive, which is the ordinary case; a date means the acreage stops "
					"counting from it, pro-rated."
				),
				operational=(
					"Whether to send a crew there next season, and whether the water and "
					"spray programme still applies to it."
				),
			),
			ComplianceField(
				fieldname="pre_yield_end_date",
				label="Pre-Yield End",
				fieldtype="Date",
				framework="Managerial accounting — Sustainable CF/Acre (v0.19.5)",
				why=(
					"Perennials spend their first years as capital rather than as crop — "
					"cherry is commonly three or four. Recorded separately from "
					"`productive_from_date` so a block still in its pre-yield years is "
					"COUNTED and reported rather than merely absent: those acres are next "
					"year's denominator, and a reader who cannot see them coming cannot "
					"read the trend."
				),
				operational=(
					"When the block moves onto the picking plan, and when the establishment "
					"budget stops. Both are planned years ahead off this date."
				),
			),
			ComplianceField(
				fieldname="food_safety_zone",
				label="Food Safety Zone",
				fieldtype="Data",
				framework="FSMA Produce Safety Rule 21 CFR 112; GAP / GlobalGAP zoning",
				why=(
					"Zoning is how a hazard assessment is expressed on the ground — which "
					"ground is adjacent to a dairy, a road, a wildlife corridor."
				),
				operational=(
					"Which blocks get walked for animal intrusion before a pick, and which "
					"can be picked at all after a flood event."
				),
			),
			ComplianceField(
				fieldname="last_spray_date",
				label="Last Spray Date",
				fieldtype="Date",
				framework="EPA WPS 40 CFR 170.407 REI; FIFRA label PHI",
				why="The date the REI and PHI windows are counted from.",
				operational=(
					"Whether a crew can enter this block today. It is read before every "
					"pick and every thinning pass."
				),
			),
		),
	),
)


def targets_by_doctype() -> dict:
	return {target.doctype: target for target in TARGETS}


# ── the installer ───────────────────────────────────────────────────────────
def install_compliance_fields(dry_run: bool = False, respect_switch: bool = True) -> dict:
	"""Add every missing compliance field. Idempotent, and NEVER raises.

	Never raises for the same reason `ensure_party_types` does not: this runs from
	`after_migrate`, and an exception there aborts `bench migrate` for the whole
	bench. A field that cannot be added is worth reporting; it is not worth taking
	somebody's migration down over, and least of all on a site where the failure is
	that another app's doctype is half-migrated at the moment we look at it.

	`respect_switch` is False only for the MCP tool, where the dispatcher has
	already checked the same switch and checking it twice would report "off" for a
	call that got through.

	Returns a report: per target, what was created, what was already there, what
	could not be done and why, and — the number worth reading — how many existing
	rows do not satisfy each newly required field.
	"""
	report = {
		"created": [],
		"existing": [],
		"skipped": [],
		"failed": [],
		"targets": [],
		"dry_run": bool(dry_run),
		"switch": f"allow_{SWITCH}",
		"enabled": True,
	}

	if respect_switch and not _switch_on():
		report["enabled"] = False
		report["skipped"] = [
			{
				"doctype": target.doctype,
				"reason": (
					f"allow_{SWITCH} is off, so this app adds no field to any doctype. "
					"Compliance fields are the one place erpnext_mcp extends another "
					"app's schema, and an operator who has turned that off means it."
				),
			}
			for target in TARGETS
		]
		return report

	for target in TARGETS:
		report["targets"].append(_apply(target, dry_run, report))
	return report


def _switch_on() -> bool:
	try:
		return settings.tool_enabled(SWITCH)
	except Exception:
		# Settings unreadable — a request landing mid-migrate, or the Single not
		# yet created on a first install. The switch defaults ON, and the whole
		# point of this installer is that it runs on a fresh site, so the safe
		# answer here is the declared default rather than "off".
		return True


def _apply(target: Target, dry_run: bool, report: dict) -> dict:
	out = {
		"doctype": target.doctype,
		"owner_app": target.owner_app,
		"mode": target.mode,
		"purpose": target.purpose,
		"installed": False,
		"created": [],
		"existing": [],
		"missing": [],
		"failed": [],
		"backlog": {},
	}

	if not doctype_present(target.doctype):
		out["note"] = target.absent_note or f"{target.doctype} is not installed on this site."
		report["skipped"].append({"doctype": target.doctype, "reason": out["note"]})
		return out
	out["installed"] = True

	for spec in target.fields:
		if _existing(target.doctype, spec.fieldname):
			out["existing"].append(spec.fieldname)
			report["existing"].append(f"{target.doctype}.{spec.fieldname}")
			if spec.reqd:
				out["backlog"][spec.fieldname] = _backlog(target.doctype, spec.fieldname)
			continue

		if target.mode == "verify":
			# See Target.mode. A declared field that is absent is an unfinished
			# migration, and papering over it with a Custom Field would leave the
			# site with two columns and no error.
			out["missing"].append(spec.fieldname)
			report["failed"].append(
				{
					"doctype": target.doctype,
					"fieldname": spec.fieldname,
					"reason": (
						f"{target.doctype}.{spec.fieldname} ships as a declared field of this "
						"app's own DocType and is not on this site — which means the DocType "
						"did not migrate, not that a Custom Field is missing. Run `bench "
						"--site <site> migrate`. Nothing was added: a Custom Field over the "
						"top of an unfinished migration would give this site two columns and "
						"no error."
					),
				}
			)
			continue

		if dry_run:
			out["created"].append(spec.fieldname)
			report["created"].append(f"{target.doctype}.{spec.fieldname}")
			if spec.reqd:
				out["backlog"][spec.fieldname] = _backlog(target.doctype, spec.fieldname)
			continue

		problem = _create(target.doctype, spec)
		if problem:
			out["failed"].append({"fieldname": spec.fieldname, "reason": problem})
			report["failed"].append(
				{"doctype": target.doctype, "fieldname": spec.fieldname, "reason": problem}
			)
			continue
		out["created"].append(spec.fieldname)
		report["created"].append(f"{target.doctype}.{spec.fieldname}")
		if spec.reqd:
			out["backlog"][spec.fieldname] = _backlog(target.doctype, spec.fieldname)

	return out


def doctype_present(doctype: str) -> bool:
	"""Is the target doctype on this site? Public: `get_compliance_field_map` asks."""
	try:
		return compat.doctype_exists(doctype)
	except Exception:
		return False


def field_present(doctype: str, fieldname: str) -> bool:
	"""Is one compliance field already here? Public for the same reason."""
	return _existing(doctype, fieldname)


def _existing(doctype: str, fieldname: str) -> bool:
	"""Is the field on this site AT ALL — declared, custom, or added by anybody?

	Deliberately not "is there a Custom Field row we wrote". A later version of
	farm_precision_ag that adds `epa_reg_number` itself must not end up with two
	columns, and an operator who added the field by hand in the Desk has already
	solved the problem this installer exists to solve.
	"""
	try:
		if compat.has_field(doctype, fieldname):
			return True
	except Exception:
		return False
	try:
		return bool(frappe.db.exists(CUSTOM_FIELD, {"dt": doctype, "fieldname": fieldname}))
	except Exception:
		return False


def _create(doctype: str, spec: ComplianceField) -> str:
	"""Insert one Custom Field. Returns "" on success, or why it could not."""
	try:
		doc = frappe.new_doc(CUSTOM_FIELD)
		for key, value in spec.as_custom_field(doctype).items():
			if key == "module" and not compat.has_field(CUSTOM_FIELD, "module"):
				continue
			if key == "insert_after" and not compat.has_field(doctype, value):
				# The anchor field is not on this site's version of the doctype.
				# Frappe puts the field at the end, which is cosmetic. Losing the
				# field over a layout preference would not be.
				continue
			doc.set(key, value)
		doc.insert(ignore_permissions=True)
		return ""
	except Exception as exc:
		return f"{type(exc).__name__}: {exc}"


def _backlog(doctype: str, fieldname: str) -> dict:
	"""How many existing rows do not satisfy a newly required field.

	This is the number worth reading in the whole report. `reqd` binds on save,
	not retroactively, so the history stays readable — but every one of these rows
	is a record that was never compliant, and re-saving one is now refused. The
	count is the operation's compliance backlog stated in rows.
	"""
	out = {"rows_missing_a_value": None, "total_rows": None, "note": ""}
	try:
		total = frappe.db.count(doctype)
	except Exception:
		out["note"] = "this site would not answer a row count for that doctype"
		return out
	out["total_rows"] = int(total or 0)
	if not total:
		out["note"] = "no existing rows, so nothing to backfill"
		return out
	if total > BACKLOG_CAP:
		out["note"] = (
			f"more than {BACKLOG_CAP} rows; not counted. Query the doctype directly if the "
			"exact backlog matters."
		)
		return out
	try:
		missing = frappe.db.count(doctype, {fieldname: ("in", (None, ""))})
	except Exception:
		# A column that exists in meta but not yet in the table — the Custom Field
		# was inserted in this same transaction and the ALTER has not landed. Every
		# existing row is missing a value by definition.
		out["rows_missing_a_value"] = int(total or 0)
		out["note"] = (
			"the column was created in this run, so every existing row is missing a value "
			"until somebody fills it in."
		)
		return out
	out["rows_missing_a_value"] = int(missing or 0)
	if missing:
		out["note"] = (
			f"{missing} of {total} existing {doctype} record(s) have no value for this now-required "
			"field. They remain readable — Frappe enforces `reqd` on save, not retroactively — but "
			"none of them can be re-saved until it is filled in, and none of them is evidence of a "
			"compliant operation."
		)
	return out


# ── documentation, generated from the table above ───────────────────────────
def describe() -> dict:
	"""The whole compliance surface as data, for a tool and for the docs.

	`docs/compliance_fields.md` is written from this, so a field added to the
	table above cannot ship undocumented — which is the failure mode a hand-kept
	table of the same information has every single time.
	"""
	return {
		"targets": [
			{
				"doctype": target.doctype,
				"owner_app": target.owner_app,
				"mode": target.mode,
				"purpose": target.purpose,
				"field_count": len(target.fields),
				"required_fields": [spec.fieldname for spec in target.fields if spec.reqd],
				"fields": [spec.describe(target.doctype) for spec in target.fields],
			}
			for target in TARGETS
		],
		"field_count": sum(len(target.fields) for target in TARGETS),
		"required_field_count": sum(
			1 for target in TARGETS for spec in target.fields if spec.reqd
		),
		"frameworks": sorted(
			{spec.framework for target in TARGETS for spec in target.fields}
		),
	}
