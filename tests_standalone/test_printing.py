# SPDX-License-Identifier: MIT
"""Check printing: the amount in words, and the format that puts it on paper.

TWO HALVES, TESTED DIFFERENTLY.

THE WORDS GET THE HARD TESTS. The amount in words is the one part of a printed
check a person cannot proofread at a glance — the figures are read as figures and
the words are skimmed — so it is asserted at every boundary where written-out
numbers actually go wrong: the teens, the round tens, the hundred with no "and"
after it in American usage, the exact thousand, the scale changes, and the cent
that rounds. It is also the reason the check template calls OUR function and not
`frappe.utils.money_in_words`: Frappe's appends the currency name, and a check
that says "Dollars" where the stock already says DOLLARS is one a teller queries.

THE TEMPLATE GETS RENDERED. Not inspected for substrings — actually rendered,
through Jinja, against a real Payment Entry with real references, and the output
searched for the payee, the figures, the words and the invoice rows. A print
format is a thing that either produces a page or raises an UndefinedError at the
moment somebody presses Print, and only rendering it can tell which.

`jinja2` is not a declared dependency of this app (it arrives with Frappe), so
the rendering tests skip where it is absent and the rest of the file still runs.
"""

import unittest

from erpnext_mcp.render.checks import amount_in_words
from erpnext_mcp.tools import printing

from .fixtures import MAIN, MAIN_ABBR, OTHER, OTHER_ABBR, SeededTestCase
from .harness import STORE

try:  # pragma: no cover - the import IS the check
	import jinja2

	HAS_JINJA = True
except ImportError:  # pragma: no cover
	jinja2 = None
	HAS_JINJA = False

ON = {"allow_create_check_print_format": 1}
FORMAT = f"{MAIN_ABBR} Check — Middle Voucher"


class AmountInWords(unittest.TestCase):
	def assertWords(self, amount, expected):
		self.assertEqual(amount_in_words(amount), expected)

	# -- the shapes that go wrong ---------------------------------------------
	def test_the_teens_are_not_built_out_of_ten_and_a_unit(self):
		self.assertWords(13, "Thirteen and 00/100")
		self.assertWords(19, "Nineteen and 00/100")

	def test_the_round_tens_carry_no_trailing_hyphen(self):
		self.assertWords(20, "Twenty and 00/100")
		self.assertWords(90, "Ninety and 00/100")

	def test_a_compound_ten_is_hyphenated(self):
		self.assertWords(21, "Twenty-One and 00/100")
		self.assertWords(99, "Ninety-Nine and 00/100")

	def test_american_usage_has_no_and_after_the_hundred(self):
		"""'One Hundred and Twenty-Three' is the British reading, and on a check
		the word 'and' introduces the cents and nothing else."""
		self.assertWords(123, "One Hundred Twenty-Three and 00/100")
		self.assertWords(101, "One Hundred One and 00/100")

	def test_an_exact_thousand_does_not_trail_a_zero_remainder(self):
		self.assertWords(1000, "One Thousand and 00/100")
		self.assertWords(2000000, "Two Million and 00/100")

	def test_the_scales_are_short_scale(self):
		self.assertWords(1_000_000_000, "One Billion and 00/100")
		self.assertWords(1_000_000_000_000, "One Trillion and 00/100")

	def test_a_skipped_scale_is_skipped_rather_than_zeroed(self):
		"""1,000,050 has no thousands. 'One Million Zero Thousand Fifty' is what a
		naive recursion produces."""
		self.assertWords(1_000_050, "One Million Fifty and 00/100")

	# -- the cents ------------------------------------------------------------
	def test_the_cents_are_a_two_digit_numerator_over_one_hundred(self):
		self.assertWords(1234.56, "One Thousand Two Hundred Thirty-Four and 56/100")
		self.assertWords(1234.05, "One Thousand Two Hundred Thirty-Four and 05/100")

	def test_a_whole_amount_still_says_the_cents(self):
		"""A words line that stops at the dollars is one somebody can add to."""
		self.assertWords(2030, "Two Thousand Thirty and 00/100")

	def test_a_half_cent_rounds_half_up(self):
		self.assertWords(0.005, "Zero and 01/100")
		self.assertWords(1.995, "Two and 00/100")

	def test_zero_is_written_out_rather_than_left_blank(self):
		self.assertWords(0, "Zero and 00/100")

	def test_a_string_amount_is_read_as_a_number(self):
		"""ERPNext hands Decimals and strings around interchangeably."""
		self.assertWords("1234.56", "One Thousand Two Hundred Thirty-Four and 56/100")

	# -- the currency word -----------------------------------------------------
	def test_a_dollar_currency_adds_no_word_because_the_stock_says_dollars(self):
		self.assertEqual(amount_in_words(5, "USD"), "Five and 00/100")
		self.assertEqual(amount_in_words(5, "CAD"), "Five and 00/100")

	def test_a_non_dollar_currency_says_which_one(self):
		self.assertEqual(amount_in_words(5, "EUR"), "Five and 00/100 EUR")

	# -- refusals --------------------------------------------------------------
	def test_a_negative_amount_is_refused_rather_than_written_as_minus(self):
		with self.assertRaises(ValueError) as caught:
			amount_in_words(-100)
		self.assertIn("no such thing as a check for a negative amount", str(caught.exception))

	def test_an_absurd_amount_is_refused_rather_than_printed(self):
		with self.assertRaises(ValueError) as caught:
			amount_in_words(10**16)
		self.assertIn("data error", str(caught.exception))

	def test_something_that_is_not_a_number_is_refused(self):
		with self.assertRaises(ValueError):
			amount_in_words("three dollars")


class PrintTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)

	def a_payment(self, name="PE-0001", amount=2030.55, references=True, party_name="Sorren Orchards LLC"):
		STORE.seed(
			"Payment Entry",
			[
				{
					"name": name,
					"docstatus": 1,
					"company": MAIN,
					"posting_date": "2026-07-25",
					"payment_type": "Pay",
					"party_type": "Supplier",
					"party": "SUPP-0007",
					"party_name": party_name,
					"paid_amount": amount,
					"paid_from_account_currency": "USD",
					"reference_no": "10428",
					"reference_date": "2026-07-25",
					"remarks": "July orchard services",
					"references": (
						[
							{
								"reference_doctype": "Purchase Invoice",
								"reference_name": "PINV-0042",
								"due_date": "2026-08-10",
								"total_amount": 2030.55,
								"outstanding_amount": 2030.55,
								"allocated_amount": 2030.55,
								"parent": name,
								"parenttype": "Payment Entry",
								"idx": 1,
							}
						]
						if references
						else []
					),
				}
			],
		)
		return name

	# -- rendering -----------------------------------------------------------
	def render(self, name="PE-0001", **kwargs):
		"""Render the stored Print Format the way Frappe's print view would.

		The Jinja globals are the subset the template actually reaches for, built
		from the real functions rather than from mocks — `fmt_money` and
		`formatdate` come off the harness's own `frappe.utils`, and
		`erpnext_mcp_amount_in_words` is the same object the hook registers. A
		render against stubs of those would prove the template parses and nothing
		about what it says.
		"""
		import frappe as frappe_stub

		html = STORE.get_raw("Print Format", kwargs.pop("format_name", FORMAT))["html"]
		doc = frappe_stub.get_doc("Payment Entry", name)
		# `autoescape=False` because that is what Frappe's own print environment
		# does — every stock ERPNext print format relies on it — and a test that
		# escaped where production does not would pass on markup that breaks a
		# real page. `StrictUndefined` is the opposite trade: stricter than Frappe's
		# `DebugUndefined` on purpose, so a field nobody has raises here instead of
		# printing the word "Undefined" onto a check.
		env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
		env.globals["frappe"] = _JinjaFrappe()
		env.globals["erpnext_mcp_amount_in_words"] = amount_in_words
		return env.from_string(html).render(doc=doc)


class _JinjaFrappe:
	"""The `frappe` namespace a print format sees, in the parts this one uses.

	Frappe builds this through `safe_exec.get_safe_globals`, which copies every
	function out of `frappe.utils.data` into a `frappe.utils` namespace. Only
	three of them appear in the check template, so only three are here — and they
	are real implementations, because a formatter that returned a placeholder
	would make every assertion below about the placeholder.
	"""

	class utils:
		@staticmethod
		def fmt_money(value, currency="USD", **kwargs):
			return f"{float(value or 0):,.2f}"

		@staticmethod
		def formatdate(value, fmt=None):
			text = str(value or "")
			if len(text) >= 10 and text[4] == "-":
				return f"{text[5:7]}/{text[8:10]}/{text[0:4]}"
			return text

		@staticmethod
		def money_in_words(value, currency="USD", **kwargs):  # pragma: no cover - fallback path
			return f"{float(value or 0):,.2f} {currency} only"


@unittest.skipUnless(HAS_JINJA, "needs jinja2, which arrives with Frappe on a real bench")
class TheFormatRenders(PrintTestCase):
	def test_it_renders_against_a_submitted_payment_entry_without_raising(self):
		"""StrictUndefined: a template that reaches for a field nobody has raises
		here rather than at the moment somebody presses Print."""
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("<div", self.render())

	def test_the_payee_line_uses_the_suppliers_full_name(self):
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		html = self.render()
		self.assertIn("Sorren Orchards LLC", html)
		self.assertIn("PAY TO THE ORDER OF", html)

	def test_the_payee_falls_back_to_the_docname_when_there_is_no_full_name(self):
		"""A blank payee line above a real amount is a check somebody can fill in."""
		self.a_payment(party_name="")
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("SUPP-0007", self.render())

	def test_the_amount_appears_in_figures_and_in_words(self):
		self.a_payment(amount=2030.55)
		self.tool_data("create_check_print_format", {"company": MAIN})
		html = self.render()
		self.assertIn("2,030.55", html)
		self.assertIn("Two Thousand Thirty and 55/100", html)

	def test_the_words_line_is_filled_out_to_the_end(self):
		"""So nobody can add to it after it is signed."""
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("*" * 40, self.render())

	def test_the_check_date_prefers_the_reference_date(self):
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("07/25/2026", self.render())

	def test_both_stubs_carry_the_invoice_detail(self):
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		html = self.render()
		self.assertEqual(html.count("PINV-0042"), 2)
		self.assertEqual(html.count("Payee copy"), 1)
		self.assertEqual(html.count("File copy"), 1)

	def test_a_payment_with_no_references_falls_back_to_the_remark(self):
		"""An off-invoice payment — a utility bill, a deposit — is the ordinary
		case for a farm, and a stub that raised on it would be useless. The remark
		is what the payee gets told instead of an invoice number."""
		self.a_payment(references=False)
		self.tool_data("create_check_print_format", {"company": MAIN})
		html = self.render()
		self.assertEqual(html.count("July orchard services"), 3)  # memo + two stubs
		self.assertIn("2,030.55", html)

	def test_a_payment_with_neither_references_nor_a_remark_says_so(self):
		self.a_payment(references=False)
		STORE.get_raw("Payment Entry", "PE-0001")["remarks"] = ""
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertEqual(
			self.render().count("No invoice references on this payment."), 2
		)

	def test_the_memo_carries_the_remark(self):
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("July orchard services", self.render())

	def test_a_signature_image_is_rendered_when_one_was_given(self):
		self.a_payment()
		self.tool_data(
			"create_check_print_format",
			{"company": MAIN, "signature_image_url": "/private/files/sig.png"},
		)
		self.assertIn('src="/private/files/sig.png"', self.render())

	def test_without_a_signature_image_only_the_line_prints(self):
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		html = self.render()
		self.assertIn("AUTHORIZED SIGNATURE", html)
		self.assertNotIn("<img", html)

	def test_a_signature_url_cannot_break_out_of_its_attribute(self):
		"""It reaches the template from a tool argument and lands inside src="…"."""
		self.a_payment()
		self.tool_data(
			"create_check_print_format",
			{"company": MAIN, "signature_image_url": '/f.png" onerror="alert(1)'},
		)
		html = self.render()
		self.assertNotIn('onerror="alert(1)"', html)
		self.assertIn("&quot;", STORE.get_raw("Print Format", FORMAT)["html"])

	# -- the panel geometry ---------------------------------------------------
	def test_the_page_is_three_panels_of_three_and_a_half_inches(self):
		self.a_payment()
		self.tool_data("create_check_print_format", {"company": MAIN})
		html = self.render()
		self.assertEqual(html.count('class="chk-panel'), 3)
		self.assertIn("height: 3.5in", html)


class TheTool(PrintTestCase):
	def test_it_creates_a_custom_jinja_format_on_payment_entry(self):
		data = self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertEqual(data["action"], "created")
		self.assertEqual(data["print_format"], FORMAT)
		row = STORE.get_raw("Print Format", FORMAT)
		self.assertEqual(row["doc_type"], "Payment Entry")
		self.assertEqual(row["standard"], "No")
		self.assertEqual(row["print_format_type"], "Jinja")
		self.assertEqual(row["page_size"], "Letter")

	def test_the_margins_are_zero_because_the_stock_is_preprinted(self):
		self.tool_data("create_check_print_format", {"company": MAIN})
		row = STORE.get_raw("Print Format", FORMAT)
		for field in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
			with self.subTest(field=field):
				self.assertEqual(row[field], 0)

	def test_it_names_itself_after_the_company_abbreviation(self):
		self.tool_data("create_check_print_format", {"company": OTHER})
		self.assertTrue(STORE.get_raw("Print Format", f"{OTHER_ABBR} Check — Middle Voucher"))

	def test_two_companies_get_two_formats(self):
		"""ERPNext does not scope Print Formats by company, so the name is what
		tells them apart."""
		self.tool_data("create_check_print_format", {"company": MAIN})
		self.tool_data("create_check_print_format", {"company": OTHER})
		self.assertEqual(len(STORE.rows("Print Format")), 2)

	def test_a_custom_name_is_used_as_given(self):
		data = self.tool_data(
			"create_check_print_format", {"company": MAIN, "format_name": "Payroll Checks"}
		)
		self.assertEqual(data["print_format"], "Payroll Checks")

	def test_a_re_run_reports_unchanged_and_writes_nothing(self):
		self.tool_data("create_check_print_format", {"company": MAIN})
		before = dict(STORE.get_raw("Print Format", FORMAT))
		data = self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertEqual(data["action"], "unchanged")
		self.assertEqual(STORE.get_raw("Print Format", FORMAT)["html"], before["html"])

	def test_changing_the_payee_field_updates_the_format(self):
		self.tool_data("create_check_print_format", {"company": MAIN})
		data = self.tool_data(
			"create_check_print_format", {"company": MAIN, "payee_field": "party"}
		)
		self.assertEqual(data["action"], "updated")
		self.assertIn("doc.party or", STORE.get_raw("Print Format", FORMAT)["html"])

	def test_it_says_micr_is_not_rendered(self):
		"""Because somebody will ask, and the answer is load-bearing."""
		data = self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("NOT rendered", data["micr"])
		self.assertIn("magnetic ink", data["micr"])

	def test_it_names_the_stock_to_buy(self):
		data = self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertIn("Deluxe form 1000/9000", data["stock"])
		self.assertIn("3.5-inch panels", data["stock"])


class ToolRefusals(PrintTestCase):
	def test_a_company_that_does_not_exist_is_refused(self):
		self.assertIn(
			"Nowhere", self.tool_error("create_check_print_format", {"company": "Nowhere Ltd"})
		)

	def test_a_payee_field_this_site_lacks_is_refused_rather_than_printing_blank(self):
		message = self.tool_error(
			"create_check_print_format", {"company": MAIN, "payee_field": "supplier_full_name"}
		)
		self.assertIn("no field 'supplier_full_name'", message)
		self.assertIn("print blank on every check", message)
		self.assertEqual(STORE.rows("Print Format"), [])

	def test_a_standard_format_is_never_overwritten(self):
		"""A standard format is rewritten from an app's files on every migrate, so
		anything written into one disappears at the next upgrade without a word."""
		STORE.seed(
			"Print Format",
			[{"name": FORMAT, "doc_type": "Payment Entry", "standard": "Yes", "html": "<p>theirs</p>"}],
		)
		message = self.tool_error("create_check_print_format", {"company": MAIN})
		self.assertIn("STANDARD format", message)
		self.assertIn("disappear at the next upgrade", message)
		self.assertEqual(STORE.get_raw("Print Format", FORMAT)["html"], "<p>theirs</p>")

	def test_a_format_pointed_at_another_doctype_is_refused(self):
		STORE.seed(
			"Print Format",
			[{"name": FORMAT, "doc_type": "Journal Entry", "standard": "No", "html": "<p>x</p>"}],
		)
		message = self.tool_error("create_check_print_format", {"company": MAIN})
		self.assertIn("prints 'Journal Entry'", message)
		self.assertIn("format_name", message)

	def test_a_non_dollar_company_gets_a_warning_rather_than_a_refusal(self):
		"""The layout assumes DOLLARS preprinted. Saying so beats refusing: a
		company can perfectly well have a currency and US stock."""
		STORE.get_raw("Company", MAIN)["default_currency"] = "EUR"
		data = self.tool_data("create_check_print_format", {"company": MAIN})
		self.assertEqual(data["action"], "created")
		self.assertIn("EUR", data["warning"])
		self.assertIn("DOLLARS preprinted", data["warning"])


class DryRun(PrintTestCase):
	def test_it_reports_the_plan_and_writes_nothing(self):
		data = self.tool_data("create_check_print_format", {"company": MAIN, "dry_run": True})
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["action"], "created")
		self.assertGreater(data["html_characters"], 1000)
		self.assertEqual(STORE.rows("Print Format"), [])

	def test_a_dry_run_over_an_existing_format_says_unchanged(self):
		self.tool_data("create_check_print_format", {"company": MAIN})
		data = self.tool_data("create_check_print_format", {"company": MAIN, "dry_run": True})
		self.assertEqual(data["action"], "unchanged")

	def test_a_dry_run_still_refuses_a_bad_payee_field(self):
		self.assertIn(
			"no field 'nope'",
			self.tool_error(
				"create_check_print_format",
				{"company": MAIN, "payee_field": "nope", "dry_run": True},
			),
		)


class SwitchAndAvailability(PrintTestCase):
	def test_it_is_off_out_of_the_box(self):
		self.configure(enabled=1)
		self.assertIn(
			"allow_create_check_print_format",
			self.tool_error("create_check_print_format", {"company": MAIN}),
		)

	def test_it_is_declared_mutating(self):
		from erpnext_mcp import registry

		self.assertIn("create_check_print_format", registry.MUTATING_TOOLS)

	def test_a_site_without_payment_entry_does_not_advertise_it(self):
		from erpnext_mcp import registry

		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Payment Entry")
		self.assertFalse(registry.is_available("create_check_print_format"))
		self.assertIn(
			"Payment Entry",
			self.tool_error("create_check_print_format", {"company": MAIN}),
		)


class TheTemplateItself(unittest.TestCase):
	def test_the_payee_field_token_is_substituted(self):
		html = printing.build_check_html("party")
		self.assertIn("doc.party or doc.party", html)
		self.assertNotIn("__PAYEE_FIELD__", html)

	def test_the_signature_token_is_substituted_even_when_empty(self):
		self.assertNotIn("__SIGNATURE__", printing.build_check_html())

	def test_the_template_carries_no_company_specific_name(self):
		"""This app is installed on other people's sites; a template naming one
		farm would be a snapshot of one install frozen into all of them."""
		lowered = printing.CHECK_TEMPLATE.lower()
		for word in ("oml", "orchard meadow", "polehn", "sorren"):
			with self.subTest(word=word):
				self.assertNotIn(word, lowered)

	def test_it_falls_back_to_frappes_own_words_when_the_hook_did_not_take(self):
		"""A check with no amount in words is not a check."""
		self.assertIn("erpnext_mcp_amount_in_words is defined", printing.CHECK_TEMPLATE)
		self.assertIn("frappe.utils.money_in_words", printing.CHECK_TEMPLATE)

	def test_the_jinja_hook_is_declared_under_both_names(self):
		"""Frappe renamed this hook from `jenv` to `jinja`, and which one a bench
		reads depends on its version."""
		from erpnext_mcp import hooks

		target = "erpnext_mcp_amount_in_words:erpnext_mcp.render.checks.amount_in_words"
		self.assertIn(target, hooks.jinja["methods"])
		self.assertIn(target, hooks.jenv["methods"])
