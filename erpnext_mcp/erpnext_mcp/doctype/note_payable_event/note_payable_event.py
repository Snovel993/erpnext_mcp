# SPDX-License-Identifier: MIT
"""Controller for Note Payable Event — a child table, and empty on purpose.

WHY THIS FILE EXISTS AT ALL, given that it does nothing. Frappe imports
`<app>/<module>/doctype/<scrubbed name>/<scrubbed name>.py` for **every** DocType
it loads, child tables included, and `bench migrate` reaches that import while
syncing the JSON. A folder with a JSON and no module takes the whole migrate
down with a `ModuleNotFoundError`, which is what v0.7.0 shipped and v0.7.1 fixed.
An empty controller is mandatory, not optional.

WHY THERE IS NO LOGIC IN IT. A row here records something that already happened
— a payment that produced a draft Journal Entry, or the disposition that closed
the note. The rules worth enforcing are all about the *sequence*: that the
running balance never goes negative, that nothing is recorded against a closed
note. None of those can be seen from inside one row, so they live on the parent,
`NotePayable`, and in the tools that write them.
"""

from frappe.model.document import Document


class NotePayableEvent(Document):
	pass
