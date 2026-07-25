# SPDX-License-Identifier: MIT
"""A Frappe stand-in, so this app's logic can be tested without a bench.

WHY THIS EXISTS. The tests that matter most here are about *refusal*: does a
disabled tool stay invisible, does a bad token get an opaque 401, does an
unbalanced Journal Entry get rejected before anything is written, does a failed
mutation still leave an audit row. Every one of those is answerable from the
app's own logic, and none of them needs MariaDB, redis or a site. Requiring a
full bench to run them means they get run rarely, which for refusal tests is the
same as not having them.

So this module installs an in-memory `frappe` into `sys.modules` before the app
is imported: a small document store with real filter semantics, a real doctype
meta (loaded from the app's own shipped DocType JSON, so the tests assert against
the defaults that actually ship), and the handful of framework functions the app
touches. It is a test double, not an emulator — it implements what this app uses
and nothing else. Reaching for a framework function it does not have raises an
AttributeError naming the missing function (see the module `__getattr__` in
`_build_frappe`), so the double can never quietly return None for something the
real framework would have answered.

WHAT IT DELIBERATELY DOES NOT PROVE. Whether ERPNext's Journal Entry validation
accepts a given posting date. Whether `add_payment_entries` exists on this
version's Bank Transaction. Whether the DocType JSON migrates. Those are
integration facts about a real site, and they belong to the FrappeTestCase suite
in `erpnext_mcp/tests/`, which runs inside a bench. The two suites are not
alternatives — this one is fast and covers logic, that one is slow and covers the
framework contract.

Import this module before importing anything from `erpnext_mcp`.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import secrets
import sys
import traceback
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(REPO_ROOT, "erpnext_mcp", "erpnext_mcp", "doctype")

if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)


# ── the dict-with-attributes Frappe passes everywhere ───────────────────────
class FrappeDict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			return None

	def __setattr__(self, key, value):
		self[key] = value

	def __delattr__(self, key):
		self.pop(key, None)


# ── exceptions ──────────────────────────────────────────────────────────────
class ValidationError(Exception):
	pass


class DoesNotExistError(ValidationError):
	pass


class PermissionError_(ValidationError):
	pass


# ── schema ──────────────────────────────────────────────────────────────────
#: Field lists for the ERPNext doctypes this app reads. Only the fields the app
#: actually selects need to be here; `compat.existing_fields` filters against
#: this, which is the same thing it does against a real site's meta.
ERPNEXT_SCHEMA = {
	"Company": [
		"name", "abbr", "default_currency", "country", "chart_of_accounts",
		"parent_company", "is_group", "tax_id", "cost_center",
	],
	"Account": [
		"name", "account_name", "account_number", "parent_account", "is_group",
		"root_type", "account_type", "account_currency", "disabled", "freeze_account",
		"lft", "rgt", "company",
	],
	"GL Entry": [
		"name", "account", "posting_date", "debit", "credit", "company",
		"is_cancelled", "voucher_type", "voucher_no", "party", "party_type",
	],
	"Journal Entry": [
		"name", "posting_date", "company", "voucher_type", "naming_series",
		"total_debit", "total_credit", "difference", "user_remark", "remark",
		"cheque_no", "cheque_date", "bill_no", "bill_date", "finance_book",
		"docstatus", "owner", "creation", "modified", "modified_by", "is_opening",
		"clearance_date", "mode_of_payment", "multi_currency", "accounts",
	],
	"Journal Entry Account": [
		"idx", "account", "account_type", "party_type", "party", "debit", "credit",
		"debit_in_account_currency", "credit_in_account_currency", "account_currency",
		"exchange_rate", "against_account", "cost_center", "project",
		"reference_type", "reference_name", "reference_due_date", "user_remark",
		"is_advance", "parent", "parenttype", "bank_account",
	],
	"Fiscal Year": ["name", "year_start_date", "year_end_date", "disabled", "companies"],
	"Fiscal Year Company": ["parent", "parenttype", "company"],
	"Bank Account": ["name", "account_name", "bank", "company", "account", "iban"],
	"Bank Transaction": [
		"name", "date", "bank_account", "company", "description", "status",
		"reference_number", "currency", "party_type", "party", "bank_party_name",
		"docstatus", "deposit", "withdrawal", "allocated_amount",
		"unallocated_amount", "payment_entries",
	],
	"Bank Transaction Payments": [
		"payment_document", "payment_entry", "allocated_amount", "parent", "parenttype",
	],
	"Bank Statement": [
		"name", "bank_account", "from_date", "to_date", "opening_balance",
		"closing_balance", "company",
	],
	"Payment Entry": ["name", "posting_date", "paid_amount", "docstatus", "company"],
	"User": ["name", "enabled", "full_name"],
	"DocType": ["name", "module", "issingle"],
	"Singles": ["doctype", "field", "value"],
}

#: Doctypes this app owns. Their meta is loaded from the shipped JSON so tests
#: assert against the real defaults rather than a copy that can drift.
APP_DOCTYPES = {
	"ERPNext MCP Settings": "erpnext_mcp_settings",
	"MCP Action Log": "mcp_action_log",
}


def _load_app_doctype(folder: str) -> dict:
	with open(os.path.join(DOCTYPE_DIR, folder, f"{folder}.json")) as handle:
		return json.load(handle)


class Field(FrappeDict):
	pass


class Meta:
	"""Just enough of frappe.model.meta.Meta for `compat` to interrogate."""

	def __init__(self, doctype: str, fields: list[Field], issingle: bool = False):
		self.doctype = doctype
		self.fields = fields
		self.issingle = issingle
		self._by_name = {f.fieldname: f for f in fields}

	def has_field(self, fieldname: str) -> bool:
		return fieldname in self._by_name

	def get_field(self, fieldname: str):
		return self._by_name.get(fieldname)


def _build_meta() -> dict:
	metas = {}
	for doctype, fields in ERPNEXT_SCHEMA.items():
		metas[doctype] = Meta(
			doctype, [Field(fieldname=name, fieldtype="Data") for name in fields]
		)
	for doctype, folder in APP_DOCTYPES.items():
		payload = _load_app_doctype(folder)
		metas[doctype] = Meta(
			doctype,
			[Field(**field) for field in payload["fields"]],
			issingle=bool(payload.get("issingle")),
		)
	return metas


META = _build_meta()


# ── filters ─────────────────────────────────────────────────────────────────
def _match(row: dict, filters) -> bool:
	if not filters:
		return True
	if isinstance(filters, str):
		return row.get("name") == filters
	if isinstance(filters, list):
		# [[fieldname, operator, value], ...]
		return all(_match_one(row, f[0], (f[1], f[2])) for f in filters)
	return all(_match_one(row, field, value) for field, value in filters.items())


def _match_one(row: dict, field: str, condition) -> bool:
	actual = row.get(field)
	if not isinstance(condition, (tuple, list)):
		return _eq(actual, condition)
	operator, expected = condition[0], condition[1]
	operator = str(operator).lower()
	if operator == "=":
		return _eq(actual, expected)
	if operator in ("!=", "not="):
		return not _eq(actual, expected)
	if operator == "in":
		return any(_eq(actual, item) for item in expected)
	if operator == "not in":
		return not any(_eq(actual, item) for item in expected)
	if operator == "like":
		return _like(actual, expected)
	if operator == "not like":
		return not _like(actual, expected)
	if operator == "between":
		low, high = expected
		return actual is not None and _key(low) <= _key(actual) <= _key(high)
	if operator == "is":
		if expected == "set":
			return actual not in (None, "")
		return actual in (None, "")
	if operator in ("<", "<=", ">", ">="):
		if actual is None:
			return False
		left, right = _key(actual), _key(expected)
		return {
			"<": left < right,
			"<=": left <= right,
			">": left > right,
			">=": left >= right,
		}[operator]
	raise NotImplementedError(f"stub filter operator {operator!r}")


def _eq(actual, expected) -> bool:
	if isinstance(expected, (int, float)) and not isinstance(expected, bool):
		return float(actual or 0) == float(expected)
	return str(actual if actual is not None else "") == str(
		expected if expected is not None else ""
	)


def _like(actual, pattern: str) -> bool:
	# MariaDB LIKE with the site's default collation: case-insensitive.
	text = str(actual or "").lower()
	needle = str(pattern or "").lower()
	if needle.startswith("%") and needle.endswith("%"):
		return needle.strip("%") in text
	if needle.startswith("%"):
		return text.endswith(needle.lstrip("%"))
	if needle.endswith("%"):
		return text.startswith(needle.rstrip("%"))
	return text == needle


def _key(value):
	"""Comparison key that keeps numbers numeric and everything else a string."""
	if isinstance(value, (int, float)) and not isinstance(value, bool):
		return value
	if isinstance(value, (datetime.date, datetime.datetime)):
		return value.isoformat()
	try:
		return float(value)
	except (TypeError, ValueError):
		return str(value)


# ── documents ───────────────────────────────────────────────────────────────
CHILD_TABLES = {
	("Journal Entry", "accounts"): "Journal Entry Account",
	("Bank Transaction", "payment_entries"): "Bank Transaction Payments",
	("Fiscal Year", "companies"): "Fiscal Year Company",
}


class Document(FrappeDict):
	"""A stand-in for frappe.model.document.Document.

	Runs the controller hooks this app relies on (`validate`, `before_save`,
	`on_update`) in Frappe's order, because two of this app's guarantees — the
	settings form refusing a bad CIDR, the audit log refusing an update — live in
	exactly those hooks.
	"""

	def __init__(self, data=None):
		super().__init__(data or {})
		self.flags = FrappeDict()
		self._doc_before_save = None

	# -- lifecycle ------------------------------------------------------------
	def insert(self, ignore_permissions=False, ignore_if_duplicate=False):
		self.flags.in_insert = True
		if not self.get("name"):
			self.name = STORE.next_name(self.doctype)
		self.creation = _now()
		self.modified = self.creation
		self.owner = self.get("owner") or frappe.session.user
		self.docstatus = int(self.get("docstatus") or 0)
		self._run("before_validate")
		self._run("validate")
		self._run("before_save")
		STORE.put(self)
		self._run("after_insert")
		self._run("on_update")
		self.flags.in_insert = False
		return self

	def save(self, ignore_permissions=False):
		if not self.get("name"):
			return self.insert(ignore_permissions=ignore_permissions)
		self._doc_before_save = STORE.get_raw(self.doctype, self.name)
		self.modified = _now()
		self._run("before_validate")
		self._run("validate")
		self._run("before_save")
		STORE.put(self)
		self._run("on_update")
		return self

	def submit(self):
		self.docstatus = 1
		self._run("before_submit")
		STORE.put(self)
		self._run("on_submit")
		return self

	def cancel(self):
		self.docstatus = 2
		self._run("before_cancel")
		STORE.put(self)
		self._run("on_cancel")
		return self

	def reload(self):
		fresh = STORE.get_raw(self.doctype, self.name)
		if fresh:
			self.update(copy.deepcopy(fresh))
		return self

	def _run(self, hook: str):
		method = getattr(self, hook, None)
		if callable(method):
			method()

	# -- data -----------------------------------------------------------------
	def append(self, fieldname, value=None):
		rows = self.get(fieldname)
		if not isinstance(rows, list):
			rows = []
			self[fieldname] = rows
		child = Document(dict(value or {}))
		child.doctype = CHILD_TABLES.get((self.doctype, fieldname), "")
		child.parenttype = self.doctype
		child.parentfield = fieldname
		child.idx = len(rows) + 1
		rows.append(child)
		return child

	def set(self, fieldname, value):
		self[fieldname] = value

	def get(self, key, default=None):
		return dict.get(self, key, default)

	def as_dict(self, no_nulls=False, convert_dates_to_str=False):
		out = {}
		for key, value in self.items():
			if key in ("flags", "_doc_before_save"):
				continue
			if no_nulls and value is None:
				continue
			out[key] = value
		return FrappeDict(out)

	def get_doc_before_save(self):
		return self._doc_before_save

	def is_new(self) -> bool:
		return not self.get("creation")

	def db_set(self, fieldname, value, **kwargs):
		self[fieldname] = value
		STORE.put(self)

	def add_comment(self, comment_type, text):
		STORE.comments.append(
			{"doctype": self.doctype, "name": self.name, "type": comment_type, "text": text}
		)

	def get_password(self, fieldname, raise_exception=True):
		value = STORE.passwords.get((self.doctype, self.get("name"), fieldname))
		if value is None and raise_exception:
			raise ValidationError(f"no password stored for {fieldname}")
		return value


class Store:
	"""The in-memory database."""

	def __init__(self):
		self.reset()

	def reset(self):
		self.tables: dict[str, dict[str, dict]] = {}
		self.singles: dict[str, dict] = {}
		self.passwords: dict[tuple, str] = {}
		self.comments: list[dict] = []
		self.errors: list[dict] = []
		self.counters: dict[str, int] = {}
		self.committed = 0
		self.rolled_back = 0
		# Rows written since the last commit, so a rollback can discard exactly
		# those — which is what the audit-survives-rollback tests need to see.
		self.pending: list[tuple[str, str]] = []

	def next_name(self, doctype: str) -> str:
		self.counters[doctype] = self.counters.get(doctype, 0) + 1
		prefix = "".join(word[0] for word in doctype.split() if word).upper() or "DOC"
		return f"{prefix}-{self.counters[doctype]:05d}"

	def put(self, doc: Document):
		self._extract_passwords(doc)
		if META.get(doc.doctype) and META[doc.doctype].issingle:
			self.singles[doc.doctype] = _plain(doc)
			return
		table = self.tables.setdefault(doc.doctype, {})
		is_new = doc.name not in table
		table[doc.name] = _plain(doc)
		if is_new:
			self.pending.append((doc.doctype, doc.name))

	def _extract_passwords(self, doc: Document):
		"""Move Password field values out of the row, as Frappe does on save.

		Frappe writes a Password field to the encrypted `__Auth` table and leaves
		a row of asterisks in the document, which is why `get_password()` exists
		and why reading the field directly gives you nothing useful. Reproducing
		that here is what makes "the token is never returned to a caller"
		something the tests can actually check rather than take on trust.
		"""
		meta = META.get(doc.doctype)
		if not meta:
			return
		for field in meta.fields:
			if field.get("fieldtype") != "Password":
				continue
			value = doc.get(field["fieldname"])
			if value and not set(str(value)) <= {"*"}:
				self.passwords[(doc.doctype, doc.get("name"), field["fieldname"])] = value
				doc[field["fieldname"]] = "*" * len(str(value))

	def get_raw(self, doctype: str, name: str):
		if META.get(doctype) and META[doctype].issingle:
			return self.singles.get(doctype)
		return self.tables.get(doctype, {}).get(name)

	def rows(self, doctype: str) -> list[dict]:
		if META.get(doctype) and META[doctype].issingle:
			single = self.singles.get(doctype)
			return [single] if single else []
		return list(self.tables.get(doctype, {}).values())

	def seed(self, doctype: str, rows: list[dict]):
		table = self.tables.setdefault(doctype, {})
		for index, row in enumerate(rows, start=1):
			row = dict(row)
			row.setdefault("name", f"{doctype}-{index}")
			row.setdefault("docstatus", 0)
			row.setdefault("creation", _now())
			table[row["name"]] = row
		# Seeded fixtures are "already committed" state.
		self.pending.clear()

	def commit(self):
		self.committed += 1
		self.pending.clear()

	def rollback(self):
		self.rolled_back += 1
		for doctype, name in self.pending:
			self.tables.get(doctype, {}).pop(name, None)
		self.pending.clear()


def _plain(doc) -> dict:
	out = {}
	for key, value in doc.items():
		if key in ("flags", "_doc_before_save"):
			continue
		if isinstance(value, list):
			out[key] = [_plain(item) if isinstance(item, dict) else item for item in value]
		else:
			out[key] = value
	return out


STORE = Store()


# ── the fake frappe.db ──────────────────────────────────────────────────────
class FakeDB:
	def get_all(
		self,
		doctype,
		filters=None,
		fields=None,
		order_by=None,
		limit=None,
		limit_page_length=None,
		pluck=None,
		as_dict=True,
		distinct=False,
		**kwargs,
	):
		rows = [row for row in STORE.rows(doctype) if _match(row, filters)]
		if doctype in CHILD_TABLE_SOURCES:
			rows = [row for row in _child_rows(doctype) if _match(row, filters)]

		aggregates = [f for f in (fields or []) if "(" in str(f)]
		if aggregates:
			return [_aggregate(rows, aggregates)]

		if order_by:
			rows = _sorted(rows, order_by)
		cap = limit or limit_page_length
		if cap:
			rows = rows[: int(cap)]
		if pluck:
			return [row.get(pluck) for row in rows]
		if fields:
			return [FrappeDict({f: row.get(f) for f in fields}) for row in rows]
		return [FrappeDict(copy.deepcopy(row)) for row in rows]

	def get_value(self, doctype, filters=None, fieldname="name", as_dict=False, **kwargs):
		rows = self.get_all(doctype, filters=filters)
		if not rows:
			return None
		row = rows[0]
		if isinstance(fieldname, (list, tuple)):
			if as_dict:
				return FrappeDict({f: row.get(f) for f in fieldname})
			return [row.get(f) for f in fieldname]
		if as_dict:
			return FrappeDict({fieldname: row.get(fieldname)})
		return row.get(fieldname)

	def get_values(self, doctype, filters=None, fieldname="name", as_dict=False, **kwargs):
		if doctype == "Singles":
			target = (filters or {}).get("doctype")
			return [
				FrappeDict({"field": key, "value": value})
				for key, value in (STORE.singles.get(target) or {}).items()
			]
		rows = self.get_all(doctype, filters=filters)
		fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
		return [FrappeDict({f: row.get(f) for f in fields}) for row in rows]

	def get_single_value(self, doctype, fieldname):
		return (STORE.singles.get(doctype) or {}).get(fieldname)

	def exists(self, doctype, filters=None):
		if doctype == "DocType":
			name = filters if isinstance(filters, str) else (filters or {}).get("name")
			return name if name in INSTALLED_DOCTYPES else None
		rows = self.get_all(doctype, filters=filters)
		return rows[0].get("name") if rows else None

	def count(self, doctype, filters=None):
		return len(self.get_all(doctype, filters=filters))

	def set_value(self, doctype, name, fieldname, value=None, **kwargs):
		row = STORE.get_raw(doctype, name)
		if row is None:
			return
		if isinstance(fieldname, dict):
			row.update(fieldname)
		else:
			row[fieldname] = value

	def commit(self):
		STORE.commit()

	def rollback(self):
		STORE.rollback()

	def sql(self, *args, **kwargs):  # pragma: no cover - app must not use raw SQL
		raise AssertionError(
			"erpnext_mcp must not run raw SQL: every write goes through the ORM so "
			"doctype validation runs. If a read genuinely needs SQL, extend this "
			"stub deliberately."
		)


#: Child doctypes are stored inside their parents, so a query against one has to
#: flatten the parents first.
CHILD_TABLE_SOURCES = {
	"Journal Entry Account": ("Journal Entry", "accounts"),
	"Bank Transaction Payments": ("Bank Transaction", "payment_entries"),
	"Fiscal Year Company": ("Fiscal Year", "companies"),
}


def _child_rows(child_doctype: str) -> list[dict]:
	parent_doctype, fieldname = CHILD_TABLE_SOURCES[child_doctype]
	out = []
	for parent in STORE.rows(parent_doctype):
		for row in parent.get(fieldname) or []:
			merged = dict(row)
			merged.setdefault("parent", parent.get("name"))
			merged.setdefault("parenttype", parent_doctype)
			out.append(merged)
	return out


def _aggregate(rows: list[dict], expressions: list[str]) -> FrappeDict:
	out = FrappeDict()
	for expression in expressions:
		function, _, rest = expression.partition("(")
		column = rest.split(")")[0].strip()
		alias = expression.split(" as ")[-1].strip() if " as " in expression else expression
		function = function.strip().lower()
		if function == "sum":
			out[alias] = sum(float(row.get(column) or 0) for row in rows) or 0
		elif function == "count":
			out[alias] = len(rows)
		else:  # pragma: no cover
			raise NotImplementedError(f"stub aggregate {function!r}")
	return out


def _sorted(rows: list[dict], order_by: str) -> list[dict]:
	out = list(rows)
	# Apply each clause in reverse so the leftmost wins, as SQL does.
	for clause in reversed([c.strip() for c in order_by.split(",") if c.strip()]):
		parts = clause.split()
		column = parts[0].split(".")[-1].strip("`")
		reverse = len(parts) > 1 and parts[1].lower() == "desc"
		out.sort(key=lambda row: _key(row.get(column) or ""), reverse=reverse)
	return out


#: Which doctypes this fake site "has installed". Tests flip entries to exercise
#: the graceful-degrade paths (a site without Bank Statement, say).
INSTALLED_DOCTYPES = set(ERPNEXT_SCHEMA) | set(APP_DOCTYPES)


# ── utils ───────────────────────────────────────────────────────────────────
def _now() -> str:
	return datetime.datetime(2026, 7, 24, 9, 0, 0).isoformat(sep=" ")


def _getdate(value=None):
	if value is None:
		return datetime.date(2026, 7, 24)
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip().split(" ")[0].split("T")[0]
	parts = text.split("-")
	if len(parts) != 3:
		raise ValueError(f"cannot parse date {value!r}")
	return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))


def _build_utils() -> types.ModuleType:
	module = types.ModuleType("frappe.utils")
	module.now = _now
	module.nowdate = lambda: _getdate().isoformat()
	module.today = lambda: _getdate().isoformat()
	module.getdate = _getdate
	module.flt = lambda value, precision=None: round(float(value or 0), precision or 2)
	module.cint = lambda value: int(float(value or 0))
	module.cstr = lambda value: "" if value is None else str(value)
	return module


# ── the module itself ───────────────────────────────────────────────────────
def _controller(doctype: str):
	"""Resolve a doctype to this app's controller class, as Frappe does."""
	folder = APP_DOCTYPES.get(doctype)
	if not folder:
		return Document
	module = __import__(
		f"erpnext_mcp.erpnext_mcp.doctype.{folder}.{folder}", fromlist=["x"]
	)
	return getattr(module, doctype.replace(" ", ""), Document)


def _build_frappe() -> types.ModuleType:
	module = types.ModuleType("frappe")

	module._dict = FrappeDict
	module.ValidationError = ValidationError
	module.DoesNotExistError = DoesNotExistError
	module.PermissionError = PermissionError_
	module.db = FakeDB()
	module.local = FrappeDict(
		site="test.localhost",
		request=None,
		session=FrappeDict(user="Guest", data=FrappeDict()),
	)
	module.flags = FrappeDict()

	def _translate(text, *args, **kwargs):
		return text

	module._ = _translate

	def get_meta(doctype):
		if doctype not in META:
			raise ValidationError(f"stub has no meta for {doctype!r}")
		return META[doctype]

	def get_doc(*args, **kwargs):
		if args and isinstance(args[0], dict):
			payload = dict(args[0])
			doctype = payload.pop("doctype")
			return _controller(doctype)({**payload, "doctype": doctype})
		doctype = args[0]
		if META.get(doctype) and META[doctype].issingle:
			data = copy.deepcopy(STORE.singles.get(doctype) or {})
			data["doctype"] = doctype
			data.setdefault("name", doctype)
			return _controller(doctype)(data)
		name = args[1] if len(args) > 1 else kwargs.get("name")
		row = STORE.get_raw(doctype, name)
		if row is None:
			raise DoesNotExistError(f"{doctype} {name} not found")
		data = copy.deepcopy(row)
		data["doctype"] = doctype
		doc = _controller(doctype)(data)
		for fieldname in ("accounts", "payment_entries", "companies"):
			if isinstance(doc.get(fieldname), list):
				doc[fieldname] = [Document(item) for item in doc[fieldname]]
		return doc

	def new_doc(doctype):
		doc = _controller(doctype)({"doctype": doctype, "docstatus": 0})
		for field in META[doctype].fields if doctype in META else []:
			if field.get("default") not in (None, ""):
				doc[field["fieldname"]] = field["default"]
		return doc

	def get_cached_doc(*args, **kwargs):
		return get_doc(*args, **kwargs)

	def get_single(doctype):
		return get_doc(doctype)

	def whitelist(*dargs, **dkwargs):
		def decorator(function):
			function.__wrapped_whitelisted__ = True
			return function

		if dargs and callable(dargs[0]):
			return decorator(dargs[0])
		return decorator

	def throw(message, exc=None, title=None):
		raise (exc or ValidationError)(str(message))

	def only_for(roles, message=False):
		roles = [roles] if isinstance(roles, str) else list(roles)
		if not set(roles) & set(get_roles(module.session.user)):
			raise PermissionError_(f"requires role: {', '.join(roles)}")

	def get_roles(user=None):
		return ROLES.get(user or module.session.user, [])

	def get_request_header(name, default=None):
		request = module.local.request
		if request is None:
			return default
		return request.headers.get(name, default)

	def set_user(user):
		module.local.session.user = user

	def log_error(title=None, message=None, reference_doctype=None, reference_name=None):
		STORE.errors.append({"title": title, "message": message})

	def get_traceback(with_context=False):
		return traceback.format_exc()

	def generate_hash(txt=None, length=56):
		return secrets.token_hex(max(1, length // 2))[:length]

	def msgprint(message, title=None, indicator=None, **kwargs):
		STORE.comments.append({"type": "msgprint", "text": str(message)})

	def get_attr(path):
		parts = path.split(".")
		obj = __import__(".".join(parts[:-1]), fromlist=["x"])
		return getattr(obj, parts[-1])

	module.get_meta = get_meta
	module.get_doc = get_doc
	module.new_doc = new_doc
	module.get_cached_doc = get_cached_doc
	module.get_single = get_single
	module.whitelist = whitelist
	module.throw = throw
	module.only_for = only_for
	module.get_roles = get_roles
	module.get_request_header = get_request_header
	module.set_user = set_user
	module.log_error = log_error
	module.get_traceback = get_traceback
	module.generate_hash = generate_hash
	module.msgprint = msgprint
	module.get_attr = get_attr
	module.utils = _build_utils()

	# `frappe.session` and `frappe.request` are properties on the real module;
	# a module-level __getattr__ is the closest a stub gets.
	def __getattr__(name):
		if name == "session":
			return module.local.session
		if name == "request":
			return module.local.request
		if name == "site":
			return module.local.site
		raise AttributeError(
			f"erpnext_mcp used frappe.{name}, which this test double does not "
			"implement. Add it to tests_standalone/harness.py deliberately, or "
			"reconsider whether the app should depend on it."
		)

	module.__getattr__ = __getattr__
	return module


ROLES = {"Administrator": ["System Manager", "Accounts Manager", "Accounts User"]}


def install() -> types.ModuleType:
	"""Put the stub into sys.modules. Idempotent."""
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "__is_stub__", False):
		return sys.modules["frappe"]
	module = _build_frappe()
	module.__is_stub__ = True
	sys.modules["frappe"] = module

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Document
	model.document = document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	sys.modules["frappe.utils"] = module.utils
	module.model = model
	return module


frappe = install()


# ── request plumbing ────────────────────────────────────────────────────────
class FakeRequest:
	def __init__(self, body="", headers=None, remote_addr="127.0.0.1", method="POST", host="test.localhost"):
		self.headers = {k: v for k, v in (headers or {}).items()}
		self._body = body if isinstance(body, str) else json.dumps(body)
		self.remote_addr = remote_addr
		self.method = method
		self.host = host

	def get_data(self, as_text=False):
		return self._body if as_text else self._body.encode()


# ── the base test case ──────────────────────────────────────────────────────
class MCPTestCase(unittest.TestCase):
	"""Resets the fake site, and gives every test a configured-but-off server."""

	TOKEN = "t" * 48

	def setUp(self):
		STORE.reset()
		INSTALLED_DOCTYPES.clear()
		INSTALLED_DOCTYPES.update(set(ERPNEXT_SCHEMA) | set(APP_DOCTYPES))
		frappe.local.request = None
		frappe.local.session = FrappeDict(user="Administrator", data=FrappeDict())
		self.configure()

	def configure(self, **overrides):
		"""Write the settings single, defaults from the shipped JSON.

		Starts from what the app itself would seed on install, so a test that
		does not override anything is testing the real out-of-the-box posture.

		Note the values go in as the *strings* the DocType JSON declares, not as
		integers. That is faithful: `tabSingles.value` is a text column, so a
		Check field on a Single reads back as `"0"` — which is truthy in Python
		and is exactly how a switch-is-off bug gets shipped. `settings._as_bool`
		is what stops it, and this fixture is what would catch its removal.
		"""
		values = {}
		for field in META["ERPNext MCP Settings"].fields:
			if field.get("default") not in (None, ""):
				values[field["fieldname"]] = field["default"]
		values.update({"enabled": 1, "doctype": "ERPNext MCP Settings"})
		values.update(overrides)
		STORE.singles["ERPNext MCP Settings"] = values
		STORE.passwords[("ERPNext MCP Settings", "ERPNext MCP Settings", "auth_token")] = (
			self.TOKEN
		)
		return values

	def set_token(self, token):
		STORE.passwords[("ERPNext MCP Settings", "ERPNext MCP Settings", "auth_token")] = token

	def request(self, payload, token=None, headers=None, remote_addr="127.0.0.1", method="POST"):
		"""Point frappe.local.request at a fake request and return it."""
		all_headers = {"Content-Type": "application/json"}
		if token is not False:
			all_headers["Authorization"] = f"Bearer {token or self.TOKEN}"
		all_headers.update(headers or {})
		request = FakeRequest(
			body=payload if isinstance(payload, str) else json.dumps(payload),
			headers=all_headers,
			remote_addr=remote_addr,
			method=method,
		)
		frappe.local.request = request
		return request

	def call(self, method, params=None, request_id=1, **kwargs):
		"""POST one JSON-RPC message through the real endpoint. Returns (body, status)."""
		# Imported here rather than at module scope: the stub has to be in
		# sys.modules before anything from erpnext_mcp is loaded.
		from erpnext_mcp import mcp

		message = {"jsonrpc": "2.0", "id": request_id, "method": method}
		if params is not None:
			message["params"] = params
		self.request(message, **kwargs)
		response = mcp.handle()
		body = response.get_data(as_text=True)
		parsed = json.loads(body) if body.strip() else None
		return parsed, response.status_code

	def tool(self, name, arguments=None, **kwargs):
		"""Call one tool and return its parsed result dict."""
		body, status = self.call(
			"tools/call", {"name": name, "arguments": arguments or {}}, **kwargs
		)
		self.assertEqual(status, 200, body)
		return body["result"]

	def tool_data(self, name, arguments=None, **kwargs):
		"""Call one tool, assert it succeeded, and return its parsed payload."""
		result = self.tool(name, arguments, **kwargs)
		self.assertFalse(
			result.get("isError"), f"{name} failed: {result['content'][0]['text']}"
		)
		return json.loads(result["content"][0]["text"])

	def tool_error(self, name, arguments=None, **kwargs):
		"""Call one tool, assert it failed, and return the error text."""
		result = self.tool(name, arguments, **kwargs)
		self.assertTrue(result.get("isError"), f"{name} unexpectedly succeeded: {result}")
		return result["content"][0]["text"]

	# -- convenience assertions ----------------------------------------------
	def audit_rows(self, **filters):
		rows = STORE.rows("MCP Action Log")
		return [row for row in rows if _match(row, filters or None)]

	def assertAudited(self, tool_name, status=None):
		rows = self.audit_rows(tool_name=tool_name)
		self.assertTrue(rows, f"no MCP Action Log row for {tool_name}")
		if status:
			self.assertEqual(rows[-1]["result_status"], status, rows[-1])
		return rows[-1]
