# SPDX-License-Identifier: MIT
"""Fiscal years: the calendar the ledger is allowed to post into.

Three things these tests are really about.

THE OVERLAP CHECK IS COMPANY-AWARE. Two fiscal years covering the same day for
the same company make ERPNext's own `get_fiscal_year` ambiguous, and which year a
posting lands in stops being a fact about the posting. A year with no companies
is global and collides with everything; two restricted years collide only where
they share one. Every branch of that has a test, because the shape of the rule is
the whole feature — an overlap check that only compared dates would refuse the
per-company years a group structure needs, and one that only compared companies
would let a global year sit on top of a restricted one.

MOVING THE DATES ORPHANS POSTINGS. It moves nothing; it changes which year every
posting already written falls into, retroactively. A GL Entry that ends up in no
fiscal year drops out of period comparisons and cannot be corrected without
reopening a year that no longer covers it. `update_fiscal_year` counts those
before it writes and refuses, so the refusal is tested against real GL rows in
the fixture rather than against a mock.

ERPNEXT'S ONE-YEAR RULE. `FiscalYear.validate_dates` refuses an end date that is
not exactly one year after the start less a day, unless `is_short_year` is set,
and its message does not say what date it wanted. This computes it — including
the leap-year case, which is the only reason the arithmetic is worth testing at
all.
"""

from .fixtures import MAIN, OTHER, SeededTestCase
from .harness import STORE, frappe

ALL_ON = {"allow_create_fiscal_year": 1, "allow_update_fiscal_year": 1}


class FiscalYearTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def payload(self, **overrides):
		values = {
			"year_name": "2024",
			"year_start_date": "2024-01-01",
			"year_end_date": "2024-12-31",
		}
		values.update(overrides)
		return {key: value for key, value in values.items() if value is not None}

	def create(self, **overrides):
		return self.tool_data("create_fiscal_year", self.payload(**overrides))


# ── create_fiscal_year ──────────────────────────────────────────────────────
class CreateFiscalYear(FiscalYearTestCase):
	def test_it_creates_a_year_named_after_itself(self):
		data = self.create()
		self.assertEqual(data["name"], "2024")
		self.assertEqual(data["year"], "2024")
		self.assertEqual(data["year_start_date"], "2024-01-01")
		self.assertEqual(data["year_end_date"], "2024-12-31")
		self.assertTrue(frappe.db.exists("Fiscal Year", "2024"))

	def test_the_docname_comes_from_the_year_field_as_erpnext_names_it(self):
		"""Not written to `name` directly — a Fiscal Year names itself from `year`,
		and a tool that set the docname by hand would work here and diverge on a
		real site."""
		self.create()
		self.assertEqual(frappe.db.get_value("Fiscal Year", "2024", "year"), "2024")

	def test_a_year_with_no_companies_is_global_and_says_so(self):
		data = self.create()
		self.assertEqual(data["companies"], [])
		self.assertEqual(data["scope"], "every company on this site")
		self.assertIn("every company on this site", data["note"])

	def test_it_can_be_restricted_to_companies(self):
		data = self.create(year_name="2024-OTHER", companies=[OTHER])
		self.assertEqual(data["companies"], [OTHER])
		self.assertIn(OTHER, data["note"])

	def test_a_single_company_can_be_given_as_a_string(self):
		data = self.create(year_name="2024-OTHER", companies=OTHER)
		self.assertEqual(data["companies"], [OTHER])

	def test_a_company_abbreviation_resolves(self):
		from .fixtures import OTHER_ABBR

		data = self.create(year_name="2024-OTHER", companies=[OTHER_ABBR])
		self.assertEqual(data["companies"], [OTHER])

	def test_it_shows_up_in_list_fiscal_years(self):
		self.create()
		data = self.tool_data("list_fiscal_years", {})
		self.assertIn("2024", [year["name"] for year in data["fiscal_years"]])

	def test_it_says_what_it_has_and_has_not_done(self):
		data = self.create()
		self.assertIn("permission for a date, not a posting", data["note"])
		self.assertIn("set_opening_balance", data["next_step"])

	def test_a_disabled_year_is_created_with_a_warning_that_it_still_refuses_postings(self):
		data = self.create(disabled=True)
		self.assertTrue(data["disabled"])
		self.assertIn("still refuse postings", data["warning"])

	def test_auto_created_is_recorded(self):
		data = self.create(auto_created=True)
		self.assertTrue(data["auto_created"])

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_fiscal_year", self.payload())
		self.assertIn("allow_create_fiscal_year", message)
		self.assertFalse(frappe.db.exists("Fiscal Year", "2024"))


class CreateFiscalYearRefusals(FiscalYearTestCase):
	def test_a_name_already_in_use_is_refused_with_its_dates(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_name="2026", year_start_date="2027-01-01", year_end_date="2027-12-31"),
		)
		self.assertIn("already exists", message)
		self.assertIn("2026-01-01", message)
		self.assertIn("update_fiscal_year", message)

	def test_an_end_before_the_start_is_refused(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_start_date="2024-12-31", year_end_date="2024-01-01"),
		)
		self.assertIn("is before year_start_date", message)

	def test_a_range_that_is_not_exactly_one_year_names_the_date_it_wanted(self):
		message = self.tool_error(
			"create_fiscal_year", self.payload(year_end_date="2024-06-30")
		)
		self.assertIn("has to end 2024-12-31", message)
		self.assertIn("is_short_year", message)
		self.assertFalse(frappe.db.exists("Fiscal Year", "2024"))

	def test_a_deliberately_short_year_is_allowed_when_said_so(self):
		data = self.create(year_end_date="2024-06-30", is_short_year=True)
		self.assertTrue(data["is_short_year"])
		self.assertEqual(data["year_end_date"], "2024-06-30")

	def test_the_one_year_rule_handles_a_leap_day_start(self):
		"""A year starting on 29 February. One year later is clamped to 28 February
		— there is no 29th — so the last day is the 27th. That clamp is the only
		reason this arithmetic is worth its own test, and getting it wrong produces
		a refusal naming a date that does not exist."""
		data = self.create(
			year_name="2020-21", year_start_date="2020-02-29", year_end_date="2021-02-27"
		)
		self.assertEqual(data["year_end_date"], "2021-02-27")
		self.assertEqual(data["expected_end_date_for_a_full_year"], "2021-02-27")

	def test_a_one_day_year_is_refused(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_start_date="2024-01-01", year_end_date="2024-01-01"),
		)
		self.assertIn("cannot be one day long", message)

	def test_a_company_this_site_does_not_have_is_refused(self):
		message = self.tool_error(
			"create_fiscal_year", self.payload(companies=["Nonesuch Holdings"])
		)
		self.assertIn("no Company named", message)

	def test_an_empty_company_entry_is_refused(self):
		message = self.tool_error("create_fiscal_year", self.payload(companies=["", MAIN]))
		self.assertIn("empty entry", message)

	def test_companies_as_an_object_is_refused_with_an_example(self):
		message = self.tool_error("create_fiscal_year", self.payload(companies={"company": MAIN}))
		self.assertIn("must be a list of Company names", message)


class FiscalYearOverlaps(FiscalYearTestCase):
	"""The fixture has 2026 (restricted to the main company) and 2025 (global)."""

	def test_a_global_year_overlapping_a_global_year_is_refused(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_name="2025-dup", year_start_date="2025-01-01", year_end_date="2025-12-31"),
		)
		self.assertIn("overlaps 1 existing fiscal year", message)
		self.assertIn("2025", message)
		self.assertIn("it applies to every company", message)

	def test_a_global_year_overlapping_a_restricted_one_is_refused(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_name="2026-dup", year_start_date="2026-01-01", year_end_date="2026-12-31"),
		)
		self.assertIn("2026", message)
		self.assertIn("this year would apply to every company", message)

	def test_a_restricted_year_overlapping_a_global_one_is_refused(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(
				year_name="2025-etc",
				year_start_date="2025-01-01",
				year_end_date="2025-12-31",
				companies=[MAIN],
			),
		)
		self.assertIn("2025", message)
		self.assertIn("it applies to every company", message)

	def test_two_restricted_years_that_share_no_company_do_not_collide(self):
		"""The case a date-only check would wrongly refuse: two companies, each
		with its own fiscal calendar for the same period."""
		self.tool_data(
			"create_fiscal_year",
			self.payload(
				year_name="2027-etc",
				year_start_date="2027-01-01",
				year_end_date="2027-12-31",
				companies=[MAIN],
			),
		)
		data = self.tool_data(
			"create_fiscal_year",
			self.payload(
				year_name="2027-sel",
				year_start_date="2027-01-01",
				year_end_date="2027-12-31",
				companies=[OTHER],
			),
		)
		self.assertEqual(data["companies"], [OTHER])

	def test_two_restricted_years_that_share_a_company_do_collide(self):
		self.tool_data(
			"create_fiscal_year",
			self.payload(
				year_name="2027-etc",
				year_start_date="2027-01-01",
				year_end_date="2027-12-31",
				companies=[MAIN],
			),
		)
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(
				year_name="2027-both",
				year_start_date="2027-01-01",
				year_end_date="2027-12-31",
				companies=[MAIN, OTHER],
			),
		)
		self.assertIn(f"they share {MAIN}", message)

	def test_a_partial_overlap_at_the_edge_still_counts(self):
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_start_date="2024-12-31", year_end_date="2025-12-30", is_short_year=True),
		)
		self.assertIn("overlaps", message)

	def test_an_adjacent_year_touching_at_the_boundary_is_fine(self):
		"""2024 ends the day before the fixture's 2025 starts."""
		data = self.create()
		self.assertEqual(data["year_end_date"], "2024-12-31")

	def test_a_disabled_year_still_holds_its_range(self):
		frappe.db.set_value("Fiscal Year", "2025", "disabled", 1)
		message = self.tool_error(
			"create_fiscal_year",
			self.payload(year_name="2025-dup", year_start_date="2025-01-01", year_end_date="2025-12-31"),
		)
		self.assertIn("disabled", message)
		self.assertIn("Disabling a year does not free its range", message)


# ── update_fiscal_year ──────────────────────────────────────────────────────
class UpdateFiscalYear(FiscalYearTestCase):
	def test_it_disables_and_re_enables(self):
		data = self.tool_data("update_fiscal_year", {"year_name": "2026", "disabled": True})
		self.assertTrue(data["disabled"])
		self.assertIn("refuse new postings", data["warning"])
		back = self.tool_data("update_fiscal_year", {"year_name": "2026", "disabled": False})
		self.assertFalse(back["disabled"])

	def test_disabling_deletes_nothing_and_says_what_stays(self):
		data = self.tool_data("update_fiscal_year", {"year_name": "2026", "disabled": True})
		self.assertGreater(data["gl_entries_in_the_new_range"], 0)
		self.assertIn("Nothing was deleted", data["warning"])

	def test_widening_a_range_is_allowed(self):
		data = self.tool_data(
			"update_fiscal_year",
			{"year_name": "2026", "new_year_end_date": "2027-06-30", "is_short_year": True},
		)
		self.assertEqual(data["year_end_date"], "2027-06-30")
		self.assertEqual(
			sorted(data["changes"]), ["is_short_year", "year_end_date"]
		)

	def test_a_move_that_would_orphan_postings_is_refused_with_the_count(self):
		"""The fixture posts to 2026 through the year. Shrinking it to January
		leaves the rest of them in no fiscal year at all."""
		message = self.tool_error(
			"update_fiscal_year",
			{
				"year_name": "2026",
				"new_year_end_date": "2026-01-31",
				"is_short_year": True,
			},
		)
		self.assertIn("outside any fiscal year", message)
		self.assertIn("GL Entry row(s)", message)
		self.assertEqual(
			str(frappe.db.get_value("Fiscal Year", "2026", "year_end_date")), "2026-12-31"
		)

	def test_a_year_with_no_postings_can_be_moved_freely(self):
		self.create()
		data = self.tool_data(
			"update_fiscal_year",
			{"year_name": "2024", "new_year_start_date": "2023-07-01", "new_year_end_date": "2024-06-30"},
		)
		self.assertEqual(data["year_start_date"], "2023-07-01")
		self.assertEqual(data["gl_entries_in_the_new_range"], 0)

	def test_a_move_onto_another_years_range_is_refused(self):
		self.create()
		message = self.tool_error(
			"update_fiscal_year",
			{"year_name": "2024", "new_year_start_date": "2025-01-01", "new_year_end_date": "2025-12-31"},
		)
		self.assertIn("overlaps", message)
		self.assertIn("2025", message)

	def test_it_cannot_rename(self):
		message = self.tool_error("update_fiscal_year", {"year_name": "2026", "year": "2026-27"})
		self.assertIn("cannot be renamed", message)

	def test_it_cannot_change_the_companies(self):
		message = self.tool_error(
			"update_fiscal_year", {"year_name": "2026", "companies": [OTHER]}
		)
		self.assertIn("cannot change which companies", message)

	def test_asking_for_nothing_is_refused(self):
		message = self.tool_error("update_fiscal_year", {"year_name": "2026"})
		self.assertIn("nothing to change", message)

	def test_asking_for_the_value_it_already_has_is_refused(self):
		message = self.tool_error(
			"update_fiscal_year", {"year_name": "2026", "new_year_end_date": "2026-12-31"}
		)
		self.assertIn("already has those values", message)

	def test_an_unknown_year_is_refused_with_the_ones_that_exist(self):
		message = self.tool_error("update_fiscal_year", {"year_name": "1999", "disabled": True})
		self.assertIn("no Fiscal Year named '1999'", message)
		self.assertIn("2026", message)
		self.assertIn("list_fiscal_years", message)

	def test_a_shortened_year_still_has_to_be_declared_short(self):
		self.create()
		message = self.tool_error(
			"update_fiscal_year", {"year_name": "2024", "new_year_end_date": "2024-06-30"}
		)
		self.assertIn("has to end 2024-12-31", message)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("update_fiscal_year", {"year_name": "2026", "disabled": True})
		self.assertIn("allow_update_fiscal_year", message)
		self.assertFalse(frappe.db.get_value("Fiscal Year", "2026", "disabled"))


# ── the point of all of it ──────────────────────────────────────────────────
class BookingIntoAYearThatDidNotExist(FiscalYearTestCase):
	"""Why the tool exists: an opening balance for a period the site cannot reach.

	The double does not enforce ERPNext's posting-date rule — that is a framework
	fact, and it has its own in-bench test — so what is asserted here is the part
	this app owns: that the year the caller needs can be created first, in one
	call, and that it covers the date they are about to use.
	"""

	def test_the_year_can_be_created_then_posted_into(self):
		from .fixtures import EQUIPMENT, OPENING_EQUITY, seed_v7, seed_v8

		seed_v7()
		seed_v8()
		self.configure(enabled=1, allow_set_opening_balance=1, **ALL_ON)

		year = self.tool_data(
			"create_fiscal_year",
			{"year_name": "2023", "year_start_date": "2023-01-01", "year_end_date": "2023-12-31"},
		)
		self.assertLessEqual(year["year_start_date"], "2023-03-20")
		self.assertGreaterEqual(year["year_end_date"], "2023-03-20")

		data = self.tool_data(
			"set_opening_balance",
			{
				"company": MAIN,
				"posting_date": "2023-03-20",
				"user_remark": "Equipment transferred from PFI on dissolution",
				"entries": [{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 52650}],
			},
		)
		self.assertEqual(data["opening_equity_account"], OPENING_EQUITY)
		self.assertEqual(
			str(frappe.get_doc("Journal Entry", data["name"]).get("posting_date")), "2023-03-20"
		)


class FiscalYearAudit(FiscalYearTestCase):
	def test_a_refusal_writes_an_audit_row_and_creates_nothing(self):
		before = frappe.db.count("Fiscal Year")
		self.tool_error(
			"create_fiscal_year",
			self.payload(year_name="2025-dup", year_start_date="2025-01-01", year_end_date="2025-12-31"),
		)
		self.assertEqual(frappe.db.count("Fiscal Year"), before)
		self.assertAudited("create_fiscal_year", status="Error")

	def test_the_seeded_fixture_still_has_the_years_the_other_suites_expect(self):
		"""Guards the guard: these tests create and move fiscal years, and every
		other module's posting dates depend on 2025 and 2026 being there."""
		self.assertEqual(
			sorted(str(name) for name in frappe.db.get_all("Fiscal Year", pluck="name")),
			["2025", "2026"],
		)
		self.assertEqual(STORE.rows("Fiscal Year")[0]["year_start_date"], "2026-01-01")
