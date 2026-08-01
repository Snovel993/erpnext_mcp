# SPDX-License-Identifier: MIT
"""A Frappe stand-in, so this app's logic can be tested without a bench.

WHY THIS EXISTS. The tests that matter most here are about *refusal*: does a
disabled tool stay invisible, does a bad token get an opaque 401, does an
unbalanced Journal Entry get rejected before anything is written, does a failed
mutation still leave an audit row. Every one of those is answerable from the
app's own logic, and none of them needs MariaDB, redis or a site. Requiring a
full bench to run them means they get run rarely, which for refusal tests is the
same as not having them.

So this module installs an in-memory `frappe` into `sys.modules` before the app
is imported: a small document store with real filter semantics, a real doctype
meta (loaded from the app's own shipped DocType JSON, so the tests assert against
the defaults that actually ship), and the handful of framework functions the app
touches. It is a test double, not an emulator — it implements what this app uses
and nothing else. Reaching for a framework function it does not have raises an
AttributeError naming the missing function (see the module `__getattr__` in
`_build_frappe`), so the double can never quietly return None for something the
real framework would have answered.

WHAT IT DELIBERATELY DOES NOT PROVE. Whether ERPNext's Journal Entry validation
accepts a given posting date. Whether `add_payment_entries` exists on this
version's Bank Transaction. Whether the DocType JSON migrates. Those are
integration facts about a real site, and they belong to the FrappeTestCase suite
in `erpnext_mcp/tests/`, which runs inside a bench. The two suites are not
alternatives — this one is fast and covers logic, that one is slow and covers the
framework contract.

Import this module before importing anything from `erpnext_mcp`.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import secrets
import shutil
import sys
import tempfile
import traceback
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(REPO_ROOT, "erpnext_mcp", "erpnext_mcp", "doctype")

#: A real directory standing in for the site folder, so `frappe.get_site_path`
#: answers with somewhere a tool can genuinely write. The report generators take
#: an `output_path` and confine it to the site's own files directories; a double
#: that returned a fictional path would let the confinement logic be tested and
#: the writing not be, which is the half that corrupts something.
SITE_ROOT = tempfile.mkdtemp(prefix="erpnext-mcp-site-")

#: Subdirectories of the site folder the app is allowed to write into.
SITE_FILE_DIRS = (("private", "files"), ("public", "files"))


def get_site_path(*parts) -> str:
	return os.path.join(SITE_ROOT, *[str(part) for part in parts])


def reset_site_files() -> None:
	"""Empty the fake site's file directories between tests, and recreate them."""
	for parts in SITE_FILE_DIRS:
		path = get_site_path(*parts)
		shutil.rmtree(path, ignore_errors=True)
		os.makedirs(path, exist_ok=True)

if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)


# ── the dict-with-attributes Frappe passes everywhere ───────────────────────
class FrappeDict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			return None

	def __setattr__(self, key, value):
		self[key] = value

	def __delattr__(self, key):
		self.pop(key, None)


# ── exceptions ──────────────────────────────────────────────────────────────
class ValidationError(Exception):
	pass


class DoesNotExistError(ValidationError):
	pass


class PermissionError_(ValidationError):
	pass


class LinkValidationError(ValidationError):
	"""What Frappe raises for a Link pointing at something that is not there.

	Modelled because v0.12.0 shipped a `bench migrate` that died on it and this
	suite passed the whole way. `Party Type` names itself after its `party_type`
	field, and that field is a **Link to DocType** — so registering a party type
	called "Family" requires a DocType called "Family" to exist. There was none,
	`_validate_links()` refused the insert, and the patch took the migration down
	with it.

	The double had no link validation at all, so it inserted the row happily.
	That is the same shape of failure as the Account `MandatoryError` below: a
	test double that answers a question the real framework refuses is a double
	that certifies code which cannot run. See `Document._validate_links`.
	"""


class MandatoryError(ValidationError):
	"""What Frappe raises for an empty `reqd` field, and what the app has to dodge.

	Modelled because it is the exact failure a live site hit: ERPNext's Account
	marks `parent_account` required, so creating a new *root* account — which by
	definition has no parent — dies with
	`MandatoryError: [Account, 1000 - Assets - ABC]: parent_account` before any of
	this app's own logic runs. The double used to insert roots quite happily,
	which is precisely why the standalone suite passed against code that could not
	create one. See `AccountDocument.validate`.
	"""


# ── schema ──────────────────────────────────────────────────────────────────
#: Field lists for the ERPNext doctypes this app reads. Only the fields the app
#: actually selects need to be here; `compat.existing_fields` filters against
#: this, which is the same thing it does against a real site's meta.
ERPNEXT_SCHEMA = {
	"Company": [
		"name",
		"abbr",
		"default_currency",
		"country",
		"chart_of_accounts",
		"parent_company",
		"is_group",
		"tax_id",
		"cost_center",
		# The default-account fields `set_company_defaults` writes. Listed here
		# rather than invented per test, because the tool asks the site whether
		# each one exists and a fixture that answered "yes" to everything would
		# make the version-tolerance path untestable.
		"default_receivable_account",
		"default_payable_account",
		"default_cash_account",
		"default_bank_account",
		"default_income_account",
		"default_expense_account",
		"cost_of_goods_sold_account",
		"round_off_account",
		"round_off_cost_center",
		"exchange_gain_loss_account",
		"write_off_account",
		"default_deferred_revenue_account",
		# v0.8.0 added thirteen more supported defaults. Six are here — enough to
		# exercise every new shape of rule: a P&L account with no type constraint
		# (disposal_account), an Asset with a required type
		# (capital_work_in_progress_account), an Expense, two Liabilities of which
		# one is the counter-intuitive Receivable-typed advance account, and a cost
		# center.
		"disposal_account",
		"capital_work_in_progress_account",
		"stock_adjustment_account",
		"stock_received_but_not_billed",
		"default_advance_received_account",
		"default_selling_cost_center",
		# No `default_deferred_expense_account`, and none of the other seven
		# v0.8.0 keys: supported defaults this fixture's ERPNext does not have, so
		# the "your version has no such field" refusal is exercised by a real
		# absence rather than a mock.
	],
	"Account": [
		"name",
		"account_name",
		"account_number",
		"parent_account",
		"is_group",
		"root_type",
		"report_type",
		"account_type",
		"account_currency",
		"tax_rate",
		"disabled",
		"freeze_account",
		"lft",
		"rgt",
		"company",
	],
	# No `description`: stock ERPNext's Account has none, which is why
	# `tools.accounts` falls back to a comment. A site that added the custom
	# field is a separate case, and its test adds the field deliberately.
	"Currency": ["name", "enabled"],
	# `Party Type` is what a GL Entry's `party_type` points at, and v0.12.0 adds
	# two of its own to it. `Country` is here so `create_company`'s ISO check has
	# something real to refuse against rather than a mock that always agrees.
	"Party Type": ["name", "party_type", "account_type"],
	"Country": ["name", "code"],
	# `Contact` is CORE FRAPPE, and it is here because v0.12.1 needs the fixture
	# to know that. A Party Type's name has to be a DocType — so `Contact`
	# registers on a real site precisely because Frappe ships this, and `Family`
	# did not until this app started shipping one. A fixture that omitted Contact
	# would make the two look alike and hide the whole distinction.
	"Contact": ["name", "first_name", "last_name", "email_id", "company_name"],
	"GL Entry": [
		"name",
		"account",
		"posting_date",
		"debit",
		"credit",
		"company",
		"is_cancelled",
		"voucher_type",
		"voucher_no",
		# The Journal Entry Account row this GL row came from. It is what makes
		# "update the party on line 2" exact rather than approximate: an entry
		# with two lines to the same account for the same amount produces two GL
		# rows that differ in nothing else, and a fixture without this column
		# would let `update_journal_entry_party` match both and look correct.
		"voucher_detail_no",
		"party",
		"party_type",
		"cost_center",
		# `is_opening` is "Yes"/"No" on a GL Entry, not a Check. The 1099
		# pre-fill excludes opening entries from payments, and a fixture without
		# the column would make that exclusion untestable.
		"is_opening",
	],
	"Cost Center": [
		"name",
		"cost_center_name",
		"cost_center_number",
		"parent_cost_center",
		"is_group",
		"disabled",
		"company",
		"lft",
		"rgt",
	],
	"Accounting Dimension": ["name", "label", "fieldname", "document_type", "disabled"],
	"Module Def": ["name", "app_name"],
	"Journal Entry": [
		"name",
		"posting_date",
		"company",
		"voucher_type",
		"naming_series",
		"total_debit",
		"total_credit",
		"difference",
		"user_remark",
		"remark",
		"cheque_no",
		"cheque_date",
		"bill_no",
		"bill_date",
		"finance_book",
		"docstatus",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"is_opening",
		"clearance_date",
		"mode_of_payment",
		"multi_currency",
		"accounts",
	],
	"Journal Entry Account": [
		# A child row has a docname of its own, and it is not decoration: a GL
		# Entry points back at it through `voucher_detail_no`, and that pointer is
		# the only thing distinguishing two identical lines of one voucher.
		"name",
		"idx",
		"account",
		"account_type",
		"party_type",
		"party",
		"debit",
		"credit",
		"debit_in_account_currency",
		"credit_in_account_currency",
		"account_currency",
		"exchange_rate",
		"against_account",
		"cost_center",
		"project",
		"reference_type",
		"reference_name",
		"reference_due_date",
		# THE FIELD THAT CAUSED v0.14.0's FEATURE E. ERPNext's
		# `JournalEntry.get_gl_entries` fills `GL Entry.voucher_detail_no` from
		# THIS field, not from the line's docname — it names a payment schedule
		# row on an invoice being settled and is empty on an ordinary line. The
		# fixture had no such column, so nothing here could express the truth
		# that a Journal Entry's GL rows carry no line docname at all.
		"reference_detail_no",
		"user_remark",
		"is_advance",
		"parent",
		"parenttype",
		"bank_account",
	],
	# `year` is the field a Fiscal Year names itself from, so it is the one
	# `create_fiscal_year` sets — a double without it would let the tool name a
	# year by writing `name` directly, which is not what a real insert does.
	"Fiscal Year": [
		"name",
		"year",
		"year_start_date",
		"year_end_date",
		"disabled",
		"is_short_year",
		"auto_created",
		"companies",
	],
	"Fiscal Year Company": ["parent", "parenttype", "company"],
	# ERPNext splits the institution from the account at it. Both are here because
	# `create_bank_account` writes both, and a double with only the second would
	# let the tool "succeed" while the Bank it claims to have created went nowhere.
	"Bank": ["name", "bank_name", "swift_number", "website"],
	"Bank Account": [
		"name",
		"account_name",
		"bank",
		"company",
		"account",
		"iban",
		"bank_account_no",
		"branch_code",
		"is_company_account",
		"is_default",
		"party_type",
		"party",
		"disabled",
	],
	"Bank Transaction": [
		"name",
		"date",
		"bank_account",
		"company",
		"description",
		"status",
		"reference_number",
		"currency",
		"party_type",
		"party",
		"bank_party_name",
		"docstatus",
		"deposit",
		"withdrawal",
		"allocated_amount",
		"unallocated_amount",
		"payment_entries",
	],
	"Bank Transaction Payments": [
		"payment_document",
		"payment_entry",
		"allocated_amount",
		"parent",
		"parenttype",
	],
	"Bank Statement": [
		"name",
		"bank_account",
		"from_date",
		"to_date",
		"opening_balance",
		"closing_balance",
		"company",
	],
	# ERPNext's check-cutting document. The v0.14.0 check Print Format renders
	# against it, so the fields it prints have to be here — a fixture that stopped
	# at the amount would let a template reference a column nobody has.
	"Payment Entry": [
		"name",
		"posting_date",
		"paid_amount",
		"docstatus",
		"company",
		"payment_type",
		"party_type",
		"party",
		"party_name",
		"paid_from",
		"paid_from_account_currency",
		"paid_to",
		"paid_to_account_currency",
		"reference_no",
		"reference_date",
		"remarks",
		"mode_of_payment",
		"bank_account",
		"references",
	],
	"Payment Entry Reference": [
		"reference_doctype",
		"reference_name",
		"due_date",
		"total_amount",
		"outstanding_amount",
		"allocated_amount",
		"parent",
		"parenttype",
	],
	# Core Frappe. Only the columns `create_check_print_format` writes or reads —
	# `standard` above all, because a STANDARD format is one an app rewrites on
	# every migrate and the refusal that protects it is the point.
	"Print Format": [
		"name",
		"print_format_name",
		"doc_type",
		"module",
		"standard",
		"custom_format",
		"print_format_type",
		"print_format_builder",
		"disabled",
		"page_size",
		"margin_top",
		"margin_bottom",
		"margin_left",
		"margin_right",
		"align_labels_right",
		"line_breaks",
		"show_section_headings",
		"html",
	],
	"User": ["name", "enabled", "full_name"],
	"DocType": [
		"name",
		"module",
		"issingle",
		"istable",
		"custom",
		"autoname",
		"naming_rule",
		"track_changes",
		"fields",
		"permissions",
	],
	"Singles": ["doctype", "field", "value"],
	# ── v0.2.0 ──────────────────────────────────────────────────────────────
	"Workflow": [
		"name",
		"workflow_name",
		"document_type",
		"is_active",
		"workflow_state_field",
		"send_email_alert",
		"override_status",
		"states",
		"transitions",
	],
	"Workflow Document State": [
		"state",
		"doc_status",
		"allow_edit",
		"update_field",
		"update_value",
		"is_optional_state",
		"message",
		"parent",
		"parenttype",
	],
	"Workflow Transition": [
		"state",
		"action",
		"next_state",
		"allowed",
		"allow_self_approval",
		"condition",
		"parent",
		"parenttype",
	],
	"Report": [
		"name",
		"report_name",
		"ref_doctype",
		"report_type",
		"module",
		"is_standard",
		"disabled",
		"prepared_report",
		"add_total_row",
		"json",
		"query",
	],
	"File": [
		"name",
		"file_name",
		"file_url",
		"file_size",
		"is_private",
		"is_folder",
		"attached_to_doctype",
		"attached_to_name",
		"attached_to_field",
		"content_hash",
		"folder",
		"owner",
		"creation",
	],
	"Comment": [
		"name",
		"comment_type",
		"content",
		"comment_by",
		"comment_email",
		"reference_doctype",
		"reference_name",
		"owner",
		"creation",
		"modified",
	],
	"ToDo": [
		"name",
		"status",
		"priority",
		"date",
		"description",
		"reference_type",
		"reference_name",
		"assigned_by",
		"allocated_to",
		"owner",
		"creation",
		"modified",
	],
	"Employee": [
		"name",
		"employee_name",
		"employee_number",
		"department",
		"designation",
		"status",
		"date_of_joining",
		"relieving_date",
		"company",
		"user_id",
		"reports_to",
		"branch",
		"employment_type",
	],
	"Attendance": [
		"name",
		"employee",
		"employee_name",
		"attendance_date",
		"status",
		"department",
		"company",
		"docstatus",
	],
	"Leave Allocation": [
		"name",
		"employee",
		"leave_type",
		"from_date",
		"to_date",
		"new_leaves_allocated",
		"total_leaves_allocated",
		"docstatus",
	],
	"Leave Type": ["name", "max_leaves_allowed", "is_lwp"],
	"Sales Order": [
		"name",
		"customer",
		"customer_name",
		"transaction_date",
		"delivery_date",
		"grand_total",
		"rounded_total",
		"currency",
		"status",
		"per_delivered",
		"per_billed",
		"docstatus",
		"company",
		"owner",
	],
	# `supplier_type` is ERPNext's Company/Individual Select, and it is the field
	# the 1099 pre-fill classifies on. `tax_withholding_category` and `tax_id`
	# are here because the tool reads them off the site when they exist and says
	# so when they do not — a fixture that always had them would leave the
	# degraded path untested.
	"Supplier": [
		"name",
		"supplier_name",
		"supplier_type",
		"supplier_group",
		"tax_id",
		"tax_category",
		"tax_withholding_category",
		"country",
		"disabled",
		"is_transporter",
	],
	"Purchase Order": [
		"name",
		"supplier",
		"supplier_name",
		"transaction_date",
		"schedule_date",
		"grand_total",
		"rounded_total",
		"currency",
		"status",
		"per_received",
		"per_billed",
		"docstatus",
		"company",
		"owner",
		"workflow_state",
	],
	"Sales Invoice": [
		"name",
		"customer",
		"customer_name",
		"posting_date",
		"due_date",
		"grand_total",
		"outstanding_amount",
		"currency",
		"status",
		"company",
		"is_return",
		"docstatus",
	],
	# No "Purchase Invoice", deliberately: this fixture is a site that does not
	# have one, which is what makes the "that DocType is not installed"
	# degradation in the fiscal-year packet and in create_accounting_dimension
	# testable against a real absence. A test that needs it registers it.
	"Custom Field": [
		"name",
		"dt",
		"fieldname",
		"label",
		"fieldtype",
		"options",
		"insert_after",
		"idx",
		"reqd",
		"hidden",
		"read_only",
		"in_list_view",
		"in_standard_filter",
		"depends_on",
		"default",
		"description",
		"module",
		"owner",
		"modified",
	],
	# ── v0.15.0: what the Compliance Command Center is built out of ─────────
	#
	# Frappe's own dashboard doctypes. Modelled because `dashboard.py` builds
	# them on every migrate and the property that has to be true — that a second
	# migrate changes nothing — is only testable against something that records
	# what the first one wrote.
	"Dashboard": ["name", "dashboard_name", "is_default", "is_standard", "module", "charts", "cards"],
	"Dashboard Chart": [
		"name",
		"chart_name",
		"chart_type",
		"document_type",
		"based_on",
		"group_by_type",
		"group_by_based_on",
		"time_interval",
		"timespan",
		"timeseries",
		"type",
		"filters_json",
		"number_of_groups",
		"is_public",
		"module",
	],
	"Dashboard Chart Link": ["name", "chart"],
	"Number Card": [
		"name",
		"label",
		"document_type",
		"function",
		"aggregate_function_based_on",
		"filters_json",
		"is_public",
		"color",
		"type",
		"module",
	],
	"Number Card Link": ["name", "card"],
	# ── v0.16.0: the Farm Task Dispatch Kanban board and its landing page ────
	"Kanban Board": [
		"name",
		"kanban_board_name",
		"reference_doctype",
		"field_name",
		"private",
		"show_labels",
		"columns",
	],
	"Kanban Board Column": ["name", "column_name", "indicator", "status", "order"],
	"Workspace": [
		"name",
		"title",
		"label",
		"module",
		"icon",
		"public",
		"is_hidden",
		"content",
		"sequence_id",
		"shortcuts",
		"links",
		"number_cards",
		"charts",
	],
	"Workspace Shortcut": ["name", "label", "type", "link_to", "doc_view", "kanban_board", "color"],
	"Workspace Link": [
		"name",
		"type",
		"label",
		"link_type",
		"link_to",
		"link_count",
		"onboard",
		"hidden",
	],
	"Workspace Number Card": ["name", "number_card_name", "label"],
	"Workspace Chart": ["name", "chart_name", "label"],
	"Client Script": [
		"name",
		"dt",
		"view",
		"enabled",
		"script",
		"script_type",
		"module",
		"owner",
		"modified",
	],
	"Purchase Receipt": ["name", "supplier", "posting_date", "docstatus"],
	# ── v0.7.0: assets ──────────────────────────────────────────────────────
	"Asset": [
		"name",
		"asset_name",
		"item_code",
		"asset_category",
		"company",
		"purchase_date",
		"available_for_use_date",
		"gross_purchase_amount",
		"asset_quantity",
		"is_existing_asset",
		"calculate_depreciation",
		"cost_center",
		"location",
		"status",
		"docstatus",
	],
	"Asset Category": ["name", "asset_category_name", "accounts"],
	"Asset Category Account": [
		"company",
		"fixed_asset_account",
		"accumulated_depreciation_account",
		"depreciation_expense_account",
		"parent",
		"parenttype",
	],
	"Item": [
		"name",
		"item_code",
		"item_name",
		"item_group",
		"stock_uom",
		"is_fixed_asset",
		"is_stock_item",
		"asset_category",
		"disabled",
	],
	"Item Group": ["name", "is_group"],
	"UOM": ["name", "enabled"],
	# No "Location", deliberately: ERPNext's Asset requires one on some versions
	# and not others, and this fixture is a site without it — which is what makes
	# `create_asset`'s "set it only where the field exists" branch a real case.
}

#: Doctypes whose docname is built from one of their own fields, as ERPNext
#: names them. Only the ones this app's behaviour depends on: `create_asset`
#: creates an Item and then links the Asset to whatever the Item ended up
#: called, and a double that named it `I-00001` would make the link untestable.
ERPNEXT_AUTONAME = {
	"Item": "field:item_code",
	"Bank": "field:bank_name",
	"Fiscal Year": "field:year",
	"Supplier": "field:supplier_name",
	# A Party Type IS its name, which is what makes `frappe.db.exists("Party
	# Type", "Family")` the check every caller writes.
	"Party Type": "field:party_type",
	# ERPNext's Company is `field:company_name`, and `create_company` depends on
	# it: a Company that came back named "C-00001" would make every account
	# docname built from its abbreviation point at a company nobody can find.
	"Company": "field:company_name",
	# A Dashboard, a Chart and a Card are each named by their label, which is what
	# makes `frappe.db.exists("Number Card", "Critical Compliance Alerts")` the
	# idempotence check `dashboard.install_command_center` writes.
	"Dashboard": "field:dashboard_name",
	"Dashboard Chart": "field:chart_name",
	"Number Card": "field:label",
	# Same for a Kanban Board, which is what makes `install_dispatch_board`
	# idempotent: the second migrate finds "Farm Task Dispatch" and leaves it
	# exactly as somebody has since arranged it.
	"Kanban Board": "field:kanban_board_name",
}

#: Doctypes this app owns. Their meta is loaded from the shipped JSON so tests
#: assert against the real defaults rather than a copy that can drift.
APP_DOCTYPES = {
	"ERPNext MCP Settings": "erpnext_mcp_settings",
	"MCP Action Log": "mcp_action_log",
	"Cap Table Entry": "cap_table_entry",
	"Member Event": "member_event",
	"Governance Document": "governance_document",
	"Asset Cost Profile": "asset_cost_profile",
	"Asset Cost Center Allocation": "asset_cost_center_allocation",
	"Asset Depreciation Posting": "asset_depreciation_posting",
	"Note Payable": "note_payable",
	"Note Payable Event": "note_payable_event",
	"Parcel": "parcel",
	"Parcel Conveyance Event": "parcel_conveyance_event",
	"Lease": "lease",
	"Related Party": "related_party",
	"Field": "field",
	"Irrigation Zone": "irrigation_zone",
	"Housing Unit": "housing_unit",
	"Housing Assignment": "housing_assignment",
	"Family": "family",
	"Staged File Upload Session": "staged_file_upload_session",
	"Staged File Chunk": "staged_file_chunk",
	# ── v0.15.0: the compliance framework ───────────────────────────────────
	"Compliance Policy": "compliance_policy",
	"Certification": "certification",
	"Certification Renewal": "certification_renewal",
	"Regulatory Filing": "regulatory_filing",
	"Audit Event": "audit_event",
	"Audit Corrective Action": "audit_corrective_action",
	"Compliance Alert": "compliance_alert",
	# ── v0.16.0: Farm Task Dispatch and the records a completion produces ────
	"Farm Task": "farm_task",
	"Farm Task Assignment": "farm_task_assignment",
	"Farm Task Evidence": "farm_task_evidence",
	"Housing Inspection": "housing_inspection",
	"Detector Test": "detector_test",
	"Water Test": "water_test",
}


def _load_app_doctype(folder: str) -> dict:
	payload = _APP_DOCTYPE_CACHE.get(folder)
	if payload is None:
		with open(os.path.join(DOCTYPE_DIR, folder, f"{folder}.json")) as handle:
			payload = json.load(handle)
		_APP_DOCTYPE_CACHE[folder] = payload
	return payload


#: The shipped DocType JSON, read once. `reset_meta` rebuilds the meta objects
#: between tests — a test that adds a Custom Field or a whole DocType must not
#: leak it into the next one — and rereading two files per test would be a file
#: system call in every setUp for no benefit.
_APP_DOCTYPE_CACHE: dict = {}


class Field(FrappeDict):
	pass


class Meta:
	"""Just enough of frappe.model.meta.Meta for `compat` to interrogate.

	`autoname` is here because the app reads it: `dimensions._naming_field` asks a
	master DocType how it names itself before deciding whether to set a field or
	pass a name. A double that always answered "" would make every dimension value
	take the fallback path, which is the one that does *not* produce the readable
	docname the tool exists to produce.

	`max_attachments` is a class attribute rather than a constructor argument
	because Frappe's own default is 0-meaning-unlimited on every DocType, and
	`attach_file_to_document` reads it on every call. A test that wants the limit
	to bite sets it on the one meta it cares about; `reset_meta` rebuilds the
	objects between tests, so it cannot leak.
	"""

	#: 0 is Frappe's "no limit", and is what every DocType has unless somebody set one.
	max_attachments = 0

	def __init__(self, doctype: str, fields: list[Field], issingle: bool = False, autoname: str = ""):
		self.doctype = doctype
		self.fields = fields
		self.issingle = issingle
		self.autoname = autoname
		self._by_name = {f.fieldname: f for f in fields}

	def has_field(self, fieldname: str) -> bool:
		return fieldname in self._by_name

	def get_field(self, fieldname: str):
		return self._by_name.get(fieldname)

	def add(self, field: Field) -> None:
		"""Register a field added at runtime, as inserting a Custom Field does."""
		if field.fieldname in self._by_name:
			return
		self.fields.append(field)
		self._by_name[field.fieldname] = field


#: Select options this double reproduces verbatim, because the app reads them
#: off `frappe.get_meta` and branches on what it finds. `Account.account_type`
#: is the whole reason: `charts.site_account_types()` asks the site which types
#: it supports and substitutes a fallback for one it does not, and a double that
#: answered "no options at all" would make that path — the one that decides what
#: a hundred-account import writes — silently untested.
#:
#: This is ERPNext v15's list. Note what is NOT in it: "Credit Card", which the
#: shipped `us_llc_farm` template asks for on 2160.
#: ERPNext fields that are Links or Dynamic Links rather than plain Data, as
#: `(doctype, fieldname) -> (fieldtype, options)`.
#:
#: THIS TABLE IS WHY v0.12.1 EXISTS. Everything in `ERPNEXT_SCHEMA` used to be
#: modelled as Data, so `Party Type.party_type` — which is really a **Link to
#: DocType** — accepted any string the app handed it. v0.12.0 registered a party
#: type called "Family" against a site with no Family DocType, this suite said
#: fine, and `bench migrate` on a real bench raised `LinkValidationError` and
#: aborted.
#:
#: The party trio is modelled in full because it is one mechanism: `party_type`
#: names a DocType, and `party` is a **Dynamic Link** resolved through it. That
#: is what makes a Party Type's name load-bearing rather than a label, and it is
#: the fact the release was missing.
ERPNEXT_FIELD_LINKS = {
	("Party Type", "party_type"): ("Link", "DocType"),
	("GL Entry", "party_type"): ("Link", "DocType"),
	("GL Entry", "party"): ("Dynamic Link", "party_type"),
	("Journal Entry Account", "party_type"): ("Link", "DocType"),
	("Journal Entry Account", "party"): ("Dynamic Link", "party_type"),
}

ERPNEXT_FIELD_OPTIONS = {
	("Account", "account_type"): "\n".join(
		[
			"",
			"Accumulated Depreciation",
			"Asset Received But Not Billed",
			"Bank",
			"Cash",
			"Chargeable",
			"Capital Work in Progress",
			"Cost of Goods Sold",
			"Current Asset",
			"Current Liability",
			"Depreciation",
			"Direct Expense",
			"Direct Income",
			"Equity",
			"Expense Account",
			"Expenses Included In Asset Valuation",
			"Expenses Included In Valuation",
			"Fixed Asset",
			"Income Account",
			"Indirect Expense",
			"Indirect Income",
			"Liability",
			"Payable",
			"Payment",
			"Payroll Payable",
			"Provision",
			"Receivable",
			"Round Off",
			"Round Off for Opening",
			"Service Received But Not Billed",
			"Stock",
			"Stock Adjustment",
			"Stock Received But Not Billed",
			"Tax",
			"Temporary",
		]
	),
	("Account", "root_type"): "Asset\nLiability\nIncome\nExpense\nEquity",
	# ── v0.16.1 ─────────────────────────────────────────────────────────────
	# THE OPTIONS THAT COST A RELEASE. v0.16.0 wrote `indicator="gray"` at this
	# field and the Kanban Board insert threw on a site where the options are
	# capitalised — silently, because `install.py` discarded the report. The
	# double could not catch it, because it did not police Select options at all.
	#
	# The casing here is DELIBERATELY NOT the casing v0.16.0 assumed, and this
	# fixture does not claim to know what any given Frappe version ships. That is
	# the whole point: `dashboard._select_value` now reads the options off the
	# site and matches case-insensitively, and `TheIndicatorPaletteIsNotAssumed`
	# re-declares this field three different ways to prove the board still
	# installs against all of them.
	("Kanban Board Column", "indicator"): "Blue\nOrange\nRed\nGreen\nGray\nPurple\nYellow\nPink",
	("Kanban Board Column", "status"): "Active\nArchived",
	("Workspace Shortcut", "type"): "DocType\nReport\nPage\nDashboard\nURL",
	("Workspace Shortcut", "doc_view"): (
		"\nList\nReport Builder\nDashboard\nTree\nNew\nCalendar\nKanban\nImage\nInbox\nGantt"
	),
	("Workspace Link", "type"): "Card Break\nLink",
	("Workspace Link", "link_type"): "DocType\nPage\nReport\nDashboard",
	# v15's Journal Entry voucher types. `set_opening_balance` sets "Opening
	# Entry" only when the site's own meta offers it, so a double with no options
	# would leave that branch — the one that keeps opening balances out of the
	# period's activity in every report that separates them — untested.
	("Journal Entry", "voucher_type"): "\n".join(
		[
			"Journal Entry",
			"Inter Company Journal Entry",
			"Bank Entry",
			"Cash Entry",
			"Credit Card Entry",
			"Debit Note",
			"Credit Note",
			"Contra Entry",
			"Excise Entry",
			"Write Off Entry",
			"Opening Entry",
			"Depreciation Entry",
			"Exchange Rate Revaluation",
			"Exchange Gain Or Loss",
		]
	),
}


def _erpnext_field(doctype: str, name: str):
	"""One ERPNext field, as a Link, a Dynamic Link, a Select or plain Data."""
	link = ERPNEXT_FIELD_LINKS.get((doctype, name))
	if link:
		return Field(fieldname=name, fieldtype=link[0], options=link[1], label=name)
	return Field(
		fieldname=name,
		fieldtype="Select" if (doctype, name) in ERPNEXT_FIELD_OPTIONS else "Data",
		options=ERPNEXT_FIELD_OPTIONS.get((doctype, name)),
		label=name,
	)


def _build_meta() -> dict:
	metas = {}
	for doctype, fields in ERPNEXT_SCHEMA.items():
		metas[doctype] = Meta(
			doctype,
			[_erpnext_field(doctype, name) for name in fields],
			autoname=ERPNEXT_AUTONAME.get(doctype, ""),
		)
	for doctype, folder in APP_DOCTYPES.items():
		payload = _load_app_doctype(folder)
		metas[doctype] = Meta(
			doctype,
			[Field(**field) for field in payload["fields"]],
			issingle=bool(payload.get("issingle")),
			autoname=str(payload.get("autoname") or ""),
		)
	return metas


META = _build_meta()


def reset_meta() -> None:
	"""Put the schema back to what this fixture ships, discarding runtime additions.

	The same object is kept rather than rebound, because tests import `META` by
	name. Called from `MCPTestCase.setUp`: a test that creates an accounting
	dimension really does add a DocType and a Custom Field to the site, and the
	next test has to start from a site where neither exists.
	"""
	META.clear()
	META.update(_build_meta())


def register_doctype(doctype: str, fields, issingle: bool = False, autoname: str = "") -> None:
	"""Make a DocType exist on the fake site, as inserting a DocType does."""
	META[doctype] = Meta(
		doctype,
		[Field(**field) if isinstance(field, dict) else field for field in fields or ()],
		issingle=issingle,
		autoname=autoname,
	)
	INSTALLED_DOCTYPES.add(doctype)


def add_field(doctype: str, fieldname: str, fieldtype: str = "Data", options=None, label=None) -> None:
	"""Make a field exist on a DocType, as inserting a Custom Field does."""
	meta = META.get(doctype)
	if meta is None:
		raise ValidationError(f"stub has no meta for {doctype!r}, so it cannot take a custom field")
	meta.add(Field(fieldname=fieldname, fieldtype=fieldtype, options=options, label=label))


# ── filters ─────────────────────────────────────────────────────────────────
def _match(row: dict, filters) -> bool:
	if not filters:
		return True
	if isinstance(filters, str):
		return row.get("name") == filters
	if isinstance(filters, list):
		# [[fieldname, operator, value], ...]
		return all(_match_one(row, f[0], (f[1], f[2])) for f in filters)
	return all(_match_one(row, field, value) for field, value in filters.items())


def _match_one(row: dict, field: str, condition) -> bool:
	actual = row.get(field)
	if not isinstance(condition, (tuple, list)):
		return _eq(actual, condition)
	operator, expected = condition[0], condition[1]
	operator = str(operator).lower()
	if operator == "=":
		return _eq(actual, expected)
	if operator in ("!=", "not="):
		return not _eq(actual, expected)
	if operator == "in":
		return any(_eq(actual, item) for item in expected)
	if operator == "not in":
		return not any(_eq(actual, item) for item in expected)
	if operator == "like":
		return _like(actual, expected)
	if operator == "not like":
		return not _like(actual, expected)
	if operator == "between":
		low, high = expected
		return actual is not None and _key(low) <= _key(actual) <= _key(high)
	if operator == "is":
		if expected == "set":
			return actual not in (None, "")
		return actual in (None, "")
	if operator in ("<", "<=", ">", ">="):
		if actual is None:
			return False
		left, right = _key(actual), _key(expected)
		return {
			"<": left < right,
			"<=": left <= right,
			">": left > right,
			">=": left >= right,
		}[operator]
	raise NotImplementedError(f"stub filter operator {operator!r}")


def _eq(actual, expected) -> bool:
	if isinstance(expected, (int, float)) and not isinstance(expected, bool):
		return float(actual or 0) == float(expected)
	return str(actual if actual is not None else "") == str(expected if expected is not None else "")


def _like(actual, pattern: str) -> bool:
	# MariaDB LIKE with the site's default collation: case-insensitive.
	text = str(actual or "").lower()
	needle = str(pattern or "").lower()
	if needle.startswith("%") and needle.endswith("%"):
		return needle.strip("%") in text
	if needle.startswith("%"):
		return text.endswith(needle.lstrip("%"))
	if needle.endswith("%"):
		return text.startswith(needle.rstrip("%"))
	return text == needle


def _key(value):
	"""Comparison key that keeps numbers numeric and everything else a string."""
	if isinstance(value, (int, float)) and not isinstance(value, bool):
		return value
	if isinstance(value, (datetime.date, datetime.datetime)):
		return value.isoformat()
	try:
		return float(value)
	except (TypeError, ValueError):
		return str(value)


# ── documents ───────────────────────────────────────────────────────────────
CHILD_TABLES = {
	("Journal Entry", "accounts"): "Journal Entry Account",
	("Bank Transaction", "payment_entries"): "Bank Transaction Payments",
	("Fiscal Year", "companies"): "Fiscal Year Company",
	("Workflow", "states"): "Workflow Document State",
	("Workflow", "transitions"): "Workflow Transition",
	("Asset Category", "accounts"): "Asset Category Account",
	("Asset Cost Profile", "cost_center_allocation"): "Asset Cost Center Allocation",
	("Asset Cost Profile", "depreciation_postings"): "Asset Depreciation Posting",
	("Note Payable", "payment_events"): "Note Payable Event",
	("Parcel", "conveyance_events"): "Parcel Conveyance Event",
	("Payment Entry", "references"): "Payment Entry Reference",
	("Certification", "renewals"): "Certification Renewal",
	("Audit Event", "corrective_actions_required"): "Audit Corrective Action",
	("Dashboard", "charts"): "Dashboard Chart Link",
	("Dashboard", "cards"): "Number Card Link",
	("Kanban Board", "columns"): "Kanban Board Column",
	("Workspace", "shortcuts"): "Workspace Shortcut",
	("Workspace", "links"): "Workspace Link",
	("Workspace", "number_cards"): "Workspace Number Card",
	("Workspace", "charts"): "Workspace Chart",
	("Farm Task Assignment", "evidence_files"): "Farm Task Evidence",
	("Housing Inspection", "photos"): "Farm Task Evidence",
	("Detector Test", "photos"): "Farm Task Evidence",
	("Water Test", "sample_photos"): "Farm Task Evidence",
}

#: Child tables `frappe.get_doc` rehydrates into Documents rather than leaving as
#: plain dicts. A row this app appends to and re-reads has to behave the same on
#: the second read as on the first.
REHYDRATED_CHILD_FIELDS = (
	"accounts",
	"payment_entries",
	"companies",
	"cost_center_allocation",
	"depreciation_postings",
	"payment_events",
	"references",
	"renewals",
	"corrective_actions_required",
	"columns",
	"shortcuts",
	"links",
	"number_cards",
	"charts",
	"evidence_files",
	"photos",
	"sample_photos",
)


class Document(FrappeDict):
	"""A stand-in for frappe.model.document.Document.

	Runs the controller hooks this app relies on (`validate`, `before_save`,
	`on_update`) in Frappe's order, because two of this app's guarantees — the
	settings form refusing a bad CIDR, the audit log refusing an update — live in
	exactly those hooks.
	"""

	def __init__(self, data=None):
		super().__init__(data or {})
		self.flags = FrappeDict()
		self._doc_before_save = None

	# -- lifecycle ------------------------------------------------------------
	def insert(self, ignore_permissions=False, ignore_if_duplicate=False):
		self.flags.in_insert = True
		# Frappe runs `autoname` before validation, and a doctype whose docname
		# is built from its own fields (Account is the one this app writes)
		# depends on that order. Falling straight through to a serial name would
		# make every docname in a chart-of-accounts import a fiction.
		self._run("autoname")
		if not self.get("name"):
			self.name = _autoname_from_meta(self) or STORE.next_name(self.doctype)
		self.creation = _now()
		self.modified = self.creation
		self.owner = self.get("owner") or frappe.session.user
		self.docstatus = int(self.get("docstatus") or 0)
		self._run("before_validate")
		self._run("validate")
		self._run("before_save")
		self._validate_links()
		self._validate_selects()
		self._name_children()
		STORE.put(self)
		self._run("after_insert")
		self._run("on_update")
		self.flags.in_insert = False
		return self

	def save(self, ignore_permissions=False):
		if not self.get("name"):
			return self.insert(ignore_permissions=ignore_permissions)
		self._doc_before_save = STORE.get_raw(self.doctype, self.name)
		self.modified = _now()
		self._run("before_validate")
		self._run("validate")
		self._run("before_save")
		self._validate_links()
		self._validate_selects()
		self._name_children()
		STORE.put(self)
		self._run("on_update")
		return self

	def _name_children(self):
		"""Give every child row a docname, as Frappe does on save.

		Not cosmetic. Frappe names child rows with a hash and other tables point
		at them: a GL Entry's `voucher_detail_no` IS the Journal Entry Account
		row's name, and it is the only thing that tells two identical lines of one
		voucher apart. A double that left children unnamed would let a tool that
		matched on it appear to work while matching nothing, or — worse — let one
		that matched on account and amount instead look correct here and update
		the wrong row on a real site.

		Names are assigned once and never reassigned, because a row that changed
		its name on every save would orphan whatever already points at it.
		"""
		for (parent, fieldname), child_doctype in CHILD_TABLES.items():
			if parent != self.doctype:
				continue
			for index, row in enumerate(self.get(fieldname) or [], start=1):
				if not isinstance(row, dict):  # pragma: no cover - rows are always dicts
					continue
				row.setdefault("idx", index)
				if not row.get("name"):
					row["name"] = STORE.next_child_name(child_doctype)
				row.setdefault("parent", self.get("name"))
				row.setdefault("parenttype", self.doctype)
				row.setdefault("parentfield", fieldname)

	def submit(self):
		self.docstatus = 1
		self._run("before_submit")
		STORE.put(self)
		self._run("on_submit")
		return self

	def cancel(self):
		self.docstatus = 2
		self._run("before_cancel")
		STORE.put(self)
		self._run("on_cancel")
		return self

	def reload(self):
		fresh = STORE.get_raw(self.doctype, self.name)
		if fresh:
			self.update(copy.deepcopy(fresh))
		return self

	def _validate_links(self):
		"""Refuse a Link or Dynamic Link that points at nothing, as Frappe does.

		WHY THIS IS WORTH THE FIDELITY. Frappe runs this on every insert and save,
		and it is the check that stopped v0.12.0 migrating: a Party Type whose
		`party_type` field is a Link to DocType cannot name a DocType that does
		not exist. Without this here, the suite certified a patch that took a real
		bench down.

		Three cases, and the third is the one that matters:

		  * a Link whose `options` is an ordinary doctype — the value has to be a
		    row in it.
		  * a **Dynamic Link** — the target doctype comes from the field named in
		    `options`, which is how a Journal Entry line's `party` is resolved
		    through its `party_type`.
		  * a Link whose `options` is the literal `"DocType"` — the value has to
		    be a doctype this site HAS. That is the Party Type case.

		Scoped to doctypes whose meta the fixture actually knows, and skipped
		entirely when `flags.ignore_links` is set, both of which Frappe also does.
		A link to a doctype the fixture has never heard of is not validated, since
		the double cannot tell "absent record" from "absent schema" and guessing
		wrong would refuse perfectly good fixtures.
		"""
		if self.flags.get("ignore_links"):
			return
		self._validate_links_on(self.doctype, self)
		# Child rows carry links of their own, and on a Journal Entry they carry
		# the ones that matter: `party_type` and `party` are on the LINE, not on
		# the header. Validating only the parent would have left the whole party
		# mechanism unchecked, which is the gap this release closed.
		for (parent, fieldname), child_doctype in CHILD_TABLES.items():
			if parent != self.doctype:
				continue
			for row in self.get(fieldname) or []:
				self._validate_links_on(child_doctype, row)

	def _validate_selects(self):
		"""Refuse a Select value the field does not offer, as Frappe does.

		THIS IS THE v0.16.1 GAP, AND IT COST A RELEASE. The double validated Links
		faithfully and did not look at Selects at all, so v0.16.0 could write
		`indicator="gray"` into a `Kanban Board Column` whose real options are
		capitalised, pass 2864 tests, and then throw on `doc.insert()` during
		`bench migrate` on Tim's site. The exception was swallowed by an installer
		that discarded its own report, so the migration reported success and the
		board did not exist.

		Both halves of that failure are now closed — the installer prints what it
		could not build, and this refuses the value that broke it — but this is the
		half that makes the *class* of bug catchable rather than the instance.

		Faithful to Frappe in the three ways that matter: an empty value is always
		allowed (it means "not set"), a field with no options at all is not
		policed (that is a customised or dynamically-populated Select, and Frappe
		does not police those either), and child rows are checked as well as the
		parent — which is where `indicator` actually lives.
		"""
		if self.flags.get("ignore_validate"):
			return
		self._validate_selects_on(self.doctype, self)
		for (parent, fieldname), child_doctype in CHILD_TABLES.items():
			if parent != self.doctype:
				continue
			for row in self.get(fieldname) or []:
				self._validate_selects_on(child_doctype, row)

	def _validate_selects_on(self, doctype: str, doc):
		meta = META.get(doctype)
		if meta is None:
			return
		for field in meta.fields:
			if field.get("fieldtype") != "Select":
				continue
			options = str(field.get("options") or "").split("\n")
			if not [line for line in options if line.strip()]:
				# No options: a Select whose choices the site fills in at runtime.
				continue
			value = doc.get(field["fieldname"])
			if value in (None, ""):
				continue
			if str(value) not in options:
				raise ValidationError(
					f"{value!r} is not a valid value for {doctype}.{field['fieldname']}. "
					f"Options are: {', '.join(line for line in options if line.strip())}"
				)

	def _validate_links_on(self, doctype: str, doc):
		meta = META.get(doctype)
		if meta is None:
			return
		for field in meta.fields:
			fieldtype = field.get("fieldtype")
			if fieldtype not in ("Link", "Dynamic Link"):
				continue
			value = doc.get(field.get("fieldname"))
			if value in (None, "", 0):
				continue

			if fieldtype == "Dynamic Link":
				target = doc.get(str(field.get("options") or ""))
				if not target:
					continue
			else:
				target = str(field.get("options") or "")
			if not target:
				continue

			if target == "DocType":
				if str(value) not in INSTALLED_DOCTYPES:
					raise LinkValidationError(
						f"Could not find {field.get('label') or field.get('fieldname')}: {value}"
					)
				continue
			if target not in INSTALLED_DOCTYPES:
				continue
			if not STORE.get_raw(target, str(value)):
				raise LinkValidationError(
					f"Could not find {field.get('label') or field.get('fieldname')}: {value}"
				)

	def _run(self, hook: str):
		method = getattr(self, hook, None)
		if callable(method):
			method()

	# -- data -----------------------------------------------------------------
	def append(self, fieldname, value=None):
		rows = self.get(fieldname)
		if not isinstance(rows, list):
			rows = []
			self[fieldname] = rows
		child = Document(dict(value or {}))
		child.doctype = CHILD_TABLES.get((self.doctype, fieldname), "")
		child.parenttype = self.doctype
		child.parentfield = fieldname
		child.idx = len(rows) + 1
		rows.append(child)
		return child

	def set(self, fieldname, value):
		self[fieldname] = value

	def get(self, key, default=None):
		return dict.get(self, key, default)

	def as_dict(self, no_nulls=False, convert_dates_to_str=False):
		out = {}
		for key, value in self.items():
			if key in ("flags", "_doc_before_save"):
				continue
			if no_nulls and value is None:
				continue
			out[key] = value
		return FrappeDict(out)

	def get_doc_before_save(self):
		return self._doc_before_save

	def is_new(self) -> bool:
		return not self.get("creation")

	def db_set(self, fieldname, value, **kwargs):
		self[fieldname] = value
		STORE.put(self)

	def add_comment(self, comment_type, text):
		"""Frappe inserts a Comment row and RETURNS IT. So does this.

		The return value is not incidental: a tool that reports the docname of the
		note it left needs one, and a double returning None would let the tool ship
		reporting None forever. `STORE.comments` stays as it is because a dozen
		existing tests read it, and the Comment row is what a tool asserting on the
		timeline actually queries.
		"""
		STORE.comments.append(
			{"doctype": self.doctype, "name": self.name, "type": comment_type, "text": text}
		)
		return frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": comment_type,
				"content": text,
				"reference_doctype": self.doctype,
				"reference_name": self.name,
			}
		).insert()

	def get_password(self, fieldname, raise_exception=True):
		value = STORE.passwords.get((self.doctype, self.get("name"), fieldname))
		if value is None and raise_exception:
			raise ValidationError(f"no password stored for {fieldname}")
		return value


class FileDocument(Document):
	"""Only File has `get_content`, so only File gets it here.

	Putting it on the base Document would make every doctype quack like a file
	and hide a real bug where the app reads content off the wrong thing.

	`content` is faithful to Frappe in the part that matters: a File inserted with
	content has its bytes written to storage and its `file_url`, `file_name` and
	`file_size` filled in from them, and the content itself is NOT a column on the
	row afterwards. An app that read the field back would be reading something a
	real site does not keep.
	"""

	def get_content(self):
		if self.name not in STORE.file_contents:
			raise OSError(f"no stored content for File {self.name}")
		return STORE.file_contents[self.name]

	def validate(self):
		if self.get("content") is None:
			return
		data = _as_bytes(self["content"])
		self["file_name"] = self.get("file_name") or "attachment"
		self["file_size"] = len(data)
		folder = "/private/files/" if int(self.get("is_private") or 0) else "/files/"
		self["file_url"] = folder + self["file_name"]

	def on_update(self):
		if self.get("content") is None:
			return
		STORE.file_contents[self.name] = _as_bytes(self.pop("content"))
		STORE.put(self)


def _as_bytes(value) -> bytes:
	return value.encode("utf-8") if isinstance(value, str) else bytes(value or b"")


def account_autoname(account_number, account_name, abbr: str) -> str:
	"""ERPNext's `get_account_autoname`, reproduced.

	Deliberately a second implementation rather than an import of
	`erpnext_mcp.charts.account_docname`. The app duplicates this rule too (it
	has to, to predict a docname during a dry run), and two independent copies of
	a rule that must match ERPNext is how a test notices one of them drifting. A
	shared helper would agree with itself and prove nothing.
	"""
	parts = [str(account_name or "").strip()]
	if str(abbr or "").strip():
		parts.append(str(abbr).strip())
	number = str(account_number or "").strip()
	if number:
		parts.insert(0, number)
	return " - ".join(part for part in parts if part)


class AccountDocument(Document):
	"""Account, with the parts of ERPNext's controller this app leans on.

	Being faithful here is not decoration. Three of this project's shipped bugs
	came from the double being more permissive than the framework, and Account is
	where that would bite hardest: its docname *encodes* two of its own fields,
	and ERPNext refuses to save a root account at all. A double that named
	accounts `A-00001` and happily saved roots would make every rename, every
	root refusal and every parent link in `tools/accounts.py` untestable — and
	those are the whole of what the module does.

	Reproduced from `Account.autoname`, `validate_parent`, `validate_root_details`
	and `set_root_and_report_type`. Everything else ERPNext's controller does
	(nested-set maintenance, child-company sync, currency checks) is left out;
	the app does not depend on it.
	"""

	def autoname(self):
		abbr = frappe.db.get_value("Company", self.get("company"), "abbr") or ""
		self.name = account_autoname(self.get("account_number"), self.get("account_name"), abbr)

	def validate(self):
		parent = str(self.get("parent_account") or "").strip()
		if not parent:
			# validate_root_details: an account with no parent is a root, and a
			# root that already exists cannot be saved at all.
			if not self.flags.in_insert:
				raise ValidationError("Root cannot be edited.")
			if not int(self.get("is_group") or 0):
				raise ValidationError(f"The root account {self.get('account_name')} must be a group")
			# ...and then Frappe's own mandatory pass, which runs after the
			# controller hook and refuses the insert because ERPNext's Account
			# marks parent_account `reqd`. ERPNext's chart importer gets past it
			# with `flags.ignore_mandatory` for root nodes only, and so does this
			# app. Without this branch the double would create roots the framework
			# refuses, which is how the bug shipped.
			if not self.flags.ignore_mandatory:
				raise MandatoryError(f"[Account, {self.get('name')}]: parent_account")
		else:
			row = STORE.get_raw("Account", parent)
			if row is None:
				raise DoesNotExistError(f"Could not find Parent Account: {parent}")
			if not int(row.get("is_group") or 0):
				raise ValidationError(f"Account {parent} cannot be a parent account: it is a ledger")
			if row.get("company") != self.get("company"):
				raise ValidationError("Account and parent account must belong to the same company")
			if not self.get("root_type"):
				self.root_type = row.get("root_type")
		# set_root_and_report_type
		self.report_type = (
			"Balance Sheet"
			if self.get("root_type") in ("Asset", "Liability", "Equity")
			else "Profit and Loss"
		)


class CostCenterDocument(Document):
	"""Cost Center, with the parts of ERPNext's controller this app leans on.

	Reproduced from `CostCenter.autoname`, `validate_mandatory` and
	`validate_parent_cost_center`. The two rules that matter to the app are the
	docname — `"<number> - <name> - <abbr>"`, the same shape Account uses and the
	one `dimensions.cost_center_docname` predicts — and the root rule, which is
	that a cost center with no parent must be named exactly after its company.
	The app cites that rule in two refusals, so a double that let anything be a
	root would make both of them look like this app being obstructive.
	"""

	def autoname(self):
		abbr = frappe.db.get_value("Company", self.get("company"), "abbr") or ""
		self.name = account_autoname(self.get("cost_center_number"), self.get("cost_center_name"), abbr)

	def validate(self):
		parent = str(self.get("parent_cost_center") or "").strip()
		if not parent:
			if self.get("cost_center_name") != self.get("company"):
				raise ValidationError("Please enter parent cost center")
			return
		if self.get("cost_center_name") == self.get("company"):
			raise ValidationError("Root cannot have a parent cost center")
		row = STORE.get_raw("Cost Center", parent)
		if row is None:
			raise DoesNotExistError(f"Could not find Parent Cost Center: {parent}")
		if not int(row.get("is_group") or 0):
			raise ValidationError(
				f"{parent} is not a group node. Please select a group node as parent cost center"
			)
		if row.get("company") != self.get("company"):
			raise ValidationError("Cost Center and parent cost center must belong to the same company")


class BankAccountDocument(Document):
	"""Bank Account, which names itself after the account and the institution.

	ERPNext's `BankAccount.autoname` is `" - ".join(filter(None, [account_name,
	bank]))`, which is why the fixture's one account is called
	`Operating - Example Bank`. Reproduced because `create_bank_account` reports
	the docname it produced and a caller wires a bank feed to that string; a double
	that named it `BA-00001` would make the one field anybody copies out of the
	response a fiction.
	"""

	def autoname(self):
		parts = [str(self.get("account_name") or "").strip(), str(self.get("bank") or "").strip()]
		self.name = " - ".join(part for part in parts if part)


class DocTypeDocument(Document):
	"""Inserting a DocType makes it exist, which is the whole point of the test.

	`create_accounting_dimension` can generate the master DocType a dimension's
	values live in. A double where that insert wrote a row nobody could then
	create records in would let the tool "succeed" and prove nothing.
	"""

	def on_update(self):
		register_doctype(
			self.name,
			self.get("fields") or [],
			issingle=bool(int(self.get("issingle") or 0)),
			autoname=str(self.get("autoname") or ""),
		)


class CustomFieldDocument(Document):
	"""Inserting a Custom Field makes `frappe.get_meta` report the field.

	Faithful, and load-bearing twice over: it is what lets a test create a
	dimension and then use it on a journal entry line, and it is what makes
	`create_accounting_dimension`'s idempotency — "this doctype already has the
	field, skip it" — reachable at all.
	"""

	def on_update(self):
		add_field(
			self.get("dt"),
			self.get("fieldname"),
			fieldtype=str(self.get("fieldtype") or "Data"),
			options=self.get("options"),
			label=self.get("label"),
		)


def _autoname_from_meta(doc) -> str:
	"""Frappe's `field:<fieldname>` naming rule, which the dimension masters use."""
	meta = META.get(doc.doctype)
	autoname = str(getattr(meta, "autoname", "") or "") if meta else ""
	if not autoname.startswith("field:"):
		return ""
	return str(doc.get(autoname.split(":", 1)[1]) or "").strip()


#: Link fields `rename_doc` repoints, per renamed doctype. See `rename_doc`.
RENAME_LINK_FIELDS = {
	"Account": (
		("Account", "parent_account"),
		("GL Entry", "account"),
		("Bank Account", "account"),
	),
	"Cost Center": (
		("Cost Center", "parent_cost_center"),
		("GL Entry", "cost_center"),
		("Company", "cost_center"),
		("Company", "round_off_cost_center"),
	),
}


class JournalEntryDocument(Document):
	"""ERPNext's Journal Entry, in the one respect that broke a real ledger.

	`JournalEntry.set_amounts_in_account_currency` does not fill the
	`*_in_account_currency` columns in from `debit`/`credit`. It runs the other
	way: `debit = debit_in_account_currency * exchange_rate`, on every validate.
	So a line built with `debit` alone inserts looking correct — the zero check
	has already run against the values as given — and is written to the database
	with its debit zeroed. The draft then exists, reads as 0.00, and is refused
	the moment anything validates it again:

	    Row 1: Both Debit and Credit values cannot be zero

	which is what four auto-generated opening-balance entries did on a live site
	under v0.8.0, and why `tools/mutate.py` now fills both columns for every line.

	Modelling it here in that order — check, then derive — is the point. A double
	that derived first would fail the *insert*, which is not what the site did,
	and a double that filled the columns in from `debit` (the intuitive
	direction) would make the broken code pass. This is the fourth time in this
	project's history that a permissive double let a real site break; see the
	0.8.0 changelog on `AccountDocument` and mandatory fields.

	`before_submit` re-runs it because real Frappe's `submit()` goes through
	`save()`, and validating only on insert would let a draft full of zeros post.
	"""

	def validate(self):
		total_debit = 0.0
		total_credit = 0.0
		for row in self.get("accounts") or []:
			debit = float(row.get("debit") or 0)
			credit = float(row.get("credit") or 0)
			if not debit and not credit:
				raise ValidationError(
					f"Row {row.get('idx')}: Both Debit and Credit values cannot be zero"
				)
			rate = float(row.get("exchange_rate") or 0) or 1.0
			row["debit_in_account_currency"] = round(float(row.get("debit_in_account_currency") or 0), 2)
			row["credit_in_account_currency"] = round(
				float(row.get("credit_in_account_currency") or 0), 2
			)
			row["exchange_rate"] = rate
			row["debit"] = round(row["debit_in_account_currency"] * rate, 2)
			row["credit"] = round(row["credit_in_account_currency"] * rate, 2)
			total_debit += row["debit"]
			total_credit += row["credit"]
		self.total_debit = round(total_debit, 2)
		self.total_credit = round(total_credit, 2)
		self.difference = round(total_debit - total_credit, 2)
		if abs(self.difference) > 0.005:
			raise ValidationError(
				f"Total Debit must be equal to Total Credit. The difference is {self.difference}"
			)

	def before_submit(self):
		self.validate()


def post_journal_entry_gl(name: str) -> list[dict]:
	"""Write the GL Entry rows a real ERPNext submit would write for one entry.

	THE FIFTH TIME A PERMISSIVE DOUBLE CERTIFIED CODE THAT COULD NOT WORK. Until
	v0.14.0 the tests seeded GL rows by hand with
	`voucher_detail_no = <the account line's docname>`, because that is the
	obvious thing to write and because it is true of Sales Invoice Item. It is
	not true of Journal Entry. ERPNext's `JournalEntry.get_gl_entries` fills that
	column from the line's **`reference_detail_no`** — a pointer at a payment
	schedule row on an invoice being settled, empty on every ordinary line — so a
	real Journal Entry's GL rows carry NO line docname whatsoever.

	v0.13.0's `update_journal_entry_party` looked its GL rows up by that column,
	the fixture agreed, every test passed, and on Tim's site the tool matched zero
	rows on every submitted entry: it updated the voucher, left the general ledger
	saying the old party, and blamed the site in a warning. That is precisely the
	failure the module docstring at the top of this file promises this double
	exists to prevent, and it happened anyway because the double was written from
	the same wrong belief as the code.

	TWO THINGS ARE MODELLED, AND THE SECOND MATTERS AS MUCH AS THE FIRST.

	  * `voucher_detail_no` comes from `reference_detail_no`, so it is empty
	    unless a test deliberately sets one.
	  * GL entries are MERGED. `make_gl_entries` runs `merge_similar_entries` by
	    default, which collapses rows sharing an account, cost center, party and
	    against-voucher into ONE row with the amounts summed. So a two-line entry
	    posting twice to the same account produces one GL row, not two — and a
	    tool that writes a party onto it would attribute both lines to one person.
	    A double that emitted one row per line would have made that unreachable.

	Cancelled and draft entries post nothing, as they do on a real site. Returns
	the rows it wrote.
	"""
	entry = STORE.get_raw("Journal Entry", name)
	if entry is None:
		raise DoesNotExistError(f"Journal Entry {name} not found")
	if int(entry.get("docstatus") or 0) != 1:
		return []

	merged: dict = {}
	order: list = []
	for row in entry.get("accounts") or []:
		detail_no = str(row.get("reference_detail_no") or "")
		key = (
			str(row.get("account") or ""),
			str(row.get("cost_center") or ""),
			str(row.get("party_type") or ""),
			str(row.get("party") or ""),
			detail_no,
			str(row.get("reference_type") or ""),
			str(row.get("reference_name") or ""),
		)
		if key not in merged:
			merged[key] = {
				"name": f"GL-{name}-{len(order) + 1}",
				"account": row.get("account"),
				"posting_date": entry.get("posting_date"),
				"debit": 0.0,
				"credit": 0.0,
				"company": entry.get("company"),
				"is_cancelled": 0,
				"voucher_type": "Journal Entry",
				"voucher_no": name,
				"voucher_detail_no": detail_no,
				"party_type": row.get("party_type"),
				"party": row.get("party"),
				"cost_center": row.get("cost_center"),
				"against_voucher_type": row.get("reference_type"),
				"against_voucher": row.get("reference_name"),
				"is_opening": "Yes" if entry.get("is_opening") == "Yes" else "No",
			}
			order.append(key)
		merged[key]["debit"] = round(merged[key]["debit"] + float(row.get("debit") or 0), 2)
		merged[key]["credit"] = round(merged[key]["credit"] + float(row.get("credit") or 0), 2)

	rows = [merged[key] for key in order]
	table = STORE.tables.setdefault("GL Entry", {})
	for row in rows:
		row.setdefault("docstatus", 1)
		row.setdefault("creation", _now())
		table[row["name"]] = row
	return rows


#: Doctypes whose stub behaviour differs from a plain Document.
STUB_CONTROLLERS = {
	"File": FileDocument,
	"Account": AccountDocument,
	"Bank Account": BankAccountDocument,
	"Cost Center": CostCenterDocument,
	"DocType": DocTypeDocument,
	"Custom Field": CustomFieldDocument,
	"Journal Entry": JournalEntryDocument,
}


class Store:
	"""The in-memory database."""

	def __init__(self):
		self.reset()

	def reset(self):
		self.file_contents: dict[str, bytes] = {}
		self.denied_permissions: set = set()
		self.installed_apps: list[str] = ["frappe", "erpnext"]
		self.report_runners: dict = {}
		self.tables: dict[str, dict[str, dict]] = {}
		self.singles: dict[str, dict] = {}
		self.passwords: dict[tuple, str] = {}
		self.comments: list[dict] = []
		self.errors: list[dict] = []
		self.counters: dict[str, int] = {}
		self.committed = 0
		self.rolled_back = 0
		self._seed_doctypes()
		# Rows written since the last commit, so a rollback can discard exactly
		# those — which is what the audit-survives-rollback tests need to see.
		self.pending: list[tuple[str, str]] = []
		# Before-images of rows CHANGED or DELETED since the last commit, so a
		# rollback puts them back. Discarding new rows was never the whole of what
		# a rollback does, and modelling only that half made a multi-step tool look
		# atomic when it was not: `convey_parcel` repoints a dozen leases and
		# housing units before it deletes anything, and a double that kept those
		# updates through a rollback would certify a half-conveyed parcel as
		# impossible while a real MariaDB transaction was the only thing making it
		# so. One entry per (doctype, name), taken the FIRST time it is touched —
		# the state to restore is the one the transaction opened with, not the one
		# the second write found.
		self.before_images: dict[tuple[str, str], dict | None] = {}

	def _seed_doctypes(self):
		"""A row per DocType, because `tabDocType` really is a table.

		`frappe.db.exists("DocType", …)` is answered from `INSTALLED_DOCTYPES`
		(tests flip entries there to simulate a site missing an optional doctype),
		but `issingle` and `istable` are read as ordinary columns — which is how
		`create_accounting_dimension` refuses a Single or a child table as a
		dimension master. Those refusals need real rows to read.
		"""
		rows = {}
		child_tables = set(CHILD_TABLES.values())
		for doctype in ERPNEXT_SCHEMA:
			rows[doctype] = {
				"name": doctype,
				"module": "Core",
				"issingle": 0,
				"istable": 1 if doctype in child_tables else 0,
			}
		for doctype, folder in APP_DOCTYPES.items():
			payload = _load_app_doctype(folder)
			rows[doctype] = {
				"name": doctype,
				"module": payload.get("module") or "ERPNext MCP",
				"issingle": int(payload.get("issingle") or 0),
				"istable": int(payload.get("istable") or 0),
			}
		self.tables["DocType"] = rows

	def next_name(self, doctype: str) -> str:
		self.counters[doctype] = self.counters.get(doctype, 0) + 1
		prefix = "".join(word[0] for word in doctype.split() if word).upper() or "DOC"
		return f"{prefix}-{self.counters[doctype]:05d}"

	def next_child_name(self, doctype: str) -> str:
		"""A child row's docname. Frappe uses a hash; the shape does not matter.

		What matters is that it is opaque and stable: a test that could predict it
		from the row's contents would let a tool "find" a row by reconstructing the
		name rather than by following the pointer, which is not what a real site
		allows.
		"""
		key = f"child:{doctype}"
		self.counters[key] = self.counters.get(key, 0) + 1
		return f"{secrets.token_hex(5)}{self.counters[key]:03d}"

	def put(self, doc: Document):
		self._extract_passwords(doc)
		if META.get(doc.doctype) and META[doc.doctype].issingle:
			self.singles[doc.doctype] = _plain(doc)
			return
		table = self.tables.setdefault(doc.doctype, {})
		is_new = doc.name not in table
		self.snapshot(doc.doctype, doc.name)
		table[doc.name] = _plain(doc)
		if is_new:
			self.pending.append((doc.doctype, doc.name))

	def snapshot(self, doctype: str, name: str) -> None:
		"""Remember what a row looked like before this transaction touched it.

		Only the first touch is recorded. A row updated three times and then
		rolled back has to come back as it was before the first of them, and
		keeping the latest before-image would restore it to the state left by the
		second write — which is not a state the database was ever in.
		"""
		key = (doctype, name)
		if key in self.before_images:
			return
		row = self.tables.get(doctype, {}).get(name)
		self.before_images[key] = copy.deepcopy(row) if row is not None else None

	def _extract_passwords(self, doc: Document):
		"""Move Password field values out of the row, as Frappe does on save.

		Frappe writes a Password field to the encrypted `__Auth` table and leaves
		a row of asterisks in the document, which is why `get_password()` exists
		and why reading the field directly gives you nothing useful. Reproducing
		that here is what makes "the token is never returned to a caller"
		something the tests can actually check rather than take on trust.
		"""
		meta = META.get(doc.doctype)
		if not meta:
			return
		for field in meta.fields:
			if field.get("fieldtype") != "Password":
				continue
			value = doc.get(field["fieldname"])
			if value and not set(str(value)) <= {"*"}:
				self.passwords[(doc.doctype, doc.get("name"), field["fieldname"])] = value
				doc[field["fieldname"]] = "*" * len(str(value))

	def get_raw(self, doctype: str, name: str):
		if META.get(doctype) and META[doctype].issingle:
			return self.singles.get(doctype)
		return self.tables.get(doctype, {}).get(name)

	def rows(self, doctype: str) -> list[dict]:
		if META.get(doctype) and META[doctype].issingle:
			single = self.singles.get(doctype)
			return [single] if single else []
		return list(self.tables.get(doctype, {}).values())

	def seed(self, doctype: str, rows: list[dict]):
		table = self.tables.setdefault(doctype, {})
		for index, row in enumerate(rows, start=1):
			row = dict(row)
			row.setdefault("name", f"{doctype}-{index}")
			row.setdefault("docstatus", 0)
			row.setdefault("creation", _now())
			table[row["name"]] = row
		# Seeded fixtures are "already committed" state.
		self.pending.clear()
		self.before_images.clear()

	def commit(self):
		self.committed += 1
		self.pending.clear()
		self.before_images.clear()

	def rollback(self):
		self.rolled_back += 1
		for doctype, name in self.pending:
			self.tables.get(doctype, {}).pop(name, None)
		for (doctype, name), row in self.before_images.items():
			table = self.tables.setdefault(doctype, {})
			if row is None:
				table.pop(name, None)
			else:
				table[name] = copy.deepcopy(row)
		self.pending.clear()
		self.before_images.clear()


def _plain(doc) -> dict:
	out = {}
	for key, value in doc.items():
		if key in ("flags", "_doc_before_save"):
			continue
		if isinstance(value, list):
			out[key] = [_plain(item) if isinstance(item, dict) else item for item in value]
		else:
			out[key] = value
	return out


STORE = Store()


# ── the fake frappe.db ──────────────────────────────────────────────────────
#: Sentinel for "the caller did not pass order_by", so the double can tell that
#: apart from an explicit None. Real Frappe uses the same trick, which is what
#: makes the default invisible at the call site and therefore easy to get wrong.
DEFAULT_ORDER_BY = object()

#: Tables that are NOT DocType tables and so have none of the framework columns.
#: `tabSingles` is three columns: doctype, field, value. Ordering by `modified`
#: — which `frappe.db.get_value` and `get_values` do unless told otherwise —
#: is `Unknown column 'modified' in 'ORDER BY'`.
#:
#: v0.2.0 shipped an `after_migrate` hook that did exactly that and broke
#: `bench migrate` on a live site. The double answered the query happily, which
#: is why the standalone suite did not catch it. It refuses now.
FRAMEWORKLESS_TABLES = {"Singles", "__Auth", "__global_search", "tabSeries"}

#: Columns every real DocType table has, whether or not this fixture's schema
#: bothers to list them.
FRAMEWORK_COLUMNS = frozenset({"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"})


class OperationalError(Exception):
	"""What pymysql raises for a bad column. Mirrored so tests can assert on it."""


def _reject_default_ordering(doctype: str, order_by) -> None:
	"""Fail the way MariaDB does when a query would ORDER BY a missing column.

	Only the default is rejected. A caller that passed `order_by=None`, or named
	a column the table has, knows what it is doing — the bug this reproduces is
	specifically the one you cannot see at the call site.
	"""
	if order_by is not DEFAULT_ORDER_BY:
		return
	if doctype not in FRAMEWORKLESS_TABLES:
		return
	raise OperationalError(
		f"(1054, \"Unknown column 'modified' in 'ORDER BY'\") — `tab{doctype}` is "
		"not a DocType table and has no framework columns. Pass order_by=None, or "
		"use the framework's own accessor (frappe.db.get_singles_dict for "
		"tabSingles)."
	)


class FakeDB:
	def get_all(
		self,
		doctype,
		filters=None,
		fields=None,
		order_by=None,
		limit=None,
		limit_page_length=None,
		pluck=None,
		as_dict=True,
		distinct=False,
		group_by=None,
		**kwargs,
	):
		rows = [row for row in STORE.rows(doctype) if _match(row, filters)]
		if doctype in CHILD_TABLE_SOURCES:
			rows = [row for row in _child_rows(doctype) if _match(row, filters)]

		aggregates = [f for f in (fields or []) if "(" in str(f)]
		if aggregates:
			if group_by:
				return _grouped_aggregate(rows, fields, aggregates, group_by)
			return [_aggregate(rows, aggregates)]

		if order_by:
			rows = _sorted(rows, order_by)
		cap = limit or limit_page_length
		if cap:
			rows = rows[: int(cap)]
		if pluck:
			return [row.get(pluck) for row in rows]
		if fields:
			return [FrappeDict({f: row.get(f) for f in fields}) for row in rows]
		return [FrappeDict(copy.deepcopy(row)) for row in rows]

	def get_value(
		self,
		doctype,
		filters=None,
		fieldname="name",
		as_dict=False,
		order_by=DEFAULT_ORDER_BY,
		**kwargs,
	):
		# Real `get_value` is `get_values(..., limit=1)`, so it inherits the same
		# default ordering and the same failure on a frameworkless table.
		_reject_default_ordering(doctype, order_by)
		rows = self.get_all(doctype, filters=filters)
		if not rows:
			return None
		row = rows[0]
		if isinstance(fieldname, (list, tuple)):
			if as_dict:
				return FrappeDict({f: row.get(f) for f in fieldname})
			return [row.get(f) for f in fieldname]
		if as_dict:
			return FrappeDict({fieldname: row.get(fieldname)})
		return row.get(fieldname)

	def get_values(
		self,
		doctype,
		filters=None,
		fieldname="name",
		as_dict=False,
		order_by=DEFAULT_ORDER_BY,
		**kwargs,
	):
		_reject_default_ordering(doctype, order_by)
		if doctype == "Singles":
			target = (filters or {}).get("doctype")
			return [
				FrappeDict({"field": key, "value": value})
				for key, value in (STORE.singles.get(target) or {}).items()
			]
		rows = self.get_all(doctype, filters=filters)
		fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
		return [FrappeDict({f: row.get(f) for f in fields}) for row in rows]

	def get_singles_dict(self, doctype, debug=False, *, for_update=False, cast=False):
		"""The framework's own reader for `tabSingles` — no ORDER BY to get wrong.

		Returns `{fieldname: value}` for the fields that have a stored row, which
		is exactly the "which fields are already set" question `seed_defaults`
		asks. `doctype` and `name` are excluded because tabSingles rows are
		(doctype, field, value) — the doctype is the filter, not a field.
		"""
		stored = STORE.singles.get(doctype) or {}
		return FrappeDict({k: copy.deepcopy(v) for k, v in stored.items() if k not in ("doctype", "name")})

	def get_single_value(self, doctype, fieldname):
		return (STORE.singles.get(doctype) or {}).get(fieldname)

	def exists(self, doctype, filters=None):
		if doctype == "DocType":
			name = filters if isinstance(filters, str) else (filters or {}).get("name")
			return name if name in INSTALLED_DOCTYPES else None
		rows = self.get_all(doctype, filters=filters)
		return rows[0].get("name") if rows else None

	def count(self, doctype, filters=None):
		return len(self.get_all(doctype, filters=filters))

	def set_value(self, doctype, name, fieldname, value=None, **kwargs):
		if doctype in CHILD_TABLE_SOURCES:
			return self._set_child_value(doctype, name, fieldname, value)
		row = STORE.get_raw(doctype, name)
		if row is None:
			return
		STORE.snapshot(doctype, name)
		if isinstance(fieldname, dict):
			row.update(fieldname)
		else:
			row[fieldname] = value

	def _set_child_value(self, doctype, name, fieldname, value):
		"""`frappe.db.set_value` against a child row, found by its own docname.

		Real Frappe writes `tabJournal Entry Account` directly and this is how a
		submitted document's line gets a field changed at all — `party` is not
		allowed on submit, so `doc.save()` is not available. The double stores
		children inside their parents, so the row has to be located by walking
		them; the observable behaviour is the same, which is the point.

		Silently does nothing for a name that matches no row, exactly as
		`frappe.db.set_value` does for a missing document.
		"""
		for parent_doctype, fieldname_on_parent in CHILD_TABLE_SOURCES[doctype]:
			self._set_child_value_in(parent_doctype, fieldname_on_parent, doctype, name, fieldname, value)

	def _set_child_value_in(self, parent_doctype, fieldname_on_parent, doctype, name, fieldname, value):
		for parent in STORE.rows(parent_doctype):
			for row in parent.get(fieldname_on_parent) or []:
				if row.get("name") != name:
					continue
				# The child lives inside its parent's row, so the parent is what a
				# rollback has to restore.
				STORE.snapshot(parent_doctype, parent.get("name"))
				if isinstance(fieldname, dict):
					row.update(fieldname)
				else:
					row[fieldname] = value
				return

	def commit(self):
		STORE.commit()

	def rollback(self):
		STORE.rollback()

	def sql(self, *args, **kwargs):  # pragma: no cover - app must not use raw SQL
		raise AssertionError(
			"erpnext_mcp must not run raw SQL: every write goes through the ORM so "
			"doctype validation runs. If a read genuinely needs SQL, extend this "
			"stub deliberately."
		)


#: Child doctypes are stored inside their parents, so a query against one has to
#: flatten the parents first. The value is a TUPLE OF (parent, fieldname) PAIRS
#: rather than a single pair, because v0.16.0 ships a child table with four
#: parents: `Farm Task Evidence` is the photographs on a task completion, on a
#: housing inspection, on a detector test and on a water sample — one shape of
#: row, one place to change it, four documents that carry it. A double that
#: assumed one parent per child would have flattened only the first and reported
#: every other record's evidence as absent, which is the kind of empty result
#: that reads as "no photographs were filed".
CHILD_TABLE_SOURCES = {
	"Journal Entry Account": (("Journal Entry", "accounts"),),
	"Parcel Conveyance Event": (("Parcel", "conveyance_events"),),
	"Bank Transaction Payments": (("Bank Transaction", "payment_entries"),),
	"Fiscal Year Company": (("Fiscal Year", "companies"),),
	"Workflow Document State": (("Workflow", "states"),),
	"Workflow Transition": (("Workflow", "transitions"),),
	"Farm Task Evidence": (
		("Farm Task Assignment", "evidence_files"),
		("Housing Inspection", "photos"),
		("Detector Test", "photos"),
		("Water Test", "sample_photos"),
	),
}


def _child_rows(child_doctype: str) -> list[dict]:
	out = []
	for parent_doctype, fieldname in CHILD_TABLE_SOURCES[child_doctype]:
		for parent in STORE.rows(parent_doctype):
			for row in parent.get(fieldname) or []:
				merged = dict(row)
				merged.setdefault("parent", parent.get("name"))
				merged.setdefault("parenttype", parent_doctype)
				merged.setdefault("parentfield", fieldname)
				out.append(merged)
	return out


def _aggregate(rows: list[dict], expressions: list[str]) -> FrappeDict:
	out = FrappeDict()
	for expression in expressions:
		function, _, rest = expression.partition("(")
		column = rest.split(")")[0].strip()
		alias = expression.split(" as ")[-1].strip() if " as " in expression else expression
		function = function.strip().lower()
		if function == "sum":
			out[alias] = sum(float(row.get(column) or 0) for row in rows) or 0
		elif function == "count":
			out[alias] = len(rows)
		else:  # pragma: no cover
			raise NotImplementedError(f"stub aggregate {function!r}")
	return out


def _grouped_aggregate(rows, fields, aggregates, group_by):
	"""`SELECT <key>, sum(...) ... GROUP BY <key>`, as the packets use it."""
	key = str(group_by).split(",")[0].strip().strip("`").split(".")[-1]
	plain = [f for f in (fields or []) if "(" not in str(f)]
	buckets: dict = {}
	for row in rows:
		buckets.setdefault(row.get(key), []).append(row)
	out = []
	for value, group in buckets.items():
		entry = _aggregate(group, aggregates)
		for column in plain:
			entry[column] = group[0].get(column)
		entry[key] = value
		out.append(entry)
	return out


def _sort_key(value):
	"""A total ordering key, so a column of mixed types cannot raise.

	`0 or ""` was the original spelling and it has a hole in it that only shows on
	a column that is legitimately zero: `chunk_index` counts from 0, so ordering
	staged upload pieces turned index 0 into the empty string and then compared a
	string against the integers beside it — `TypeError: '<' not supported between
	instances of 'int' and 'str'`. MariaDB has no such problem, so this was the
	double refusing a query a real site answers, which is the mirror image of the
	usual failure and just as capable of blocking working code.

	Empty and NULL sort first, as MariaDB puts NULLs first ascending; then
	numbers; then text. The three-part tuple is what makes the comparison total
	whatever the column holds.
	"""
	if value is None or value == "":
		return (0, 0.0, "")
	key = _key(value)
	if isinstance(key, (int, float)) and not isinstance(key, bool):
		return (1, float(key), "")
	return (2, 0.0, str(key))


def _sorted(rows: list[dict], order_by: str) -> list[dict]:
	out = list(rows)
	# Apply each clause in reverse so the leftmost wins, as SQL does.
	for clause in reversed([c.strip() for c in order_by.split(",") if c.strip()]):
		parts = clause.split()
		column = parts[0].split(".")[-1].strip("`")
		reverse = len(parts) > 1 and parts[1].lower() == "desc"
		out.sort(key=lambda row: _sort_key(row.get(column)), reverse=reverse)
	return out


#: Which doctypes this fake site "has installed". Tests flip entries to exercise
#: the graceful-degrade paths (a site without Bank Statement, say).
INSTALLED_DOCTYPES = set(ERPNEXT_SCHEMA) | set(APP_DOCTYPES)


# ── utils ───────────────────────────────────────────────────────────────────
def _now() -> str:
	return datetime.datetime(2026, 7, 24, 9, 0, 0).isoformat(sep=" ")


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 7, 24)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip().split(" ")[0].split("T")[0]
	parts = text.split("-")
	if len(parts) != 3:
		raise ValueError(f"cannot parse date {value!r}")
	return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))


def _build_utils() -> types.ModuleType:
	module = types.ModuleType("frappe.utils")
	module.now = _now
	module.nowdate = lambda: _getdate().isoformat()
	module.today = lambda: _getdate().isoformat()
	module.getdate = _getdate
	module.flt = lambda value, precision=None: round(float(value or 0), precision or 2)
	module.cint = lambda value: int(float(value or 0))
	module.cstr = lambda value: "" if value is None else str(value)

	def get_url(uri=None, full_address=False):
		"""frappe.utils.get_url — the site's own address, as the server sees it."""
		base = "https://test.localhost"
		return f"{base}/{str(uri).lstrip('/')}" if uri else base

	module.get_url = get_url

	def add_days(date, days):
		return _getdate(date) + datetime.timedelta(days=days)

	def date_diff(later, earlier):
		return (_getdate(later) - _getdate(earlier)).days

	module.add_days = add_days
	module.date_diff = date_diff

	def time_diff_in_seconds(later, earlier):
		"""frappe.utils.time_diff_in_seconds — seconds between two datetimes.

		v0.16.0's dispatch tools use it to work out how long a task actually took
		from its clock-in and clock-out. Faithful in the one way that matters:
		it takes DATETIMES and returns a float, so a task that ran twenty-five
		minutes reports twenty-five and not zero, which is what a date-only
		double would have said.
		"""

		def parse(value):
			if isinstance(value, datetime.datetime):
				return value
			if isinstance(value, datetime.date):
				return datetime.datetime(value.year, value.month, value.day)
			return datetime.datetime.fromisoformat(str(value).strip().replace("T", " "))

		return (parse(later) - parse(earlier)).total_seconds()

	module.time_diff_in_seconds = time_diff_in_seconds

	def add_to_date(
		date=None,
		years=0,
		months=0,
		weeks=0,
		days=0,
		hours=0,
		minutes=0,
		seconds=0,
		as_string=False,
		as_datetime=False,
	):
		"""frappe.utils.add_to_date, in the shapes this app uses it.

		`as_string=True, as_datetime=True` returns Frappe's own DATETIME_FORMAT,
		which matters: the staged-upload sweeper compares the result against the
		`modified` COLUMN, and a value formatted any other way — an isoformat with
		a `T`, say — compares as a string and quietly matches nothing. Months and
		years are refused rather than approximated, because this double has no
		dateutil and a 30-day month would be a lie a test could come to rely on.
		"""
		if months or years:  # pragma: no cover - nothing in the app asks for these
			raise NotImplementedError("stub add_to_date does not do months or years")
		if isinstance(date, datetime.datetime):
			base = date
		elif isinstance(date, datetime.date):
			base = datetime.datetime(date.year, date.month, date.day)
		else:
			base = datetime.datetime.fromisoformat(str(date or _now()).strip().replace("T", " "))
		moved = base + datetime.timedelta(
			weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds
		)
		if as_string:
			return moved.strftime("%Y-%m-%d %H:%M:%S.%f" if as_datetime else "%Y-%m-%d")
		return moved

	module.add_to_date = add_to_date
	return module


# ── the module itself ───────────────────────────────────────────────────────
def _controller(doctype: str):
	"""Resolve a doctype to this app's controller class, as Frappe does.

	Including the child tables. Frappe imports `<folder>/<folder>.py` for every
	DocType it loads and does not make an exception for a table, which is what
	v0.7.0 learned the hard way — so neither does this. A folder with a JSON and
	no module raises ImportError here for the same reason `bench migrate` raises
	ModuleNotFoundError there.
	"""
	folder = APP_DOCTYPES.get(doctype)
	if not folder:
		return STUB_CONTROLLERS.get(doctype, Document)
	module = __import__(f"erpnext_mcp.erpnext_mcp.doctype.{folder}.{folder}", fromlist=["x"])
	return getattr(module, doctype.replace(" ", ""), Document)


def _build_frappe() -> types.ModuleType:
	module = types.ModuleType("frappe")

	module._dict = FrappeDict
	module.ValidationError = ValidationError
	module.DoesNotExistError = DoesNotExistError
	module.PermissionError = PermissionError_
	module.MandatoryError = MandatoryError
	module.LinkValidationError = LinkValidationError
	module.db = FakeDB()
	module.local = FrappeDict(
		site="test.localhost",
		request=None,
		session=FrappeDict(user="Guest", data=FrappeDict()),
	)
	module.flags = FrappeDict()
	# common_site_config.json merged with site_config.json. Real Frappe exposes
	# the same object as frappe.conf and frappe.local.conf.
	module.conf = FrappeDict()
	module.local.conf = module.conf
	# What a whitelisted method fills in to serve a file. Frappe's
	# `frappe.utils.response.as_binary` reads type/filename/filecontent off it.
	module.response = FrappeDict()

	def _translate(text, *args, **kwargs):
		return text

	module._ = _translate

	def get_meta(doctype):
		if doctype not in META:
			raise ValidationError(f"stub has no meta for {doctype!r}")
		return META[doctype]

	def get_doc(*args, **kwargs):
		if args and isinstance(args[0], dict):
			payload = dict(args[0])
			doctype = payload.pop("doctype")
			return _controller(doctype)({**payload, "doctype": doctype})
		doctype = args[0]
		if META.get(doctype) and META[doctype].issingle:
			data = copy.deepcopy(STORE.singles.get(doctype) or {})
			data["doctype"] = doctype
			data.setdefault("name", doctype)
			return _controller(doctype)(data)
		name = args[1] if len(args) > 1 else kwargs.get("name")
		row = STORE.get_raw(doctype, name)
		if row is None:
			raise DoesNotExistError(f"{doctype} {name} not found")
		data = copy.deepcopy(row)
		data["doctype"] = doctype
		doc = _controller(doctype)(data)
		for fieldname in REHYDRATED_CHILD_FIELDS:
			if isinstance(doc.get(fieldname), list):
				doc[fieldname] = [Document(item) for item in doc[fieldname]]
		return doc

	def new_doc(doctype):
		doc = _controller(doctype)({"doctype": doctype, "docstatus": 0})
		for field in META[doctype].fields if doctype in META else []:
			if field.get("default") not in (None, ""):
				doc[field["fieldname"]] = field["default"]
		return doc

	def get_cached_doc(*args, **kwargs):
		return get_doc(*args, **kwargs)

	def get_single(doctype):
		return get_doc(doctype)

	def whitelist(*dargs, **dkwargs):
		def decorator(function):
			function.__wrapped_whitelisted__ = True
			return function

		if dargs and callable(dargs[0]):
			return decorator(dargs[0])
		return decorator

	def throw(message, exc=None, title=None):
		raise (exc or ValidationError)(str(message))

	def only_for(roles, message=False):
		roles = [roles] if isinstance(roles, str) else list(roles)
		if not set(roles) & set(get_roles(module.session.user)):
			raise PermissionError_(f"requires role: {', '.join(roles)}")

	def get_roles(user=None):
		return ROLES.get(user or module.session.user, [])

	def get_request_header(name, default=None):
		request = module.local.request
		if request is None:
			return default
		return request.headers.get(name, default)

	def set_user(user):
		module.local.session.user = user

	def log_error(title=None, message=None, reference_doctype=None, reference_name=None):
		STORE.errors.append({"title": title, "message": message})

	def get_traceback(with_context=False):
		return traceback.format_exc()

	def generate_hash(txt=None, length=56):
		return secrets.token_hex(max(1, length // 2))[:length]

	def msgprint(message, title=None, indicator=None, **kwargs):
		STORE.comments.append({"type": "msgprint", "text": str(message)})

	def get_attr(path):
		parts = path.split(".")
		obj = __import__(".".join(parts[:-1]), fromlist=["x"])
		return getattr(obj, parts[-1])

	def get_installed_apps():
		return list(STORE.installed_apps)

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		"""Allow by default; tests deny specific (doctype, ptype) or (doctype, name).

		Default-allow is the right polarity for a double: a test that forgets to
		grant something should not silently pass because everything was denied,
		and every permission test here is about a *refusal*, which it has to ask
		for explicitly.
		"""
		name = doc if isinstance(doc, str) else (doc.get("name") if doc else None)
		denied = (
			(doctype, ptype) in STORE.denied_permissions
			or (doctype, name) in STORE.denied_permissions
			or (doctype, name, ptype) in STORE.denied_permissions
		)
		if denied and throw:
			raise PermissionError_(f"no {ptype} permission for {doctype} {name}")
		return not denied

	def get_list(doctype, **kwargs):
		"""`get_all` with the permission check `get_all` skips."""
		if not has_permission(doctype, "read"):
			raise PermissionError_(f"no read permission for {doctype}")
		return module.db.get_all(doctype, **kwargs)

	def scrub(text):
		return str(text or "").replace(" ", "_").replace("-", "_").lower()

	def clear_cache(user=None, doctype=None, *args, **kwargs):
		"""A no-op: this double has no meta cache to invalidate.

		Present so the app can call it — adding a Custom Field without clearing
		the cache is a real bug on a real site, and a double that raised
		AttributeError here would push the app into not doing it.
		"""

	def delete_doc(doctype, name, force=False, ignore_permissions=False, **kwargs):
		STORE.snapshot(doctype, name)
		STORE.tables.get(doctype, {}).pop(name, None)

	def rename_doc(doctype, old, new, force=False, merge=False, **kwargs):
		"""Move a docname and repoint the links this app can observe.

		Real Frappe rewrites every Link field on the site that pointed at the old
		name. Reproducing that generically would mean a link graph this double
		does not have, so `RENAME_LINK_FIELDS` names the ones the account tools
		actually depend on — the child's `parent_account` above all, since an
		import that renamed a group and orphaned its children is exactly the
		failure a test here should catch.
		"""
		table = STORE.tables.setdefault(doctype, {})
		if old not in table:
			raise DoesNotExistError(f"{doctype} {old} not found")
		if new == old:
			return old
		if new in table:
			raise ValidationError(f"{doctype} {new} already exists")
		row = table.pop(old)
		row["name"] = new
		table[new] = row
		STORE.pending = [(dt, new if (dt == doctype and dn == old) else dn) for dt, dn in STORE.pending]
		for link_doctype, fieldname in RENAME_LINK_FIELDS.get(doctype, ()):
			for other in STORE.rows(link_doctype):
				if other.get(fieldname) == old:
					other[fieldname] = new
		return new

	module.clear_cache = clear_cache
	module.get_installed_apps = get_installed_apps
	module.has_permission = has_permission
	module.get_list = get_list
	module.scrub = scrub
	module.delete_doc = delete_doc
	module.rename_doc = rename_doc
	module.get_meta = get_meta
	module.get_doc = get_doc
	module.new_doc = new_doc
	module.get_cached_doc = get_cached_doc
	module.get_single = get_single
	module.whitelist = whitelist
	module.throw = throw
	module.only_for = only_for
	module.get_roles = get_roles
	module.get_request_header = get_request_header
	module.set_user = set_user
	module.log_error = log_error
	module.get_traceback = get_traceback
	module.generate_hash = generate_hash
	module.msgprint = msgprint
	module.get_attr = get_attr
	module.get_site_path = get_site_path
	module.utils = _build_utils()

	# `frappe.session` and `frappe.request` are properties on the real module;
	# a module-level __getattr__ is the closest a stub gets.
	def __getattr__(name):
		if name == "session":
			return module.local.session
		if name == "request":
			return module.local.request
		if name == "site":
			return module.local.site
		raise AttributeError(
			f"erpnext_mcp used frappe.{name}, which this test double does not "
			"implement. Add it to tests_standalone/harness.py deliberately, or "
			"reconsider whether the app should depend on it."
		)

	module.__getattr__ = __getattr__
	return module


ROLES = {
	"Administrator": [
		"System Manager",
		"Accounts Manager",
		"Accounts User",
		"Purchase Manager",
		"Purchase User",
	]
}


def set_roles(user: str, roles) -> None:
	"""Give a fake user a role set, for the permission and workflow tests."""
	ROLES[user] = list(roles)


# ── frappe.model.workflow ───────────────────────────────────────────────────
def _active_workflow_for(doc):
	name = frappe.db.get_value("Workflow", {"document_type": doc.doctype, "is_active": 1}, "name")
	return frappe.get_doc("Workflow", name) if name else None


def _stub_get_transitions(doc, workflow=None, raise_exception=False):
	"""Frappe's transition resolution, faithfully enough to test against.

	Role and condition only. Frappe's get_transitions does NOT check
	self-approval — that rule lives in apply_workflow and throws at execution
	time — and a double that filtered it here would hide the fact that
	`list_available_actions` has to apply the rule itself. Verified against a
	real Workflow in erpnext_mcp/tests/test_workflow_scenarios.py.
	"""
	workflow = workflow or _active_workflow_for(doc)
	if workflow is None:
		return []
	state_field = workflow.get("workflow_state_field") or "workflow_state"
	current = doc.get(state_field)
	roles = set(frappe.get_roles(frappe.session.user) or [])
	out = []
	for row in workflow.get("transitions") or []:
		if row.get("state") != current:
			continue
		if row.get("allowed") and row["allowed"] not in roles:
			continue
		condition = row.get("condition")
		# A real Frappe uses safe_eval here; the double uses eval because the
		# only conditions it ever sees are the ones a test wrote.
		if condition and not eval(condition, {"doc": doc, "frappe": frappe}):
			continue
		out.append(dict(row))
	return out


def _stub_apply_workflow(doc, action):
	"""As Frappe does it — including enforcing self-approval *here*, late."""
	workflow = _active_workflow_for(doc)
	if workflow is None:
		raise ValidationError(f"no active workflow for {doc.doctype}")
	state_field = workflow.get("workflow_state_field") or "workflow_state"
	for row in _stub_get_transitions(doc, workflow):
		if row.get("action") != action:
			continue
		if (
			frappe.session.user != "Administrator"
			and not row.get("allow_self_approval")
			and doc.get("owner") == frappe.session.user
		):
			raise ValidationError("Self approval is not allowed")
		next_state = row.get("next_state")
		doc.set(state_field, next_state)
		target = next((s for s in workflow.get("states") or [] if s.get("state") == next_state), {})
		doc.docstatus = int(target.get("doc_status") or 0)
		doc.save()
		return doc
	raise ValidationError(f"transition {action!r} is not allowed")


# ── frappe.desk.query_report / reportview ───────────────────────────────────
def _stub_query_report_run(report_name, filters=None, user=None, **kwargs):
	ref_doctype = frappe.db.get_value("Report", report_name, "ref_doctype")
	if not frappe.has_permission(ref_doctype, "report"):
		raise PermissionError_(f"no report permission for {ref_doctype}")
	runner = STORE.report_runners.get(report_name)
	if runner is None:
		raise ValidationError(f"stub has no runner registered for report {report_name!r}")
	return runner(filters or {}, user)


def _stub_reportview_get(doctype, *args, **kwargs):
	params = getattr(frappe.local, "form_dict", None) or {}
	if not frappe.has_permission(doctype, "read"):
		raise PermissionError_(f"no read permission for {doctype}")
	fields = json.loads(params.get("fields") or "[]")
	conditions = json.loads(params.get("filters") or "[]")
	rows = frappe.db.get_all(
		doctype,
		filters={condition[1]: (condition[2], condition[3]) for condition in conditions},
		fields=fields,
		limit=params.get("page_length"),
	)
	return {"keys": fields, "values": [[row.get(field) for field in fields] for row in rows]}


# ── erpnext.accounts.doctype.account.account ────────────────────────────────
def _stub_update_account_number(name, account_name, account_number=None, from_descendant=False):
	"""ERPNext's own rename helper, reproduced closely enough to test against.

	The shape that matters to the app is the awkward one: it writes the two
	*fields* with `db.set_value` and then, only if the resulting autoname differs,
	renames the *document*. Two writes, in that order — which is precisely why
	`tools.accounts` delegates here instead of calling `rename_doc` and leaving a
	document whose name and fields disagree forever.

	Also faithful in its return value: the new docname when the name moved, and
	`None` when it did not. A double that always returned a name would hide the
	`returned or name` the caller needs.
	"""
	company = frappe.db.get_value("Account", name, "company")
	if not company:
		return None
	frappe.db.set_value("Account", name, "account_name", str(account_name).strip())
	frappe.db.set_value("Account", name, "account_number", account_number)
	abbr = frappe.db.get_value("Company", company, "abbr") or ""
	new_name = account_autoname(account_number, account_name, abbr)
	if name != new_name:
		frappe.rename_doc("Account", name, new_name, force=1)
		return new_name
	return None


def _install_erpnext_account_api() -> None:
	path = "erpnext.accounts.doctype.account.account"
	leaf = types.ModuleType(path)
	leaf.update_account_number = _stub_update_account_number
	leaf.get_account_autoname = account_autoname
	parts = path.split(".")
	for index in range(1, len(parts)):
		branch = ".".join(parts[:index])
		sys.modules.setdefault(branch, types.ModuleType(branch))
	sys.modules[path] = leaf


def install() -> types.ModuleType:
	"""Put the stub into sys.modules. Idempotent."""
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "__is_stub__", False):
		return sys.modules["frappe"]
	module = _build_frappe()
	module.__is_stub__ = True
	sys.modules["frappe"] = module

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Document
	workflow = types.ModuleType("frappe.model.workflow")
	workflow.get_transitions = _stub_get_transitions
	workflow.apply_workflow = _stub_apply_workflow
	model.document = document
	model.workflow = workflow
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	sys.modules["frappe.model.workflow"] = workflow
	sys.modules["frappe.utils"] = module.utils
	module.model = model

	desk = types.ModuleType("frappe.desk")
	query_report = types.ModuleType("frappe.desk.query_report")
	query_report.run = _stub_query_report_run
	reportview = types.ModuleType("frappe.desk.reportview")
	reportview.get = _stub_reportview_get
	desk.query_report = query_report
	desk.reportview = reportview
	sys.modules["frappe.desk"] = desk
	sys.modules["frappe.desk.query_report"] = query_report
	sys.modules["frappe.desk.reportview"] = reportview
	module.desk = desk
	_install_erpnext_account_api()
	return module


frappe = install()


# ── request plumbing ────────────────────────────────────────────────────────
class FakeRequest:
	def __init__(
		self,
		body="",
		headers=None,
		remote_addr="127.0.0.1",
		method="POST",
		host="test.localhost",
		scheme="https",
	):
		self.headers = {k: v for k, v in (headers or {}).items()}
		self._body = body if isinstance(body, str) else json.dumps(body)
		self.remote_addr = remote_addr
		self.method = method
		self.host = host
		self.scheme = scheme

	def get_data(self, as_text=False):
		return self._body if as_text else self._body.encode()


# ── the base test case ──────────────────────────────────────────────────────
class MCPTestCase(unittest.TestCase):
	"""Resets the fake site, and gives every test a configured-but-off server."""

	TOKEN = "t" * 48

	def setUp(self):
		# Meta first: a test that created a DocType or a Custom Field changed the
		# schema, and `STORE.reset` builds its tabDocType rows from it.
		reset_meta()
		STORE.reset()
		reset_site_files()
		frappe.conf.clear()
		INSTALLED_DOCTYPES.clear()
		INSTALLED_DOCTYPES.update(set(ERPNEXT_SCHEMA) | set(APP_DOCTYPES))
		frappe.local.request = None
		frappe.local.session = FrappeDict(user="Administrator", data=FrappeDict())
		self.configure()

	def configure(self, **overrides):
		"""Write the settings single, defaults from the shipped JSON.

		Starts from what the app itself would seed on install, so a test that
		does not override anything is testing the real out-of-the-box posture.

		Note the values go in as the *strings* the DocType JSON declares, not as
		integers. That is faithful: `tabSingles.value` is a text column, so a
		Check field on a Single reads back as `"0"` — which is truthy in Python
		and is exactly how a switch-is-off bug gets shipped. `settings._as_bool`
		is what stops it, and this fixture is what would catch its removal.
		"""
		values = {}
		for field in META["ERPNext MCP Settings"].fields:
			if field.get("default") not in (None, ""):
				values[field["fieldname"]] = field["default"]
		values.update({"enabled": 1, "doctype": "ERPNext MCP Settings"})
		values.update(overrides)
		STORE.singles["ERPNext MCP Settings"] = values
		STORE.passwords[("ERPNext MCP Settings", "ERPNext MCP Settings", "auth_token")] = self.TOKEN
		return values

	def set_token(self, token):
		STORE.passwords[("ERPNext MCP Settings", "ERPNext MCP Settings", "auth_token")] = token

	def request(self, payload, token=None, headers=None, remote_addr="127.0.0.1", method="POST"):
		"""Point frappe.local.request at a fake request and return it."""
		all_headers = {"Content-Type": "application/json"}
		if token is not False:
			# X-MCP-Token, not Authorization: Bearer — see security.presented_token
			# for why that is the documented header. The Bearer path has its own
			# test rather than being the default the whole suite exercises.
			all_headers["X-MCP-Token"] = token or self.TOKEN
		all_headers.update(headers or {})
		request = FakeRequest(
			body=payload if isinstance(payload, str) else json.dumps(payload),
			headers=all_headers,
			remote_addr=remote_addr,
			method=method,
		)
		frappe.local.request = request
		return request

	def call(self, method, params=None, request_id=1, **kwargs):
		"""POST one JSON-RPC message through the real endpoint. Returns (body, status)."""
		# Imported here rather than at module scope: the stub has to be in
		# sys.modules before anything from erpnext_mcp is loaded.
		from erpnext_mcp import mcp

		message = {"jsonrpc": "2.0", "id": request_id, "method": method}
		if params is not None:
			message["params"] = params
		self.request(message, **kwargs)
		response = mcp.handle()
		# Frappe commits at the end of a served request. Without this the double
		# accumulates uncommitted rows across calls, so a rollback inside call N
		# would discard everything calls 1..N-1 wrote — which is not a thing that
		# can happen on a real site, and made "re-running an import is safe" look
		# like a bug in the app rather than in the double.
		STORE.commit()
		body = response.get_data(as_text=True)
		parsed = json.loads(body) if body.strip() else None
		return parsed, response.status_code

	def tool(self, name, arguments=None, **kwargs):
		"""Call one tool and return its parsed result dict."""
		body, status = self.call("tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)
		self.assertEqual(status, 200, body)
		return body["result"]

	def tool_data(self, name, arguments=None, **kwargs):
		"""Call one tool, assert it succeeded, and return its parsed payload."""
		result = self.tool(name, arguments, **kwargs)
		self.assertFalse(result.get("isError"), f"{name} failed: {result['content'][0]['text']}")
		return json.loads(result["content"][0]["text"])

	def tool_error(self, name, arguments=None, **kwargs):
		"""Call one tool, assert it failed, and return the error text."""
		result = self.tool(name, arguments, **kwargs)
		self.assertTrue(result.get("isError"), f"{name} unexpectedly succeeded: {result}")
		return result["content"][0]["text"]

	# -- convenience assertions ----------------------------------------------
	def audit_rows(self, **filters):
		rows = STORE.rows("MCP Action Log")
		return [row for row in rows if _match(row, filters or None)]

	def assertAudited(self, tool_name, status=None):
		rows = self.audit_rows(tool_name=tool_name)
		self.assertTrue(rows, f"no MCP Action Log row for {tool_name}")
		if status:
			self.assertEqual(rows[-1]["result_status"], status, rows[-1])
		return rows[-1]
