# SPDX-License-Identifier: MIT
"""Chart-of-accounts templates: what one is, and the rules a chart has to obey.

A TEMPLATE IS DATA, NOT A QUERY. Every template in this package is a plain
Python literal. Nothing here touches the database, which is what makes
`propose_clean_chart` a genuinely read-only planning tool and lets a template be
reviewed, diffed and version-controlled like any other source file. A template
that needed a live site to describe itself could not be reviewed before it ran,
and reviewing before it runs is the entire point.

ACCOUNT TYPE IS THE PART VERSIONS DISAGREE ABOUT. `root_type` has been the same
five values for a decade. `account_type` is a Select whose option list ERPNext
edits between releases, so this module never trusts its own idea of what is
valid: `site_account_types()` reads the option list off `frappe.get_meta`, and a
type the site does not offer is swapped for a documented fallback and reported,
rather than being sent to a doctype that will reject it.

The root-type mapping below is used only to *refuse what is positively wrong* —
a Payable under Income, say. A type this module has never heard of but the site
accepts is allowed through with a note. That polarity matters: a validation
table baked into an app is a snapshot of one ERPNext version, and the failure
mode of a stale snapshot should be a note, not a locked door.

ADDING A TEMPLATE is one file in this package ending in `register(...)`. The
package imports every sibling at load time, exactly as `erpnext_mcp.packets`
does, so there is no list to update. `us_c_corp`, `us_s_corp` and
`us_partnership` are the obvious next three; they differ from `us_llc_farm`
almost entirely in the 3000s.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import frappe

from ..errors import ToolError

#: ERPNext's five root types. Unlike account_type this list is stable, and a
#: value outside it is a caller error rather than a version difference.
ROOT_TYPES = ("Asset", "Liability", "Income", "Expense", "Equity")

#: Which root_type each account_type belongs under, as far as this app knows.
#: Used ONLY to reject a positively wrong pairing — see the module docstring.
#: A type absent from every entry here is unknown, not invalid.
ACCOUNT_TYPES_BY_ROOT = {
	"Asset": {
		"Accumulated Depreciation",
		"Asset Received But Not Billed",
		"Bank",
		"Capital Work in Progress",
		"Cash",
		"Chargeable",
		"Current Asset",
		"Fixed Asset",
		"Receivable",
		"Stock",
		"Temporary",
	},
	"Liability": {
		"Current Liability",
		"Liability",
		"Payable",
		"Payroll Payable",
		"Provision",
		"Service Received But Not Billed",
		"Stock Received But Not Billed",
		"Tax",
		"Temporary",
	},
	"Equity": {"Equity", "Temporary"},
	"Income": {"Direct Income", "Income Account", "Indirect Income", "Temporary"},
	"Expense": {
		"Chargeable",
		"Cost of Goods Sold",
		"Depreciation",
		"Direct Expense",
		"Expense Account",
		"Expenses Included In Asset Valuation",
		"Expenses Included In Valuation",
		"Indirect Expense",
		"Round Off",
		"Round Off for Opening",
		"Stock Adjustment",
		"Tax",
		"Temporary",
	},
}

#: What to use when a template asks for an account_type this site's ERPNext does
#: not offer. Each entry is a deliberate accounting judgement, not a guess:
#: "Credit Card" is a payable that some versions surface separately and others
#: do not, and booking it as a plain Payable is how ERPNext handled it before
#: the option existed. An unmapped type degrades to no type at all, which is
#: always legal — an account without an account_type still posts.
ACCOUNT_TYPE_FALLBACKS = {
	"Credit Card": "Payable",
	"Payroll Payable": "Payable",
	"Provision": "Payable",
	"Service Received But Not Billed": "Payable",
	"Capital Work in Progress": "Fixed Asset",
	"Round Off for Opening": "Round Off",
	"Direct Income": "Income Account",
	"Indirect Income": "Income Account",
	"Direct Expense": "Expense Account",
	"Indirect Expense": "Expense Account",
	"Cost of Goods Sold": "Expense Account",
}

#: Keys a template node — and therefore a node in `import_chart_of_accounts`'s
#: input — may carry. Anything else is rejected by name: a caller who wrote
#: `"type"` for `"account_type"` should be told, not quietly given an untyped
#: account across a hundred-account import.
NODE_FIELDS = (
	"account_number",
	"account_name",
	"root_type",
	"account_type",
	"account_currency",
	"tax_rate",
	"is_group",
	"parent_account",
	"description",
	"optional",
	"children",
)

#: A chart is a tree, and a tree deep enough to be a mistake is a mistake. Ten
#: is roughly twice the depth of any real chart of accounts.
MAX_DEPTH = 10

#: Ceiling on one import. ERPNext rebuilds the account nested set on every
#: insert, so a thousand-account import is slow enough to time out a request —
#: better to refuse with a number than to half-run and roll back.
MAX_ACCOUNTS = 400


@dataclass
class ChartTemplate:
	"""One named starting chart of accounts.

	`tree` is a list of root nodes. Each node is a dict of `NODE_FIELDS`;
	`root_type` is required on roots and inherited by every descendant, because
	an account whose root_type differs from its parent's is a chart ERPNext will
	not accept and there is no reason to let a template express one.
	"""

	key: str
	title: str
	entity_type: str
	jurisdiction: str
	summary: str
	tree: list
	notes: tuple = ()

	def describe(self) -> dict:
		leaves, groups = 0, 0
		for node, _parent, _depth in walk(self.tree):
			if node.get("is_group"):
				groups += 1
			else:
				leaves += 1
		return {
			"template": self.key,
			"title": self.title,
			"entity_type": self.entity_type,
			"jurisdiction": self.jurisdiction,
			"summary": self.summary,
			"group_accounts": groups,
			"ledger_accounts": leaves,
			"total_accounts": groups + leaves,
			"notes": list(self.notes),
		}


#: key → ChartTemplate. Populated by `register` at import time.
TEMPLATES: dict = {}


def register(template: ChartTemplate) -> ChartTemplate:
	if template.key in TEMPLATES:
		raise RuntimeError(f"duplicate chart template {template.key!r}")
	TEMPLATES[template.key] = template
	return template


# ── walking and validating a tree ───────────────────────────────────────────
def walk(tree, parent=None, depth=0):
	"""Yield `(node, parent_node_or_None, depth)` depth-first, parents first.

	Dependency order by construction: a node is always yielded before its
	children, which is the order ERPNext needs to insert them in.
	"""
	for node in tree or ():
		yield node, parent, depth
		yield from walk(node.get("children") or (), node, depth + 1)


def parse_accounts_json(raw) -> list:
	"""Coerce whatever a caller sent into a validated list of root nodes.

	Accepts three shapes, because all three are things a model will send:
	a JSON *string*, a bare list of roots, or the whole `propose_clean_chart`
	response (from which the `accounts` key is taken). Anything else is refused
	with the shape it should have been.
	"""
	if isinstance(raw, str):
		text = raw.strip()
		if not text:
			raise ToolError("accounts_json is empty")
		try:
			raw = json.loads(text)
		except ValueError as exc:
			raise ToolError(f"accounts_json is not valid JSON: {exc}") from None

	if isinstance(raw, dict):
		if "accounts" not in raw:
			raise ToolError(
				"accounts_json given as an object must have an 'accounts' key holding "
				"the list of root accounts — that is the shape propose_clean_chart "
				"returns, so its whole response can be passed straight through."
			)
		raw = raw["accounts"]

	if not isinstance(raw, list) or not raw:
		raise ToolError(
			"accounts_json must be a non-empty list of root accounts, e.g. "
			'[{"account_number": "1000", "account_name": "Assets", '
			'"root_type": "Asset", "is_group": true, "children": [...]}]'
		)

	validate_tree(raw)
	return raw


def validate_tree(tree) -> None:
	"""Structural checks that need no database at all.

	Everything here is a property of the JSON itself — field names, root types,
	group/leaf consistency, duplicate numbers, depth, size. Doing it before any
	site lookup means a malformed tree fails on the first pass with every problem
	named, instead of one round trip per mistake.
	"""
	seen_numbers: dict = {}
	count = 0

	for node, parent, depth in walk(tree):
		count += 1
		where = _where(node)
		if not isinstance(node, dict):
			raise ToolError(f"every account must be an object, got {type(node).__name__}")

		unknown = sorted(set(node) - set(NODE_FIELDS))
		if unknown:
			raise ToolError(
				f"{where} has unsupported field(s): {', '.join(unknown)}. Supported: {', '.join(NODE_FIELDS)}"
			)

		if not str(node.get("account_name") or "").strip():
			raise ToolError(f"{where} has no account_name")

		if depth >= MAX_DEPTH:
			raise ToolError(
				f"{where} is nested {depth + 1} deep; the limit is {MAX_DEPTH}. "
				"A chart of accounts this deep is almost always a malformed tree."
			)

		root_type = str(node.get("root_type") or "").strip()
		if parent is None:
			if root_type not in ROOT_TYPES:
				raise ToolError(
					f"{where} is a root account and needs root_type, one of "
					f"{', '.join(ROOT_TYPES)}; got {root_type or '<empty>'}"
				)
		elif root_type:
			parent_root = str(parent.get("root_type") or "").strip()
			if parent_root and root_type != parent_root:
				raise ToolError(
					f"{where} declares root_type {root_type!r} under a parent whose "
					f"root_type is {parent_root!r}. ERPNext requires a subtree to share "
					"one root type; drop the field and it is inherited."
				)

		children = node.get("children")
		if children is not None and not isinstance(children, list):
			raise ToolError(f"{where}: children must be a list, got {type(children).__name__}")
		if children and not node.get("is_group"):
			raise ToolError(
				f"{where} has children but is_group is not set. In ERPNext only a "
				"group account can have children; only a leaf can be posted to."
			)
		if parent is None and not node.get("is_group"):
			raise ToolError(
				f"{where} is a root account, so it must be a group — ERPNext refuses a "
				"root that can be posted to."
			)

		number = str(node.get("account_number") or "").strip()
		if number:
			if number in seen_numbers:
				raise ToolError(
					f"account_number {number!r} appears twice in this chart: "
					f"{seen_numbers[number]} and {node.get('account_name')}. "
					"Account numbers are unique per company in ERPNext."
				)
			seen_numbers[number] = node.get("account_name")

		tax_rate = node.get("tax_rate")
		if tax_rate is not None:
			try:
				float(tax_rate)
			except (TypeError, ValueError):
				raise ToolError(f"{where}: tax_rate must be a number, got {tax_rate!r}") from None

	if count > MAX_ACCOUNTS:
		raise ToolError(
			f"this chart has {count} accounts; the per-import limit is {MAX_ACCOUNTS}. "
			"ERPNext rebuilds the account tree on every insert, so a larger import "
			"risks timing out half way. Split it by root type and import each in turn."
		)


def _where(node) -> str:
	if not isinstance(node, dict):
		return "account"
	number = str(node.get("account_number") or "").strip()
	name = str(node.get("account_name") or "?").strip()
	return f"account {number} {name}".strip() if number else f"account {name!r}"


# ── account types, as this site spells them ─────────────────────────────────
def site_account_types() -> set:
	"""The `account_type` values this site's Account doctype actually offers.

	An empty set means "could not tell" — an old Frappe whose Meta does not
	expose options, or a doctype this app should not be second-guessing. Callers
	treat that as "allow anything", because refusing every type on the basis of a
	failed introspection would be worse than letting the doctype refuse it.
	"""
	try:
		field_meta = frappe.get_meta("Account").get_field("account_type")
	except Exception:
		return set()
	options = getattr(field_meta, "options", None) if field_meta else None
	if not options:
		return set()
	return {line.strip() for line in str(options).split("\n") if line.strip()}


def resolve_account_type(account_type: str, root_type: str, supported: set) -> tuple:
	"""Map a wanted account_type onto one this site accepts.

	Returns `(usable_type, note_or_empty)`. The note is non-empty exactly when
	the answer differs from what was asked for, so a caller can surface every
	substitution rather than discovering one later in a report.
	"""
	wanted = (account_type or "").strip()
	if not wanted:
		return "", ""
	if supported and wanted not in supported:
		fallback = ACCOUNT_TYPE_FALLBACKS.get(wanted, "")
		if fallback and fallback in supported:
			return fallback, (
				f"this ERPNext has no account_type {wanted!r}; using {fallback!r}, "
				"which is how the same account is classified on versions without it"
			)
		return "", (
			f"this ERPNext has no account_type {wanted!r} and there is no equivalent "
			"here; the account is created without an account_type, which is legal and "
			"does not stop it posting"
		)
	problem = account_type_conflict(wanted, root_type)
	if problem:
		return "", problem
	return wanted, ""


def account_type_conflict(account_type: str, root_type: str) -> str:
	"""Why `account_type` cannot sit under `root_type` — or "" if it can.

	Silent about types this table has never heard of. See the module docstring:
	an unknown type is a version difference, not an error.
	"""
	wanted = (account_type or "").strip()
	if not wanted:
		return ""
	known = {t for types in ACCOUNT_TYPES_BY_ROOT.values() for t in types}
	if wanted not in known:
		return ""
	if wanted in ACCOUNT_TYPES_BY_ROOT.get(root_type, set()):
		return ""
	belongs = sorted(root for root, types in ACCOUNT_TYPES_BY_ROOT.items() if wanted in types)
	return f"account_type {wanted!r} belongs under root_type {' or '.join(belongs)}, not {root_type!r}"


# ── docnames ────────────────────────────────────────────────────────────────
def account_docname(account_number: str, account_name: str, abbr: str) -> str:
	"""ERPNext's Account autoname, reproduced.

	`"<number> - <name> - <abbr>"`, or `"<name> - <abbr>"` when unnumbered. This
	app needs it in two places a real insert cannot help with: predicting a
	docname during a dry run, and knowing what a rename will produce.

	It is duplicated from `erpnext.accounts.doctype.account.account.
	get_account_autoname` rather than imported because a dry run has to work on a
	site whose ERPNext is a different version from the one this was written
	against, and being wrong about a *predicted* name is survivable in a way that
	failing to produce a plan is not. The real insert still names itself.
	"""
	parts = [str(account_name or "").strip()]
	if str(abbr or "").strip():
		parts.append(str(abbr).strip())
	number = str(account_number or "").strip()
	if number:
		parts.insert(0, number)
	return " - ".join(part for part in parts if part)
