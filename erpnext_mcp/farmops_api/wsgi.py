# SPDX-License-Identifier: MIT
"""The entry point gunicorn is pointed at, and a way to run this by hand.

    gunicorn --bind 0.0.0.0:5250 erpnext_mcp.farmops_api.wsgi:application

`supervisord.conf` in `fafo-umbrel-store/fafo-erpnext/build/` runs exactly that,
with the bench's own `env/bin/gunicorn` and `directory=…/frappe-bench/sites` —
which is what makes `frappe.init()` find the site's config, the same way every
`bench` command does.

`python -m erpnext_mcp.farmops_api.wsgi` starts Werkzeug's development server on
the same port instead. That is for a bench shell and a `curl`, on the day
somebody needs to know whether the service or the proxy is the problem; it is
single-threaded and it says so when it starts.
"""

from __future__ import annotations

from .app import DEFAULT_PORT, application

__all__ = ["application"]


def main() -> None:  # pragma: no cover - an operator's diagnostic, not a code path
	import os

	from werkzeug.serving import run_simple

	from .session import site_name

	port = int(os.environ.get("FARMOPS_API_PORT") or DEFAULT_PORT)
	host = os.environ.get("FARMOPS_API_HOST") or "127.0.0.1"
	print(
		f"farmops-api (development server, single threaded) on http://{host}:{port} "
		f"— site {site_name()!r}. Use gunicorn for anything a phone will call."
	)
	run_simple(host, port, application)


if __name__ == "__main__":  # pragma: no cover
	main()
