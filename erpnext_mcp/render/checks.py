# SPDX-License-Identifier: MIT
"""Amount in words, US check convention, and the check layout that uses it.

WHY THIS IS NOT `frappe.utils.money_in_words`. Frappe's version is a general
internationalised helper: it appends the currency's own name ("One Thousand
Two Hundred Thirty Four Dollars and Fifty Six Cents Only"), it varies with the
site's number format, and on an Indian-format site it groups in lakhs. Every one
of those is wrong on a US laser check, where the amount line ends in a
preprinted **DOLLARS** and the fraction is written over one hundred rather than
spelled out. A check whose words say "Dollars" twice is a check a teller can
query, and a check that reads "Twelve Lakh" is one a US bank will not take.

THE CONVENTION, PRECISELY. `1234.56` becomes

    One Thousand Two Hundred Thirty-Four and 56/100

with no currency word, no "Only", a hyphen inside the compound tens, and the
cents as a two-digit numerator over 100. A whole-dollar amount gets `00/100`
rather than nothing, because a words line that stops at the dollars is a line
somebody can add to. The template that consumes this fills the rest of the line
with asterisks for the same reason.

WHY IT IS TESTED SEPARATELY FROM THE TEMPLATE. This is the one part of a printed
check that a person cannot proofread at a glance against the numeric amount — the
digits are read as digits and the words are skimmed. So it gets its own tests, at
the boundaries where written-out numbers actually go wrong: the teens, the round
tens, the hundred that has no "and" before it in American usage, the exact
thousand, and the cent that rounds.

NO CURRENCY SYMBOL AND NO ROUNDING POLICY. The caller passes what is on the
Payment Entry. Half-cents are rounded half-up, which is what every accounting
system this will ever face already did before the number reached here.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_UNITS = (
	"Zero",
	"One",
	"Two",
	"Three",
	"Four",
	"Five",
	"Six",
	"Seven",
	"Eight",
	"Nine",
	"Ten",
	"Eleven",
	"Twelve",
	"Thirteen",
	"Fourteen",
	"Fifteen",
	"Sixteen",
	"Seventeen",
	"Eighteen",
	"Nineteen",
)

_TENS = (
	"",
	"",
	"Twenty",
	"Thirty",
	"Forty",
	"Fifty",
	"Sixty",
	"Seventy",
	"Eighty",
	"Ninety",
)

#: Short scale, which is what a US bank reads. Deliberately stops at trillion:
#: past that the number is not a check.
_SCALES = ((10**12, "Trillion"), (10**9, "Billion"), (10**6, "Million"), (1000, "Thousand"))

#: The largest amount this will write out. A check for more than a trillion
#: dollars is a data error, and returning a plausible-looking string for one
#: would print it.
MAX_AMOUNT = Decimal(10) ** 15


def amount_in_words(amount, currency: str = "USD") -> str:
	"""`1234.56` → `"One Thousand Two Hundred Thirty-Four and 56/100"`.

	`currency` is accepted and ignored for USD and every other dollar currency,
	because US and Canadian laser check stock has DOLLARS preprinted at the end of
	the line. For anything else the currency code is appended, so a check drawn in
	a currency the stock was not printed for says which currency it is rather than
	silently reading as dollars.

	Registered as a Jinja method (`erpnext_mcp_amount_in_words`) so the shipped
	check Print Format can call it. See `hooks.py`.
	"""
	try:
		value = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
	except (InvalidOperation, ValueError, ArithmeticError):
		raise ValueError(f"amount_in_words cannot read {amount!r} as a number") from None

	if value < 0:
		# A negative check is not a thing. Saying so beats printing "Minus One
		# Hundred", which a teller would have to interpret.
		raise ValueError(
			f"amount_in_words was given {value}, and there is no such thing as a check for a "
			"negative amount. A refund from a payee is money coming in, not a check going out."
		)
	if value >= MAX_AMOUNT:
		raise ValueError(
			f"amount_in_words was given {value}, past the trillion this writes out. An amount "
			"that large on a check is a data error, and printing a plausible-looking one would "
			"put it on paper."
		)

	dollars = int(value)
	cents = int((value - dollars) * 100)
	words = f"{_whole(dollars)} and {cents:02d}/100"
	code = str(currency or "USD").strip().upper()
	if code and code not in _DOLLAR_CODES:
		words = f"{words} {code}"
	return words


#: Currencies whose stock says DOLLARS. Anything else gets its code appended.
_DOLLAR_CODES = frozenset({"", "USD", "CAD", "AUD", "NZD"})


def _whole(number: int) -> str:
	"""A non-negative integer in words, US short scale, no "and" before the tens.

	American usage has no conjunction between the hundreds and what follows —
	"One Hundred Twenty-Three", not "One Hundred and Twenty-Three". The `and` on a
	check belongs to the cents and to nothing else, which is what makes the whole
	line unambiguous.
	"""
	if number < 20:
		return _UNITS[number]
	for size, name in _SCALES:
		if number >= size:
			head, tail = divmod(number, size)
			return f"{_whole(head)} {name}" + (f" {_whole(tail)}" if tail else "")
	if number >= 100:
		head, tail = divmod(number, 100)
		return f"{_UNITS[head]} Hundred" + (f" {_whole(tail)}" if tail else "")
	tens, units = divmod(number, 10)
	return _TENS[tens] + (f"-{_UNITS[units]}" if units else "")
