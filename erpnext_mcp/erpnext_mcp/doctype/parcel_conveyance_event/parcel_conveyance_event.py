# SPDX-License-Identifier: MIT
"""Controller for Parcel Conveyance Event — a child table, and empty on purpose.

WHY THIS FILE EXISTS AT ALL, given that it does nothing. Frappe imports
`<app>/<module>/doctype/<scrubbed name>/<scrubbed name>.py` for **every** DocType
it loads, child tables included, and `bench migrate` reaches that import while
syncing the JSON. A folder with a JSON and no module takes the whole migrate down
with a `ModuleNotFoundError`, which is what v0.7.0 shipped and v0.7.1 fixed. An
empty controller is mandatory, not optional.

WHY THERE IS NO LOGIC IN IT. A row here records a conveyance that has already
happened — the parcel it hangs off is the one that RECEIVED the ground, and the
record it came from no longer exists. Nothing about the row can be validated from
inside the row: whether the counts are right is a fact about the migration that
wrote them, and whether the conveyance was allowed at all is a fact about a lease
on a parcel that has been deleted. Both live in `realestate.convey_parcel`, which
is the only thing that writes here.

WHY THE TRAIL IS ON THE SURVIVOR. A conveyance destroys one record and creates
another, so there is exactly one document left to carry the history and it is the
new one. The row names the entity the ground came from and the docname it had, so
a reader who finds "Mill Creek - HLD" and remembers "Mill Creek - OML" can join
the two without either record having to still exist.
"""

from frappe.model.document import Document


class ParcelConveyanceEvent(Document):
	pass
