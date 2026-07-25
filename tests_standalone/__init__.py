# SPDX-License-Identifier: MIT
"""Standalone test suite — runs without a Frappe bench.

Importing this package installs the in-memory `frappe` double (see `harness`).
It happens here, in the package `__init__`, because a test module is free to
import `erpnext_mcp` at the top of the file and the stub has to already be in
`sys.modules` by then. Doing it per-module instead would make the suite depend
on import order inside every file, which is the kind of thing that works until
somebody sorts the imports.

    python3 -m unittest discover -s tests_standalone -t .
"""

from . import harness  # noqa: F401  (imported for its import-time side effect)
