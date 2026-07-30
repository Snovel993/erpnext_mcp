# SPDX-License-Identifier: MIT
"""The land register and the lease register.

Four things these tests are really about.

THE DOCNAME CARRIES THE ENTITY. Family land gets reorganised, and two entities in
one family end up with a "Home Place" apiece. So the tests assert on the shape of
the docname as well as on the fields, and on the refusal a bare parcel name gets
when it matches two entities.

THE COUNTY'S KEY IS THE COUNTY'S KEY. An assessor parcel number claimed by two
parcels of one entity means one of them is a typo, and that refusal has a test
because it is the one that catches a bad import.

DIRECTION IS STATED AND CHECKED, NEVER ENFORCED. `create_lease` reports whether
the party names agree with the stated direction and creates the lease either way.
The test that matters is the one proving an inconsistent claim is a *warning* —
a refusal built on comparing a legal name to a Company docname is a refusal
nobody could get past.

NOTHING EXPIRES A LEASE. A lease marked Active whose expiration date has passed
is reported and left alone. There is a test that the status is unchanged after
listing, because a status that flipped itself on a calendar would erase the
difference between "still running month to month" and "nobody has looked at this
in years".
"""

import base64
import json

from .fixtures import ASSET_CATEGORY, MAIN, MAIN_ABBR, OTHER, V11TestCase

ALL_ON = {
	"allow_create_parcel": 1,
	"allow_update_parcel": 1,
	"allow_list_parcels": 1,
	"allow_get_parcel": 1,
	"allow_link_parcel_to_asset": 1,
	"allow_create_lease": 1,
	"allow_update_lease": 1,
	"allow_list_leases": 1,
	"allow_get_lease": 1,
	"allow_create_related_party": 1,
	"allow_attach_governance_document": 1,
	"allow_create_asset": 1,
}


class RealEstateTestCase(V11TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_parcel(self, parcel_name="Red Camp", **overrides):
		payload = {
			"owning_entity": MAIN,
			"parcel_name": parcel_name,
			"parcel_id": "1N-13E-8-1200",
			"county": "Wasco",
			"state": "OR",
			"acreage": 37.49,
			"use_type": "Orchard",
			"address": "2535 Dry Hollow Rd",
		}
		payload.update(overrides)
		return self.tool_data("create_parcel", payload)

	def a_lease(self, lease_name="Mill Creek Ground Lease 2025", **overrides):
		payload = {
			"owning_entity": MAIN,
			"lease_name": lease_name,
			"direction": "Outbound",
			"lessor": f"{MAIN} Holdings",
			"lessee": "Cooper Family Orchards",
			"effective_date": "2025-01-01",
			"expiration_date": "2027-12-31",
			"rent_amount": 1500,
			"rent_frequency": "Monthly",
		}
		payload.update(overrides)
		return self.tool_data("create_lease", payload)

	def a_party(self, party_name="Highland Ltd Liability Co.", **overrides):
		payload = {
			"company": MAIN,
			"party_name": party_name,
			"party_type": "LLC",
			"relationship_to_company": "Other",
			"effective_date": "2020-01-01",
		}
		payload.update(overrides)
		return self.tool_data("create_related_party", payload)


# ── create_parcel ───────────────────────────────────────────────────────────
class CreateParcel(RealEstateTestCase):
	def test_it_registers_a_parcel_under_a_docname_carrying_the_entity(self):
		data = self.a_parcel()
		self.assertEqual(data["name"], f"Red Camp - {MAIN_ABBR}")
		self.assertEqual(data["parcel_name"], "Red Camp")
		self.assertEqual(data["owning_entity"], MAIN)
		self.assertEqual(data["acreage"], 37.49)
		self.assertEqual(data["use_type"], "Orchard")
		self.assertEqual(data["county"], "Wasco")

	def test_company_is_accepted_as_an_alias_for_owning_entity(self):
		"""Every other tool in this app calls it `company`; refusing that would
		cost a round trip every time a model reaches for the name it knows."""
		data = self.tool_data(
			"create_parcel", {"company": MAIN, "parcel_name": "Doane Road", "acreage": 14.84}
		)
		self.assertEqual(data["owning_entity"], MAIN)

	def test_the_same_name_twice_for_one_entity_is_refused_naming_the_existing_parcel(self):
		self.a_parcel()
		message = self.tool_error("create_parcel", {"owning_entity": MAIN, "parcel_name": "Red Camp"})
		self.assertIn(f"Red Camp - {MAIN_ABBR}", message)
		self.assertIn("update_parcel", message)
		self.assertIn("Nothing was created", message)

	def test_the_same_name_under_another_entity_is_allowed(self):
		"""Two entities in one family really do each have a Home Place."""
		self.a_parcel("Home Place")
		data = self.tool_data(
			"create_parcel", {"owning_entity": OTHER, "parcel_name": "Home Place", "acreage": 10}
		)
		self.assertEqual(data["owning_entity"], OTHER)
		self.assertNotEqual(data["name"], f"Home Place - {MAIN_ABBR}")

	def test_a_duplicate_assessor_parcel_id_is_refused_because_it_is_the_county_key(self):
		self.a_parcel()
		message = self.tool_error(
			"create_parcel",
			{"owning_entity": MAIN, "parcel_name": "40-Acre", "parcel_id": "1N-13E-8-1200"},
		)
		self.assertIn("county's key", message)
		self.assertIn("typo", message)
		self.assertIn("Nothing was created", message)

	def test_negative_acreage_and_negative_value_are_refused(self):
		self.assertIn(
			"acreage must be zero or more",
			self.tool_error("create_parcel", {"owning_entity": MAIN, "parcel_name": "X", "acreage": -1}),
		)
		self.assertIn(
			"appraised_value must be zero or more",
			self.tool_error(
				"create_parcel", {"owning_entity": MAIN, "parcel_name": "Y", "appraised_value": -5}
			),
		)

	def test_an_unknown_use_type_is_refused_with_the_list_off_the_doctype(self):
		message = self.tool_error(
			"create_parcel", {"owning_entity": MAIN, "parcel_name": "Z", "use_type": "Vineyard"}
		)
		self.assertIn("use_type must be one of", message)
		self.assertIn("Orchard", message)

	def test_a_parcel_name_is_required(self):
		self.assertIn("parcel_name is required", self.tool_error("create_parcel", {"owning_entity": MAIN}))

	def test_a_value_with_no_as_of_date_warns_rather_than_refusing(self):
		"""A figure somebody remembered is worth recording. It just should not be
		mistaken for a valuation."""
		data = self.a_parcel(appraised_value=1115000)
		self.assertEqual(data["appraised_value"], 1115000.0)
		self.assertTrue(any("as-of date" in warning for warning in data["warnings"]))

	def test_a_value_with_no_appraisal_document_warns(self):
		data = self.a_parcel(appraised_value=1115000, appraised_as_of="2026-02-20")
		self.assertTrue(any("no report behind it" in warning for warning in data["warnings"]))

	def test_a_fully_documented_valuation_produces_no_warnings(self):
		archive = self.tool_data(
			"attach_governance_document",
			{
				"company": MAIN,
				"title": "Moore Valuation M202547",
				"category": "Prior Statement",
				"effective_date": "2026-02-20",
			},
		)
		data = self.a_parcel(
			appraised_value=1115000,
			appraised_as_of="2026-02-20",
			appraiser="Gregory W. Moore MAI",
			appraisal_document=archive["name"],
		)
		self.assertNotIn("warnings", data)
		self.assertEqual(data["appraisal_document"], archive["name"])

	def test_a_title_holder_from_another_company_is_refused(self):
		party = self.a_party()
		self.assertTrue(party["name"].endswith(MAIN_ABBR))
		message = self.tool_error(
			"create_parcel",
			{"owning_entity": OTHER, "parcel_name": "Elsewhere", "title_holder": party["name"]},
		)
		self.assertIn("belongs to", message)
		self.assertIn("Nothing was created", message)

	def test_a_title_holder_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"create_parcel",
			{"owning_entity": MAIN, "parcel_name": "Elsewhere", "title_holder": "Nobody - ETC"},
		)
		self.assertIn("no Related Party named", message)

	def test_the_title_holder_link_records_who_actually_holds_title(self):
		party = self.a_party()
		data = self.a_parcel(title_holder=party["name"])
		self.assertEqual(data["title_holder"], party["name"])


# ── update_parcel ───────────────────────────────────────────────────────────
class UpdateParcel(RealEstateTestCase):
	def test_it_changes_a_field_and_echoes_before_and_after(self):
		self.a_parcel()
		data = self.tool_data("update_parcel", {"parcel": "Red Camp", "county": "Hood River"})
		self.assertEqual(data["county"], "Hood River")
		self.assertEqual(data["changes"]["county"], ["Wasco", "Hood River"])

	def test_it_resolves_a_bare_parcel_name(self):
		self.a_parcel()
		data = self.tool_data("update_parcel", {"parcel": "Red Camp", "acreage": 38.0})
		self.assertEqual(data["name"], f"Red Camp - {MAIN_ABBR}")

	def test_a_bare_name_matching_two_entities_is_refused_with_both_named(self):
		self.a_parcel("Home Place")
		self.tool_data("create_parcel", {"owning_entity": OTHER, "parcel_name": "Home Place"})
		message = self.tool_error("update_parcel", {"parcel": "Home Place", "county": "Wasco"})
		self.assertIn("matches 2 parcels", message)
		self.assertIn("owning_entity", message)

	def test_renaming_is_refused_because_the_docname_is_built_from_the_name(self):
		self.a_parcel()
		message = self.tool_error("update_parcel", {"parcel": "Red Camp", "parcel_name": "Red Camp II"})
		self.assertIn("cannot be changed", message)
		self.assertIn("Nothing was changed", message)

	def test_moving_a_parcel_between_entities_is_refused_as_a_conveyance(self):
		self.a_parcel()
		message = self.tool_error(
			"update_parcel", {"parcel": f"Red Camp - {MAIN_ABBR}", "owning_entity": OTHER}
		)
		self.assertIn("conveyance", message)

	def test_setting_the_asset_link_here_is_refused_and_points_at_the_right_tool(self):
		self.a_parcel()
		message = self.tool_error("update_parcel", {"parcel": "Red Camp", "related_asset": "anything"})
		self.assertIn("link_parcel_to_asset", message)

	def test_a_no_op_update_is_refused_rather_than_silently_succeeding(self):
		self.a_parcel()
		message = self.tool_error("update_parcel", {"parcel": "Red Camp", "county": "Wasco"})
		self.assertIn("nothing to change", message)

	def test_a_new_value_without_a_new_date_warns(self):
		self.a_parcel(appraised_value=1000000, appraised_as_of="2020-01-01")
		data = self.tool_data("update_parcel", {"parcel": "Red Camp", "appraised_value": 1115000})
		self.assertIn("appraised_as_of did not", data["warning"])

	def test_an_empty_string_clears_an_optional_field(self):
		self.a_parcel()
		data = self.tool_data("update_parcel", {"parcel": "Red Camp", "county": ""})
		self.assertIsNone(data["county"])

	def test_a_duplicate_assessor_id_is_refused_on_update_too(self):
		self.a_parcel()
		self.a_parcel("40-Acre", parcel_id="1N-13E-8-1300")
		message = self.tool_error(
			"update_parcel", {"parcel": "40-Acre", "parcel_id": "1N-13E-8-1200"}
		)
		self.assertIn("already on", message)
		self.assertIn("Nothing was changed", message)


# ── list_parcels ────────────────────────────────────────────────────────────
class ListParcels(RealEstateTestCase):
	def some_parcels(self):
		self.a_parcel("Red Camp", acreage=37.49, appraised_value=1115000, appraised_as_of="2026-02-20")
		self.a_parcel(
			"Mill Creek",
			parcel_id="1N-13E-9-100",
			acreage=131.43,
			appraised_value=3100000,
			appraised_as_of="2026-02-20",
			use_type="Labor Housing",
		)
		self.a_parcel("40-Acre", parcel_id="1N-13E-9-200", acreage=40.02, use_type="Orchard")

	def test_it_totals_acreage_and_value_and_computes_a_per_acre(self):
		self.some_parcels()
		data = self.tool_data("list_parcels", {"owning_entity": MAIN})
		self.assertEqual(data["count"], 3)
		self.assertEqual(data["total_acreage"], 208.94)
		self.assertEqual(data["total_appraised_value"], 4215000.0)
		self.assertEqual(data["average_per_acre"], round(4215000.0 / 208.94, 2))

	def test_it_buckets_by_use_type(self):
		self.some_parcels()
		data = self.tool_data("list_parcels", {"owning_entity": MAIN})
		self.assertEqual(data["by_use_type"]["Orchard"]["count"], 2)
		self.assertEqual(data["by_use_type"]["Labor Housing"]["acreage"], 131.43)

	def test_it_reports_the_oldest_and_newest_appraisal(self):
		self.a_parcel("Red Camp", appraised_value=1, appraised_as_of="2020-01-01")
		self.a_parcel("Mill Creek", parcel_id="x", appraised_value=1, appraised_as_of="2026-02-20")
		data = self.tool_data("list_parcels", {"owning_entity": MAIN})
		self.assertEqual(data["oldest_appraisal"], "2020-01-01")
		self.assertEqual(data["newest_appraisal"], "2026-02-20")

	def test_it_names_the_parcels_with_no_value(self):
		self.some_parcels()
		data = self.tool_data("list_parcels", {"owning_entity": MAIN})
		self.assertEqual(data["parcels_without_value"], [f"40-Acre - {MAIN_ABBR}"])

	def test_it_warns_when_a_value_has_no_appraisal_document_behind_it(self):
		self.some_parcels()
		data = self.tool_data("list_parcels", {"owning_entity": MAIN})
		self.assertIn("no appraisal document", data["warning"])

	def test_filtering_by_county_and_use_type(self):
		self.some_parcels()
		self.assertEqual(
			self.tool_data("list_parcels", {"owning_entity": MAIN, "use_type": "Orchard"})["count"], 2
		)
		self.assertEqual(
			self.tool_data("list_parcels", {"owning_entity": MAIN, "county": "Nowhere"})["count"], 0
		)

	def test_filtering_by_whether_an_asset_is_linked(self):
		self.some_parcels()
		self.link_an_asset()
		linked = self.tool_data("list_parcels", {"owning_entity": MAIN, "linked_to_asset": True})
		unlinked = self.tool_data("list_parcels", {"owning_entity": MAIN, "linked_to_asset": False})
		self.assertEqual(linked["count"], 1)
		self.assertEqual(unlinked["count"], 2)

	def link_an_asset(self):
		asset = self.tool_data(
			"create_asset",
			{
				"company": MAIN,
				"asset_name": "Red Camp land",
				"item_code": "LAND-RC",
				"asset_category": ASSET_CATEGORY,
				"purchase_date": "2024-01-01",
				"purchase_amount": 240000,
				"useful_life_months": 12,
			},
		)
		return self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": asset["asset"]})

	def test_an_empty_register_answers_rather_than_refusing(self):
		data = self.tool_data("list_parcels", {"owning_entity": MAIN})
		self.assertEqual(data["count"], 0)
		self.assertEqual(data["total_acreage"], 0)
		self.assertIsNone(data["average_per_acre"])

	def test_a_limit_that_hides_part_of_the_register_warns_before_the_totals_are_trusted(self):
		self.some_parcels()
		data = self.tool_data("list_parcels", {"owning_entity": MAIN, "limit": 1})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["total_in_register"], 3)
		self.assertIn("Raise `limit`", data["warning"])


# ── get_parcel ──────────────────────────────────────────────────────────────
class GetParcel(RealEstateTestCase):
	def test_it_returns_the_leases_over_the_parcel_in_both_directions(self):
		parcel = self.a_parcel()
		self.a_lease("Out", parcel=parcel["name"], direction="Outbound")
		self.a_lease(
			"In",
			parcel=parcel["name"],
			direction="Inbound",
			lessor="Somebody Else",
			lessee=MAIN,
		)
		data = self.tool_data("get_parcel", {"parcel": "Red Camp"})
		self.assertEqual(len(data["leases"]), 2)
		self.assertEqual({lease["direction"] for lease in data["leases"]}, {"Inbound", "Outbound"})
		self.assertEqual(data["active_leases"], 2)

	def test_an_unknown_parcel_is_refused_with_the_tool_that_lists_them(self):
		message = self.tool_error("get_parcel", {"parcel": "Nowhere"})
		self.assertIn("no Parcel called", message)
		self.assertIn("list_parcels", message)

	def test_a_parcel_with_no_asset_says_so_rather_than_omitting_the_key(self):
		self.a_parcel()
		data = self.tool_data("get_parcel", {"parcel": "Red Camp"})
		self.assertIsNone(data["asset"])
		self.assertEqual(data["leases"], [])


# ── link_parcel_to_asset ────────────────────────────────────────────────────
class LinkParcelToAsset(RealEstateTestCase):
	def an_asset(self, name="Red Camp land", amount=240000):
		return self.tool_data(
			"create_asset",
			{
				"company": MAIN,
				"asset_name": name,
				"item_code": name.upper().replace(" ", "-"),
				"asset_category": ASSET_CATEGORY,
				"purchase_date": "2024-01-01",
				"purchase_amount": amount,
				"useful_life_months": 12,
			},
		)["asset"]

	def test_it_reports_the_gap_between_cost_and_market(self):
		"""The gap is the point: unrealised appreciation is the number an estate
		conversation turns on, and neither record shows it alone."""
		self.a_parcel(appraised_value=1115000, appraised_as_of="2026-02-20")
		data = self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": self.an_asset()})
		self.assertEqual(data["asset"]["gross_purchase_amount"], 240000.0)
		self.assertEqual(data["asset"]["appraised_value"], 1115000.0)
		self.assertEqual(data["unrealised_appreciation"], 875000.0)

	def test_it_posts_nothing(self):
		self.a_parcel(appraised_value=1115000)
		before = self.tool_data("get_account_balance", {"company": MAIN, "account": "1710 - Equipment - ETC"})
		self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": self.an_asset()})
		after = self.tool_data("get_account_balance", {"company": MAIN, "account": "1710 - Equipment - ETC"})
		self.assertEqual(before["balance"], after["balance"])

	def test_an_asset_already_claimed_by_another_parcel_is_refused(self):
		self.a_parcel("Red Camp")
		self.a_parcel("Mill Creek", parcel_id="other")
		asset = self.an_asset()
		self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": asset})
		message = self.tool_error("link_parcel_to_asset", {"parcel": "Mill Creek", "asset": asset})
		self.assertIn("already linked to parcel", message)
		self.assertIn("Nothing was changed", message)

	def test_relinking_a_parcel_needs_replace(self):
		self.a_parcel()
		first = self.an_asset("first")
		second = self.an_asset("second")
		self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": first})
		message = self.tool_error("link_parcel_to_asset", {"parcel": "Red Camp", "asset": second})
		self.assertIn("replace=true", message)
		data = self.tool_data(
			"link_parcel_to_asset", {"parcel": "Red Camp", "asset": second, "replace": True}
		)
		self.assertEqual(data["related_asset"], second)
		self.assertEqual(data["replaced"], first)

	def test_relinking_to_the_same_asset_is_refused_as_a_no_op(self):
		self.a_parcel()
		asset = self.an_asset()
		self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": asset})
		message = self.tool_error(
			"link_parcel_to_asset", {"parcel": "Red Camp", "asset": asset, "replace": True}
		)
		self.assertIn("already linked", message)

	def test_an_asset_that_does_not_exist_is_refused_with_the_tool_that_makes_one(self):
		self.a_parcel()
		message = self.tool_error("link_parcel_to_asset", {"parcel": "Red Camp", "asset": "ACC-ASS-9999"})
		self.assertIn("create_asset", message)

	def test_a_dry_run_writes_nothing_and_shows_the_comparison(self):
		self.a_parcel(appraised_value=1115000)
		asset = self.an_asset()
		data = self.tool_data(
			"link_parcel_to_asset", {"parcel": "Red Camp", "asset": asset, "dry_run": True}
		)
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["asset_summary"]["appraisal_over_book"], 875000.0)
		self.assertIsNone(self.tool_data("get_parcel", {"parcel": "Red Camp"})["related_asset"])

	def test_a_parcel_with_no_appraised_value_warns_that_the_gap_is_unknown(self):
		self.a_parcel()
		data = self.tool_data("link_parcel_to_asset", {"parcel": "Red Camp", "asset": self.an_asset()})
		self.assertIn("no appraised value", data["warning"])


# ── create_lease ────────────────────────────────────────────────────────────
class CreateLease(RealEstateTestCase):
	def test_it_records_a_lease_and_annualises_the_rent(self):
		data = self.a_lease()
		self.assertEqual(data["name"], f"Mill Creek Ground Lease 2025 - {MAIN_ABBR}")
		self.assertEqual(data["direction"], "Outbound")
		self.assertEqual(data["status"], "Active")
		self.assertEqual(data["annualised_rent"], 18000.0)

	def test_it_books_nothing(self):
		window = {"company": MAIN, "from_date": "2020-01-01", "to_date": "2030-01-01"}
		before = self.tool_data("get_journal_entries", window)["count"]
		self.a_lease(rent_amount=100000)
		after = self.tool_data("get_journal_entries", window)["count"]
		self.assertEqual(before, after)

	def test_a_crop_share_has_no_annual_rate(self):
		"""Reporting one as zero would understate the whole rent roll."""
		data = self.a_lease(rent_frequency="Crop Share", rent_amount=0)
		self.assertIsNone(data["annualised_rent"])

	def test_an_inbound_lease_is_recorded_the_same_way(self):
		data = self.a_lease(
			"Taken In", direction="Inbound", lessor="Highland Ltd Liability Co.", lessee=MAIN
		)
		self.assertEqual(data["direction"], "Inbound")
		self.assertEqual(data["direction_check"]["verdict"], "consistent")

	def test_a_direction_that_disagrees_with_the_names_warns_and_still_creates(self):
		"""A refusal built on comparing a legal name to a Company docname is a
		refusal nobody could get past."""
		data = self.a_lease("Backwards", direction="Inbound", lessor=MAIN, lessee="Somebody Else")
		self.assertEqual(data["direction_check"]["verdict"], "inconsistent")
		self.assertIn("runs the other way", data["warning"])
		self.assertEqual(data["status"], "Active")

	def test_two_unrelated_names_leave_the_direction_unverified_without_complaint(self):
		data = self.a_lease("Neither", lessor="A Trust", lessee="B Orchards")
		self.assertEqual(data["direction_check"]["verdict"], "unverified")
		self.assertNotIn("warning", data)

	def test_the_same_party_on_both_sides_is_refused(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Self",
				"direction": "Outbound",
				"lessor": "Same Party",
				"lessee": "same party",
				"effective_date": "2025-01-01",
			},
		)
		self.assertIn("cannot lease from itself", message)

	def test_an_expiration_before_the_effective_date_is_refused(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Backwards dates",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-06-01",
				"expiration_date": "2025-01-01",
			},
		)
		self.assertIn("before effective_date", message)
		self.assertIn("Nothing was created", message)

	def test_terminated_without_a_date_is_refused(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Ended",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-01-01",
				"status": "Terminated",
			},
		)
		self.assertIn("termination_date", message)
		self.assertIn("not a record anybody can rely on", message)

	def test_negative_rent_is_refused_as_a_lease_in_the_other_direction(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Negative",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-01-01",
				"rent_amount": -100,
			},
		)
		self.assertIn("other direction", message)

	def test_a_duplicate_lease_name_is_refused_and_suggests_naming_it_for_the_term(self):
		self.a_lease()
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Mill Creek Ground Lease 2025",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2026-01-01",
			},
		)
		self.assertIn("Name a renewal for its term", message)

	def test_an_unknown_direction_is_refused_with_the_two_that_exist(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Sideways",
				"direction": "Sideways",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-01-01",
			},
		)
		self.assertIn("Outbound", message)
		self.assertIn("Inbound", message)

	def test_a_parcel_from_another_entity_is_refused(self):
		self.tool_data("create_parcel", {"owning_entity": OTHER, "parcel_name": "Elsewhere"})
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Wrong parcel",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-01-01",
				"parcel": "Elsewhere",
			},
		)
		self.assertIn("no Parcel called", message)

	def test_the_executed_lease_can_be_attached_as_base64_and_is_stored_private(self):
		content = base64.b64encode(b"%PDF-1.4 lease").decode()
		data = self.a_lease(file_content=content, file_name="mill-creek-2025.pdf")
		self.assertEqual(data["attachment"]["file_name"], "mill-creek-2025.pdf")
		self.assertTrue(data["attachment"]["is_private"])
		self.assertIn("/private/files/", data["lease_document"])

	def test_content_and_a_url_together_are_refused(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Both",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-01-01",
				"file_content": base64.b64encode(b"x").decode(),
				"file_name": "x.pdf",
				"lease_document_url": "/files/x.pdf",
			},
		)
		self.assertIn("not both", message)

	def test_content_without_a_file_name_is_refused(self):
		message = self.tool_error(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Nameless",
				"direction": "Outbound",
				"lessor": "A",
				"lessee": "B",
				"effective_date": "2025-01-01",
				"file_content": base64.b64encode(b"x").decode(),
			},
		)
		self.assertIn("file_name is required", message)


# ── update_lease ────────────────────────────────────────────────────────────
class UpdateLease(RealEstateTestCase):
	def test_it_changes_the_status_and_echoes_the_change(self):
		self.a_lease()
		data = self.tool_data(
			"update_lease", {"lease": "Mill Creek Ground Lease 2025", "status": "Expired"}
		)
		self.assertEqual(data["status"], "Expired")
		self.assertEqual(data["changes"]["status"], ["Active", "Expired"])

	def test_terminating_needs_a_date_in_the_same_call(self):
		self.a_lease()
		message = self.tool_error(
			"update_lease", {"lease": "Mill Creek Ground Lease 2025", "status": "Terminated"}
		)
		self.assertIn("termination_date", message)
		self.assertIn("Nothing was changed", message)

	def test_terminating_with_a_date_works(self):
		self.a_lease()
		data = self.tool_data(
			"update_lease",
			{
				"lease": "Mill Creek Ground Lease 2025",
				"status": "Terminated",
				"termination_date": "2026-03-31",
				"termination_reason": "Operator surrendered the ground.",
			},
		)
		self.assertEqual(data["status"], "Terminated")
		self.assertEqual(data["termination_date"], "2026-03-31")

	def test_a_termination_before_the_effective_date_is_refused(self):
		self.a_lease()
		message = self.tool_error(
			"update_lease",
			{
				"lease": "Mill Creek Ground Lease 2025",
				"status": "Terminated",
				"termination_date": "2024-01-01",
			},
		)
		self.assertIn("before this lease's effective date", message)

	def test_renaming_is_refused_and_says_a_renewal_is_a_new_lease(self):
		self.a_lease()
		message = self.tool_error(
			"update_lease", {"lease": "Mill Creek Ground Lease 2025", "lease_name": "2026"}
		)
		self.assertIn("new lease with its own term", message)

	def test_making_one_party_both_sides_is_refused(self):
		self.a_lease()
		message = self.tool_error(
			"update_lease", {"lease": "Mill Creek Ground Lease 2025", "lessee": f"{MAIN} Holdings"}
		)
		self.assertIn("cannot lease from itself", message)

	def test_a_no_op_update_is_refused(self):
		self.a_lease()
		message = self.tool_error(
			"update_lease", {"lease": "Mill Creek Ground Lease 2025", "status": "Active"}
		)
		self.assertIn("nothing to change", message)

	def test_a_rent_change_says_it_restates_nothing_already_booked(self):
		self.a_lease()
		data = self.tool_data(
			"update_lease", {"lease": "Mill Creek Ground Lease 2025", "rent_amount": 1800}
		)
		self.assertEqual(data["annualised_rent"], 21600.0)
		self.assertIn("does not restate anything already booked", data["note"])


# ── list_leases ─────────────────────────────────────────────────────────────
class ListLeases(RealEstateTestCase):
	def a_portfolio(self):
		self.a_lease("Out A", rent_amount=1500, rent_frequency="Monthly")
		self.a_lease("Out B", rent_amount=24000, rent_frequency="Annual")
		self.a_lease(
			"In A",
			direction="Inbound",
			lessor="Highland Ltd Liability Co.",
			lessee=MAIN,
			rent_amount=1000,
			rent_frequency="Quarterly",
		)
		self.a_lease("Share", rent_frequency="Crop Share", rent_amount=0)

	def test_the_rent_roll_separates_the_two_directions(self):
		self.a_portfolio()
		data = self.tool_data("list_leases", {"owning_entity": MAIN})
		self.assertEqual(data["annual_rent_receivable"], 42000.0)
		self.assertEqual(data["annual_rent_payable"], 4000.0)
		self.assertEqual(data["net_annual_rent"], 38000.0)

	def test_a_crop_share_is_listed_rather_than_counted_as_zero(self):
		self.a_portfolio()
		data = self.tool_data("list_leases", {"owning_entity": MAIN})
		self.assertEqual(
			[row["rent_frequency"] for row in data["rent_not_annualisable"]], ["Crop Share"]
		)

	def test_an_expired_lease_is_left_alone_and_reported(self):
		"""Nothing here flips a status on a calendar. A test that the record is
		UNCHANGED is the point of this one."""
		self.a_lease("Ran out", expiration_date="2025-12-31")
		data = self.tool_data("list_leases", {"owning_entity": MAIN})
		self.assertEqual(data["active_past_expiration"], [f"Ran out - {MAIN_ABBR}"])
		self.assertIn("NOTHING HERE CHANGED IT", data["warning"])
		self.assertEqual(
			self.tool_data("get_lease", {"lease": "Ran out"})["status"], "Active"
		)

	def test_expiring_soon_uses_the_window_it_is_given(self):
		self.a_lease("Soon", expiration_date="2026-08-15")
		wide = self.tool_data("list_leases", {"owning_entity": MAIN, "expiring_within_days": 90})
		narrow = self.tool_data("list_leases", {"owning_entity": MAIN, "expiring_within_days": 5})
		self.assertEqual(len(wide["expiring_soon"]), 1)
		self.assertEqual(narrow["expiring_soon"], [])

	def test_expiring_soon_names_the_counterparty_on_the_right_side(self):
		self.a_lease("Soon", expiration_date="2026-08-15")
		data = self.tool_data("list_leases", {"owning_entity": MAIN})
		self.assertEqual(data["expiring_soon"][0]["counterparty_name"], "Cooper Family Orchards")

	def test_a_negative_window_is_refused(self):
		message = self.tool_error(
			"list_leases", {"owning_entity": MAIN, "expiring_within_days": -1}
		)
		self.assertIn("zero or more days", message)

	def test_active_on_filters_by_the_dates_on_the_record(self):
		self.a_lease("Old", effective_date="2020-01-01", expiration_date="2021-12-31")
		self.a_lease("Current", effective_date="2025-01-01", expiration_date="2027-12-31")
		data = self.tool_data("list_leases", {"owning_entity": MAIN, "active_on": "2026-01-01"})
		self.assertEqual([lease["lease_name"] for lease in data["leases"]], ["Current"])

	def test_filtering_by_direction_and_status(self):
		self.a_portfolio()
		self.assertEqual(
			self.tool_data("list_leases", {"owning_entity": MAIN, "direction": "Inbound"})["count"], 1
		)
		self.assertEqual(
			self.tool_data("list_leases", {"owning_entity": MAIN, "status": "Expired"})["count"], 0
		)

	def test_an_empty_register_answers_with_a_zero_rent_roll(self):
		data = self.tool_data("list_leases", {"owning_entity": MAIN})
		self.assertEqual(data["count"], 0)
		self.assertEqual(data["annual_rent_receivable"], 0.0)


# ── get_lease ───────────────────────────────────────────────────────────────
class GetLease(RealEstateTestCase):
	def test_it_carries_the_parcel_detail_and_the_in_force_answer(self):
		parcel = self.a_parcel()
		self.a_lease(parcel=parcel["name"])
		data = self.tool_data("get_lease", {"lease": "Mill Creek Ground Lease 2025"})
		self.assertEqual(data["parcel_detail"]["name"], parcel["name"])
		self.assertTrue(data["in_force_today"])

	def test_a_terminated_lease_is_not_in_force(self):
		self.a_lease()
		self.tool_data(
			"update_lease",
			{
				"lease": "Mill Creek Ground Lease 2025",
				"status": "Terminated",
				"termination_date": "2026-01-31",
			},
		)
		data = self.tool_data("get_lease", {"lease": "Mill Creek Ground Lease 2025"})
		self.assertFalse(data["in_force_today"])

	def test_it_lists_attachments_without_returning_their_bytes(self):
		self.a_lease(file_content=base64.b64encode(b"%PDF-1.4 lease").decode(), file_name="l.pdf")
		data = self.tool_data("get_lease", {"lease": "Mill Creek Ground Lease 2025"})
		self.assertEqual(len(data["attachments"]), 1)
		self.assertNotIn("content", json.dumps(data["attachments"]))

	def test_an_unknown_lease_is_refused(self):
		self.assertIn("no Lease called", self.tool_error("get_lease", {"lease": "Nothing"}))


# ── switches ────────────────────────────────────────────────────────────────
class Switches(RealEstateTestCase):
	def test_every_mutating_land_tool_ships_off(self):
		"""The shipped posture, read from the DocType JSON via the fixture."""
		self.configure(enabled=1)
		for tool, arguments in (
			("create_parcel", {"owning_entity": MAIN, "parcel_name": "X"}),
			("update_parcel", {"parcel": "X", "county": "Y"}),
			("link_parcel_to_asset", {"parcel": "X", "asset": "Y"}),
			(
				"create_lease",
				{
					"owning_entity": MAIN,
					"lease_name": "X",
					"direction": "Outbound",
					"lessor": "A",
					"lessee": "B",
					"effective_date": "2025-01-01",
				},
			),
			("update_lease", {"lease": "X", "status": "Expired"}),
		):
			with self.subTest(tool=tool):
				message = self.tool_error(tool, arguments)
				self.assertIn(f"allow_{tool}", message)
				self.assertIn("switched off", message)

	def test_the_read_tools_ship_on(self):
		self.configure(enabled=1)
		self.assertEqual(self.tool_data("list_parcels", {"owning_entity": MAIN})["count"], 0)
		self.assertEqual(self.tool_data("list_leases", {"owning_entity": MAIN})["count"], 0)

	def test_a_disabled_read_tool_names_the_field_to_tick(self):
		self.configure(enabled=1, allow_list_parcels=0)
		message = self.tool_error("list_parcels", {"owning_entity": MAIN})
		self.assertIn("allow_list_parcels", message)

	def test_the_tools_disappear_from_the_catalogue_when_their_doctype_is_missing(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Parcel")
		message = self.tool_error("list_parcels", {"owning_entity": MAIN})
		self.assertIn("not available on this site", message)
		self.assertIn("Parcel DocType", message)

	def test_every_land_tool_is_audited(self):
		self.a_parcel()
		self.assertAudited("create_parcel", "Success")
		self.tool_error("create_parcel", {"owning_entity": MAIN, "parcel_name": "Red Camp"})
		self.assertAudited("create_parcel", "Error")
