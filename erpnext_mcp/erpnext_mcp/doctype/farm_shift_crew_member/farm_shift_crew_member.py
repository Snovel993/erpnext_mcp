# SPDX-License-Identifier: MIT
"""Controller for Farm Shift Crew Member — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `farm_task_evidence.py` for the same note and
`test_packaging.py` for the release that learned it the hard way.

EVERY RULE ABOUT A CREW ROW IS A RULE ABOUT THE SHIFT IT IS ON. Whether
`joined_at` is possible depends on when the shift started; whether `left_at` is
possible depends on when it ended; whether this person is on the crew twice
depends on the other rows. The row knows none of that, so the checking lives in
`farm_shift.py` where the whole shift is in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class FarmShiftCrewMember(Document):
	pass
