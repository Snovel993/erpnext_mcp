# SPDX-License-Identifier: MIT
"""In-bench tests for v0.8.0: notes payable, bank accounts and opening balances.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_notes

WHAT ONLY A BENCH CAN SHOW HERE. The standalone suite proves the logic against a
double, and a double agrees with whatever it was written to agree with. These are
facts about Frappe and about the operator's own ERPNext:

  * **The two new DocTypes migrate**, including the child table and its
    controller module. A DocType JSON Frappe refuses is a feature that does not
    exist, and v0.7.0 shipped exactly that.
  * **The Note Payable controller's `frappe.throw`s fire** on the Desk path,
    which is the one a human takes and the one a tool's own checks cannot cover.
  * **ERPNext accepts an opening-balance Journal Entry** with `is_opening = Yes`
    and voucher type `Opening Entry`. That is the assertion most worth having on
    a real site: this app sets both, and if a version of ERPNext validates them
    differently the failure lands on a live opening balance.
  * **ERPNext accepts a Bank Account** built by `create_bank_account`, including
    its own `autoname` — the docname a caller copies into a bank feed.
  * **A new root account can actually be created.** THE v0.8.0 BUG: ERPNext marks
    `parent_account` required, so importing a chart with a new top-level account
    died with MandatoryError on the first root. Only a real Account doctype can
    prove the fix.

Everything is created inside the test transaction and rolled back, except the
audit rows the base class cleans up. Tests skip rather than fail where the
operator's site cannot support them — a site with no Equity account cannot take
an opening balance, which is a fact about the site rather than a broken app.
"""

import frappe

from .test_integration import MCPIntegrationTestCase

V8_TOOLS = (
	"create_bank_account",
	"set_opening_balance",
	"delete_account",
	"create_note_payable",
	"record_loan_payment",
	"list_notes_payable",
	"close_note_payable",
	"create_account",
	"import_chart_of_accounts",
	"create_fiscal_year",
	"update_fiscal_year",
	"create_journal_entry",
)

#: Prefixed so nothing here can collide with a real note, bank or account on the
#: operator's site.
PREFIX = "MCPTEST"

#: Every DocType v0.8.0 adds. The child table included — see the module-import
#: test, and CHANGELOG 0.7.1 for why that is not a formality.
V8_DOCTYPES = ("Note Payable", "Note Payable Event")


class V8IntegrationTestCase(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable(*V8_TOOLS)
		self.company = self.any_company()

	#: Account types a note's own accounts must not have — see
	#: `tools/notes._vetted_account`. A Payable-typed liability would make the
	#: note's principal show up as a supplier balance that never ages out.
	PARTY_TYPES = ("Payable", "Receivable")

	def account_of(self, root_type, account_type=None, exclude_party_types=False):
		"""One postable account of a given shape on this site, or skip.

		Discovered rather than created, like every other site-shape helper here:
		the point of this app is that it works against whatever chart the operator
		already has, and a test that built its own would stop testing that.
		"""
		filters = {
			"company": self.company,
			"is_group": 0,
			"disabled": 0,
			"root_type": root_type,
		}
		if account_type is not None:
			filters["account_type"] = account_type
		rows = frappe.db.get_all(
			"Account", filters=filters, fields=["name", "account_type"], limit=50, order_by="name asc"
		)
		for row in rows:
			if exclude_party_types and (row.account_type or "") in self.PARTY_TYPES:
				continue
			return row.name
		self.skipTest(
			f"site has no postable {root_type} account"
			+ (f" of type {account_type!r}" if account_type else "")
			+ f" for {self.company}"
		)

	def a_note(self, **overrides):
		payload = {
			"borrower": self.company,
			"note_name": f"{PREFIX} equipment note",
			"lender": f"{PREFIX} Bank",
			"principal_original": 120000,
			"origination_date": "2020-01-01",
			"maturity_date": "2030-01-01",
			"interest_rate": 6.5,
			"linked_gl_account": self.account_of("Liability", exclude_party_types=True),
		}
		payload.update(overrides)
		return self.tool_data("create_note_payable", payload)


class DocTypesMigrated(V8IntegrationTestCase):
	def test_every_doctype_this_version_adds_is_on_the_site(self):
		for doctype in V8_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype), f"{doctype} did not migrate")

	def test_frappe_can_import_every_doctypes_module(self):
		"""A row can exist for a doctype whose module cannot be imported — that gap
		is where v0.7.0's failed migrate lived. `load_doctype_module` is the frame
		at the top of that traceback."""
		from frappe.modules.utils import load_doctype_module

		for doctype in V8_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertIsNotNone(load_doctype_module(doctype), f"{doctype} has no module")
				controller = frappe.get_controller(doctype)
				self.assertEqual(
					controller.__name__,
					doctype.replace(" ", ""),
					f"{doctype} fell back to a base class, so its validation does not run",
				)

	def test_the_event_table_really_is_a_child_table(self):
		self.assertTrue(frappe.db.get_value("DocType", "Note Payable Event", "istable"))

	def test_every_new_tool_has_a_switch_that_migrated(self):
		meta = frappe.get_meta("ERPNext MCP Settings")
		for name in V8_TOOLS:
			with self.subTest(tool=name):
				self.assertTrue(meta.has_field(f"allow_{name}"), f"allow_{name} did not migrate")


class NotePayableContract(V8IntegrationTestCase):
	def test_the_docname_is_the_note_name_and_the_company_abbreviation(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		data = self.a_note()
		self.assertEqual(data["name"], f"{PREFIX} equipment note - {abbr}")

	def test_the_controller_refuses_a_duplicate_from_the_desk_path(self):
		"""Not through the tool — through the path a human takes, which the tool's
		own duplicate check cannot cover."""
		self.a_note()
		duplicate = frappe.get_doc(
			{
				"doctype": "Note Payable",
				"note_name": f"{PREFIX} equipment note",
				"borrower": self.company,
				"lender": "Someone Else",
				"status": "Active",
				"principal_original": 1,
				"origination_date": "2021-01-01",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert()

	def test_the_controller_refuses_a_maturity_before_origination(self):
		note = frappe.get_doc(
			{
				"doctype": "Note Payable",
				"note_name": f"{PREFIX} backwards note",
				"borrower": self.company,
				"lender": f"{PREFIX} Bank",
				"status": "Active",
				"principal_original": 1000,
				"origination_date": "2026-01-01",
				"maturity_date": "2025-01-01",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			note.insert()

	def test_a_payment_produces_a_draft_journal_entry_erpnext_accepts(self):
		liability = self.account_of("Liability", exclude_party_types=True)
		expense = self.account_of("Expense")
		bank = self.account_of("Asset", exclude_party_types=True)
		note = self.a_note(linked_gl_account=liability, interest_expense_account=expense)
		data = self.tool_data(
			"record_loan_payment",
			{
				"note": note["name"],
				"payment_date": self.open_posting_date(self.company),
				"total_amount": 1100,
				"principal_split": 1000,
				"interest_split": 100,
				"offset_bank_account": bank,
			},
		)
		entry = frappe.get_doc("Journal Entry", data["journal_entry"])
		self.assertEqual(entry.docstatus, 0)
		self.assertEqual(float(entry.total_debit), 1100.0)
		self.assertEqual(data["principal_outstanding_after"], 119000.0)
		self.assertEqual(len(frappe.get_doc("Note Payable", note["name"]).get("payment_events")), 1)

	def test_closing_writes_no_journal_entry_at_all(self):
		note = self.a_note()
		before = frappe.db.count("Journal Entry")
		self.tool_data(
			"close_note_payable",
			{
				"note": note["name"],
				"disposition": "Written Off",
				"disposition_date": "2026-06-30",
				"narrative": "Forgiven under the settlement deed, per counsel's advice.",
			},
		)
		self.assertEqual(frappe.db.count("Journal Entry"), before)
		self.assertEqual(frappe.db.get_value("Note Payable", note["name"], "status"), "Written Off")


class OpeningBalanceContract(V8IntegrationTestCase):
	def test_erpnext_accepts_an_entry_flagged_as_opening(self):
		"""The assertion most worth having on a real site. This app sets
		`is_opening` and the `Opening Entry` voucher type, and if a version of
		ERPNext validates either differently the failure lands on a live opening
		balance — the one entry nobody can re-derive."""
		equity = self.account_of("Equity")
		asset = self.account_of("Asset", exclude_party_types=True)
		data = self.tool_data(
			"set_opening_balance",
			{
				"company": self.company,
				"posting_date": self.open_posting_date(self.company),
				"opening_equity_account": equity,
				"user_remark": f"{PREFIX} equipment transferred in on dissolution",
				"entries": [{"account": asset, "dr_or_cr": "dr", "amount": 52650}],
			},
		)
		entry = frappe.get_doc("Journal Entry", data["name"])
		self.assertEqual(entry.docstatus, 0)
		self.assertEqual(entry.is_opening, "Yes")
		self.assertEqual(float(entry.total_debit), float(entry.total_credit))
		credit = next(row for row in entry.accounts if float(row.credit or 0))
		self.assertEqual(credit.account, equity)
		self.assertEqual(float(credit.credit), 52650.0)

	def test_the_opening_entry_voucher_type_is_one_this_erpnext_offers(self):
		options = frappe.get_meta("Journal Entry").get_field("voucher_type").options or ""
		self.assertIn(
			"Opening Entry",
			[line.strip() for line in options.split("\n")],
			"this ERPNext has no 'Opening Entry' voucher type; set_opening_balance "
			"falls back to an ordinary one, which is handled but worth knowing",
		)


class FiscalYearContract(V8IntegrationTestCase):
	"""Why `create_fiscal_year` exists, proved against the rule that motivates it.

	ERPNext refuses a posting whose date falls outside a fiscal year, and it
	refuses it from inside the document being saved. A double cannot show that —
	it is a framework fact — so the end-to-end case lives here: create the year,
	then post into it, and watch the same posting fail before the year exists.
	"""

	def free_year(self):
		"""A four-digit year no existing Fiscal Year overlaps, or skip.

		Historic on purpose. The operator's site has real fiscal years covering
		the periods it trades in, and this must not collide with one of them.
		"""
		existing = frappe.db.get_all(
			"Fiscal Year", fields=["name", "year_start_date", "year_end_date"], limit=500
		)
		for candidate in range(1970, 2000):
			start = f"{candidate}-01-01"
			end = f"{candidate}-12-31"
			if frappe.db.exists("Fiscal Year", str(candidate)):
				continue
			clash = any(
				str(row.year_start_date) <= end and start <= str(row.year_end_date) for row in existing
			)
			if not clash:
				return str(candidate)
		self.skipTest("no free four-digit year between 1970 and 1999 on this site")

	def test_erpnext_accepts_a_year_this_tool_created(self):
		year = self.free_year()
		data = self.tool_data(
			"create_fiscal_year",
			{
				"year_name": year,
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			},
		)
		self.assertEqual(data["name"], year)
		row = frappe.db.get_value(
			"Fiscal Year", year, ["year", "year_start_date", "year_end_date"], as_dict=True
		)
		self.assertIsNotNone(row, "the fiscal year was not created")
		self.assertEqual(row.year, year, "ERPNext named it from something other than `year`")
		self.assertEqual(str(row.year_start_date), f"{year}-01-01")

	def test_erpnexts_own_one_year_rule_agrees_with_this_apps_arithmetic(self):
		"""`FiscalYear.validate_dates` insists the end is one year after the start
		less a day. This app computes that date so it can say which one it wanted;
		if the two ever disagree, the refusal names a date ERPNext would reject."""
		from erpnext_mcp.tools.fiscal import one_year_end

		year = self.free_year()
		self.assertEqual(one_year_end(f"{year}-01-01"), f"{year}-12-31")
		result = self.tool(
			"create_fiscal_year",
			{
				"year_name": year,
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-06-30",
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn(f"{year}-12-31", result["content"][0]["text"])

	def test_a_posting_outside_every_fiscal_year_is_refused_by_erpnext(self):
		"""The rule the tool exists for. If this ever stops failing, ERPNext has
		changed and `create_fiscal_year` is no longer a prerequisite for anything."""
		year = self.free_year()
		debit, credit = self.leaf_accounts(self.company, 2)
		result = self.tool(
			"create_journal_entry",
			{
				"company": self.company,
				"posting_date": f"{year}-06-15",
				"user_remark": f"{PREFIX} posting into a year that does not exist",
				"accounts": [
					{"account": debit, "debit": 10},
					{"account": credit, "credit": 10},
				],
			},
		)
		self.assertTrue(
			result["isError"],
			"ERPNext accepted a posting outside every fiscal year — create_fiscal_year "
			"is no longer the prerequisite this app documents it as",
		)

	def test_and_is_accepted_once_the_year_exists(self):
		year = self.free_year()
		debit, credit = self.leaf_accounts(self.company, 2)
		self.tool_data(
			"create_fiscal_year",
			{
				"year_name": year,
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			},
		)
		data = self.tool_data(
			"create_journal_entry",
			{
				"company": self.company,
				"posting_date": f"{year}-06-15",
				"user_remark": f"{PREFIX} posting into a year created a moment ago",
				"accounts": [
					{"account": debit, "debit": 10},
					{"account": credit, "credit": 10},
				],
			},
		)
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(
			str(frappe.db.get_value("Journal Entry", data["name"], "posting_date")), f"{year}-06-15"
		)

	def test_disabling_and_re_enabling_round_trips(self):
		year = self.free_year()
		self.tool_data(
			"create_fiscal_year",
			{
				"year_name": year,
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			},
		)
		self.tool_data("update_fiscal_year", {"year_name": year, "disabled": True})
		self.assertTrue(frappe.db.get_value("Fiscal Year", year, "disabled"))
		self.tool_data("update_fiscal_year", {"year_name": year, "disabled": False})
		self.assertFalse(frappe.db.get_value("Fiscal Year", year, "disabled"))


class BankAccountContract(V8IntegrationTestCase):
	def test_erpnext_accepts_the_bank_and_the_bank_account(self):
		account = self.account_of("Asset", account_type="Bank")
		data = self.tool_data(
			"create_bank_account",
			{
				"company": self.company,
				"account_name": f"{PREFIX} Operating",
				"bank_name": f"{PREFIX} Bank of Nowhere",
				"account": account,
				"account_no": "0001",
			},
		)
		self.assertTrue(data["bank_created"])
		self.assertTrue(frappe.db.exists("Bank", f"{PREFIX} Bank of Nowhere"))
		self.assertEqual(frappe.db.get_value("Bank Account", data["name"], "account"), account)

	def test_the_docname_is_the_one_erpnext_builds(self):
		"""A caller copies this string into a bank feed's configuration, so it has
		to be the key ERPNext's own autoname produced rather than one this app
		predicted."""
		account = self.account_of("Asset", account_type="Bank")
		data = self.tool_data(
			"create_bank_account",
			{
				"company": self.company,
				"account_name": f"{PREFIX} Savings",
				"bank_name": f"{PREFIX} Bank of Nowhere",
				"account": account,
			},
		)
		self.assertTrue(frappe.db.exists("Bank Account", data["name"]))
		self.assertIn(f"{PREFIX} Savings", data["name"])


class NewRootAccountContract(V8IntegrationTestCase):
	"""THE v0.8.0 BUG, and the only place it can really be proved.

	ERPNext marks `parent_account` required, so creating a top-level account died
	with `MandatoryError: [Account, …]: parent_account`. The standalone suite
	models the refusal, but only a real Account doctype settles whether
	`ignore_mandatory` is enough — and whether ERPNext's own `validate_root_details`
	then accepts the document.
	"""

	def tree(self, number):
		return [
			{
				"account_number": number,
				"account_name": f"{PREFIX} Root {number}",
				"root_type": "Expense",
				"is_group": True,
				"children": [{"account_number": f"{number}1", "account_name": f"{PREFIX} Leaf {number}"}],
			}
		]

	def free_number(self):
		for candidate in range(99000, 99100):
			if not frappe.db.exists("Account", {"company": self.company, "account_number": str(candidate)}):
				return str(candidate)
		self.skipTest("no free account number in the 99xxx range on this site")

	def test_a_new_root_is_created_rather_than_raising_mandatoryerror(self):
		number = self.free_number()
		data = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.tree(number), "dry_run": False},
		)
		self.assertEqual(data["counts"]["created"], 2)
		root = frappe.db.get_value(
			"Account",
			{"company": self.company, "account_number": number},
			["name", "parent_account", "root_type", "is_group"],
			as_dict=True,
		)
		self.assertIsNotNone(root, "the root account was not created")
		self.assertFalse(root.parent_account, "the root was given a parent")
		self.assertEqual(root.root_type, "Expense")
		self.assertTrue(root.is_group)

	def test_the_dry_run_reports_it_as_a_new_root_first(self):
		number = self.free_number()
		plan = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.tree(number)},
		)
		self.assertEqual(len(plan["new_root_accounts"]), 1)
		self.assertIn("ignore_mandatory", plan["new_root_note"])

	def test_the_child_still_gets_the_mandatory_check_erpnext_meant_to_run(self):
		"""The flag is set per document. A child that skipped mandatory validation
		would be this app quietly disabling a framework check."""
		number = self.free_number()
		self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.tree(number), "dry_run": False},
		)
		child = frappe.db.get_value(
			"Account", {"company": self.company, "account_number": f"{number}1"}, "parent_account"
		)
		self.assertTrue(child, "the child account has no parent")


class DeleteAccountContract(V8IntegrationTestCase):
	def test_an_account_it_just_created_can_be_deleted_and_its_number_freed(self):
		parent = frappe.db.get_value(
			"Account", {"company": self.company, "is_group": 1, "root_type": "Expense"}, "name"
		)
		if not parent:
			self.skipTest(f"site has no Expense group account for {self.company}")
		number = None
		for candidate in range(99500, 99600):
			if not frappe.db.exists("Account", {"company": self.company, "account_number": str(candidate)}):
				number = str(candidate)
				break
		if number is None:
			self.skipTest("no free account number in the 995xx range on this site")

		created = self.tool_data(
			"create_account",
			{
				"company": self.company,
				"account_number": number,
				"account_name": f"{PREFIX} Scratch",
				"root_type": "Expense",
				"parent_account": parent,
			},
		)
		data = self.tool_data("delete_account", {"name": created["name"], "company": self.company})
		self.assertEqual(data["deleted"], created["name"])
		self.assertFalse(frappe.db.exists("Account", created["name"]))
		self.assertFalse(
			frappe.db.exists("Account", {"company": self.company, "account_number": number}),
			"the account number was not freed, which is the whole point of the tool",
		)

	def test_an_account_with_history_is_refused_and_survives(self):
		"""Discovered rather than created: an account this site has actually posted
		to is the case that matters, and creating one would mean posting."""
		account = frappe.db.get_value("GL Entry", {"company": self.company, "is_cancelled": 0}, "account")
		if not account:
			self.skipTest(f"site has no GL entries for {self.company}")
		result = self.tool("delete_account", {"name": account, "company": self.company})
		self.assertTrue(result["isError"])
		self.assertTrue(frappe.db.exists("Account", account))
