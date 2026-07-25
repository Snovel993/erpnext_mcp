# SPDX-License-Identifier: MIT
"""Tool implementations, split by whether they can change anything.

`read` never writes. `mutate` writes only through ERPNext's own document
methods, and every function in it is unreachable until an operator enables the
matching switch. Keeping the two in separate modules means a review can answer
"can this change the ledger?" by looking at which file a function is in.
"""
