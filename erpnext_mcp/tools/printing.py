# SPDX-License-Identifier: MIT
"""Cutting a printed check out of ERPNext, on stock you buy off the shelf.

WHY THIS EXISTS. Sorren's monthly invoice, the utilities, the occasional vendor
who does not take an ACH — those get paid by check, and until now they got paid
by somebody writing one out by hand and then keying it into the ledger
afterwards. The ledger is the thing that ends up wrong: a hand-written check is
a Payment Entry somebody has to remember to make, with a number they have to
read off a stub.

WHAT ERPNext ALREADY DOES, AND WHAT IT DOES NOT. Payment Entry IS ERPNext's
check-cutting document: it carries the party, the amount, the bank account, the
reference number and the invoices being settled, and it posts the ledger side
itself. What it has no opinion about is where any of that lands on a piece of
paper. That is a Print Format, and ERPNext ships none that fits US laser check
stock — its own Cheque Print Template is built around a different (largely
Indian) stock layout with the fields positioned by pixel offsets somebody
measures by hand.

THE LAYOUT. Three panels of 3.5 inches on US Letter: the check itself on top, a
remittance stub in the middle, a second remittance stub at the bottom. That is
the commonest US business format — Deluxe form 1000/9000 and the Costco and
Intuit equivalents of it — and it is the one where the middle voucher tears off
for the payee and the bottom stays in the file. The check panel carries date,
payee, the amount in figures in a box, the amount in words, the memo and a
signature line; the stubs carry the invoice-by-invoice detail that answers "what
was this for" without anybody having to ring up and ask.

MICR IS NOT RENDERED, AND THAT IS NOT A LIMITATION. The routing and account
numbers along the bottom of a check are printed in magnetic ink on the stock you
buy, by the people who sold it to you, against your account. Rendering them from
here would mean putting a bank account number into an HTML template on a server,
printing it in a font that is not magnetic, on paper that is not the right
weight, and having it rejected at the branch. Buy the stock preprinted. The
README has the ordering notes.

WHY THE TEMPLATE IS A CONSTANT AND THE PRINT FORMAT IS A RECORD. A Print Format
shipped as an app fixture would be one record with one name on every site that
installs this, and its `standard = "Yes"` would mean an operator's edits are
overwritten on the next `bench migrate`. So the HTML lives here and
`create_check_print_format` instantiates it per Company as a CUSTOM format named
after the company's abbreviation. Two companies on one site get two formats, each
naming its own company, and an operator who tunes the margins for their printer
keeps the tuning.
"""

import frappe

from .. import compat
from ..args import as_bool, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..render.checks import amount_in_words

PRINT_FORMAT = "Print Format"
CHECK_DOCTYPE = "Payment Entry"

#: What the format is called when the caller does not say. The abbreviation goes
#: first because a Print Format list is sorted by name and an operator with four
#: companies wants theirs together.
DEFAULT_FORMAT_SUFFIX = "Check — Middle Voucher"

#: The Payment Entry field the payee line is taken from. `party_name` is the
#: supplier's full name where ERPNext has one and empty where it does not, which
#: is why the template falls back to `party` — the docname — rather than printing
#: a blank line above the amount.
DEFAULT_PAYEE_FIELD = "party_name"

#: US Letter, and not configurable through this tool. Check stock is Letter; a
#: format that offered A4 would offer a way to print a check that does not line
#: up with the paper it is printed on.
PAGE_SIZE = "Letter"

#: The template, with two tokens substituted at creation time. Deliberately one
#: string rather than a file: it is version-controlled beside the tool that
#: writes it, and a site that has edited its own copy keeps the edit because this
#: only overwrites when asked.
#:
#: THE AMOUNT IN WORDS HAS A FALLBACK, and v0.14.1 is why it earns its place.
#: `erpnext_mcp_amount_in_words` reaches Jinja through the `jinja` hook in
#: hooks.py — as a BARE dotted path, taking its Jinja name from the callable's
#: own `__name__`. v0.14.0 wrote the older `jenv` hook's `"<name>:<path>"` syntax
#: under that key instead, `frappe.get_attr` was handed the whole string, and
#: since Frappe builds the Jinja environment to render the error page too, every
#: page on a live site returned 500. The guard below is what a check still prints
#: through when the method is not registered for any reason at all: Frappe's own
#: `money_in_words` is wordier and says "Dollars" where the stock already says
#: DOLLARS, but it is a valid check, and a check with no amount in words is not.
CHECK_TEMPLATE = """
<style>
  .chk-check-page { width: 8.5in; margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif;
                    color: #000; font-size: 10.5pt; }
  .chk-panel { height: 3.5in; box-sizing: border-box; padding: 0.30in 0.45in; position: relative;
               page-break-inside: avoid; }
  .chk-check { }
  .chk-stub { border-top: 1px dashed #999; font-size: 9pt; }
  .chk-date { position: absolute; top: 0.62in; right: 0.55in; text-align: right; }
  .chk-date .lbl { font-size: 7.5pt; letter-spacing: .06em; color: #333; }
  .chk-payee { position: absolute; top: 1.28in; left: 0.72in; right: 1.85in; }
  .chk-payee .name { font-size: 12pt; font-weight: 600; }
  .chk-figures { position: absolute; top: 1.20in; right: 0.50in; border: 1px solid #000;
                 padding: 3px 8px; font-size: 12pt; font-weight: 700; min-width: 1.05in;
                 text-align: right; }
  .chk-words { position: absolute; top: 1.72in; left: 0.55in; right: 1.35in;
               border-bottom: 1px solid #000; padding-bottom: 1px; font-size: 10pt; }
  .chk-memo { position: absolute; bottom: 0.48in; left: 0.55in; width: 3.1in;
              border-top: 1px solid #000; padding-top: 2px; font-size: 8.5pt; }
  .chk-sign { position: absolute; bottom: 0.48in; right: 0.55in; width: 3.0in;
              border-top: 1px solid #000; padding-top: 2px; font-size: 7.5pt; text-align: center;
              color: #333; }
  .chk-sign img { max-height: 0.42in; display: block; margin: -0.50in auto 0.04in auto; }
  .chk-stub-head { display: flex; justify-content: space-between; font-weight: 600;
                   border-bottom: 1px solid #000; padding-bottom: 3px; margin-bottom: 5px; }
  .chk-stub table { width: 100%; border-collapse: collapse; }
  .chk-stub th { text-align: left; font-size: 8pt; border-bottom: 1px solid #666; padding: 2px 4px; }
  .chk-stub td { padding: 2px 4px; vertical-align: top; }
  .chk-num { text-align: right; white-space: nowrap; }
  .chk-total { border-top: 1px solid #000; font-weight: 700; }
  .chk-fill { color: #666; letter-spacing: .02em; }
</style>

{%- set payee = doc.__PAYEE_FIELD__ or doc.party or "" -%}
{%- set currency = doc.paid_from_account_currency or doc.paid_to_account_currency or "USD" -%}
{%- if erpnext_mcp_amount_in_words is defined -%}
  {%- set words = erpnext_mcp_amount_in_words(doc.paid_amount, currency) -%}
{%- else -%}
  {%- set words = frappe.utils.money_in_words(doc.paid_amount, currency) -%}
{%- endif -%}
{%- set check_date = doc.reference_date or doc.posting_date -%}
{%- set figures = frappe.utils.fmt_money(doc.paid_amount, currency=currency) -%}

<div class="chk-check-page">

  <div class="chk-panel chk-check">
    <div class="chk-date">
      <div class="lbl">DATE</div>
      <div>{{ frappe.utils.formatdate(check_date, "MM/dd/yyyy") }}</div>
    </div>
    <div class="chk-payee">
      <div class="lbl" style="font-size:7.5pt;color:#333;">PAY TO THE ORDER OF</div>
      <div class="name">{{ payee }}</div>
    </div>
    <div class="chk-figures">{{ figures }}</div>
    <div class="chk-words">{{ words }}<span class="chk-fill">
      {{ "*" * 40 }}</span></div>
    <div class="chk-memo"><strong>MEMO</strong>
      {{ (doc.remarks or doc.custom_memo or "")[:70] }}</div>
    <div class="chk-sign">__SIGNATURE__AUTHORIZED SIGNATURE</div>
  </div>

  {%- for panel in ["Payee copy", "File copy"] %}
  <div class="chk-panel chk-stub">
    <div class="chk-stub-head">
      <span>{{ doc.company }}</span>
      <span>{{ payee }}</span>
      <span>{{ frappe.utils.formatdate(check_date, "MM/dd/yyyy") }}</span>
      <span>Check {{ doc.reference_no or doc.name }}</span>
    </div>
    <table>
      <thead>
        <tr><th>Document</th><th>Date</th><th class="chk-num">Invoice</th>
            <th class="chk-num">Outstanding</th><th class="chk-num">Paid</th></tr>
      </thead>
      <tbody>
      {%- for row in doc.references or [] %}
        <tr>
          <td>{{ row.reference_doctype }} {{ row.reference_name }}</td>
          <td>{{ frappe.utils.formatdate(row.due_date, "MM/dd/yyyy") if row.due_date else "" }}</td>
          <td class="chk-num">{{ frappe.utils.fmt_money(row.total_amount, currency=currency) }}</td>
          <td class="chk-num">{{ frappe.utils.fmt_money(row.outstanding_amount, currency=currency) }}</td>
          <td class="chk-num">{{ frappe.utils.fmt_money(row.allocated_amount, currency=currency) }}</td>
        </tr>
      {%- else %}
        <tr><td colspan="5">{{ doc.remarks or "No invoice references on this payment." }}</td></tr>
      {%- endfor %}
        <tr class="chk-total">
          <td colspan="4">Total paid — {{ words }}</td>
          <td class="chk-num">{{ figures }}</td>
        </tr>
      </tbody>
    </table>
    <div style="margin-top:4px;font-size:7.5pt;color:#555;">{{ panel }}</div>
  </div>
  {%- endfor %}

</div>
""".strip()


# ── 135. create_check_print_format ──────────────────────────────────────────
def create_check_print_format(args: dict) -> ToolResult:
	"""Create or update the check Print Format for one Company.

	IDEMPOTENT, AND CAREFUL ABOUT WHAT IT OVERWRITES. Re-running with the same
	arguments produces identical HTML and reports `unchanged`. A format whose HTML
	somebody has edited by hand IS overwritten — that is what asking for it again
	means — but only after the result says how many characters changed, and never
	for a format ERPNext or another app ships (`standard = "Yes"`), because that
	one is replaced on the next migrate anyway and an edit to it is an edit that
	will silently disappear.

	IT REFUSES A FORMAT POINTED AT ANOTHER DOCTYPE. A Print Format named the same
	thing but built for Journal Entry is somebody else's work, and quietly
	repointing it at Payment Entry would break whatever used it.
	"""
	compat.require_doctype(
		CHECK_DOCTYPE,
		"Payment Entry is ERPNext's check-cutting document and ships with its Accounts module.",
	)
	company = resolve_company(as_str(args, "company"), required=True)
	row = (
		frappe.db.get_value(
			"Company", company, compat.existing_fields("Company", ("name", "abbr", "default_currency")), as_dict=True
		)
		or {}
	)
	abbr = str(row.get("abbr") or "").strip()
	format_name = as_str(args, "format_name") or f"{abbr} {DEFAULT_FORMAT_SUFFIX}".strip()
	payee_field = as_str(args, "payee_field") or DEFAULT_PAYEE_FIELD
	signature_image_url = as_str(args, "signature_image_url")
	dry_run = bool(as_bool(args, "dry_run", False))

	if not compat.has_field(CHECK_DOCTYPE, payee_field):
		raise ToolError(
			f"Payment Entry has no field {payee_field!r} on this site, so the payee line would "
			"print blank on every check. The usual answer is 'party_name' — the supplier's full "
			"name where ERPNext has one — and the template already falls back to `party` when "
			"that is empty. Nothing was created."
		)

	html = build_check_html(payee_field, signature_image_url)
	existing = frappe.db.get_value(
		PRINT_FORMAT,
		format_name,
		compat.existing_fields(PRINT_FORMAT, ("name", "doc_type", "standard", "html", "disabled")),
		as_dict=True,
	)
	if existing:
		_assert_replaceable(existing, format_name)

	action = "unchanged" if existing and str(existing.get("html") or "") == html else (
		"updated" if existing else "created"
	)
	currency = str(row.get("default_currency") or "").strip().upper()

	data = {
		"print_format": format_name,
		"company": company,
		"company_abbr": abbr,
		"doc_type": CHECK_DOCTYPE,
		"payee_field": payee_field,
		"signature_image_url": signature_image_url or None,
		"page_size": PAGE_SIZE,
		"stock": (
			"US laser check stock, 8.5×11, three 3.5-inch panels: check on top, remittance stub "
			"in the middle, remittance stub at the bottom. Deluxe form 1000/9000 and the Costco "
			"and Intuit equivalents. See the README."
		),
		"micr": (
			"NOT rendered. The routing and account numbers are printed in magnetic ink on the "
			"stock by whoever sold it to you, against your account. Nothing here writes them."
		),
		"action": action,
		"html_characters": len(html),
		"dry_run": dry_run,
		"next_step": (
			f"Open a Payment Entry for {company}, press Print, and choose {format_name!r}. Set it "
			"as the default for Payment Entry in Print Settings if it is the only one you use. "
			"Run a check against blank paper first and hold it over a real one at a window — "
			"printers disagree about the top margin by a few hundredths of an inch, and that is "
			"what `margin_top` on the Print Format is for."
		),
	}
	if currency and currency not in ("USD", "CAD"):
		data["warning"] = (
			f"{company}'s default currency is {currency}. This layout assumes stock with DOLLARS "
			"preprinted at the end of the amount line, so the written amount will carry the "
			"currency code after it and the preprinted word will be wrong. Use stock printed for "
			f"{currency}, or a format laid out for it."
		)

	if dry_run:
		data["note"] = (
			f"Nothing was written. {format_name!r} would be {action}. The `html` in this result's "
			"length is what would be stored."
		)
		return ToolResult(
			data,
			f"dry run: would {action.rstrip('d')}e Print Format {format_name!r} for {company} "
			f"({len(html)} characters of HTML)"
			if action != "unchanged"
			else f"dry run: Print Format {format_name!r} is already exactly this",
		)

	if action == "unchanged":
		data["note"] = (
			f"{format_name!r} already holds exactly this template, so nothing was written. "
			"Re-running this tool is safe."
		)
		return ToolResult(data, f"Print Format {format_name!r} was already up to date", docstatus_delta="")

	if existing:
		doc = frappe.get_doc(PRINT_FORMAT, format_name)
		for field, value in _print_format_fields(html).items():
			if compat.has_field(PRINT_FORMAT, field):
				doc.set(field, value)
		doc.save()
	else:
		payload = {"doctype": PRINT_FORMAT, "name": format_name}
		if compat.has_field(PRINT_FORMAT, "print_format_name"):
			payload["print_format_name"] = format_name
		payload.update(
			{
				field: value
				for field, value in _print_format_fields(html).items()
				if compat.has_field(PRINT_FORMAT, field)
			}
		)
		doc = frappe.get_doc(payload).insert()

	data["print_format"] = doc.name
	data["note"] = (
		f"Print Format {doc.name} is a CUSTOM format (standard = No), so `bench migrate` will "
		"never overwrite it and any margin you tune for your printer survives. It is scoped to "
		f"{company} only by its name and by what it prints — ERPNext does not scope Print Formats "
		"by company, so a second company needs its own call and gets its own format."
	)
	return ToolResult(
		data,
		f"{action} Print Format {doc.name!r} for {company}: Payment Entry on US laser check "
		f"stock, payee from {payee_field}"
		+ (", with a signature image" if signature_image_url else ""),
		docstatus_delta="none → 0 (created)" if action == "created" else "",
	)


def build_check_html(payee_field: str = DEFAULT_PAYEE_FIELD, signature_image_url: str = "") -> str:
	"""The template with the caller's two choices substituted in.

	Token replacement rather than `str.format`, because the template is Jinja and
	Jinja's `{{ }}` and `{% %}` are exactly what `format` would try to interpret.
	"""
	signature = (
		f'<img src="{_escape(signature_image_url)}" alt="signature">' if signature_image_url else ""
	)
	return CHECK_TEMPLATE.replace("__PAYEE_FIELD__", payee_field).replace("__SIGNATURE__", signature)


def _escape(value: str) -> str:
	"""Enough escaping for a URL going into an HTML attribute.

	The value reaches here from a tool argument and ends up inside `src="..."` in
	a template stored on the site, so a quote in it would close the attribute and
	everything after would be markup. Narrow on purpose: this is an attribute
	value, not a document.
	"""
	return (
		str(value or "")
		.replace("&", "&amp;")
		.replace('"', "&quot;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
	)


def _print_format_fields(html: str) -> dict:
	"""Everything this app sets on a Print Format, filtered by the caller's version.

	Margins are zero because check stock is preprinted: the panels have to land
	where the perforations are, and a print format that added half an inch at the
	top would push the signature line onto the first stub.
	"""
	return {
		"doc_type": CHECK_DOCTYPE,
		"module": "ERPNext MCP",
		"standard": "No",
		"custom_format": 1,
		"print_format_type": "Jinja",
		"print_format_builder": 0,
		"disabled": 0,
		"page_size": PAGE_SIZE,
		"margin_top": 0,
		"margin_bottom": 0,
		"margin_left": 0,
		"margin_right": 0,
		"align_labels_right": 0,
		"line_breaks": 0,
		"show_section_headings": 0,
		"html": html,
	}


def _assert_replaceable(existing: dict, format_name: str) -> None:
	if str(existing.get("doc_type") or "") != CHECK_DOCTYPE:
		raise ToolError(
			f"Print Format {format_name!r} already exists on this site and prints "
			f"{existing.get('doc_type')!r}, not Payment Entry. Repointing somebody else's format "
			"at a different doctype would break whatever prints with it. Pass a `format_name` of "
			"your own. Nothing was created."
		)
	if str(existing.get("standard") or "").lower() == "yes":
		raise ToolError(
			f"Print Format {format_name!r} is a STANDARD format — it belongs to an app and is "
			"rewritten from that app's files on every `bench migrate`, so anything written here "
			"would disappear at the next upgrade without a word. Pass a `format_name` of your own "
			"and the new one will be a custom format that survives. Nothing was created."
		)


#: Exported so the tests can check the words the check actually carries against
#: the same function the template calls, rather than against a second copy of the
#: rule that could drift from it.
__all__ = ("create_check_print_format", "build_check_html", "amount_in_words", "CHECK_TEMPLATE")
