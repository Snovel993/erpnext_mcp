# SPDX-License-Identifier: MIT
"""Sustainable CF/Acre — the judgement, the split, the denominator and the arithmetic.

THE CLAIM BEHIND THE RELEASE is that a per-acre cash flow figure is only worth
quoting if every ingredient is on the record. Headline OCF is flattered by money
that will not come in again and flattered again by maintenance that was not done,
and the corrections for both are judgements somebody has to defend. So the tests
here are mostly about REFUSALS, and each one names the direction the mistake
would fall in.

SEVEN CLAIMS.

1. `TheJustificationIsTheRecord` — a proposal is a DRAFT and nothing in the create
   tool can make it count; a justification under forty characters is refused; an
   approval with no signature is refused; an approved adjustment counts and a
   draft, a rejection and a superseded row do not.

2. `OneApprovedAdjustmentPerPeriodAndCategory` — the second approval for the same
   company, period and category is refused by name, at the tool AND at the
   controller, and the correction path is supersession.

3. `TheCapexSplit` — `create_asset` refuses without a classification once the
   column is live; a Mixed split that does not add up to the invoice is refused
   within a cent; Growth and Mixed need a justification and Maintenance does not.

4. `TheBackfill` — dry by default and writes nothing; a cutoff date bounds it;
   the second run classifies zero rows because it never touches a row that has an
   answer.

5. `TheDenominatorIsWhatIsProductive` — the four time-weighting cases from the
   spec, arithmetic rather than approximation, plus the one that matters most:
   a block with no `productive_from_date` is EXCLUDED and warned about rather
   than assumed to be earning.

6. `TheArithmetic` — known inputs to a known answer, zero acres to None rather
   than to zero, unclassified assets excluded and flagged, and one company's
   figure containing nothing of another's.

7. `TheGuards` — the role gate (which is NOT the HR one), the company scope and
   the kill switch, on all six tools.
"""

import frappe

from erpnext_mcp import kpi
from erpnext_mcp.services import sustainable_cf_per_acre as service

from .fixtures import MAIN, MAIN_ABBR, OTHER, OTHER_ABBR, V12TestCase, cash, sales, supplies
from .harness import ROLES, STORE, set_roles

#: Every switch this suite needs. Listed rather than globbed so that turning one
#: off in a test is visibly a change from the on-by-default posture.
ON = {
	f"allow_{name}": 1
	for name in (
		"create_normalization_adjustment",
		"approve_normalization_adjustment",
		"reject_normalization_adjustment",
		"backfill_asset_capex_type",
		"list_normalization_adjustments",
		"get_sustainable_cf_per_acre",
		"create_asset",
		"create_field",
		"create_parcel",
		"update_field",
	)
}

SIGNATURE = "/files/accountant-signature.png"

#: A justification that clears the forty-character floor by being an actual
#: argument, which is the only kind that clears it in the sense that matters.
WHY = (
	"Hail on 2026-04-11 destroyed the frost fans on blocks 3 and 4; the replacement was a "
	"single insured event and the last hail loss on this ground was 2011."
)

YEAR = "2026"


class KPITestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)

	# ── building blocks ─────────────────────────────────────────────────────
	def an_adjustment(self, **overrides):
		payload = {
			"company": MAIN,
			"fiscal_year": YEAR,
			"period_start": "2026-01-01",
			"period_end": "2026-03-31",
			"amount": 20000,
			"direction": "Add-back to OCF",
			"category": "Weather-Event-Loss",
			"justification": WHY,
		}
		payload.update(overrides)
		return self.tool_data("create_normalization_adjustment", payload)

	def approved(self, **overrides):
		draft = self.an_adjustment(**overrides)
		return self.tool_data(
			"approve_normalization_adjustment",
			{"name": draft["name"], "approver_signature_file_token": SIGNATURE},
		)

	def a_field(self, name, acreage, productive_from=None, productive_through=None, **overrides):
		"""A Field row written straight to the store.

		Seeded rather than created through `create_field` because these tests are
		about the DENOMINATOR's arithmetic, and going through the tool would drag in
		the parcel acreage rule — which is tested where it belongs, in test_farm.py,
		and which would make a fifty-acre block need a fifty-acre parcel behind it
		in every case here.
		"""
		row = {
			"name": name,
			"field_name": name,
			"parcel": "Mill Creek",
			"owning_entity": overrides.pop("company", MAIN),
			"acreage": acreage,
			"productive_from_date": productive_from,
			"productive_through_date": productive_through,
			"pre_yield_end_date": None,
			"condition": "Good",
		}
		row.update(overrides)
		STORE.seed("Field", [row])
		return row

	def an_asset(self, name, amount, purchase_date="2026-02-01", capex_type=None, **overrides):
		row = {
			"name": name,
			"asset_name": name,
			"company": overrides.pop("company", MAIN),
			"purchase_date": purchase_date,
			"gross_purchase_amount": amount,
			"docstatus": 1,
			"capex_type": capex_type,
			"maintenance_portion": overrides.pop("maintenance_portion", None),
			"growth_portion": overrides.pop("growth_portion", None),
		}
		row.update(overrides)
		STORE.seed("Asset", [row])
		return row

	def install_capex_columns(self):
		"""Add the four Asset capex columns the way `bench migrate` does.

		Through the real installer rather than by registering the fields by hand,
		which is what makes the "this site has not migrated yet" branch of
		`kpi.capex_installed()` reachable in the tests that do NOT call this — and
		makes the happy path here the actual shipped code rather than a fixture
		pretending to be it.
		"""
		from erpnext_mcp import compliance_fields

		compliance_fields.install_compliance_fields(respect_switch=False)

	def kpi_for(self, company=MAIN, start="2026-01-01", end="2026-03-31"):
		return self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": company, "period_start": start, "period_end": end},
		)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheJustificationIsTheRecord(KPITestCase):
	def test_a_proposal_is_a_draft_and_says_it_does_not_count(self):
		data = self.an_adjustment()
		self.assertEqual(data["status"], kpi.STATUS_DRAFT)
		self.assertTrue(data["name"].startswith("NADJ-2026-"))
		self.assertIn("does NOT count", data["note"])
		self.assertIn("approve_normalization_adjustment", data["next_step"])

	def test_the_series_takes_the_year_of_the_period_not_of_today(self):
		"""A Q4 adjustment written up in January is about the year that ended."""
		data = self.an_adjustment(fiscal_year="2025", period_start="2025-10-01", period_end="2025-12-31")
		self.assertTrue(data["name"].startswith("NADJ-2025-"), data["name"])

	def test_a_justification_under_the_floor_is_refused_with_the_question_it_must_answer(self):
		message = self.tool_error(
			"create_normalization_adjustment",
			{
				"company": MAIN,
				"fiscal_year": YEAR,
				"period_start": "2026-01-01",
				"period_end": "2026-03-31",
				"amount": 20000,
				"direction": "Add-back to OCF",
				"category": "Weather-Event-Loss",
				"justification": "one-time",
			},
		)
		self.assertIn("40", message)
		self.assertIn("WHY WILL THIS NOT HAPPEN AGAIN", message)
		self.assertEqual(STORE.rows(kpi.DOCTYPE), [])

	def test_a_negative_amount_is_refused_because_direction_carries_the_sign(self):
		message = self.tool_error(
			"create_normalization_adjustment",
			{
				"company": MAIN,
				"fiscal_year": YEAR,
				"period_start": "2026-01-01",
				"period_end": "2026-03-31",
				"amount": -20000,
				"direction": "Subtract from OCF",
				"category": "Insurance-Proceeds",
				"justification": WHY,
			},
		)
		self.assertIn("POSITIVE", message)
		self.assertIn("double negative", message)

	def test_a_period_outside_the_fiscal_year_is_refused_with_both_windows(self):
		message = self.tool_error(
			"create_normalization_adjustment",
			{
				"company": MAIN,
				"fiscal_year": YEAR,
				"period_start": "2025-12-01",
				"period_end": "2026-03-31",
				"amount": 100,
				"direction": "Add-back to OCF",
				"category": "Other",
				"justification": WHY,
			},
		)
		self.assertIn("2026-01-01", message)
		self.assertIn("straddling two", message)

	def test_approving_without_a_signature_is_refused(self):
		draft = self.an_adjustment()
		message = self.tool_error("approve_normalization_adjustment", {"name": draft["name"]})
		self.assertIn("approver_signature_file_token is required", message)
		self.assertEqual(STORE.get_raw(kpi.DOCTYPE, draft["name"])["status"], kpi.STATUS_DRAFT)

	def test_the_controller_refuses_an_approved_status_with_no_signature(self):
		"""The tool is one door; the Desk is another, and the rule is on the record."""
		draft = self.an_adjustment()
		doc = frappe.get_doc(kpi.DOCTYPE, draft["name"])
		doc.status = kpi.STATUS_APPROVED
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("signature", str(caught.exception))

	def test_approving_writes_the_timestamp_rather_than_taking_it(self):
		data = self.approved()
		self.assertEqual(data["status"], kpi.STATUS_APPROVED)
		self.assertTrue(data["approved_on"])
		self.assertTrue(data["has_approver_signature"])
		self.assertIn("counts towards Sustainable CF/Acre", data["note"])

	def test_an_approved_adjustment_counts_and_a_draft_does_not(self):
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.an_adjustment(amount=5000, category="Other")  # left as a draft
		self.approved(amount=20000, category="Weather-Event-Loss")

		data = self.kpi_for()
		self.assertEqual(len(data["normalization_adjustments"]), 1)
		self.assertEqual(data["normalization_adjustments_total_addback"], 20000.0)
		self.assertEqual(data["normalized_ocf"], round(data["raw_ocf"]["value"] + 20000, 2))

	def test_a_rejection_is_kept_with_its_reason_and_does_not_count(self):
		draft = self.an_adjustment()
		data = self.tool_data(
			"reject_normalization_adjustment",
			{"name": draft["name"], "rejection_reason": "Hail is a three-year cycle on this ground."},
		)
		self.assertEqual(data["status"], kpi.STATUS_REJECTED)
		self.assertIn("three-year cycle", data["rejection_reason"])
		self.assertIn("KEPT rather than deleted", data["note"])

		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.assertEqual(self.kpi_for()["normalization_adjustments"], [])

	def test_an_approved_adjustment_cannot_be_rejected_only_superseded(self):
		approved = self.approved()
		message = self.tool_error(
			"reject_normalization_adjustment",
			{"name": approved["name"], "rejection_reason": "changed my mind"},
		)
		self.assertIn("Supersede it instead", message)

	def test_a_superseded_adjustment_stops_counting(self):
		approved = self.approved()
		correction = self.an_adjustment(amount=17500)
		doc = frappe.get_doc(kpi.DOCTYPE, approved["name"])
		doc.status = kpi.STATUS_SUPERSEDED
		doc.superseded_by = correction["name"]
		doc.save()

		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.assertEqual(self.kpi_for()["normalization_adjustments"], [])

	def test_the_register_separates_what_counts_from_what_is_waiting(self):
		self.approved(amount=20000, category="Weather-Event-Loss")
		self.an_adjustment(amount=5000, category="Other")
		data = self.tool_data("list_normalization_adjustments", {"company": MAIN})
		self.assertEqual(len(data["counted_in_the_kpi"]), 1)
		self.assertEqual(len(data["awaiting_a_decision"]), 1)
		self.assertEqual(data["totals_of_approved"]["total_addback"], 20000.0)


# ── 2 ───────────────────────────────────────────────────────────────────────
class OneApprovedAdjustmentPerPeriodAndCategory(KPITestCase):
	def test_the_second_approval_is_refused_by_name(self):
		first = self.approved()
		second = self.an_adjustment(amount=17500)
		message = self.tool_error(
			"approve_normalization_adjustment",
			{"name": second["name"], "approver_signature_file_token": SIGNATURE},
		)
		self.assertIn(first["name"], message)
		self.assertIn("TWO ANSWERS TO ONE QUESTION", message)
		self.assertEqual(STORE.get_raw(kpi.DOCTYPE, second["name"])["status"], kpi.STATUS_DRAFT)

	def test_the_controller_refuses_it_too(self):
		self.approved()
		second = self.an_adjustment(amount=17500)
		doc = frappe.get_doc(kpi.DOCTYPE, second["name"])
		doc.status = kpi.STATUS_APPROVED
		doc.approver_signature = SIGNATURE
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("already an approved", str(caught.exception))

	def test_a_different_category_in_the_same_period_is_allowed(self):
		"""A quarter can have both a hail loss and an insurance recovery in it."""
		self.approved(category="Weather-Event-Loss")
		second = self.approved(category="Insurance-Proceeds", direction="Subtract from OCF")
		self.assertEqual(second["status"], kpi.STATUS_APPROVED)

	def test_the_draft_warns_about_a_competing_approval_before_anybody_tries(self):
		first = self.approved()
		draft = self.an_adjustment(amount=17500)
		self.assertIn(first["name"], draft["duplicate_warning"])
		self.assertIn("Superseded By", draft["duplicate_warning"])

	def test_superseding_frees_the_slot(self):
		first = self.approved()
		correction = self.an_adjustment(amount=17500)
		doc = frappe.get_doc(kpi.DOCTYPE, first["name"])
		doc.status = kpi.STATUS_SUPERSEDED
		doc.superseded_by = correction["name"]
		doc.save()

		data = self.tool_data(
			"approve_normalization_adjustment",
			{"name": correction["name"], "approver_signature_file_token": SIGNATURE},
		)
		self.assertEqual(data["status"], kpi.STATUS_APPROVED)

	def test_approving_twice_is_refused_rather_than_rewriting_the_timestamp(self):
		approved = self.approved()
		message = self.tool_error(
			"approve_normalization_adjustment",
			{"name": approved["name"], "approver_signature_file_token": SIGNATURE},
		)
		self.assertIn("already Approved", message)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheCapexSplit(KPITestCase):
	"""`create_asset` is where the maintenance/growth call is made, and it is a gate."""

	def setUp(self):
		super().setUp()
		from .fixtures import seed_v7

		seed_v7()
		self.install_capex_columns()

	def a_purchase(self, **overrides):
		payload = {
			"company": MAIN,
			"asset_name": "Irrigation Pump",
			"item_code": "PUMP-01",
			"asset_category": "Farm Equipment",
			"purchase_date": "2026-02-01",
			"purchase_amount": 30000,
			"useful_life_months": 60,
		}
		payload.update(overrides)
		return payload

	def test_no_capex_type_is_refused_and_says_why_there_is_no_default(self):
		message = self.tool_error("create_asset", self.a_purchase())
		self.assertIn("capex_type is required", message)
		self.assertIn("There is no default", message)
		self.assertEqual(STORE.rows("Asset"), [])

	def test_maintenance_defaults_its_portion_to_the_whole_purchase(self):
		data = self.tool_data("create_asset", self.a_purchase(capex_type="Maintenance"))
		self.assertEqual(data["capex_type"], "Maintenance")
		self.assertEqual(data["maintenance_portion"], 30000.0)
		self.assertEqual(data["growth_portion"], 0.0)

	def test_growth_needs_a_justification_and_maintenance_does_not(self):
		message = self.tool_error("create_asset", self.a_purchase(capex_type="Growth"))
		self.assertIn("capex_justification is required", message)
		self.assertIn("WHAT CAPACITY DOES THIS ADD", message)

		data = self.tool_data(
			"create_asset",
			self.a_purchase(
				capex_type="Growth",
				capex_justification="Second sprayer; the block count went from nine to fourteen.",
			),
		)
		self.assertEqual(data["growth_portion"], 30000.0)
		self.assertEqual(data["maintenance_portion"], 0.0)

	def test_a_mixed_split_that_does_not_add_up_is_refused(self):
		message = self.tool_error(
			"create_asset",
			self.a_purchase(
				capex_type="Mixed",
				maintenance_portion=20000,
				growth_portion=5000,
				capex_justification="Bigger tractor; the extra is the capacity the old one lacked.",
			),
		)
		self.assertIn("25000", message)
		self.assertIn("30000", message)
		self.assertEqual(STORE.rows("Asset"), [])

	def test_a_rounded_cent_is_not_a_disagreement(self):
		data = self.tool_data(
			"create_asset",
			self.a_purchase(
				capex_type="Mixed",
				maintenance_portion=20000.005,
				growth_portion=9999.995,
				capex_justification="Bigger tractor; the extra is the capacity the old one lacked.",
			),
		)
		self.assertEqual(data["capex_type"], "Mixed")

	def test_mixed_needs_both_portions(self):
		message = self.tool_error(
			"create_asset",
			self.a_purchase(
				capex_type="Mixed",
				maintenance_portion=20000,
				capex_justification="Bigger tractor; the extra is the capacity the old one lacked.",
			),
		)
		self.assertIn("BOTH maintenance_portion and growth_portion", message)

	def test_the_gate_does_not_bite_before_the_column_exists(self):
		"""A tool that refused every asset for want of an unmigrated column would
		take asset creation down for the length of an upgrade."""
		self.assertTrue(kpi.capex_installed())
		STORE.tables["Custom Field"] = {}
		from .harness import reset_meta

		reset_meta()
		self.assertFalse(kpi.capex_installed())
		data = self.tool_data("create_asset", self.a_purchase())
		self.assertIsNone(data["capex_type"])


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheBackfill(KPITestCase):
	def setUp(self):
		super().setUp()
		self.install_capex_columns()
		self.an_asset("OLD-PUMP", 12000, purchase_date="2019-06-01")
		self.an_asset("OLD-TRACTOR", 48000, purchase_date="2022-03-01")
		self.an_asset("NEW-SPRAYER", 30000, purchase_date="2026-02-01")
		self.an_asset("ALREADY", 5000, purchase_date="2018-01-01", capex_type="Growth")

	def test_it_is_dry_by_default_and_writes_nothing(self):
		data = self.tool_data("backfill_asset_capex_type", {})
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["classified"], 3)
		self.assertIn("NOTHING WAS WRITTEN", data["dry_run_note"])
		for name in ("OLD-PUMP", "OLD-TRACTOR", "NEW-SPRAYER"):
			self.assertIn(STORE.get_raw("Asset", name)["capex_type"], (None, ""))

	def test_a_cutoff_date_bounds_it(self):
		data = self.tool_data(
			"backfill_asset_capex_type", {"cutoff_purchase_date": "2025-01-01", "dry_run": False}
		)
		self.assertEqual(data["classified"], 2)
		self.assertEqual(STORE.get_raw("Asset", "OLD-PUMP")["capex_type"], "Maintenance")
		self.assertEqual(STORE.get_raw("Asset", "OLD-PUMP")["maintenance_portion"], 12000.0)
		self.assertIn(STORE.get_raw("Asset", "NEW-SPRAYER")["capex_type"], (None, ""))

	def test_it_never_overwrites_a_classification_somebody_made(self):
		self.tool_data("backfill_asset_capex_type", {"dry_run": False})
		self.assertEqual(STORE.get_raw("Asset", "ALREADY")["capex_type"], "Growth")

	def test_the_second_run_classifies_nothing(self):
		first = self.tool_data("backfill_asset_capex_type", {"dry_run": False})
		second = self.tool_data("backfill_asset_capex_type", {"dry_run": False})
		self.assertEqual(first["classified"], 3)
		self.assertEqual(second["classified"], 0)
		self.assertIn("already classified", second["empty_note"])

	def test_mixed_is_refused_as_a_bulk_default(self):
		message = self.tool_error(
			"backfill_asset_capex_type", {"default_capex_type": "Mixed", "dry_run": False}
		)
		self.assertIn("cannot be a bulk default", message)

	def test_it_says_it_is_a_starting_position_rather_than_an_answer(self):
		data = self.tool_data("backfill_asset_capex_type", {})
		self.assertIn("STARTING POSITION, NOT AN ANSWER", data["note"])
		self.assertIn("existing productive plant carrying on", data["heuristic"])


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheDenominatorIsWhatIsProductive(KPITestCase):
	"""Time weighting, arithmetic rather than approximation.

	Inclusive days at both ends, because `period_end` is an inclusive date
	everywhere else in this app: 1 January to 31 March is ninety days in 2026, not
	eighty-nine. That choice is what makes every case below a round number instead
	of a number with a tolerance on it.
	"""

	def acres(self, start="2026-01-01", end="2026-03-31"):
		return service.productive_acres(MAIN, start, end)

	def test_a_block_productive_all_year_counts_in_full_for_a_quarter(self):
		self.a_field("Block 1 - MC", 40.0, productive_from="2026-01-01", productive_through="2026-12-31")
		out = self.acres()
		self.assertEqual(out["period_days"], 90)
		self.assertEqual(out["time_weighted"], 40.0)
		self.assertEqual(out["field_count_productive"], 1)

	def test_a_block_coming_into_bearing_mid_period_is_weighted(self):
		"""Productive from 15 February is forty-five of the quarter's ninety days."""
		self.a_field("Block 2 - MC", 40.0, productive_from="2026-02-15", productive_through="2026-12-31")
		out = self.acres()
		self.assertEqual(out["itemized"][0]["days_productive_in_period"], 45)
		self.assertEqual(out["time_weighted"], 20.0)

	def test_a_block_pulled_mid_year_is_weighted_for_the_part_it_earned(self):
		self.a_field("Block 3 - MC", 100.0, productive_from="2026-01-01", productive_through="2026-07-15")
		out = self.acres("2026-01-01", "2026-12-31")
		self.assertEqual(out["period_days"], 365)
		self.assertEqual(out["itemized"][0]["days_productive_in_period"], 196)
		self.assertEqual(out["time_weighted"], round(100.0 * 196 / 365, 4))

	def test_a_block_with_no_productive_from_date_is_excluded_and_named(self):
		"""The refusal that matters most, and the one it is easiest to get wrong kindly.

		Assuming an undated block is productive puts acres in the denominator that
		may be a three-year-old planting — which makes the figure LOOK conservative
		while quietly turning a data gap into a number somebody acts on.
		"""
		self.a_field("Block 1 - MC", 40.0, productive_from="2026-01-01")
		self.a_field("Block 9 - MC", 25.0, productive_from=None)
		out = self.acres()
		self.assertEqual(out["time_weighted"], 40.0)
		self.assertEqual(out["field_count_undated"], 1)
		self.assertEqual(out["undated_acres"], 25.0)
		self.assertIn("Block 9 - MC", out["undated_fields"])

		warning = next(w for w in self.kpi_for()["computation_warnings"] if "Block 9 - MC" in w)
		self.assertIn("not assumed productive", warning)

	def test_fallow_ground_has_acreage_and_is_not_in_the_denominator(self):
		self.a_field("Block 1 - MC", 40.0, productive_from="2026-01-01")
		self.a_field("Block 8 - MC", 30.0, productive_from="2020-01-01", condition="Fallow")
		out = self.acres()
		self.assertEqual(out["time_weighted"], 40.0)
		self.assertEqual(out["field_count_fallow"], 1)
		self.assertEqual(out["fallow_acres"], 30.0)

	def test_a_pre_yield_planting_is_counted_separately_because_it_is_next_years_denominator(self):
		self.a_field("Block 1 - MC", 40.0, productive_from="2026-01-01")
		self.a_field("Block 7 - MC", 18.0, productive_from="2029-04-01", pre_yield_end_date="2029-03-31")
		out = self.acres()
		self.assertEqual(out["time_weighted"], 40.0)
		self.assertEqual(out["field_count_pre_yield"], 1)
		self.assertEqual(out["pre_yield_acres"], 18.0)

		warning = next(w for w in self.kpi_for()["computation_warnings"] if "pre-yield" in w)
		self.assertIn("next year's denominator", warning)

	def test_a_block_retired_before_the_period_is_out_of_it_entirely(self):
		self.a_field("Block 1 - MC", 40.0, productive_from="2026-01-01")
		self.a_field("Block 6 - MC", 12.0, productive_from="2015-01-01", productive_through="2025-11-30")
		out = self.acres()
		self.assertEqual(out["time_weighted"], 40.0)
		self.assertEqual(out["field_count_retired"], 1)

	def test_another_companys_ground_is_not_in_this_companys_denominator(self):
		self.a_field("Block 1 - MC", 40.0, productive_from="2026-01-01")
		self.a_field("Highland 1 - HL", 500.0, productive_from="2020-01-01", company=OTHER)
		self.assertEqual(self.acres()["time_weighted"], 40.0)
		self.assertEqual(service.productive_acres(OTHER, "2026-01-01", "2026-03-31")["time_weighted"], 500.0)


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheArithmetic(KPITestCase):
	"""Known inputs to a known answer, and the two ways it declines to answer."""

	def setUp(self):
		super().setUp()
		self.install_capex_columns()
		# Wipe the base fixture's ledger so the operating figure is exactly what
		# this test puts in it. `_gl_entries` seeds a balanced handful for the
		# balance tools; here they would be noise in a number stated to the cent.
		STORE.tables["GL Entry"] = {}

	def a_sale(self, amount, posting_date="2026-02-01", voucher="ACC-JV-2026-90001", company=MAIN):
		"""Cash in against Income — an operating receipt, by the direct method."""
		abbr = MAIN_ABBR if company == MAIN else OTHER_ABBR
		STORE.seed(
			"GL Entry",
			[
				{
					"name": f"{voucher}-1",
					"account": cash(abbr),
					"posting_date": posting_date,
					"debit": amount,
					"credit": 0,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
				},
				{
					"name": f"{voucher}-2",
					"account": sales(abbr),
					"posting_date": posting_date,
					"debit": 0,
					"credit": amount,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
				},
			],
		)

	def a_cost(self, amount, posting_date="2026-02-15", voucher="ACC-JV-2026-90002", company=MAIN):
		abbr = MAIN_ABBR if company == MAIN else OTHER_ABBR
		STORE.seed(
			"GL Entry",
			[
				{
					"name": f"{voucher}-1",
					"account": supplies(abbr),
					"posting_date": posting_date,
					"debit": amount,
					"credit": 0,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
				},
				{
					"name": f"{voucher}-2",
					"account": cash(abbr),
					"posting_date": posting_date,
					"debit": 0,
					"credit": amount,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
				},
			],
		)

	def test_known_inputs_produce_the_known_answer(self):
		"""raw 100k, add-back 20k, maintenance capex 30k, 100 acres → $900/acre."""
		self.a_sale(140000)
		self.a_cost(40000)
		self.approved(amount=20000, category="Weather-Event-Loss")
		self.an_asset("PUMP", 30000, capex_type="Maintenance", maintenance_portion=30000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")

		data = self.kpi_for()
		self.assertEqual(data["raw_ocf"]["value"], 100000.0)
		self.assertEqual(data["normalized_ocf"], 120000.0)
		self.assertEqual(data["maintenance_capex"]["total"], 30000.0)
		self.assertEqual(data["productive_acres"]["time_weighted"], 100.0)
		self.assertEqual(data["sustainable_cf_per_acre"], 900.0)

	def test_a_subtraction_moves_the_figure_the_other_way(self):
		self.a_sale(140000)
		self.a_cost(40000)
		self.approved(amount=20000, category="Insurance-Proceeds", direction="Subtract from OCF")
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")

		data = self.kpi_for()
		self.assertEqual(data["normalization_adjustments_total_subtract"], 20000.0)
		self.assertEqual(data["normalized_ocf"], 80000.0)
		self.assertEqual(data["sustainable_cf_per_acre"], 800.0)

	def test_the_components_are_itemized_rather_than_summarised(self):
		self.a_sale(140000)
		self.approved()
		self.an_asset("PUMP", 30000, capex_type="Maintenance", maintenance_portion=30000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")

		data = self.kpi_for()
		self.assertEqual(data["normalization_adjustments"][0]["justification"], WHY)
		self.assertEqual(data["normalization_adjustments"][0]["signed_effect_on_ocf"], 20000.0)
		self.assertEqual(data["maintenance_capex"]["itemized"][0]["asset"], "PUMP")
		self.assertEqual(data["productive_acres"]["itemized"][0]["field"], "Block 1 - MC")
		self.assertIn("Read the components before the figure", data["reading_it"])

	def test_zero_productive_acres_gives_none_rather_than_zero(self):
		self.a_sale(140000)
		data = self.kpi_for()
		self.assertIsNone(data["sustainable_cf_per_acre"])
		warning = next(w for w in data["computation_warnings"] if "None rather than zero" in w)
		self.assertIn("division nobody performed", warning)

	def test_unclassified_assets_are_excluded_and_flagged_with_their_size(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.an_asset("PUMP", 30000, capex_type="Maintenance", maintenance_portion=30000)
		self.an_asset("MYSTERY", 45000, capex_type=None)

		data = self.kpi_for()
		self.assertEqual(data["maintenance_capex"]["total"], 30000.0)
		self.assertEqual(data["maintenance_capex"]["unclassified_asset_count"], 1)
		self.assertEqual(data["maintenance_capex"]["unclassified_asset_amount"], 45000.0)
		warning = next(w for w in data["computation_warnings"] if "45000" in w)
		self.assertIn("not evidence of maintenance and not evidence of growth", warning)

	def test_growth_capex_is_excluded_from_the_maintenance_figure(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.an_asset("NEW BLOCK", 80000, capex_type="Growth", growth_portion=80000)

		data = self.kpi_for()
		self.assertEqual(data["maintenance_capex"]["total"], 0.0)
		self.assertEqual(data["maintenance_capex"]["growth_capex_excluded"], 80000.0)

	def test_a_mixed_purchase_contributes_only_its_maintenance_half(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.an_asset(
			"BIGGER TRACTOR",
			90000,
			capex_type="Mixed",
			maintenance_portion=60000,
			growth_portion=30000,
		)
		self.assertEqual(self.kpi_for()["maintenance_capex"]["total"], 60000.0)

	def test_an_asset_bought_outside_the_period_is_not_in_it(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.an_asset(
			"LAST YEAR",
			30000,
			purchase_date="2025-11-01",
			capex_type="Maintenance",
			maintenance_portion=30000,
		)
		self.assertEqual(self.kpi_for()["maintenance_capex"]["total"], 0.0)

	def test_a_transfer_between_two_of_the_operations_own_accounts_moves_nothing(self):
		self.a_sale(140000)
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "XFER-1",
					"account": cash(),
					"posting_date": "2026-02-20",
					"debit": 0,
					"credit": 50000,
					"company": MAIN,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "ACC-JV-2026-90003",
				},
				{
					"name": "XFER-2",
					"account": f"1110 - Bank Checking - {MAIN_ABBR}",
					"posting_date": "2026-02-20",
					"debit": 50000,
					"credit": 0,
					"company": MAIN,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "ACC-JV-2026-90003",
				},
			],
		)
		self.assertEqual(self.kpi_for()["raw_ocf"]["value"], 140000.0)

	def test_a_cancelled_voucher_is_not_cash_that_moved(self):
		self.a_sale(140000)
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "VOID-1",
					"account": cash(),
					"posting_date": "2026-02-20",
					"debit": 999999,
					"credit": 0,
					"company": MAIN,
					"is_cancelled": 1,
					"voucher_type": "Journal Entry",
					"voucher_no": "ACC-JV-2026-90004",
				}
			],
		)
		self.assertEqual(self.kpi_for()["raw_ocf"]["value"], 140000.0)

	def test_one_companys_figure_contains_nothing_of_anothers(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		self.a_sale(900000, voucher="SEL-JV-1", company=OTHER)
		self.a_field("Highland 1 - HL", 300.0, productive_from="2025-01-01", company=OTHER)

		mine = self.kpi_for(MAIN)
		theirs = self.kpi_for(OTHER)
		self.assertEqual(mine["raw_ocf"]["value"], 140000.0)
		self.assertEqual(mine["productive_acres"]["time_weighted"], 100.0)
		self.assertEqual(theirs["raw_ocf"]["value"], 900000.0)
		self.assertEqual(theirs["productive_acres"]["time_weighted"], 300.0)

	def test_a_period_with_no_approved_adjustments_says_so_rather_than_being_silent(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		warning = next(w for w in self.kpi_for()["computation_warnings"] if "No approved normalization" in w)
		self.assertIn("flattered figure that looks clean", warning)

	def test_a_period_with_no_maintenance_capex_at_all_says_what_that_could_mean(self):
		self.a_sale(140000)
		self.a_field("Block 1 - MC", 100.0, productive_from="2025-01-01")
		warning = next(w for w in self.kpi_for()["computation_warnings"] if "No maintenance capex" in w)
		self.assertIn("borrowed from the", warning)


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheGuards(KPITestCase):
	"""The role gate, the company scope and the kill switch, on all six tools."""

	def setUp(self):
		super().setUp()
		# `ROLES` is module-level state in the double, so a test that narrows
		# Administrator's roles has to put back exactly what it found — not what
		# it assumed was there. Restoring a guessed list is how one suite quietly
		# takes another's Purchase Manager away, and the failure lands in
		# test_workflow_tools with nothing to say it came from here.
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	CALLS = (
		(
			"create_normalization_adjustment",
			{
				"company": MAIN,
				"fiscal_year": YEAR,
				"period_start": "2026-01-01",
				"period_end": "2026-03-31",
				"amount": 1000,
				"direction": "Add-back to OCF",
				"category": "Other",
				"justification": WHY,
			},
		),
		(
			"approve_normalization_adjustment",
			{"name": "NADJ-2026-0001", "approver_signature_file_token": SIGNATURE},
		),
		("reject_normalization_adjustment", {"name": "NADJ-2026-0001", "rejection_reason": "no"}),
		("backfill_asset_capex_type", {}),
		("list_normalization_adjustments", {}),
		(
			"get_sustainable_cf_per_acre",
			{"company": MAIN, "period_start": "2026-01-01", "period_end": "2026-03-31"},
		),
	)

	def test_a_principal_without_one_of_the_three_roles_is_refused_by_name(self):
		set_roles("Administrator", ["Employee", "HR User"])
		for name, arguments in self.CALLS:
			with self.subTest(tool=name):
				message = self.tool_error(name, arguments)
				self.assertIn("Accounts Manager", message)
				self.assertIn("Farm Manager", message)

	def test_the_gate_is_not_the_hr_one(self):
		"""An HR User who can file a training record has no business moving the
		number a lender reads."""
		set_roles("Administrator", ["HR Manager", "HR User"])
		message = self.tool_error("list_normalization_adjustments", {})
		self.assertNotIn("HR Manager", message)

	def test_an_entity_scoped_principal_cannot_adjust_another_entitys_books(self):
		STORE.seed(
			"User Permission",
			[
				{
					"name": "UP-KPI-1",
					"user": "Administrator",
					"allow": "Company",
					"for_value": MAIN,
					"apply_to_all_doctypes": 1,
				}
			],
		)
		message = self.tool_error(
			"create_normalization_adjustment",
			{
				"company": OTHER,
				"fiscal_year": YEAR,
				"period_start": "2026-01-01",
				"period_end": "2026-03-31",
				"amount": 1000,
				"direction": "Add-back to OCF",
				"category": "Other",
				"justification": WHY,
			},
		)
		self.assertIn(OTHER, message)
		self.assertEqual(STORE.rows(kpi.DOCTYPE), [])

	def test_every_tool_is_refused_with_the_switch_off(self):
		for name, arguments in self.CALLS:
			with self.subTest(tool=name):
				self.configure(enabled=1, **{**ON, f"allow_{name}": 0})
				message = self.tool_error(name, arguments)
				self.assertIn(f"allow_{name}", message)
				self.assertIn("switched off", message)

	def test_the_master_switch_takes_them_all_with_it(self):
		"""`enabled=0` makes the ENDPOINT behave as if it does not exist.

		Not a tool error — a 404 with `not found`, before any tool name is looked
		at. Asserted at that level rather than through `tool_error`, because the
		difference is the point: a per-tool switch refuses a tool that exists, and
		the master switch refuses to admit the endpoint is there at all.
		"""
		self.configure(enabled=0, **ON)
		for name, arguments in self.CALLS:
			with self.subTest(tool=name):
				body, status = self.call("tools/call", {"name": name, "arguments": arguments})
				self.assertEqual(status, 404, body)
				self.assertIn("not found", body["error"]["message"])

	def test_every_call_writes_an_action_log_row(self):
		self.an_adjustment()
		self.assertAudited("create_normalization_adjustment", "Success")
		self.kpi_for()
		self.assertAudited("get_sustainable_cf_per_acre", "Success")
