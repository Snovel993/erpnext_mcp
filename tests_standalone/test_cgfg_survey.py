# SPDX-License-Identifier: MIT
"""The five schema additions that make the CGFG membership survey answerable.

WHY THEY ARE ONE TEST MODULE RATHER THAN FIVE ADDITIONS TO FIVE. Items 18 to 22
of `SERVER_CHANGES.md` are one piece of work with one purpose: generating the
Columbia Gorge Fruit Growers annual membership survey from the farm's own
records rather than from somebody's recollection in February. Each is small. What
makes them worth reading together is that the survey is answerable end to end
only when all five are in, and each of them fails in the same characteristic way
— by looking answered.

TWO OF THE FIVE WERE ALREADY HALF-SERVED, AND BOTH HALVES ARE WRONG IN THE SAME
DIRECTION. `Crop.is_organic_certified` exists and cannot answer "how many acres
are certified", because one Crop record covers every block of that crop.
`Parcel.county` exists and cannot answer "which counties do you operate in",
because the question is about blocks and leased ground. The negative controls in
`TheCropFlagCannotAnswerAcreage` and `CountyIsNotCopiedOntoTheBlock` are the
tests that matter most here: without them the whole five reads as already done.

BLANK IS NOT AN ANSWER, AND EVERY ONE OF THESE COLUMNS SAYS SO. A block with no
organic status is not conventional. A customer with no sales channel is not
wholesale. A company with no consultant recorded has not said it has none. Each
register reports the unanswered set beside the answered one, because a survey
line computed over a half-classified register is a number somebody signs.
"""

from .fixtures import MAIN, MASTER_SUPPLIER, MastersTestCase, V12TestCase
from .harness import STORE

FARM_ON = {
	"allow_create_parcel": 1,
	"allow_update_parcel": 1,
	"allow_list_parcels": 1,
	"allow_create_field": 1,
	"allow_update_field": 1,
	"allow_list_fields": 1,
	"allow_get_field": 1,
	"allow_get_parcel_field_summary": 1,
	"allow_list_crops": 1,
	"allow_create_crop": 1,
	"allow_update_crop": 1,
	"allow_get_crop": 1,
	"allow_list_governance_documents": 1,
	"allow_attach_governance_document": 1,
}

MASTERS_ON = {
	"allow_list_customers": 1,
	"allow_get_customer": 1,
	"allow_create_customer": 1,
	"allow_update_customer": 1,
	"allow_list_companies": 1,
	"allow_update_company": 1,
	"allow_create_supplier": 1,
}


class SurveyTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **FARM_ON)

	def a_parcel(self, parcel_name="Mill Creek", county="Wasco", acreage=400.0, **overrides):
		payload = {
			"owning_entity": MAIN,
			"parcel_name": parcel_name,
			"acreage": acreage,
			"county": county,
			"state": "OR",
			"use_type": "Orchard",
		}
		payload.update(overrides)
		return self.tool_data("create_parcel", payload)

	def a_field(self, field_name, parcel="Mill Creek", acreage=10.0, **overrides):
		payload = {"parcel": parcel, "field_name": field_name, "acreage": acreage, "crop": "Gala"}
		payload.update(overrides)
		return self.tool_data("create_field", payload)


# ── 18. organic certification, on the ground rather than on the commodity ────


class OrganicStatusIsPerBlock(SurveyTestCase):
	"""The three-way state, and the flag derived from it."""

	def setUp(self):
		super().setUp()
		self.a_parcel()

	def test_certified_organic_derives_the_flag(self):
		field = self.a_field(
			"Block 1", organic_status="Certified Organic", organic_cert_agency="Oregon Tilth"
		)
		self.assertEqual(field["organic_status"], "Certified Organic")
		self.assertTrue(field["organic_certified"])
		self.assertEqual(field["organic_cert_agency"], "Oregon Tilth")

	def test_transitional_is_not_certified(self):
		"""The whole reason this is a Select and not a checkbox.

		A checkbox records the end state and loses the three years that lead to
		it, which is the part a buyer and an inspector both ask about.
		"""
		field = self.a_field("Block 1", organic_status="Transitional", transition_start_date="2025-04-01")
		self.assertEqual(field["organic_status"], "Transitional")
		self.assertFalse(field["organic_certified"])
		self.assertEqual(field["transition_start_date"], "2025-04-01")

	def test_blank_is_unanswered_rather_than_conventional(self):
		field = self.a_field("Block 1")
		self.assertIsNone(field["organic_status"])
		self.assertFalse(field["organic_certified"])
		register = self.tool_data("list_fields", {"company": MAIN})
		self.assertEqual(register["without_organic_status"], [field["name"]])
		self.assertNotIn("Conventional", register["acreage_by_organic_status"])

	def test_the_derived_flag_follows_a_status_change(self):
		self.a_field("Block 1", organic_status="Certified Organic", organic_cert_agency="CCOF")
		changed = self.tool_data("update_field", {"field": "Block 1", "organic_status": "Transitional"})
		self.assertFalse(changed["organic_certified"])
		self.assertEqual(changed["changed"]["organic_status"], ["Certified Organic", "Transitional"])

	def test_the_derived_flag_cannot_be_set_by_hand(self):
		"""A figure a person can edit independently is a figure that will disagree.

		The same rule the boundary's derived columns are under. Refused rather
		than accepted-and-overwritten, because silently overwriting it is how the
		caller comes to believe the two agree.
		"""
		self.a_field("Block 1")
		# create and update both refuse, and both name the field that decides it
		message = self.tool_error("update_field", {"field": "Block 1", "organic_certified": True})
		self.assertIn("organic_status", message)
		self.assertIn("Nothing was changed", message)
		message = self.tool_error(
			"create_field",
			{"parcel": "Mill Creek", "field_name": "Block 2", "organic_certified": True},
		)
		self.assertIn("organic_status", message)

	def test_an_unknown_status_is_refused_with_the_three(self):
		message = self.tool_error(
			"create_field",
			{"parcel": "Mill Creek", "field_name": "Block 2", "organic_status": "Organic-ish"},
		)
		self.assertIn("Certified Organic", message)


class OrganicContradictionsAreReportedNotRefused(SurveyTestCase):
	"""Each of these is a real state some block is genuinely in."""

	def setUp(self):
		super().setUp()
		self.a_parcel()

	def test_an_agency_on_a_conventional_block_warns(self):
		field = self.a_field("Block 1", organic_status="Conventional", organic_cert_agency="Oregon Tilth")
		self.assertTrue(
			any("certifying agency is recorded" in warning for warning in field["warnings"]),
			field["warnings"],
		)
		self.assertFalse(field["organic_certified"])

	def test_transitional_with_no_start_date_warns(self):
		field = self.a_field("Block 1", organic_status="Transitional")
		self.assertTrue(
			any("thirty-six months" in warning for warning in field["warnings"]), field["warnings"]
		)

	def test_certified_with_no_agency_warns(self):
		field = self.a_field("Block 1", organic_status="Certified Organic")
		self.assertTrue(
			any("no certifying agency" in warning for warning in field["warnings"]), field["warnings"]
		)
		self.assertTrue(field["organic_certified"])


class TheCropFlagCannotAnswerAcreage(SurveyTestCase):
	"""The negative control for item 18, and the reason it is not already done.

	`Crop.is_organic_certified` exists, is filterable, and is a fact about a
	COMMODITY. A farm running one variety on certified and conventional blocks has
	one Crop record and one boolean, and the honest answer to "total acres
	organically certified" is unobtainable from it. These two tests assert the gap
	and then assert the block-level sum closes it.
	"""

	def setUp(self):
		super().setUp()
		self.a_parcel()
		self.a_field(
			"Block 1",
			acreage=8.0,
			crop="Gala",
			organic_status="Certified Organic",
			organic_cert_agency="Oregon Tilth",
		)
		self.a_field("Block 2", acreage=12.0, crop="Gala", organic_status="Conventional")
		self.a_field(
			"Block 3",
			acreage=5.0,
			crop="Gala",
			organic_status="Transitional",
			transition_start_date="2025-03-01",
		)

	def test_one_crop_covers_blocks_in_all_three_states(self):
		"""Nothing about the crop distinguishes them, which is the whole problem."""
		register = self.tool_data("list_fields", {"company": MAIN, "crop": "Gala"})
		self.assertEqual(register["field_count"], 3)
		self.assertEqual({row["crop"] for row in register["fields"]}, {"Gala"})

	def test_the_certified_acreage_is_summed_from_the_block(self):
		register = self.tool_data("list_fields", {"company": MAIN})
		self.assertEqual(register["organic_certified_acreage"], 8.0)
		self.assertEqual(register["organic_transitional_acreage"], 5.0)
		self.assertEqual(register["total_acreage"], 25.0)
		self.assertEqual(
			register["acreage_by_organic_status"],
			{"Certified Organic": 8.0, "Conventional": 12.0, "Transitional": 5.0},
		)

	def test_the_register_filters_on_the_status_and_on_the_flag(self):
		by_status = self.tool_data("list_fields", {"company": MAIN, "organic_status": "Certified Organic"})
		self.assertEqual([row["field_name"] for row in by_status["fields"]], ["Block 1"])
		by_flag = self.tool_data("list_fields", {"company": MAIN, "organic_certified": True})
		self.assertEqual([row["field_name"] for row in by_flag["fields"]], ["Block 1"])
		self.assertEqual(by_flag["organic_certified_acreage"], 8.0)


# ── 21. county, derived from the parcel and stored nowhere else ──────────────


class CountyIsNotCopiedOntoTheBlock(SurveyTestCase):
	"""Item 21's real requirement, which is a derivation rather than a field."""

	def setUp(self):
		super().setUp()
		self.a_parcel("Mill Creek", county="Wasco", acreage=200.0)
		self.a_parcel("Sevenmile", county="Hood River", acreage=200.0)
		self.a_field("Block 1", parcel="Mill Creek", acreage=10.0)
		self.a_field("Block 2", parcel="Mill Creek", acreage=15.0)
		self.a_field("Block 3", parcel="Sevenmile", acreage=20.0)

	def test_the_field_doctype_stores_no_county(self):
		"""THE NEGATIVE CONTROL. A second copy can disagree with the first, and the
		one that is wrong is always the one nobody edited when the assessor redrew
		a line. So the column must not exist, and this test fails if somebody adds
		it."""
		row = STORE.rows("Field")[0]
		self.assertNotIn("county", row)

	def test_every_row_carries_its_parcels_county(self):
		register = self.tool_data("list_fields", {"company": MAIN})
		counties = {row["field_name"]: row["county"] for row in register["fields"]}
		self.assertEqual(counties, {"Block 1": "Wasco", "Block 2": "Wasco", "Block 3": "Hood River"})

	def test_acreage_rolls_up_by_county(self):
		register = self.tool_data("list_fields", {"company": MAIN})
		self.assertEqual(register["acreage_by_county"], {"Hood River": 20.0, "Wasco": 25.0})
		self.assertEqual(register["counties"], ["Hood River", "Wasco"])

	def test_the_register_filters_on_county(self):
		register = self.tool_data("list_fields", {"company": MAIN, "county": "Hood River"})
		self.assertEqual([row["field_name"] for row in register["fields"]], ["Block 3"])
		self.assertEqual(register["county"], "Hood River")

	def test_a_county_with_no_ground_is_refused_with_the_ones_there_are(self):
		"""Neither silently-nothing nor silently-everything: one reads as "we farm
		no ground there" and the other as "we farm all of it", and a typo means
		neither."""
		message = self.tool_error("list_fields", {"company": MAIN, "county": "Wsaco"})
		self.assertIn("Hood River", message)
		self.assertIn("Wasco", message)

	def test_moving_the_parcel_moves_every_block_on_it(self):
		"""What a read-through buys that a copy does not."""
		self.configure(enabled=1, **FARM_ON)
		self.tool_data("update_parcel", {"parcel": "Mill Creek", "county": "Sherman"})
		register = self.tool_data("list_fields", {"company": MAIN})
		counties = {row["field_name"]: row["county"] for row in register["fields"]}
		self.assertEqual(counties["Block 1"], "Sherman")
		self.assertEqual(counties["Block 2"], "Sherman")
		self.assertEqual(counties["Block 3"], "Hood River")

	def test_get_field_reports_the_county_too(self):
		field = self.tool_data("get_field", {"field": "Block 3"})
		self.assertEqual(field["county"], "Hood River")
		self.assertEqual(field["parcel_detail"]["county"], "Hood River")


# ── 19. the direct-marketed share, computed where it can be and typed where not ─


class TheTypedDirectShareSaysItIsTyped(SurveyTestCase):
	def test_a_share_is_stored_and_labelled_as_an_assertion(self):
		crop = self.tool_data(
			"create_crop", {"crop_name": "Cherry", "crop_type": "Stone Fruit", "pct_direct_marketed": 35}
		)
		self.assertEqual(crop["pct_direct_marketed"], 35.0)
		self.assertIn("assertion", crop["pct_direct_marketed_source"])

	def test_zero_and_unset_are_different_answers(self):
		"""Nothing sold direct, and nobody has worked it out."""
		self.tool_data(
			"create_crop", {"crop_name": "Cherry", "crop_type": "Stone Fruit", "pct_direct_marketed": 0}
		)
		self.tool_data("create_crop", {"crop_name": "Pear", "crop_type": "Tree Fruit"})
		register = self.tool_data("list_crops")
		shares = {row["crop_name"]: row["pct_direct_marketed"] for row in register["crops"]}
		self.assertEqual(shares["Cherry"], 0.0)
		self.assertIsNone(shares["Pear"])
		self.assertEqual(register["without_direct_marketed_share"], ["Pear"])

	def test_a_fraction_typed_for_a_percentage_is_refused(self):
		"""0.35 for thirty-five percent stores without complaint and reads as a
		third of one percent — a survey line off by two orders of magnitude that
		looks entirely plausible on the page. Only the out-of-range half can be
		caught, and it is."""
		message = self.tool_error(
			"create_crop",
			{"crop_name": "Cherry", "crop_type": "Stone Fruit", "pct_direct_marketed": 3500},
		)
		self.assertIn("0 to 100", message)

	def test_the_share_can_be_changed_and_cleared(self):
		self.tool_data(
			"create_crop", {"crop_name": "Cherry", "crop_type": "Stone Fruit", "pct_direct_marketed": 35}
		)
		changed = self.tool_data("update_crop", {"crop": "Cherry", "pct_direct_marketed": 40})
		self.assertEqual(changed["changed"]["pct_direct_marketed"], [35.0, 40.0])
		cleared = self.tool_data("update_crop", {"crop": "Cherry", "pct_direct_marketed": ""})
		self.assertIsNone(cleared["pct_direct_marketed"])


class TheSalesChannelIsOnTheCustomer(MastersTestCase):
	"""The column that makes the computed version of the share possible at all."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **MASTERS_ON)
		from erpnext_mcp.tools import masters

		masters.ensure_sales_channel_field()

	def test_migrate_installs_it(self):
		from erpnext_mcp import compat, install

		install.after_migrate()
		self.assertTrue(compat.has_field("Customer", "sales_channel"))

	def test_a_customer_is_created_with_a_channel(self):
		created = self.tool_data(
			"create_customer", {"customer_name": "Hood River Farm Stand", "sales_channel": "Direct"}
		)
		self.assertEqual(created["sales_channel"], "Direct")

	def test_an_unknown_channel_is_refused_with_the_four(self):
		message = self.tool_error(
			"create_customer", {"customer_name": "Somebody", "sales_channel": "Roadside"}
		)
		self.assertIn("Packer", message)

	def test_the_register_counts_the_classified_and_names_the_rest(self):
		"""The unclassified list is the half that matters. A direct-marketed
		percentage is only as complete as this classification."""
		self.tool_data(
			"create_customer", {"customer_name": "Hood River Farm Stand", "sales_channel": "Direct"}
		)
		self.tool_data("create_customer", {"customer_name": "Columbia Packing", "sales_channel": "Packer"})
		register = self.tool_data("list_customers")
		self.assertTrue(register["sales_channel_installed"])
		self.assertEqual(register["by_sales_channel"]["Direct"], 1)
		self.assertEqual(register["by_sales_channel"]["Packer"], 1)
		self.assertIn("(unclassified)", register["by_sales_channel"])
		self.assertIn("Southgate Markets", register["without_sales_channel"])

	def test_the_register_filters_on_the_channel(self):
		self.tool_data(
			"create_customer", {"customer_name": "Hood River Farm Stand", "sales_channel": "Direct"}
		)
		register = self.tool_data("list_customers", {"sales_channel": "Direct"})
		self.assertEqual([row["customer_name"] for row in register["customers"]], ["Hood River Farm Stand"])

	def test_an_existing_customer_is_classified_by_update(self):
		"""Nothing backfilled this and nothing will — whether a buyer is a farm
		stand or a packer is a fact only the farm has."""
		changed = self.tool_data(
			"update_customer", {"name": "Southgate Markets", "sales_channel": "Wholesale"}
		)
		self.assertEqual(changed["changed"]["sales_channel"], [None, "Wholesale"])


# ── 20. pest management, which is not one provider on a farm with two fruits ──


class PestManagementProvidersAreATable(MastersTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **MASTERS_ON, allow_create_crop=1)
		from erpnext_mcp.tools import company

		company.ensure_pest_provider_field()
		STORE.seed(
			"Crop",
			[
				{"name": "Apple", "crop_name": "Apple", "crop_type": "Tree Fruit"},
				{"name": "Sweet Cherry", "crop_name": "Sweet Cherry", "crop_type": "Stone Fruit"},
			],
		)
		self.tool_data("create_supplier", {"supplier_name": "Gorge Orchard IPM"})

	def test_migrate_installs_it(self):
		from erpnext_mcp import compat, install

		install.after_migrate()
		self.assertTrue(compat.has_field("Company", "pest_management_providers"))

	def test_two_consultants_for_two_commodities(self):
		"""THE CASE A SINGLE LINK CANNOT HOLD, and the reason this is a table.

		A farm running pome fruit and stone fruit commonly retains a different
		adviser for each. One Link stores whichever was typed last while reading
		as the whole answer.
		"""
		changed = self.tool_data(
			"update_company",
			{
				"company": MAIN,
				"pest_management_providers": [
					{
						"provider": MASTER_SUPPLIER,
						"commodity": "Apple",
						"service_type": "Scouting and written recommendations",
						"license_number": "OR-PC-4471",
					},
					{
						"provider": "Gorge Orchard IPM",
						"commodity": "Sweet Cherry",
						"service_type": "Full IPM programme",
					},
				],
			},
		)
		self.assertEqual(changed["changed"]["pest_management_providers"], ["0 row(s)", "2 row(s)"])
		providers = changed["pest_management_providers"]
		self.assertEqual(
			{(row["provider"], row["commodity"]) for row in providers},
			{(MASTER_SUPPLIER, "Apple"), ("Gorge Orchard IPM", "Sweet Cherry")},
		)
		self.assertEqual(providers[0]["license_number"], "OR-PC-4471")

	def test_no_commodity_means_the_whole_operation(self):
		"""Not an unanswered question — the ordinary case for a farm with one
		consultant, and the reads say which it is."""
		changed = self.tool_data(
			"update_company",
			{"company": MAIN, "pest_management_providers": [{"provider": MASTER_SUPPLIER}]},
		)
		row = changed["pest_management_providers"][0]
		self.assertIsNone(row["commodity"])
		self.assertEqual(row["commodity_scope"], "the whole operation")

	def test_the_register_reports_them(self):
		self.tool_data(
			"update_company",
			{
				"company": MAIN,
				"pest_management_providers": [{"provider": MASTER_SUPPLIER, "commodity": "Apple"}],
			},
		)
		register = self.tool_data("list_companies")
		entry = next(row for row in register["companies"] if row["company"] == MAIN)
		self.assertTrue(entry["pest_management_providers_installed"])
		self.assertEqual(entry["pest_management_providers"][0]["provider"], MASTER_SUPPLIER)

	def test_the_same_consultant_twice_for_one_commodity_is_refused(self):
		message = self.tool_error(
			"update_company",
			{
				"company": MAIN,
				"pest_management_providers": [
					{"provider": MASTER_SUPPLIER, "commodity": "Apple"},
					{"provider": MASTER_SUPPLIER, "commodity": "Apple"},
				],
			},
		)
		self.assertIn("row order", message)

	def test_a_bad_row_refuses_the_whole_list(self):
		"""A half-written table leaves a company with some of its advisers and no
		way to tell which half went."""
		message = self.tool_error(
			"update_company",
			{
				"company": MAIN,
				"pest_management_providers": [
					{"provider": MASTER_SUPPLIER, "commodity": "Apple"},
					{"provider": "Nobody Consulting"},
				],
			},
		)
		self.assertIn("Nobody Consulting", message)
		self.assertIn("Nothing was changed", message)
		register = self.tool_data("list_companies")
		entry = next(row for row in register["companies"] if row["company"] == MAIN)
		self.assertEqual(entry["pest_management_providers"], [])

	def test_an_unknown_commodity_is_refused_with_the_register(self):
		message = self.tool_error(
			"update_company",
			{
				"company": MAIN,
				"pest_management_providers": [{"provider": MASTER_SUPPLIER, "commodity": "Pome Fruit"}],
			},
		)
		self.assertIn("Sweet Cherry", message)

	def test_an_unrecognised_key_is_refused_rather_than_ignored(self):
		"""A key silently dropped is a consultant somebody thinks they recorded."""
		message = self.tool_error(
			"update_company",
			{
				"company": MAIN,
				"pest_management_providers": [{"provider": MASTER_SUPPLIER, "phone": "541-555-0100"}],
			},
		)
		self.assertIn("phone", message)

	def test_an_empty_list_clears_it(self):
		self.tool_data(
			"update_company",
			{"company": MAIN, "pest_management_providers": [{"provider": MASTER_SUPPLIER}]},
		)
		changed = self.tool_data("update_company", {"company": MAIN, "pest_management_providers": []})
		self.assertEqual(changed["changed"]["pest_management_providers"], ["1 row(s)", "0 row(s)"])
		self.assertEqual(changed["pest_management_providers"], [])


# ── 22. the vocabulary Part 3 has nowhere to live without ────────────────────


class GovernanceCategoriesForPartThree(SurveyTestCase):
	"""A survey generator cannot query `Other` and get anything but everything."""

	NEW = ("Succession Plan", "Family History", "Acreage History", "EFU Enterprise")

	def test_all_four_are_offered_by_the_doctype(self):
		from .harness import META

		options = META["Governance Document"].get_field("category")["options"].split("\n")
		for category in self.NEW:
			with self.subTest(category=category):
				self.assertIn(category, options)

	def test_each_one_files_and_is_found_again_by_category(self):
		for index, category in enumerate(self.NEW):
			self.tool_data(
				"attach_governance_document",
				{
					"company": MAIN,
					"category": category,
					"title": f"{category} 2026",
					"effective_date": f"2026-0{index + 1}-01",
				},
			)
		for category in self.NEW:
			with self.subTest(category=category):
				found = self.tool_data("list_governance_documents", {"company": MAIN, "category": category})
				self.assertEqual([row["title"] for row in found["documents"]], [f"{category} 2026"])

	def test_the_six_that_were_already_there_still_file(self):
		"""The other direction: adding to a Select must not disturb it."""
		self.tool_data(
			"attach_governance_document",
			{"company": MAIN, "category": "Operating Agreement", "title": "OML Operating Agreement 2020"},
		)
		found = self.tool_data("list_governance_documents", {"company": MAIN})
		self.assertEqual(found["by_category"]["Operating Agreement"], 1)
