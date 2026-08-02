# SPDX-License-Identifier: MIT
"""Every hook in `hooks.py` resolves — which v0.14.0 shipped without checking.

WHAT THIS EXISTS TO PREVENT, IN THE WORDS OF WHAT IT DID.

v0.14.0 declared a Jinja method as
`"erpnext_mcp_amount_in_words:erpnext_mcp.render.checks.amount_in_words"`. That
`"<name>:<path>"` form belongs to Frappe's OLDER `jenv` hook, whose reader splits
on the colon. The modern `jinja` hook hands each entry straight to
`frappe.get_attr` and takes the global's name from the callable's own
`__name__`. So `get_attr` received the whole string, split it on the first dot to
find an app name, and threw:

    AppNotInstalledError: App erpnext_mcp_amount_in_words:erpnext_mcp is not installed

**Frappe builds the Jinja environment to render the error page too.** The
exception was raised inside the handler for its own exception, so every page on
the site returned 500 — including the one that would have said why. A cosmetic
string on a print format took the whole UI down, and nothing in a 2400-test suite
noticed, because no test had ever read `hooks.py`.

THE FAILURE MODE IS THE POINT, NOT THE HOOK. A hook is a string the app never
executes itself: nothing imports it, nothing calls it, and every existing test
exercises the functions it names *directly*. So a hook can name a module that
does not exist, a function that was renamed, or — as here — a perfectly real
function in a syntax the reader does not speak, and the suite is green right up
until `bench migrate` on somebody's site. This module closes that by resolving
every path in the file the way FRAPPE resolves it, including its app-name rule,
which is the specific thing that threw.

IT REFUSES AN UNKNOWN HOOK KEY. `KNOWN_HOOKS` has to name every attribute in
`hooks.py`, so adding `doc_events`, `override_whitelisted_methods`,
`permission_query_conditions` or anything else fails here until somebody says
which shape it holds and therefore how it gets validated. That is deliberate
friction: this app's whole promise is that installing it cannot change how a
site behaves, and every one of those hooks is a way to break that promise
silently.
"""

import types
import unittest

from erpnext_mcp import hooks

from .harness import frappe  # noqa: F401 - installs the frappe double before erpnext_mcp imports

#: Every attribute `hooks.py` declares, and what has to be true of it.
#:
#:   "metadata"   plain values Frappe reads as configuration. Nothing to resolve.
#:   "app_list"   a list of app names.
#:   "path"       ONE dotted path to a callable.
#:   "path_map"   a dict of lists of dotted paths — `scheduler_events`.
#:   "path_dict"  a dict of ONE dotted path each, keyed by doctype — the two
#:                permission hooks. A DIFFERENT SHAPE from `path_map`, and
#:                conflating them is not cosmetic: the resolver iterates a
#:                path_map's values, so a plain string gets walked one CHARACTER
#:                at a time and every one of them "fails to resolve".
#:   "path_list"  a plain list of dotted paths — `auth_hooks`, which is the
#:                shape `validate_auth_via_hooks` iterates.
#:   "jinja"      a dict of lists of dotted paths that must carry NO colon.
#:
#: A key missing from here fails `test_every_hook_key_is_accounted_for`, which is
#: the whole point: a new hook is a new way to take a site down and it does not
#: get to arrive unexamined.
KNOWN_HOOKS = {
	"app_name": "metadata",
	"app_title": "metadata",
	"app_publisher": "metadata",
	"app_description": "metadata",
	"app_email": "metadata",
	"app_license": "metadata",
	"required_apps": "app_list",
	"after_install": "path",
	"after_migrate": "path",
	"before_uninstall": "path",
	"scheduler_events": "path_map",
	"jinja": "jinja",
	"permission_query_conditions": "path_dict",
	"has_permission": "path_dict",
	"auth_hooks": "path_list",
}

#: Hook keys this app must NOT declare, and why each one would be a lie.
#:
#: `jenv` is here because v0.14.0 declared it *as an alias of* `jinja` — two hook
#: keys with two different syntaxes pointing at one dict. It is the deprecated
#: spelling, `jinja` has existed since v14, and v14 is this app's floor, so a
#: second declaration buys nothing and doubles the surface for exactly the format
#: mistake that caused the outage.
#: `permission_query_conditions` and `has_permission` WERE here until v0.17.1,
#: on the grounds that "this app changes nobody's visibility". That was a proxy
#: for the invariant actually worth keeping — do not touch OTHER PEOPLE'S
#: doctypes — and the proxy was wrong for two doctypes this app ships and never
#: scoped. `test_permissions.py` enforces the narrower, stronger rule in its
#: place. See `permissions.py`.
FORBIDDEN_HOOKS = {
	"jenv": "the deprecated spelling of `jinja`, with a DIFFERENT syntax",
	"doc_events": "this app installs no document hooks — see the hooks.py docstring",
	"override_doctype_class": "this app overrides no doctype",
	"override_whitelisted_methods": "this app overrides no framework method",
	"fixtures": "this app ships no fixtures",
	"doctype_js": "this app adds no client script to a doctype it does not own",
}


def hook_attributes() -> dict:
	"""Everything `hooks.py` declares, excluding dunders and imported modules."""
	return {
		name: value
		for name, value in vars(hooks).items()
		if not name.startswith("_") and not isinstance(value, types.ModuleType)
	}


def dotted_paths() -> list:
	"""(hook key, path string) for every path in the file, whatever shape holds it."""
	out = []
	for name, shape in KNOWN_HOOKS.items():
		value = getattr(hooks, name, None)
		if shape == "path":
			out.append((name, value))
		elif shape == "path_list":
			for entry in value or []:
				out.append((name, entry))
		elif shape in ("path_map", "jinja"):
			for group, entries in (value or {}).items():
				for entry in entries:
					out.append((f"{name}.{group}", entry))
		elif shape == "path_dict":
			for group, entry in (value or {}).items():
				out.append((f"{name}.{group}", entry))
	return out


def resolve(path: str):
	"""`frappe.get_attr`, reproduced — INCLUDING the app-name rule that threw.

	Deliberately a second implementation rather than a call into the double.
	`frappe.get_attr` is what runs on the site, and the half of it that matters
	here is the half nobody thinks about:

	    app_name = method_string.split(".", 1)[0]
	    if app_name not in get_installed_apps(): throw(AppNotInstalledError)

	A path carrying a `name:` prefix has an "app name" of
	`erpnext_mcp_amount_in_words:erpnext_mcp`, which is not installed, and that is
	the whole outage. A test that skipped straight to `importlib` would import the
	module fine and prove nothing.
	"""
	app_name = str(path).split(".", 1)[0]
	if app_name != "erpnext_mcp":
		raise AssertionError(
			f"{path!r} resolves to app {app_name!r}, which is not this app. Frappe reads the "
			"app name as everything before the first dot and refuses a path whose app is not "
			"installed — which is exactly how a `name:path` string becomes "
			"AppNotInstalledError."
		)
	module_path, _, attribute = str(path).rpartition(".")
	module = __import__(module_path, fromlist=["x"])
	return getattr(module, attribute)


class TheHookFile(unittest.TestCase):
	def test_every_hook_key_is_accounted_for(self):
		"""A new hook does not get to arrive unexamined. Add it to KNOWN_HOOKS with
		its shape — which is a decision about how it is validated — or do not add
		it at all."""
		declared = set(hook_attributes())
		self.assertEqual(
			declared,
			set(KNOWN_HOOKS),
			"hooks.py declares something KNOWN_HOOKS does not describe (or the reverse). "
			"Every hook is a way to change how a site behaves; say which shape it holds.",
		)

	def test_the_hooks_this_app_promises_not_to_install_are_absent(self):
		for name, why in sorted(FORBIDDEN_HOOKS.items()):
			with self.subTest(hook=name):
				self.assertFalse(
					hasattr(hooks, name),
					f"hooks.py declares {name!r} — {why}. The README and the module docstring "
					"both promise it does not.",
				)


class EveryPathResolves(unittest.TestCase):
	def test_there_are_paths_to_check(self):
		"""Guard against the walk silently finding nothing and passing."""
		self.assertGreaterEqual(len(dotted_paths()), 5)

	def test_every_hook_path_resolves_to_a_callable(self):
		"""The test v0.14.0 did not have. Every one of these is a string Frappe
		imports on install, on migrate, on a scheduler tick or on the first page
		render — and none of them is executed by any other test in this suite."""
		for hook, path in dotted_paths():
			with self.subTest(hook=hook, path=path):
				self.assertTrue(path, f"{hook} has an empty path")
				resolved = resolve(path)
				self.assertTrue(
					callable(resolved), f"{hook} → {path} resolved to {resolved!r}, not a callable"
				)

	def test_no_hook_path_carries_a_colon(self):
		"""THE v0.14.0 BUG, asserted directly.

		Not one of Frappe's modern hook readers splits on a colon. `jinja` hands
		the string to `frappe.get_attr` whole; `scheduler_events` does the same;
		`after_install` and its siblings do the same. The `"<name>:<path>"` form
		is `jenv`'s alone, and `jenv` is not declared here.
		"""
		for hook, path in dotted_paths():
			with self.subTest(hook=hook, path=path):
				self.assertNotIn(
					":",
					str(path),
					f"{hook} → {path!r} carries a colon. That is the OLD `jenv` syntax; "
					"Frappe's modern hooks take a bare dotted path and derive any name they "
					"need from the callable's __name__. A colon here is "
					"AppNotInstalledError on every page render.",
				)

	def test_a_colon_prefixed_path_would_be_caught(self):
		"""The guard has to actually fail on the string that shipped, or it is
		decoration."""
		shipped = "erpnext_mcp_amount_in_words:erpnext_mcp.render.checks.amount_in_words"
		with self.assertRaises(AssertionError) as caught:
			resolve(shipped)
		# The app name Frappe would have looked for, and did not find.
		self.assertIn("erpnext_mcp_amount_in_words:erpnext_mcp", str(caught.exception))
		self.assertIn(":", shipped)


class TheJinjaMethod(unittest.TestCase):
	def test_it_resolves_and_writes_a_check_amount(self):
		"""Resolved through the hook string, not imported by name — so a hook that
		pointed at the wrong function would fail here even though the right
		function exists."""
		path = hooks.jinja["methods"][0]
		self.assertEqual(resolve(path)(1234.56), "One Thousand Two Hundred Thirty-Four and 56/100")

	def test_the_global_name_comes_from_the_function_and_is_namespaced(self):
		"""Frappe names the Jinja global after the callable's `__name__`, so the
		hook string no longer gets a say in it — and a Jinja global lands in a
		namespace shared with Frappe, ERPNext and every other installed app."""
		resolved = resolve(hooks.jinja["methods"][0])
		self.assertEqual(resolved.__name__, "erpnext_mcp_amount_in_words")
		self.assertTrue(resolved.__name__.startswith("erpnext_mcp_"))

	def test_the_check_template_calls_the_name_the_hook_actually_registers(self):
		"""The two halves have to agree, and nothing else makes them."""
		from erpnext_mcp.tools import printing

		registered = resolve(hooks.jinja["methods"][0]).__name__
		self.assertIn(registered, printing.CHECK_TEMPLATE)

	def test_the_template_still_prints_a_check_if_the_hook_is_absent(self):
		"""Belt to the brace. A check with no amount in words is not a check, so
		the template guards with `is defined` and falls back to Frappe's own."""
		from erpnext_mcp.tools import printing

		self.assertIn("erpnext_mcp_amount_in_words is defined", printing.CHECK_TEMPLATE)
		self.assertIn("frappe.utils.money_in_words", printing.CHECK_TEMPLATE)

	def test_only_one_jinja_method_is_declared(self):
		"""Every entry here is evaluated on the first page render of every request
		cycle. This app has one thing to contribute and should keep it that way."""
		self.assertEqual(len(hooks.jinja["methods"]), 1)
		self.assertEqual(set(hooks.jinja), {"methods"})


class TheScheduledJobs(unittest.TestCase):
	def test_every_scheduled_job_resolves(self):
		self.assertEqual(sorted(hooks.scheduler_events), ["daily", "hourly", "weekly"])
		for interval, paths in hooks.scheduler_events.items():
			for path in paths:
				with self.subTest(interval=interval, path=path):
					self.assertTrue(callable(resolve(path)))

	def test_they_are_these_four_and_nothing_else(self):
		"""Four jobs. Three write only this app's own doctypes or nothing at all;
		the fourth writes two credential fields on Frappe's User and had to argue
		for it — see hooks.py and tools/mobile.sweep_idle_grants.

		Asserted as an exact mapping rather than a membership check, so a third
		job fails here and has to be argued for. Every scheduled job is code that
		runs on somebody's site with nobody watching, which is the same reason
		`KNOWN_HOOKS` refuses an unexamined hook key.

		The alert sweep moved to HOURLY in v0.17.1 — see the hooks.py docstring.
		The short version: the sweep is what makes a completed task's alert go
		away, and nightly meant a worker saw the phone asking them to walk a cabin
		they had already walked, all day.
		"""
		self.assertEqual(
			hooks.scheduler_events,
			{
				"hourly": ["erpnext_mcp.alerts.sweep"],
				"daily": [
					"erpnext_mcp.tools.uploads.collect_expired_sessions",
					"erpnext_mcp.tools.mobile.sweep_idle_grants",
				],
				"weekly": ["erpnext_mcp.drift.scan"],
			},
		)

	def test_the_sweep_is_a_full_reconciliation_so_the_cadence_is_only_tuning(self):
		"""Running it twice must produce what running it once does — that is what
		makes hourly a cost decision rather than a correctness one."""
		from erpnext_mcp import alerts

		first = alerts.sweep()
		second = alerts.sweep()
		self.assertEqual(first, 0)
		self.assertEqual(second, 0)

	def test_the_upload_sweeper_never_raises(self):
		"""It runs on the site's scheduler beside everybody else's jobs."""
		from erpnext_mcp.tools import uploads

		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Staged File Upload Session")
		try:
			self.assertEqual(uploads.collect_expired_sessions(), 0)
		finally:
			INSTALLED_DOCTYPES.add("Staged File Upload Session")

	def test_the_alert_sweep_never_raises(self):
		"""Same contract, same reason. A compliance calendar that took the site's
		scheduler down would be considerably worse than one that missed a night."""
		from erpnext_mcp import alerts

		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Compliance Alert")
		try:
			self.assertEqual(alerts.sweep(), 0)
		finally:
			INSTALLED_DOCTYPES.add("Compliance Alert")

	def test_the_alert_sweep_takes_no_arguments(self):
		"""`scheduler_events` calls it bare. A job whose signature needs arguments
		is a job that raises TypeError on the first tick, on somebody's site, at
		three in the morning."""
		import inspect

		from erpnext_mcp import alerts

		signature = inspect.signature(alerts.sweep)
		self.assertEqual(list(signature.parameters), [])


class TheInstallHooks(unittest.TestCase):
	def test_install_migrate_and_uninstall_all_resolve(self):
		for name in ("after_install", "after_migrate", "before_uninstall"):
			with self.subTest(hook=name):
				self.assertTrue(callable(resolve(getattr(hooks, name))))

	def test_required_apps_names_erpnext(self):
		"""`install-app` has to refuse on a Frappe-only site rather than fail at
		the first tool call."""
		self.assertEqual(hooks.required_apps, ["erpnext"])

	def test_the_app_name_matches_the_package(self):
		"""Every hook path's first segment is read as this app's name."""
		self.assertEqual(hooks.app_name, "erpnext_mcp")
		for _hook, path in dotted_paths():
			self.assertTrue(str(path).startswith(f"{hooks.app_name}."))
