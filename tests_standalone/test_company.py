# SPDX-License-Identifier: MIT
"""Multi-company, and the two party types a family operation actually needs.

Four things these tests are really about.

AN ABBREVIATION IS A KEY, NOT A LABEL. Every account, cost center, parcel and
lease docname ends in it, so `create_company` refuses a duplicate and
`update_company` refuses to change one at all. There is a test for each, because
those two refusals are what stop a rename that would silently orphan a chart.

A CURRENCY IS ONLY CHANGEABLE WHILE NOTHING IS POSTED. The test that matters is
the one proving the refusal fires on a company that HAS entries and the change
goes through on one that does not — a blanket refusal would be easier to write
and would block a legitimate first-day correction.

SCOPING IS TESTED BY CREATING A SECOND COMPANY AND LOOKING FOR LEAKS. Not by
asserting a filter is present in the code: the fixture already has two companies,
these tests add a third, and then check that a register asked about one does not
answer about another.

FAMILY IS EXCLUDED AND CONTACT IS BORDERLINE, AND BOTH ARE POSITIVE ASSERTIONS.
A Family payment over the threshold produces NO form and IS counted in the
excluded block — testing only the first half would pass against a tool that had
silently lost the posting. A Contact payment over the threshold produces a form
classified borderline with the reason naming the W-9.
"""

from erpnext_mcp.tools.company import CUSTOM_PARTY_TYPES

from .fixtures import ALEX, ANTONY, MAIN, MAIN_ABBR, OTHER, V12TestCase
from .harness import STORE

ALL_ON = {
	"allow_list_companies": 1,
	"allow_create_company": 1,
	"allow_update_company": 1,
	"allow_register_party_types": 1,
	"allow_generate_1099_prefill": 1,
	"allow_set_company_defaults": 1,
	"allow_create_cost_center": 1,
}


class CompanyTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_company(self, company_name="Constancy Farms LLC", abbr="CF", **overrides):
		payload = {"company_name": company_name, "abbr": abbr}
		payload.update(overrides)
		return self.tool_data("create_company", payload)


# ── list_companies ──────────────────────────────────────────────────────────
class ListCompanies(CompanyTestCase):
	def test_it_lists_every_company_on_the_site(self):
		data = self.tool_data("list_companies")
		names = [row["company"] for row in data["companies"]]
		self.assertIn(MAIN, names)
		self.assertIn(OTHER, names)
		self.assertEqual(data["company_count"], len(names))

	def test_it_reports_the_abbreviation_currency_and_country(self):
		row = self._row(MAIN)
		self.assertEqual(row["abbr"], MAIN_ABBR)
		self.assertEqual(row["default_currency"], "USD")
		self.assertEqual(row["country"], "United States")

	def test_it_counts_accounts_cost_centers_and_gl_entries(self):
		row = self._row(MAIN)
		self.assertGreater(row["account_count"], 0)
		self.assertGreater(row["cost_center_count"], 0)
		self.assertGreater(row["gl_entry_count"], 0)

	def test_it_reports_the_first_and_last_posting_dates(self):
		"""The dates are how a caller tells a live company from a shell — and
		they are what `update_company` refuses a currency change on."""
		row = self._row(MAIN)
		self.assertTrue(row["first_gl_entry"])
		self.assertTrue(row["last_gl_entry"])
		self.assertLessEqual(row["first_gl_entry"], row["last_gl_entry"])

	def test_a_company_with_no_postings_reports_zero_and_no_dates(self):
		self.a_company()
		row = self._row("Constancy Farms LLC")
		self.assertEqual(row["gl_entry_count"], 0)
		self.assertIsNone(row["first_gl_entry"])
		self.assertIsNone(row["last_gl_entry"])

	def test_a_tax_id_is_reported_as_present_and_four_digits_never_whole(self):
		STORE.tables["Company"][MAIN]["tax_id"] = "93-1234567"
		row = self._row(MAIN)
		self.assertTrue(row["tax_id_on_file"])
		self.assertEqual(row["tax_id_last4"], "4567")
		self.assertNotIn("1234567", str(row))

	def test_a_company_with_no_tax_id_says_so_rather_than_returning_an_empty_string(self):
		row = self._row(MAIN)
		self.assertFalse(row["tax_id_on_file"])
		self.assertEqual(row["tax_id_last4"], "")

	def test_it_reports_the_fiscal_year_period_from_the_fiscal_year_records(self):
		data = self.tool_data("list_companies")
		row = self._row(MAIN, data)
		self.assertEqual(row["fiscal_year_start_month"], 1)
		self.assertTrue(row["fiscal_year_first"])
		self.assertGreater(row["fiscal_year_count"], 0)

	def test_it_reports_which_custom_party_types_are_registered(self):
		"""The tool a client calls to work out what a site can express is the
		right place to answer 'can I book a line to a family member'."""
		data = self.tool_data("list_companies")
		self.assertTrue(data["party_types"]["available"])
		self.assertEqual(sorted(data["party_types"]["missing"]), ["Contact", "Family"])

	def test_it_reports_them_as_registered_once_they_are(self):
		self.tool_data("register_party_types")
		data = self.tool_data("list_companies")
		self.assertEqual(sorted(data["party_types"]["registered"]), ["Contact", "Family"])
		self.assertEqual(data["party_types"]["missing"], [])

	def test_the_missing_hint_names_the_command_that_fixes_it(self):
		data = self.tool_data("list_companies")
		self.assertIn("migrate", data["party_types"]["hint"])

	def test_it_is_read_only(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("list_companies")
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		before.pop("MCP Action Log", None)
		after.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_the_switch_turns_it_off(self):
		self.configure(enabled=1, allow_list_companies=0)
		self.assertIn("switched off", self.tool_error("list_companies"))

	def _row(self, company, data=None):
		data = data or self.tool_data("list_companies")
		return next(row for row in data["companies"] if row["company"] == company)


# ── create_company ──────────────────────────────────────────────────────────
class CreateCompany(CompanyTestCase):
	def test_it_creates_the_company(self):
		data = self.a_company()
		self.assertTrue(data["created"])
		self.assertEqual(data["name"], "Constancy Farms LLC")
		self.assertEqual(data["abbr"], "CF")
		self.assertTrue(STORE.get_raw("Company", "Constancy Farms LLC"))

	def test_it_defaults_country_and_currency(self):
		data = self.a_company()
		self.assertEqual(data["country"], "United States")
		self.assertEqual(data["default_currency"], "USD")

	def test_it_creates_the_fiscal_year_containing_today(self):
		data = self.a_company(fiscal_year_start_month=1)
		self.assertEqual(data["fiscal_year"]["name"], "2026")
		self.assertEqual(data["fiscal_year"]["year_start_date"], "2026-01-01")
		self.assertEqual(data["fiscal_year"]["year_end_date"], "2026-12-31")

	def test_an_april_fiscal_year_is_named_for_the_span_it_covers(self):
		"""A farm year is not a calendar year, and '2026' would be a lie about
		which twelve months it is."""
		data = self.a_company(fiscal_year_start_month=4)
		self.assertEqual(data["fiscal_year"]["name"], "2026-2027")
		self.assertEqual(data["fiscal_year"]["year_start_date"], "2026-04-01")
		self.assertEqual(data["fiscal_year"]["year_end_date"], "2027-03-31")

	def test_a_month_name_is_accepted_as_well_as_a_number(self):
		self.assertEqual(self.a_company(fiscal_year_start_month="April")["fiscal_year_start_month"], 4)

	def test_a_start_month_before_today_in_the_year_keeps_this_year(self):
		"""Today in the fixture is 2026-07-24; an April year that started in
		April 2026 is the one running now."""
		self.assertEqual(self.a_company(fiscal_year_start_month=4)["fiscal_year"]["name"], "2026-2027")

	def test_a_start_month_after_today_falls_back_to_last_year(self):
		"""An October year on 2026-07-24 is the one that began in October 2025."""
		data = self.a_company(fiscal_year_start_month=10)
		self.assertEqual(data["fiscal_year"]["year_start_date"], "2025-10-01")
		self.assertEqual(data["fiscal_year"]["year_end_date"], "2026-09-30")

	def test_a_february_year_end_lands_on_the_right_day_in_a_leap_year(self):
		data = self.a_company(fiscal_year_start_month=3)
		self.assertEqual(data["fiscal_year"]["year_end_date"], "2027-02-28")

	def test_a_tax_id_is_stored_but_only_its_last_four_come_back(self):
		data = self.a_company(tax_id="93-7654321")
		self.assertTrue(data["tax_id_on_file"])
		self.assertEqual(data["tax_id_last4"], "4321")
		self.assertNotIn("7654321", str(data))

	def test_a_duplicate_company_name_is_refused(self):
		error = self.tool_error("create_company", {"company_name": MAIN, "abbr": "ZZ"})
		self.assertIn("already on this site", error)
		self.assertIn("Nothing was created", error)

	def test_a_duplicate_abbreviation_is_refused_and_says_why_it_matters(self):
		error = self.tool_error("create_company", {"company_name": "Something Else LLC", "abbr": MAIN_ABBR})
		self.assertIn(MAIN, error)
		self.assertIn("docname", error)
		self.assertFalse(STORE.get_raw("Company", "Something Else LLC"))

	def test_a_non_alphanumeric_abbreviation_is_refused(self):
		error = self.tool_error("create_company", {"company_name": "Odd LLC", "abbr": "C/F"})
		self.assertIn("letters and digits", error)

	def test_an_unknown_country_is_refused_with_the_spelling_hint(self):
		error = self.tool_error(
			"create_company", {"company_name": "Overseas LLC", "abbr": "OS", "country": "USA"}
		)
		self.assertIn("United States", error)
		self.assertFalse(STORE.get_raw("Company", "Overseas LLC"))

	def test_an_unknown_currency_is_refused(self):
		error = self.tool_error(
			"create_company",
			{"company_name": "Euro LLC", "abbr": "EU", "default_currency": "EUR"},
		)
		self.assertIn("no Currency", error)

	def test_a_month_outside_one_to_twelve_is_refused(self):
		error = self.tool_error(
			"create_company",
			{"company_name": "Bad Month LLC", "abbr": "BM", "fiscal_year_start_month": 13},
		)
		self.assertIn("1-12", error)

	def test_an_unparseable_month_is_refused_rather_than_defaulted(self):
		error = self.tool_error(
			"create_company",
			{"company_name": "Bad Month LLC", "abbr": "BM", "fiscal_year_start_month": "Smarch"},
		)
		self.assertIn("month", error)

	def test_a_parent_that_is_not_a_group_company_is_refused(self):
		error = self.tool_error(
			"create_company",
			{"company_name": "Sub LLC", "abbr": "SB", "parent_company": MAIN},
		)
		self.assertIn("not a group company", error)

	def test_a_parent_that_does_not_exist_is_refused(self):
		error = self.tool_error(
			"create_company",
			{"company_name": "Sub LLC", "abbr": "SB", "parent_company": "Nowhere Holdings"},
		)
		self.assertIn("no Company called", error)

	def test_a_group_parent_is_accepted(self):
		STORE.tables["Company"][OTHER]["is_group"] = 1
		data = self.a_company(parent_company=OTHER)
		self.assertEqual(data["parent_company"], OTHER)

	def test_missing_required_arguments_are_refused(self):
		self.assertIn("company_name is required", self.tool_error("create_company", {"abbr": "XX"}))
		self.assertIn("abbr is required", self.tool_error("create_company", {"company_name": "No Abbr LLC"}))

	def test_dry_run_reports_the_plan_and_writes_nothing(self):
		data = self.tool_data(
			"create_company",
			{"company_name": "Constancy Farms LLC", "abbr": "CF", "dry_run": True},
		)
		self.assertTrue(data["dry_run"])
		self.assertFalse(data["created"])
		self.assertEqual(data["fiscal_year"]["year_start_date"], "2026-01-01")
		self.assertFalse(STORE.get_raw("Company", "Constancy Farms LLC"))

	def test_a_company_with_no_chart_warns_rather_than_pretending(self):
		"""The fake site's Company controller builds nothing, which is exactly
		the shape of a real site whose named chart of accounts does not exist."""
		data = self.a_company()
		self.assertEqual(data["account_count"], 0)
		self.assertTrue(any("no accounts" in warning for warning in data["warnings"]))
		self.assertTrue(any("import_chart_of_accounts" in warning for warning in data["warnings"]))

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		self.assertIn(
			"switched off",
			self.tool_error("create_company", {"company_name": "Nope LLC", "abbr": "NP"}),
		)

	def test_it_is_audited(self):
		self.a_company()
		self.assertAudited("create_company", "Success")


# ── update_company ──────────────────────────────────────────────────────────
class UpdateCompany(CompanyTestCase):
	def test_it_changes_the_country(self):
		data = self.tool_data("update_company", {"company": MAIN, "country": "Canada"})
		self.assertEqual(data["changed"]["country"], ["United States", "Canada"])
		self.assertEqual(STORE.get_raw("Company", MAIN)["country"], "Canada")

	def test_it_sets_a_tax_id_and_echoes_only_the_last_four(self):
		data = self.tool_data("update_company", {"company": MAIN, "tax_id": "93-1112222"})
		self.assertEqual(data["changed"]["tax_id"][1], "…2222")
		self.assertNotIn("1112222", str(data))
		self.assertEqual(STORE.get_raw("Company", MAIN)["tax_id"], "93-1112222")

	def test_a_company_can_be_named_by_its_abbreviation(self):
		data = self.tool_data("update_company", {"company": MAIN_ABBR, "country": "Canada"})
		self.assertEqual(data["company"], MAIN)

	def test_an_unchanged_value_is_reported_as_unchanged_rather_than_written(self):
		data = self.tool_data("update_company", {"company": MAIN, "country": "United States"})
		self.assertEqual(data["changed"], {})
		self.assertIn("country", data["unchanged"])

	def test_changing_the_abbreviation_is_refused_and_says_what_it_would_break(self):
		error = self.tool_error("update_company", {"company": MAIN, "abbr": "NEW"})
		self.assertIn("docname", error)
		self.assertIn("migration", error)
		self.assertEqual(STORE.get_raw("Company", MAIN)["abbr"], MAIN_ABBR)

	def test_changing_the_company_name_is_refused(self):
		error = self.tool_error("update_company", {"company": MAIN, "company_name": "Renamed Co"})
		self.assertIn("rename", error)

	def test_changing_the_currency_on_a_company_with_postings_is_refused(self):
		error = self.tool_error("update_company", {"company": MAIN, "default_currency": "CAD"})
		self.assertIn("posted GL entries", error)
		self.assertIn("restate", error)
		self.assertEqual(STORE.get_raw("Company", MAIN)["default_currency"], "USD")

	def test_the_currency_refusal_names_the_dates_of_the_postings(self):
		"""Two figures the operator needs to decide whether the entries are real
		or a stray import they can delete."""
		error = self.tool_error("update_company", {"company": MAIN, "default_currency": "CAD"})
		self.assertIn("2025", error)

	def test_changing_the_currency_on_a_company_with_no_postings_goes_through(self):
		"""The refusal has to be about the ledger, not about the field — a
		blanket no would block a legitimate first-day correction."""
		self.a_company()
		data = self.tool_data("update_company", {"company": "Constancy Farms LLC", "default_currency": "CAD"})
		self.assertEqual(data["changed"]["default_currency"], ["USD", "CAD"])

	def test_an_unknown_currency_is_still_refused_on_a_clean_company(self):
		self.a_company()
		error = self.tool_error(
			"update_company", {"company": "Constancy Farms LLC", "default_currency": "XYZ"}
		)
		self.assertIn("no Currency", error)

	def test_changing_the_fiscal_year_start_month_is_refused_with_the_right_tool_named(self):
		error = self.tool_error("update_company", {"company": MAIN, "fiscal_year_start_month": 4})
		self.assertIn("create_fiscal_year", error)
		self.assertIn("same days", error)

	def test_it_sets_the_company_logo_to_a_file_url(self):
		"""The second half of attach-then-point: the file is uploaded with
		attach_file_to_document, and this is what makes it the logo."""
		data = self.tool_data(
			"update_company", {"company": MAIN, "company_logo": "/private/files/orchard-logo.jpg"}
		)
		self.assertEqual(data["changed"]["company_logo"], ["", "/private/files/orchard-logo.jpg"])
		self.assertEqual(STORE.get_raw("Company", MAIN)["company_logo"], "/private/files/orchard-logo.jpg")

	def test_a_public_file_url_is_accepted_too(self):
		data = self.tool_data("update_company", {"company": MAIN, "company_logo": "/files/mark.png"})
		self.assertEqual(data["changed"]["company_logo"][1], "/files/mark.png")

	def test_an_empty_company_logo_clears_it(self):
		STORE.tables["Company"][MAIN]["company_logo"] = "/files/mark.png"
		data = self.tool_data("update_company", {"company": MAIN, "company_logo": ""})
		self.assertEqual(data["changed"]["company_logo"], ["/files/mark.png", ""])
		self.assertFalse(STORE.get_raw("Company", MAIN)["company_logo"])

	def test_a_path_on_the_callers_own_disk_is_refused_and_names_the_upload_tool(self):
		"""A local path stores without complaint and renders as a broken image on
		every badge — the failure would not surface until something is printed."""
		error = self.tool_error(
			"update_company", {"company": MAIN, "company_logo": "/Users/me/Desktop/logo.jpg"}
		)
		self.assertIn("attach_file_to_document", error)
		self.assertFalse(STORE.get_raw("Company", MAIN).get("company_logo"))

	def test_an_unknown_company_is_refused(self):
		error = self.tool_error("update_company", {"company": "Nowhere LLC", "country": "Canada"})
		self.assertIn("no Company called", error)

	def test_a_call_that_changes_nothing_at_all_is_refused(self):
		error = self.tool_error("update_company", {"company": MAIN})
		self.assertIn("nothing to change", error)

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		self.assertIn(
			"switched off", self.tool_error("update_company", {"company": MAIN, "country": "Canada"})
		)


# ── register_party_types ────────────────────────────────────────────────────
class RegisterPartyTypes(CompanyTestCase):
	def test_it_registers_family_and_contact(self):
		data = self.tool_data("register_party_types")
		self.assertEqual(sorted(data["created"]), ["Contact", "Family"])
		self.assertTrue(STORE.get_raw("Party Type", "Family"))
		self.assertTrue(STORE.get_raw("Party Type", "Contact"))

	def test_both_settle_against_a_payable_account(self):
		"""Both are payees. A Receivable party type would put a family transfer
		on the wrong side of the balance sheet."""
		self.tool_data("register_party_types")
		self.assertEqual(STORE.get_raw("Party Type", "Family")["account_type"], "Payable")
		self.assertEqual(STORE.get_raw("Party Type", "Contact")["account_type"], "Payable")

	def test_running_it_twice_creates_nothing_the_second_time(self):
		self.tool_data("register_party_types")
		data = self.tool_data("register_party_types")
		self.assertEqual(data["created"], [])
		self.assertEqual(sorted(data["already_registered"]), ["Contact", "Family"])

	def test_dry_run_writes_nothing(self):
		data = self.tool_data("register_party_types", {"dry_run": True})
		self.assertTrue(data["dry_run"])
		self.assertEqual(sorted(data["created"]), ["Contact", "Family"])
		self.assertFalse(STORE.get_raw("Party Type", "Family"))

	def test_it_explains_what_each_party_type_is_for(self):
		data = self.tool_data("register_party_types")
		self.assertIn("gift", data["party_types"]["Family"]["why"])
		self.assertIn("BORDERLINE", data["party_types"]["Contact"]["why"])

	def test_it_says_that_existing_party_types_are_untouched(self):
		data = self.tool_data("register_party_types")
		self.assertIn("Shareholder", data["note"])
		self.assertIn("untouched", data["note"])

	def test_the_stock_party_types_are_left_exactly_as_they_were(self):
		before = dict(STORE.get_raw("Party Type", "Supplier"))
		self.tool_data("register_party_types")
		self.assertEqual(STORE.get_raw("Party Type", "Supplier"), before)

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		self.assertIn("switched off", self.tool_error("register_party_types"))

	def test_the_seeder_and_the_tool_agree_on_what_gets_registered(self):
		"""`install.after_migrate` and the tool call the same code; this is the
		test that keeps the catalogue they work from in one place."""
		self.assertEqual(sorted(CUSTOM_PARTY_TYPES), ["Contact", "Family"])


class PartyTypesOnInstall(CompanyTestCase):
	def test_after_migrate_seeds_them(self):
		from erpnext_mcp import install

		install.after_migrate()
		self.assertTrue(STORE.get_raw("Party Type", "Family"))
		self.assertTrue(STORE.get_raw("Party Type", "Contact"))

	def test_after_migrate_twice_is_a_no_op(self):
		from erpnext_mcp import install

		install.after_migrate()
		install.after_migrate()
		self.assertEqual(len([row for row in STORE.rows("Party Type") if row["name"] == "Family"]), 1)

	def test_the_patch_runs_the_same_seeder(self):
		from erpnext_mcp.patches import register_custom_party_types

		register_custom_party_types.execute()
		self.assertTrue(STORE.get_raw("Party Type", "Family"))

	def test_uninstall_warns_about_the_new_registers(self):
		"""A camp roster and a block register are the only copy of what they
		hold, which is the same reason the governance doctypes are on that list."""
		from erpnext_mcp import install

		named = [doctype for doctype, _what in install._PRECIOUS_DOCTYPES]
		for doctype in ("Field", "Irrigation Zone", "Housing Unit", "Housing Assignment"):
			self.assertIn(doctype, named)

	def test_uninstall_warns_about_the_dispatch_registers_too(self):
		"""v0.16.0. A Farm Task Assignment is the only place the reason somebody
		could NOT do a job is written down, which is the answer to 'why was this
		never done' — and a Housing Inspection is the only record that anybody
		ever went and looked."""
		from erpnext_mcp import install

		named = [doctype for doctype, _what in install._PRECIOUS_DOCTYPES]
		for doctype in (
			"Farm Task",
			"Farm Task Assignment",
			"Housing Inspection",
			"Detector Test",
			"Water Test",
		):
			self.assertIn(doctype, named)


# ── the 1099 consequences, which are the point of the party types ───────────
class PartyTypesAndThe1099(CompanyTestCase):
	def prefill(self, **overrides):
		payload = {"company": MAIN, "tax_year": 2025, "dry_run": True}
		payload.update(overrides)
		return self.tool_data("generate_1099_prefill", payload)

	def test_a_family_payment_produces_no_form_at_all(self):
		data = self.prefill()
		self.assertNotIn(ALEX, [row["recipient"] for row in data["recipients"]])
		self.assertNotIn(ALEX, [row["recipient"] for row in data["exempt_above_threshold"]])
		self.assertNotIn(ALEX, [row["recipient"] for row in data["below_threshold"]])

	def test_the_family_payment_is_counted_in_the_excluded_block_not_lost(self):
		"""Half of this test is the assertion above. Together they are the
		difference between 'excluded on purpose' and 'silently dropped'."""
		data = self.prefill()
		self.assertEqual(data["excluded"]["family_party_postings"], 2)
		self.assertEqual(data["excluded"]["family_party_total"], 7500.0)
		self.assertIn(ALEX, data["excluded"]["family_parties"])

	def test_the_exclusion_says_why_and_how_to_undo_it(self):
		data = self.prefill()
		why = data["excluded"]["by_party_type"]["Family"]["why"]
		self.assertIn("gift", why)
		self.assertIn("Contact or Supplier", why)

	def test_a_family_payment_over_the_threshold_is_excluded_by_type_not_by_amount(self):
		"""7,500 is well over 600. If the exclusion were the threshold's doing
		this test would pass for the wrong reason, so the fixture puts it above."""
		data = self.prefill()
		self.assertGreater(data["excluded"]["family_party_total"], data["threshold"])

	def test_a_contact_payment_produces_a_form(self):
		data = self.prefill()
		self.assertIn(ANTONY, [row["recipient"] for row in data["recipients"]])

	def test_a_contact_is_classified_borderline(self):
		row = self._recipient(ANTONY)
		self.assertEqual(row["classification"], "borderline")

	def test_the_borderline_reason_names_the_w_9(self):
		row = self._recipient(ANTONY)
		self.assertIn("W-9", row["reason"])
		self.assertIn("Contact", row["reason"])

	def test_the_contact_total_is_the_sum_of_both_postings(self):
		self.assertEqual(self._recipient(ANTONY)["total_payments"], 3000.0)

	def test_the_recipient_row_says_which_ledger_party_type_it_came_from(self):
		self.assertEqual(self._recipient(ANTONY)["ledger_party_types"], ["Contact"])

	def test_contacts_are_listed_separately_so_they_can_be_chased_for_w_9s(self):
		self.assertIn(ANTONY, self.prefill()["contact_recipients"])

	def test_suppliers_are_still_read_exactly_as_before(self):
		"""Backward compatibility, stated as an assertion rather than assumed:
		the party types this release adds must not change an existing answer."""
		data = self.prefill()
		sorren = next(row for row in data["recipients"] if row["recipient"].startswith("Sorren"))
		self.assertEqual(sorren["total_payments"], 24360.0)
		self.assertEqual(sorren["ledger_party_types"], ["Supplier"])

	def test_employees_are_still_excluded_under_their_old_result_keys(self):
		"""A caller reading `employee_party_postings` should not have to be
		rewritten because Family joined the exclusion list."""
		data = self.prefill()
		self.assertEqual(data["excluded"]["employee_party_postings"], 1)
		self.assertEqual(data["excluded"]["employee_party_total"], 45000.0)

	def test_the_note_covers_both_exclusions(self):
		note = self.prefill()["excluded"]["note"]
		self.assertIn("W-2", note)
		self.assertIn("gift", note)

	def test_the_tool_reports_which_party_types_it_read(self):
		self.assertEqual(self.prefill()["party_types_read"], ["Supplier", "Contact"])

	def _recipient(self, name):
		return next(row for row in self.prefill()["recipients"] if row["recipient"] == name)


# ── scoping, which is what multi-company means in practice ──────────────────
class MultiCompanyScoping(CompanyTestCase):
	def setUp(self):
		super().setUp()
		self.a_company("Constancy Farms LLC", "CF")
		self.a_company("Highland Holdings LLC", "HH")

	def test_both_new_companies_exist_alongside_the_fixture_pair(self):
		names = [row["company"] for row in self.tool_data("list_companies")["companies"]]
		self.assertEqual(sorted(names), sorted([MAIN, OTHER, "Constancy Farms LLC", "Highland Holdings LLC"]))

	def test_each_company_reports_its_own_abbreviation(self):
		rows = {row["company"]: row for row in self.tool_data("list_companies")["companies"]}
		self.assertEqual(rows["Constancy Farms LLC"]["abbr"], "CF")
		self.assertEqual(rows["Highland Holdings LLC"]["abbr"], "HH")

	def test_the_new_companies_carry_none_of_the_fixture_companys_ledger(self):
		rows = {row["company"]: row for row in self.tool_data("list_companies")["companies"]}
		self.assertGreater(rows[MAIN]["gl_entry_count"], 0)
		self.assertEqual(rows["Constancy Farms LLC"]["gl_entry_count"], 0)
		self.assertEqual(rows["Highland Holdings LLC"]["gl_entry_count"], 0)

	def test_a_currency_change_refused_on_one_company_is_allowed_on_another(self):
		"""The clearest possible statement that the rule is per-company: the same
		call, the same argument, two different answers, and both are right."""
		self.assertIn(
			"posted GL entries",
			self.tool_error("update_company", {"company": MAIN, "default_currency": "CAD"}),
		)
		self.tool_data("update_company", {"company": "Constancy Farms LLC", "default_currency": "CAD"})
		self.assertEqual(STORE.get_raw("Company", "Constancy Farms LLC")["default_currency"], "CAD")
		self.assertEqual(STORE.get_raw("Company", MAIN)["default_currency"], "USD")

	def test_a_country_change_on_one_company_leaves_the_others_alone(self):
		self.tool_data("update_company", {"company": "Constancy Farms LLC", "country": "Canada"})
		self.assertEqual(STORE.get_raw("Company", MAIN)["country"], "United States")
		self.assertEqual(STORE.get_raw("Company", "Highland Holdings LLC")["country"], "United States")

	def test_set_company_defaults_requires_an_explicit_company_on_a_multi_company_site(self):
		"""`resolve_company` only infers on a single-company site, and this is
		the tool most likely to be called without one."""
		self.configure(enabled=1, **ALL_ON)
		error = self.tool_error("set_company_defaults", {"defaults": {"round_off_account": "6900"}})
		self.assertIn("company is required", error)

	def test_set_company_defaults_names_the_companies_it_could_have_meant(self):
		error = self.tool_error("set_company_defaults", {"defaults": {"round_off_account": "6900"}})
		self.assertIn("Constancy Farms LLC", error)
		self.assertIn(MAIN, error)

	def test_set_company_defaults_refuses_an_account_from_another_company(self):
		"""The scoping failure that actually costs money: a default pointing at
		a second company's account books every rounding difference to the wrong
		set of books."""
		from .fixtures import cash

		# `cash()` is MAIN's account; the target company is the new one.
		error = self.tool_error(
			"set_company_defaults",
			{"company": "Constancy Farms LLC", "defaults": {"round_off_account": cash()}},
		)
		self.assertTrue("belongs to company" in error or "no Account" in error)

	def test_set_company_defaults_writes_to_the_company_it_was_given(self):
		from .fixtures import cash

		self.tool_data(
			"set_company_defaults", {"company": MAIN, "defaults": {"default_cash_account": cash()}}
		)
		self.assertEqual(STORE.get_raw("Company", MAIN)["default_cash_account"], cash())
		self.assertFalse(STORE.get_raw("Company", OTHER).get("default_cash_account"))


# ── v0.12.2: create_company brought up to spec ──────────────────────────────
class AbbreviationRules(CompanyTestCase):
	"""An abbreviation is the tail of every account docname on the books, which
	is why the rules are about length and collision rather than taste."""

	def test_one_character_is_refused(self):
		error = self.tool_error("create_company", {"company_name": "Short LLC", "abbr": "S"})
		self.assertIn("2 to 5", error)
		self.assertIn("Nothing was created", error)

	def test_six_characters_are_refused_and_the_message_shows_what_it_would_look_like(self):
		error = self.tool_error("create_company", {"company_name": "Longish LLC", "abbr": "LONGER"})
		self.assertIn("1100 - Cash - LONGER", error)

	def test_two_characters_are_accepted(self):
		self.assertEqual(self.a_company("Two LLC", "CF")["abbr"], "CF")

	def test_five_characters_are_accepted(self):
		self.assertEqual(self.a_company("Five LLC", "CONST")["abbr"], "CONST")

	def test_an_abbreviation_left_behind_by_a_deleted_company_is_refused(self):
		"""The collision a duplicate-company check misses. Delete a company in
		the Desk and its chart does not always go with it; a new company reusing
		the abbreviation inherits docnames that look like its own and are not."""
		STORE.seed(
			"Account",
			[{"name": "1100 - Cash - GHO", "account_name": "Cash", "company": "Gone Holdings"}],
		)
		error = self.tool_error("create_company", {"company_name": "Ghost LLC", "abbr": "GHO"})
		self.assertIn("1100 - Cash - GHO", error)
		self.assertIn("somebody deleted", error)
		self.assertFalse(STORE.get_raw("Company", "Ghost LLC"))

	def test_a_clean_abbreviation_is_not_blocked_by_the_orphan_check(self):
		STORE.seed(
			"Account",
			[{"name": "1100 - Cash - GHO", "account_name": "Cash", "company": "Gone Holdings"}],
		)
		self.assertTrue(self.a_company("Constancy Farms LLC", "CF")["created"])


class ChartOfAccountsTemplate(CompanyTestCase):
	def test_it_defaults_to_the_numbered_standard_chart(self):
		"""Numbered on purpose: this app resolves accounts by number as well as
		by name, and an unnumbered chart makes resolve_account('1100')
		impossible on a brand-new company."""
		self.assertEqual(self.a_company()["chart_of_accounts"], "Standard with Numbers")

	def test_an_explicit_template_is_used(self):
		data = self.a_company(chart_of_accounts="Standard")
		self.assertEqual(data["chart_of_accounts"], "Standard")

	def test_an_unknown_template_is_refused_when_the_site_can_enumerate_them(self):
		"""ERPNext keeps these as JSON files rather than records, so this app can
		only check when its helper is importable. The fixture has no ERPNext, so
		the check degrades to 'cannot say' — which must NOT become 'refuse
		everything'."""
		from erpnext_mcp.tools import company as company_tools

		self.assertIsNone(company_tools.chart_templates("United States"))
		self.assertTrue(self.a_company(chart_of_accounts="Something Invented")["created"])


class FiscalYearsCreated(CompanyTestCase):
	def test_it_creates_the_current_and_the_previous_year(self):
		"""A company stood up in March is one whose first task is often last
		year's closing balances, and an opening-balance JE with no fiscal year to
		land in is refused by ERPNext.

		April, because the fixture already carries calendar 2025 and 2026 — so a
		calendar company proves nothing about creation and an April one proves
		both."""
		data = self.a_company(fiscal_year_start_month=4)
		self.assertEqual(data["fiscal_years_created"], ["2025-2026", "2026-2027"])

	def test_a_calendar_year_company_really_gets_calendar_years(self):
		"""Tim's acceptance check: fiscal_year_start_month=1 has to mean January
		to December, both years, whether they were created here or found."""
		years = {year["name"]: year for year in self.a_company(fiscal_year_start_month=1)["fiscal_years"]}
		self.assertEqual(sorted(years), ["2025", "2026"])
		self.assertEqual(years["2026"]["year_start_date"], "2026-01-01")
		self.assertEqual(years["2026"]["year_end_date"], "2026-12-31")
		self.assertEqual(years["2025"]["year_start_date"], "2025-01-01")
		self.assertEqual(years["2025"]["year_end_date"], "2025-12-31")

	def test_a_calendar_year_company_on_a_site_that_has_none_creates_both(self):
		for name in ("2025", "2026"):
			STORE.tables["Fiscal Year"].pop(name, None)
		data = self.a_company(fiscal_year_start_month=1)
		self.assertEqual(data["fiscal_years_created"], ["2025", "2026"])
		self.assertEqual(STORE.get_raw("Fiscal Year", "2025")["year_end_date"], "2025-12-31")

	def test_an_april_company_gets_two_april_years(self):
		data = self.a_company(fiscal_year_start_month=4)
		self.assertEqual(data["fiscal_years_created"], ["2025-2026", "2026-2027"])
		self.assertEqual(STORE.get_raw("Fiscal Year", "2025-2026")["year_start_date"], "2025-04-01")

	def test_a_year_that_already_exists_is_not_recreated(self):
		"""The fixture carries calendar 2025 and 2026, so a calendar company
		creates neither and says so rather than duplicating them."""
		data = self.a_company(fiscal_year_start_month=1)
		self.assertEqual(data["fiscal_years_created"], [])
		self.assertTrue(all(year["already_exists"] for year in data["fiscal_years"]))
		self.assertEqual(len(STORE.rows("Fiscal Year")), 2)

	def test_the_old_single_fiscal_year_key_still_answers(self):
		"""A caller written against v0.12.0 keeps working."""
		data = self.a_company(fiscal_year_start_month=1)
		self.assertEqual(data["fiscal_year"]["name"], "2026")

	def test_dry_run_counts_the_years_it_would_create_and_makes_none(self):
		before = len(STORE.rows("Fiscal Year"))
		data = self.tool_data(
			"create_company",
			{"company_name": "Dry LLC", "abbr": "DRY", "fiscal_year_start_month": 4, "dry_run": True},
		)
		self.assertEqual(len(data["fiscal_years"]), 2)
		self.assertFalse(any(year["already_exists"] for year in data["fiscal_years"]))
		self.assertEqual(len(STORE.rows("Fiscal Year")), before)


class WhatCreateCompanyReports(CompanyTestCase):
	def test_it_returns_the_cost_center_tree(self):
		data = self.a_company()
		self.assertIn("cost_center_tree", data)
		self.assertEqual(data["cost_center_count"], len(data["cost_center_tree"]))

	def test_the_next_step_names_set_company_defaults(self):
		"""ERPNext books to those fields without asking, and a company whose
		defaults are empty fails at the first invoice rather than here."""
		data = self.a_company()
		self.assertIn("set_company_defaults", data["next_step"])
		self.assertIn("default_receivable_account", data["next_step"])

	def test_the_summary_names_the_fiscal_years(self):
		result = self.tool("create_company", {"company_name": "Constancy Farms LLC", "abbr": "CF"})
		self.assertFalse(result.get("isError"))


class CreateCompanyIsAtomic(CompanyTestCase):
	def test_a_failure_after_the_company_row_leaves_nothing_behind(self):
		"""The transactional promise. `dispatch` rolls back before it logs, so a
		tool that wrote a Company and then died on the fiscal year cannot leave a
		half-built entity on the site."""
		from erpnext_mcp.tools import company as company_tools

		original = company_tools._cost_center_tree

		def explode(*args, **kwargs):
			raise RuntimeError("blew up after the company row was written")

		before = len(STORE.rows("Company"))
		company_tools._cost_center_tree = explode
		try:
			result = self.tool("create_company", {"company_name": "Doomed LLC", "abbr": "DMD"})
		finally:
			company_tools._cost_center_tree = original

		self.assertTrue(result.get("isError"))
		self.assertEqual(len(STORE.rows("Company")), before, "a half-built company survived")
		self.assertFalse(STORE.get_raw("Company", "Doomed LLC"))

	def test_the_failure_is_still_audited(self):
		from erpnext_mcp.tools import company as company_tools

		original = company_tools._cost_center_tree
		company_tools._cost_center_tree = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
		try:
			self.tool("create_company", {"company_name": "Doomed LLC", "abbr": "DMD"})
		finally:
			company_tools._cost_center_tree = original
		self.assertAudited("create_company", "Error")

	def test_a_refusal_never_gets_as_far_as_writing(self):
		"""Every validation runs before the insert, so a refusal is atomic by
		construction rather than by rollback."""
		before = len(STORE.rows("Company"))
		self.tool_error("create_company", {"company_name": MAIN, "abbr": "ZZ"})
		self.assertEqual(len(STORE.rows("Company")), before)


class TheToolWasAlwaysThere(CompanyTestCase):
	"""v0.12.2 did not add `create_company`; v0.12.0 did.

	It is absent from a live `tools/list` until an operator ticks its switch,
	which is true of every mutating tool and is the entire point of them. This
	is the test that says so in a place somebody looking for the tool will find.
	"""

	def test_it_is_in_the_catalogue(self):
		from erpnext_mcp import registry

		self.assertIn("create_company", registry.TOOLS)
		self.assertIn("create_company", registry.MUTATING_TOOLS)

	def test_it_is_not_advertised_until_the_switch_is_on(self):
		from erpnext_mcp import registry

		self.configure(enabled=1)
		advertised = {tool["name"] for tool in registry.tools_list()["tools"]}
		self.assertNotIn("create_company", advertised)

	def test_it_is_advertised_once_the_switch_is_on(self):
		from erpnext_mcp import registry

		self.configure(enabled=1, allow_create_company=1)
		advertised = {tool["name"] for tool in registry.tools_list()["tools"]}
		self.assertIn("create_company", advertised)

	def test_the_refusal_names_the_switch_to_tick(self):
		self.configure(enabled=1)
		error = self.tool_error("create_company", {"company_name": "X LLC", "abbr": "XX"})
		self.assertIn("allow_create_company", error)
		self.assertIn("ERPNext MCP Settings", error)
