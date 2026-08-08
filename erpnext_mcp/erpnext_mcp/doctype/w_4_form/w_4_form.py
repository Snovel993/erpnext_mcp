# SPDX-License-Identifier: MIT
"""W-4 Form controller.

Dependents credits are auto-calculated on every save:
  under_17 × the year's credit + other × $500 = total_dependents_credit.

THE UNDER-17 CREDIT IS $2,200 ON THE 2026 FORM. The 2020-2025 editions of Step 3
said "Multiply the number of qualifying children under age 17 by $2,000"; the
2026 edition says $2,200. This controller computed a flat $2,000 until v0.48.1,
which under-credited every 2026 filing by $200 a child — and `w4_pdf.py` prints
whatever the row holds, so the government form went out with the wrong number in
box 3 as well as the withholding engine reading it out of `withholding.py`.

THE AMOUNT IS KEYED TO THE ROW'S OWN `tax_year`, NOT TO TODAY'S. A W-4 is filed
against an edition of the form, and this app keeps one Active W-4 per employee
per tax year — so a 2025 form that gets re-saved in 2026 (a corrected count, a
superseding edit) must still restate itself at 2025's $2,000. A flat constant
would rewrite history on the next save.
"""
from frappe.model.document import Document

#: (first tax year the amount applied, credit), newest first. A year later than
#: every entry takes the newest, so a 2027 form needs no edit here unless the IRS
#: moves the number again.
UNDER_17_CREDIT = ((2026, 2200), (0, 2000))

#: Unchanged since the 2020 redesign — the 2026 form still says "$500".
OTHER_DEPENDENT_CREDIT = ((0, 500),)


def credit_for(schedule, tax_year) -> int:
    """The per-dependent credit a form of `tax_year` claims.

    A missing year takes the NEWEST amount rather than the oldest: `tax_year` is
    mandatory, so the only row that reaches here without one is being created
    right now, and today's edition is the honest guess for it.
    """
    year = int(tax_year or 0)
    if not year:
        return schedule[0][1]
    for first_year, amount in schedule:
        if year >= first_year:
            return amount
    return schedule[-1][1]


class W4Form(Document):
    def validate(self):
        self._compute_dependents()

    def _compute_dependents(self):
        under_17 = int(self.dependents_under_17_count or 0)
        other = int(self.other_dependents_count or 0)
        self.dependents_under_17_amount = under_17 * credit_for(UNDER_17_CREDIT, self.tax_year)
        self.other_dependents_amount = other * credit_for(OTHER_DEPENDENT_CREDIT, self.tax_year)
        self.total_dependents_credit = self.dependents_under_17_amount + self.other_dependents_amount
