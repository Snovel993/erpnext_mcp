# SPDX-License-Identifier: MIT
"""In-bench integration tests.

    bench --site <site> run-tests --app erpnext_mcp

These are the other half of this app's test story. The standalone suite in
`tests_standalone/` covers the logic — refusals, arithmetic, switch handling —
against an in-memory double, and runs in a fraction of a second with no bench.
What it cannot cover is everything that is only true of a real site: that the
DocType JSON migrates, that a Password field survives a round trip through
Frappe's encryption, that ERPNext's own Journal Entry validation accepts what
`create_journal_entry` builds, that the permission rows actually keep a
non-System-Manager out.

Those facts are what these tests are for. They skip rather than fail when the
site lacks the accounting setup a case needs — a bare Frappe site with no
Company, say — so `run-tests` is meaningful on any site rather than only on a
fully configured one.
"""
