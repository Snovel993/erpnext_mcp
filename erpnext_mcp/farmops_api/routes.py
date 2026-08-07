# SPDX-License-Identifier: MIT
"""The closed list of routes, and the argument filter Frappe was doing.

THERE IS NO DISPATCHER AND NO METHOD-NAME ARGUMENT, for the same reason
`api/mobile.py` has neither: a route exists in the table below or its path 404s,
so the entire reachable surface of this service is the lines an auditor can
read in one screen. This app has two hundred-odd MCP tools; `create_journal_entry`,
`convey_parcel`, `import_chart_of_accounts` and `disable_je_gate` are among them
and NONE of them is reachable from here at any path, under any argument, by any
caller.

`test_farmops_api.py` asserts that table against `api/mobile.py` and
`api/files.py` in both directions — every route resolves to a real guarded
function, and every guarded function has a route — so another method cannot
arrive quietly and a route cannot come to point at something ungated. v0.45.0
added nine at once (onboarding, the crew clock and the bucket sync) and that
assertion is what made adding them a two-file change rather than a 404 in a
field. v0.46.0 added the three the wizard reaches BEFORE any of those nine —
`create_employee`, `search_employees` and `reactivate_employee`, which were
still being asked for at Frappe's own `/api/resource/Employee` and are the
reason the onboarding flow got no further than its Identity step. v0.46.2 added
the fourth of that set, `get_employee`: the same 404 at the same doctype's
detail path, on the branch a returning seasonal worker takes.

────────────────────────────────────────────────────────────────────────────
WHY THE ARGUMENT FILTER IS HERE
────────────────────────────────────────────────────────────────────────────

Frappe's own handler binds a request body to a whitelisted method by KEEPING THE
KEYS THAT MATCH THE SIGNATURE AND DROPPING THE REST. Every one of the wrappers
was written against that behaviour — `api/mobile.py` says so at length, because
naming every accepted argument instead of taking `**kwargs` is what makes
`record_data`, `worker_id`, `foreman` and a W-4's `status` unreachable from a
phone.

This transport does not go through Frappe's handler, so that filter is not
happening any more, and `function(**body)` would be two different bugs at once:
a phone sending a key the server does not know would get a 500 instead of an
answer, and a wrapper that ever did grow a `**kwargs` would silently start
accepting whatever the body carried. Reproducing the filter keeps every
signature meaning exactly what it meant on the old path — which is also what
lets the byte-identical cross-check in the test suite be a real comparison
rather than a coincidence.

`user` is dropped even though it IS in every signature: `guard.endpoint` injects
the authenticated caller there, and an account that can name somebody else in a
request body is not scoped to anything. `guard` drops it too. Two locks on the
one argument that would matter.
"""

from __future__ import annotations

import inspect

from ..api import fallback_auth
from ..api import files as files_api
from ..api import mobile as mobile_api

#: The path prefix every route lives under. NOT under `/api/…`: the whole point
#: of this transport is to be somewhere Frappe's request handler is not, and a
#: prefix that collided with Frappe's would be handed straight back to it by any
#: proxy in front that routes on path before it forwards.
PREFIX = "/farmops/api"


class Route:
	"""One reachable path, and the guarded function behind it.

	`path` is under `PREFIX` — `/mobile/get_task`. `handler` is the
	`@guard.endpoint`-wrapped function in `api/mobile.py` or `api/files.py`, gates
	and all. `mutating` is read off the wrapper's own `farm_ops_mutating`
	attribute rather than restated here, so the route table and the endpoint
	cannot come to disagree about whether a call writes.

	The NAME comes off the wrapper too, for the same reason: `guard.endpoint` is
	given the method name the app calls it by, so a route cannot be spelled
	differently from the method it reaches.
	"""

	__slots__ = ("handler", "mutating", "path")

	def __init__(self, prefix, handler):
		self.path = f"{prefix}/{handler.farm_ops_method}"
		self.handler = handler
		self.mutating = bool(getattr(handler, "farm_ops_mutating", False))

	def __repr__(self):  # pragma: no cover - diagnostics
		return f"<Route {self.path}{' (mutating)' if self.mutating else ''}>"


#: The mobile methods and the two file methods. ORDER IS THE APP'S ORDER —
#: the caller, lists, detail, lifecycle, field reports, compliance, upload —
#: because this table is the first thing somebody reads to learn what a phone
#: can do.
ROUTES = (
	Route("/mobile", mobile_api.get_current_user_context),
	Route("/mobile", mobile_api.list_my_tasks),
	Route("/mobile", mobile_api.list_available_tasks),
	Route("/mobile", mobile_api.get_task),
	Route("/mobile", mobile_api.claim_task),
	Route("/mobile", mobile_api.start_task),
	Route("/mobile", mobile_api.complete_task_via_mobile),
	Route("/mobile", mobile_api.reject_task),
	Route("/mobile", mobile_api.report_field_task),
	Route("/mobile", mobile_api.list_compliance_alerts),
	Route("/mobile", mobile_api.scan_asset),
	Route("/mobile", mobile_api.get_asset_detail),
	Route("/mobile", mobile_api.log_asset_state_change),
	Route("/mobile", mobile_api.get_available_actions),
	Route("/mobile", mobile_api.report_asset_issue),
	# v0.46.0. The Identity step, which comes before all of it and which the
	# wizard 404'd on: the foreman searches for somebody who has worked here
	# before, and either puts that record back on the payroll or creates a new
	# one. Every path below this line was unreachable in practice until these
	# three existed, because the flow stops at step 1.
	Route("/mobile", mobile_api.create_employee),
	Route("/mobile", mobile_api.search_employees),
	# v0.46.2. The step between the search and the rehire, and the last of the
	# Identity step's four calls to be answering at Frappe's own
	# `/api/resource/Employee/<name>` — which the funnel does not publish. It is
	# what tells the wizard which of its five steps a returning picker has already
	# done, and in tree fruit the returning picker is the common case.
	Route("/mobile", mobile_api.get_employee),
	Route("/mobile", mobile_api.reactivate_employee),
	# v0.45.0. Onboarding, in the order `OnboardingFlow` walks it: the I-9 is
	# opened, the worker fills their half, the employer verifies the documents,
	# the W-4 is signed, and the badge that will carry their piece-rate is mapped
	# to them last.
	Route("/mobile", mobile_api.create_i9_form),
	Route("/mobile", mobile_api.submit_i9_section_1),
	Route("/mobile", mobile_api.submit_i9_section_2),
	Route("/mobile", mobile_api.submit_w4),
	Route("/mobile", mobile_api.link_badge_to_employee),
	# The capture queue and the crew clock.
	Route("/mobile", mobile_api.sync_bucket_entries),
	Route("/mobile", mobile_api.start_shift),
	Route("/mobile", mobile_api.add_worker_to_shift),
	Route("/mobile", mobile_api.end_shift),
	Route("/files", files_api.stage_file_chunk),
	Route("/files", files_api.finalize_staged_file),
)

#: Path → Route. Built once at import; there is nothing dynamic about it.
BY_PATH = {route.path: route for route in ROUTES}


def accepted_arguments(handler) -> set:
	"""The body keys this method will accept. Everything else is dropped.

	Read off the signature with `inspect`, which follows `functools.wraps`
	through `guard.endpoint` to the wrapper's own parameter list — so the answer
	is always what the function actually declares, and a signature change cannot
	leave a stale allowlist behind it.

	A `**kwargs` in one of them would make this return the empty set and take
	every argument away, which is the safe direction to fail and is asserted as
	a property of the surface in `test_farmops_api.py`. None of them has one, on
	purpose.
	"""
	try:
		parameters = inspect.signature(handler).parameters
	except (TypeError, ValueError):  # pragma: no cover - a builtin, not one of ours
		return set()
	if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
		return set()
	return {
		name
		for name, parameter in parameters.items()
		if name != "user"
		and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
	}


def bind(route, body: dict) -> dict:
	"""The request body, reduced to the keys this method declares.

	`_auth` goes whatever else happens: it is envelope rather than argument, it
	is the one key in a mobile body that carries a live credential, and a call
	that reached this transport did not need it — but the app sends it on every
	request (v0.17.2 carries the pair three ways and does not know which door it
	came in), so it arrives here on every single call.
	"""
	accepted = accepted_arguments(route.handler)
	return {
		key: value for key, value in (body or {}).items() if key in accepted and key != fallback_auth.BODY_KEY
	}
