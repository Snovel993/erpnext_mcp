# SPDX-License-Identifier: MIT
"""The Financial KPI Framework: KPIs as records, and the engine that runs them.

v0.39.0, and it is the same move v0.22.0 made for compliance rules, pointed at
the finance side of the app.

────────────────────────────────────────────────────────────────────────────
WHAT CHANGED, AND WHY IT IS THE WHOLE RELEASE
────────────────────────────────────────────────────────────────────────────

v0.19.5 shipped one KPI. v0.19.6 put it under a window standard and showed the
standard generalized by registering two more beside it. All three are FUNCTIONS,
and adding a fourth is six lines of Python, a test, a review, a release and a
deploy — which is a perfectly good process for a KPI this app's authors chose,
and no process at all for a KPI somebody's lender asked about on a Tuesday.

Every operation has two or three ratios that are genuinely its own. A packing
house watches cost per bin; an operation carrying an equipment note watches debt
service coverage on the covenant's own definition and not on anybody else's; a
family office watches distributions against normalized cash flow. None of those
belong in a shipped app, and all of them belong on the dashboard of the farm
that needs them. So a KPI is now a RECORD — `Financial KPI Definition` — and
this module is what runs one.

────────────────────────────────────────────────────────────────────────────
THE ENGINE DOES NOT MOVE, AND THAT IS THE POINT OF HAVING ONE
────────────────────────────────────────────────────────────────────────────

`formula_type` has exactly two values and both are deterministic.

  * BUILT-IN delegates to a computer that ships with this app, registered in
    `services/financial_reports.py` and reviewed like any other code. The record
    still owns everything an operator would legitimately change — the window,
    the step, the lookback, the thresholds, the dashboard order, the switch —
    and owns nothing about the arithmetic.
  * EXPRESSION evaluates the text in `expression` over the variables
    `expression_inputs` names. It is parsed to an AST, every node is checked
    against an allowlist of ARITHMETIC, and the surviving tree is walked by an
    evaluator in this module that has no builtins, no attribute access, no
    subscripts, no calls except to four named numeric functions, and no way to
    reach the interpreter it is running inside.

There is no third value and there is no field anywhere on the record that holds
Python. That is a deliberate difference from `Compliance Rule.custom_python`,
and the reason is what the two things do: a compliance rule can need to express
"this record is stale unless that other record supersedes it", which is a shape
no set of fields captures. A financial KPI is a number divided by another
number. Nothing about a ratio needs a `for` loop, so the sandbox here does not
have one — and a sandbox without loops, without statements and without name
binding is a very much smaller thing to be confident about than one with them.

`alerts/sandbox.py` remains the app's restricted interpreter for the compliance
side. This is a second, strictly smaller one, and the duplication is on purpose:
they are different grammars serving different risks, and merging them would mean
the finance side inherited every node the compliance side needs.

────────────────────────────────────────────────────────────────────────────
A DEFINITION HOLDS THE QUESTION. IT NEVER HOLDS AN ANSWER
────────────────────────────────────────────────────────────────────────────

Nothing on a `Financial KPI Definition` is a value. Every figure is computed
from the ledger at the moment it is asked for, cached in `Financial KPI History`
WITH THE COMPONENTS THAT PRODUCED IT, and derivable again by rerunning the same
computation over the same window. That is what makes the cache safe to delete
and what makes a historical figure defensible a year later, and it is the rule
the whole reporting side of this app is built on.

So this module computes and caches; it stores no state of its own.

────────────────────────────────────────────────────────────────────────────
EVERY KPI GOES THROUGH THE WINDOW STANDARD. NONE OF THEM GETS ITS OWN
────────────────────────────────────────────────────────────────────────────

`compute_kpi` does not compute a window. It builds a computer — a callable
taking `(company, period_start, period_end)` — and hands it to
`windowed_reports.compute_windowed`, which is the same function
`get_windowed_report` has gone through since v0.19.6. The boundaries, the
fiscal-year anchoring, the cache, the history, the statistics and the partial-
window warnings therefore behave identically for a KPI somebody typed into a
form this morning and for the one that shipped in v0.19.5.

That is not tidiness. A framework whose new KPIs get a second, simpler window
implementation is a framework where the new KPIs are quietly wrong at the fiscal
year boundary, and nobody finds out until a lender does.

────────────────────────────────────────────────────────────────────────────
`kpi_id` IS THE CACHE KEY, WHICH IS WHY IT IS UNIQUE AND WHY IT CANNOT MOVE
────────────────────────────────────────────────────────────────────────────

A Compliance Rule is versioned by copy: an edit writes v+1 and the old row stays
so an alert raised in April can still be read against the definition that raised
it. A KPI is NOT, and the difference is what the two records produce. An alert
is an event with a definition behind it. A KPI is a LINE, and a line assembled
from two definitions of one number is a chart with an unmarked join in it —
which is the single most misleading object a finance dashboard can contain.

So `kpi_id` is unique, and changing the arithmetic of a live KPI is a new
`kpi_id` beside the old one rather than an edit to it. `source_version` on each
cached row is the second half of the same guarantee, for the case where the app
itself changes a built-in computer.

────────────────────────────────────────────────────────────────────────────
THRESHOLDS GO TO THE COMPLIANCE CALENDAR, NOT TO A SECOND ALERTING SYSTEM
────────────────────────────────────────────────────────────────────────────

A KPI past its critical threshold raises a `Compliance Alert` through the
existing sweep — the same rows, the same dismissal, the same snooze, the same
auto-clear when the value comes back inside. See
`alerts/rules.py::financial_kpi_threshold_breach`.

An operation with two alerting systems reads neither, and a covenant about to be
breached is exactly as much a Monday-morning problem as a cabin with a dead
carbon monoxide detector.

THE THRESHOLD SCAN READS THE CACHE AND NEVER COMPUTES. The alert sweep runs
hourly beside somebody's real work; a scan that recomputed every KPI for every
company would put minutes of GL arithmetic on that path. The overnight refresh
fills the cache and the scan reads the newest snapshot it finds, which is at
most a day old and is the same figure the dashboard is showing.
"""

from __future__ import annotations

import ast
import json
import math

import frappe

from .. import compat
from . import financial_reports  # noqa: F401  (registers the built-in computers)
from . import windowed_reports as windows

DOCTYPE = "Financial KPI Definition"

#: The doctype the computed values are cached in. Named here rather than reached
#: through `windows.DOCTYPE` at every call site so a reader of this module can
#: see what it writes without opening another one.
HISTORY_DOCTYPE = windows.DOCTYPE


# ── the vocabulary ──────────────────────────────────────────────────────────
CATEGORY_PROFITABILITY = "Profitability"
CATEGORY_LIQUIDITY = "Liquidity"
CATEGORY_LEVERAGE = "Leverage"
CATEGORY_EFFICIENCY = "Efficiency"
CATEGORY_OPERATIONAL = "Operational"
CATEGORY_CUSTOM = "Custom"

CATEGORIES = (
	CATEGORY_PROFITABILITY,
	CATEGORY_LIQUIDITY,
	CATEGORY_LEVERAGE,
	CATEGORY_EFFICIENCY,
	CATEGORY_OPERATIONAL,
	CATEGORY_CUSTOM,
)

UNIT_CURRENCY = "Currency"
UNIT_PERCENTAGE = "Percentage"
UNIT_RATIO = "Ratio"
UNIT_DAYS = "Days"
UNIT_ACRES = "Acres"
UNIT_UNITS = "Units"

UNITS = (UNIT_CURRENCY, UNIT_PERCENTAGE, UNIT_RATIO, UNIT_DAYS, UNIT_ACRES, UNIT_UNITS)

FORMULA_BUILTIN = "Built-in"
FORMULA_EXPRESSION = "Expression"
FORMULA_TYPES = (FORMULA_BUILTIN, FORMULA_EXPRESSION)

#: The default window, restated here rather than imported at every call site.
#: TTM for the reason `docs/reporting_ttm_standard.md` argues: a rolling twelve
#: months contains the whole agricultural cycle exactly once however it is read.
DEFAULT_WINDOW_TYPE = windows.WINDOW_TTM
DEFAULT_WINDOW_MONTHS = windows.DEFAULT_WINDOW_MONTHS
DEFAULT_STEP = windows.STEP_MONTHLY
DEFAULT_LOOKBACK_YEARS = windows.DEFAULT_LOOKBACK_YEARS

#: How many definitions one call will walk. A site with four hundred KPIs has a
#: problem this cap is not the solution to, but a cap that is reported beats a
#: dashboard that silently stops at some number nobody chose.
DEFINITION_CAP = 200

#: What `threshold_status` may say. Ordered worst-first, like alert severities,
#: so a caller sorting a dashboard by urgency sorts on the index.
STATUS_CRITICAL = "Critical"
STATUS_WARNING = "Warning"
STATUS_OK = "OK"
STATUS_UNKNOWN = "Unknown"
STATUS_NO_THRESHOLDS = "No thresholds"

STATUS_ORDER = (STATUS_CRITICAL, STATUS_WARNING, STATUS_OK, STATUS_UNKNOWN, STATUS_NO_THRESHOLDS)

FIELDS = (
	"name",
	"kpi_id",
	"title",
	"description",
	"enabled",
	"company",
	"category",
	"unit",
	"formula_type",
	"builtin_function",
	"expression",
	"expression_inputs",
	"default_window_type",
	"default_window_months",
	"default_computation_step",
	"historical_averaging_enabled",
	"historical_lookback_years",
	"threshold_warning_low",
	"threshold_critical_low",
	"threshold_warning_high",
	"threshold_critical_high",
	"dashboard_visible",
	"display_order",
	"creation",
	"modified",
	"owner",
)


# ════════════════════════════════════════════════════════════════════════════
# THE EXPRESSION SANDBOX
# ════════════════════════════════════════════════════════════════════════════
class ExpressionError(Exception):
	"""An expression that was refused, or that could not be evaluated. Never silent."""


#: Every AST node the grammar admits. Anything not in here is refused BY NAME at
#: check time, which is at save time — so an operator finds out that `import` is
#: not available while they are looking at the form, rather than at two in the
#: morning when the refresh runs and a KPI quietly stops producing values.
_ALLOWED_NODES = (
	ast.Expression,
	ast.Constant,
	ast.Name,
	ast.Load,
	ast.BinOp,
	ast.UnaryOp,
	ast.BoolOp,
	ast.Compare,
	ast.IfExp,
	ast.Call,
	# operators
	ast.Add,
	ast.Sub,
	ast.Mult,
	ast.Div,
	ast.FloorDiv,
	ast.Mod,
	ast.Pow,
	ast.UAdd,
	ast.USub,
	ast.Not,
	ast.And,
	ast.Or,
	ast.Eq,
	ast.NotEq,
	ast.Lt,
	ast.LtE,
	ast.Gt,
	ast.GtE,
)

#: The four functions an arithmetic expression may call, and they are the four a
#: financial formula actually reaches for: a floor, a ceiling, a magnitude and a
#: presentation rounding. Each is the Python builtin, bound here by value so no
#: name lookup ever reaches a real builtins mapping.
_FUNCTIONS = {
	"min": min,
	"max": max,
	"abs": abs,
	"round": round,
}

#: Ceiling on an exponent. `9 ** 9 ** 9` is nine characters and will hold a
#: worker until somebody restarts it, and no financial ratio has ever needed a
#: power above a small integer. Checked at evaluation because the exponent may
#: be a variable.
MAX_EXPONENT = 64

#: Ceiling on the parsed tree. A formula with four hundred nodes in it is not a
#: formula, and the limit is far above anything a ratio needs.
MAX_NODES = 400


def check_expression(expression: str, variables=()) -> dict:
	"""Parse and vet one expression. Raises `ExpressionError` with the reason.

	Returns `{"names": [...], "unused": [...], "node_count": int}` — the variable
	names the expression actually reads, and the ones `expression_inputs` defines
	that it never mentions. THE SECOND IS REPORTED RATHER THAN REFUSED: an unused
	input is usually half of a rename, which is worth saying out loud and is not
	worth refusing a save over, because the expression it produces is correct.

	NOTHING IS EXECUTED HERE. `ast.parse` builds a tree; it does not run one.
	"""
	text = str(expression or "").strip()
	if not text:
		raise ExpressionError(
			"an Expression KPI needs an expression. It is the arithmetic, written over the "
			"variable names expression_inputs defines — for example "
			"`(current_assets - inventory) / current_liabilities`."
		)
	if len(text) > 2000:
		raise ExpressionError(
			f"the expression is {len(text)} characters. A financial ratio that needs two thousand "
			"characters is several KPIs that each want their own definition, and a dashboard reads "
			"better for the split."
		)

	try:
		tree = ast.parse(text, mode="eval")
	except SyntaxError as exc:
		raise ExpressionError(
			f"the expression could not be parsed: {exc.msg} at offset {exc.offset}. Note that this "
			"field holds ONE arithmetic expression and not a program — there is no assignment, no "
			"statement and no line break in the grammar."
		) from None

	names: list = []
	count = 0
	for node in ast.walk(tree):
		count += 1
		if count > MAX_NODES:
			raise ExpressionError(
				f"the expression has more than {MAX_NODES} nodes in it. That is not a ratio; it is "
				"a program, and it wants to be several definitions or a shipped computer."
			)
		if not isinstance(node, _ALLOWED_NODES):
			raise ExpressionError(_refusal(node))
		if isinstance(node, ast.Name):
			if node.id in _FUNCTIONS:
				continue
			if node.id.startswith("_"):
				raise ExpressionError(
					f"{node.id!r} starts with an underscore. Underscore names are how every escape "
					"from every sandbox begins, so they are refused here whatever they turn out to "
					"mean — name the variable something a reader would recognise."
				)
			if node.id not in names:
				names.append(node.id)
		if isinstance(node, ast.Call):
			if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
				called = getattr(node.func, "id", None) or type(node.func).__name__
				raise ExpressionError(
					f"{called!r} cannot be called here. The only functions this grammar has are "
					f"{', '.join(sorted(_FUNCTIONS))} — everything else, including anything reached "
					"through an attribute, is a way back to the interpreter and is refused."
				)
			if node.keywords:
				raise ExpressionError(
					f"{node.func.id}() was called with a keyword argument. The four functions here "
					"take positional arguments only."
				)
		if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
			raise ExpressionError(
				f"{node.value!r} is a {type(node.value).__name__} constant. This grammar holds "
				"numbers only — a string in a financial formula is either a variable name that was "
				"quoted by mistake or a comparison that belongs in the record's own fields."
			)

	defined = [str(name) for name in (variables or ())]
	undefined = [name for name in names if name not in defined]
	if defined and undefined:
		raise ExpressionError(
			f"the expression reads {', '.join(repr(name) for name in undefined)}, which "
			f"expression_inputs does not define. It defines {', '.join(sorted(defined)) or '<nothing>'}. "
			"An undefined name cannot be resolved to a query, so the KPI would produce nothing at "
			"all — which on a dashboard looks exactly like a business with no revenue."
		)
	return {
		# Sorted rather than in walk order, so two identical expressions report
		# identically and a diff of two definitions is a diff of the definitions.
		"names": sorted(names),
		"unused": sorted(name for name in defined if name not in names),
		"node_count": count,
	}


def _refusal(node) -> str:
	"""The sentence for one refused node. Says what it was and why it is not here."""
	kind = type(node).__name__
	reasons = {
		"Attribute": (
			"attribute access is the standard escape from every sandbox that allowed it — "
			"`x.__class__.__bases__[0].__subclasses__()` needs no imports at all"
		),
		"Subscript": "indexing implies a container, and this grammar holds numbers",
		"Lambda": "a KPI that needs to define a function has outgrown this field and wants a shipped computer",
		"ListComp": "a comprehension implies a loop, and no ratio needs one",
		"SetComp": "a comprehension implies a loop, and no ratio needs one",
		"DictComp": "a comprehension implies a loop, and no ratio needs one",
		"GeneratorExp": "a comprehension implies a loop, and no ratio needs one",
		"Await": "there is nothing asynchronous to wait for here",
		"Starred": "argument unpacking implies a sequence, and this grammar holds numbers",
		"JoinedStr": "an f-string is a formatting operation, and a KPI is a number",
		"NamedExpr": "assignment is not in this grammar; a value that wants a name wants an expression_inputs entry",
		"List": "this grammar holds numbers, not sequences",
		"Tuple": "this grammar holds numbers, not sequences",
		"Dict": "this grammar holds numbers, not mappings",
		"Set": "this grammar holds numbers, not sets",
	}
	why = reasons.get(kind, "it is not arithmetic, and this grammar is arithmetic and nothing else")
	return (
		f"{kind} is not allowed in a KPI expression: {why}. What IS allowed: + - * / // % ** on "
		"numbers and variable names, unary minus, parentheses, comparisons inside a conditional, "
		f"and calls to {', '.join(sorted(_FUNCTIONS))}. If the KPI genuinely needs more than that, "
		"it wants a built-in computer — six lines in services/financial_reports.py, with a test."
	)


def evaluate(expression: str, values: dict) -> dict:
	"""Evaluate a checked expression over resolved variable values.

	Returns `{"value": float|None, "warnings": [...]}`. A `None` value is an
	ANSWER and not a failure: a ratio whose denominator was zero is a division
	nobody performed, and returning zero there would be read as a result. The
	warning names the variables that were zero, because "which denominator was
	empty" is the first thing anybody asks.
	"""
	checked = check_expression(expression, values.keys())
	warnings: list = []

	numbers: dict = {}
	for name in checked["names"]:
		raw = values.get(name)
		if raw is None:
			warnings.append(
				f"{name!r} resolved to nothing, so this KPI has no value for the window. That is "
				"usually a query that matched no accounts rather than a period with no activity — "
				"the resolved inputs are in the components, each with what it matched."
			)
			return {"value": None, "warnings": warnings}
		try:
			numbers[name] = float(raw)
		except (TypeError, ValueError):
			warnings.append(f"{name!r} resolved to {raw!r}, which is not a number, so nothing was computed.")
			return {"value": None, "warnings": warnings}

	try:
		result = _eval(ast.parse(str(expression).strip(), mode="eval").body, numbers)
	except ZeroDivisionError:
		zeros = sorted(name for name, value in numbers.items() if value == 0)
		warnings.append(
			"the expression divided by zero, so there is no value for this window. "
			+ (
				f"The variable(s) that resolved to zero: {', '.join(zeros)}."
				if zeros
				else "No single input was zero, so the empty denominator is a sub-expression."
			)
			+ " A null here is the honest answer — a farm with no acres in production has no cash "
			"flow per acre, and a zero would be read as one."
		)
		return {"value": None, "warnings": warnings}
	except ExpressionError as exc:
		warnings.append(f"the expression could not be evaluated: {exc}")
		return {"value": None, "warnings": warnings}
	except (OverflowError, ValueError) as exc:
		warnings.append(f"the expression overflowed: {type(exc).__name__}: {exc}. Nothing was computed.")
		return {"value": None, "warnings": warnings}

	try:
		result = float(result)
	except (TypeError, ValueError):
		warnings.append(f"the expression produced {result!r}, which is not a number.")
		return {"value": None, "warnings": warnings}
	if not math.isfinite(result):
		warnings.append(
			f"the expression produced {result}, which is not a finite number. Nothing is reported "
			"rather than an infinity, because an infinity on a dashboard is read as a very large "
			"result rather than as a broken formula."
		)
		return {"value": None, "warnings": warnings}
	return {"value": round(result, 6), "warnings": warnings}


def _eval(node, names: dict):
	"""Walk one checked tree. Reaches nothing but `names` and `_FUNCTIONS`."""
	if isinstance(node, ast.Constant):
		return node.value
	if isinstance(node, ast.Name):
		if node.id in names:
			return names[node.id]
		if node.id in _FUNCTIONS:
			return _FUNCTIONS[node.id]
		raise ExpressionError(f"{node.id!r} has no value.")
	if isinstance(node, ast.UnaryOp):
		operand = _eval(node.operand, names)
		if isinstance(node.op, ast.USub):
			return -operand
		if isinstance(node.op, ast.UAdd):
			return +operand
		return not operand
	if isinstance(node, ast.BinOp):
		left = _eval(node.left, names)
		right = _eval(node.right, names)
		if isinstance(node.op, ast.Add):
			return left + right
		if isinstance(node.op, ast.Sub):
			return left - right
		if isinstance(node.op, ast.Mult):
			return left * right
		if isinstance(node.op, ast.Div):
			return left / right
		if isinstance(node.op, ast.FloorDiv):
			return left // right
		if isinstance(node.op, ast.Mod):
			return left % right
		if isinstance(node.op, ast.Pow):
			if abs(right) > MAX_EXPONENT:
				raise ExpressionError(
					f"an exponent of {right} was refused (the ceiling is {MAX_EXPONENT}). No "
					"financial ratio needs one, and a large exponent is how a one-line expression "
					"holds a worker until somebody restarts it."
				)
			return left**right
		raise ExpressionError(f"{type(node.op).__name__} is not an operator this grammar has.")
	if isinstance(node, ast.BoolOp):
		values = [_eval(value, names) for value in node.values]
		if isinstance(node.op, ast.And):
			result = True
			for value in values:
				result = result and value
			return result
		result = False
		for value in values:
			result = result or value
		return result
	if isinstance(node, ast.Compare):
		left = _eval(node.left, names)
		for operator, comparator in zip(node.ops, node.comparators, strict=True):
			right = _eval(comparator, names)
			if not _compare(operator, left, right):
				return False
			left = right
		return True
	if isinstance(node, ast.IfExp):
		return _eval(node.body, names) if _eval(node.test, names) else _eval(node.orelse, names)
	if isinstance(node, ast.Call):
		function = _FUNCTIONS.get(getattr(node.func, "id", ""))
		if function is None:
			raise ExpressionError("only min, max, abs and round can be called here.")
		return function(*[_eval(argument, names) for argument in node.args])
	raise ExpressionError(_refusal(node))


def _compare(operator, left, right) -> bool:
	if isinstance(operator, ast.Eq):
		return left == right
	if isinstance(operator, ast.NotEq):
		return left != right
	if isinstance(operator, ast.Lt):
		return left < right
	if isinstance(operator, ast.LtE):
		return left <= right
	if isinstance(operator, ast.Gt):
		return left > right
	if isinstance(operator, ast.GtE):
		return left >= right
	raise ExpressionError(f"{type(operator).__name__} is not a comparison this grammar has.")


# ════════════════════════════════════════════════════════════════════════════
# THE INPUTS: what a variable in an expression IS
# ════════════════════════════════════════════════════════════════════════════
SOURCE_GL = "gl"
SOURCE_REPORT = "report"
SOURCE_KPI = "kpi"
SOURCE_CONSTANT = "constant"
SOURCES = (SOURCE_GL, SOURCE_REPORT, SOURCE_KPI, SOURCE_CONSTANT)

#: The five roots ERPNext's chart has, and which side of the ledger each one
#: naturally sits on. THIS IS WHY A `sign` FIELD IS ALMOST NEVER NEEDED: revenue
#: read as debits-less-credits comes out negative on every well-kept set of
#: books, and a figure that is negative everywhere is one everybody misreads as
#: a loss. Income, Liability and Equity are credit-balance roots; Asset and
#: Expense are debit-balance.
ROOT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")
CREDIT_ROOTS = ("Income", "Liability", "Equity")

SIGN_NATURAL = "natural"
SIGN_CREDIT = "credit_minus_debit"
SIGN_DEBIT = "debit_minus_credit"
SIGNS = (SIGN_NATURAL, SIGN_CREDIT, SIGN_DEBIT)

#: How deep a `kpi` input may reach before the chain is refused. Three is more
#: than any real KPI needs and short enough that a cycle somebody built by hand
#: is found on the first computation rather than by a worker running out of
#: stack at two in the morning.
MAX_KPI_DEPTH = 3

#: Most GL rows one input will read. The same ceiling `revenue` uses.
GL_ROW_CAP = 100000


def parse_inputs(raw) -> dict:
	"""`expression_inputs` as a validated `{name: spec}` mapping.

	Raises `ExpressionError` naming the variable and what was wrong with it.
	Accepts a dict as well as a JSON string, because the MCP tool takes one and
	the DocType field holds the other and they must not diverge in what they
	admit.
	"""
	if raw in (None, "", {}):
		return {}
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except (TypeError, ValueError) as exc:
			raise ExpressionError(
				f"expression_inputs is not valid JSON ({exc}). It is a JSON OBJECT mapping each "
				'variable name to its source, for example: {"revenue": {"source": "gl", '
				'"root_type": "Income"}}.'
			) from None
	if not isinstance(raw, dict):
		raise ExpressionError(
			f"expression_inputs must be a JSON object, not a {type(raw).__name__}. Each key is a "
			"variable name the expression uses, and each value says what that variable is."
		)

	out: dict = {}
	for name, spec in raw.items():
		key = str(name or "").strip()
		if not key:
			raise ExpressionError("expression_inputs has an entry with an empty variable name.")
		if not key.isidentifier() or key.startswith("_"):
			raise ExpressionError(
				f"{key!r} is not usable as a variable name. It has to be a plain identifier — "
				"letters, digits and underscores, not starting with a digit or an underscore — "
				"because it is what the expression writes."
			)
		out[key] = _parse_one_input(key, spec)
	return out


def _parse_one_input(name: str, spec) -> dict:
	if isinstance(spec, (int, float)) and not isinstance(spec, bool):
		# A bare number is the constant case written the short way. Accepted
		# because refusing it would only teach people to write the long form of
		# something entirely unambiguous.
		return {"source": SOURCE_CONSTANT, "value": float(spec)}
	if not isinstance(spec, dict):
		raise ExpressionError(
			f"the input {name!r} is a {type(spec).__name__}. Each input is a JSON object with a "
			f"`source` of {', '.join(SOURCES)} — or a bare number, which means a constant."
		)

	source = str(spec.get("source") or "").strip().lower()
	if source not in SOURCES:
		raise ExpressionError(
			f"the input {name!r} has source {spec.get('source')!r}. It must be one of: "
			f"`gl` (movement or balance on ledger accounts over the window), `report` (a component "
			f"of a built-in computer), `kpi` (another definition's value) or `constant` (a number "
			f"with a name)."
		)

	if source == SOURCE_CONSTANT:
		try:
			value = float(spec.get("value"))
		except (TypeError, ValueError):
			raise ExpressionError(
				f"the constant input {name!r} has no numeric `value`. A constant is a number with a "
				"name, which is what a magic number in a formula should always have been."
			) from None
		if not math.isfinite(value):
			raise ExpressionError(f"the constant input {name!r} is not a finite number.")
		return {"source": SOURCE_CONSTANT, "value": value}

	if source == SOURCE_KPI:
		target = str(spec.get("kpi_id") or "").strip()
		if not target:
			raise ExpressionError(
				f"the kpi input {name!r} names no `kpi_id`. It is the kpi_id of another "
				"Financial KPI Definition, whose value for the same window becomes this variable."
			)
		return {"source": SOURCE_KPI, "kpi_id": target}

	if source == SOURCE_REPORT:
		report_name = str(spec.get("report_name") or "").strip()
		if not report_name:
			raise ExpressionError(
				f"the report input {name!r} names no `report_name`. This site has: "
				f"{', '.join(sorted(windows.COMPUTERS)) or '<none>'}."
			)
		if report_name not in windows.COMPUTERS:
			raise ExpressionError(
				f"the report input {name!r} names {report_name!r}, which is not a computer this "
				f"site has. It has: {', '.join(sorted(windows.COMPUTERS)) or '<none>'}. A KPI "
				"pointing at a computer that does not exist produces nothing, for ever, quietly."
			)
		path = str(spec.get("path") or "").strip() or windows.COMPUTERS[report_name]["value_key"]
		return {"source": SOURCE_REPORT, "report_name": report_name, "path": path}

	# ── gl ──────────────────────────────────────────────────────────────
	root_type = str(spec.get("root_type") or "").strip()
	accounts = [str(entry).strip() for entry in (spec.get("accounts") or []) if str(entry).strip()]
	account_type = str(spec.get("account_type") or "").strip()
	prefix = str(spec.get("account_number_prefix") or "").strip()
	if not root_type and not accounts and not account_type and not prefix:
		raise ExpressionError(
			f"the gl input {name!r} narrows nothing. Give it at least one of `root_type` "
			f"({', '.join(ROOT_TYPES)}), `account_type`, `accounts` (an explicit list of account "
			"names) or `account_number_prefix`. An unnarrowed query would sum the entire chart of "
			"accounts to approximately zero, which is a number nobody would question."
		)
	if root_type and root_type not in ROOT_TYPES:
		raise ExpressionError(
			f"the gl input {name!r} has root_type {root_type!r}. ERPNext's chart has exactly five: "
			f"{', '.join(ROOT_TYPES)}."
		)
	sign = str(spec.get("sign") or SIGN_NATURAL).strip().lower()
	if sign not in SIGNS:
		raise ExpressionError(
			f"the gl input {name!r} has sign {sign!r}; it must be one of {', '.join(SIGNS)}. "
			"`natural` is almost always right: it reads credit-balance roots (Income, Liability, "
			"Equity) as credits less debits and debit-balance roots (Asset, Expense) the other "
			"way, so every figure comes out positive on a well-kept set of books."
		)
	return {
		"source": SOURCE_GL,
		"root_type": root_type,
		"account_type": account_type,
		"accounts": accounts,
		"account_number_prefix": prefix,
		"sign": sign,
		# A balance-sheet figure is a POSITION at the window's end, not a movement
		# across it. A current ratio computed from twelve months of movement in a
		# cash account is not a current ratio; it is a cash flow with a ratio's
		# name on it, and it will be wrong by whatever the opening balance was.
		"balance": bool(spec.get("balance")),
	}


def resolve_inputs(specs: dict, company: str, period_start: str, period_end: str, _seen=()) -> dict:
	"""Every variable's value for one window, with the trail that produced it.

	Returns `{"values": {name: float|None}, "resolved": [...], "warnings": [...]}`.
	`resolved` is what rides in the components dict and is the reason an Expression
	KPI is as defensible as a built-in one: a reader sees not just that
	`current_assets` was 412,000 but that it came from eleven accounts of root type
	Asset, read as a balance at the window's end.
	"""
	values: dict = {}
	resolved: list = []
	warnings: list = []
	for name, spec in (specs or {}).items():
		outcome = _resolve_one(name, spec, company, period_start, period_end, _seen)
		values[name] = outcome["value"]
		resolved.append(outcome["trail"])
		warnings.extend(outcome["warnings"])
	return {"values": values, "resolved": resolved, "warnings": warnings}


def _resolve_one(name: str, spec: dict, company: str, period_start: str, period_end: str, seen) -> dict:
	source = spec.get("source")
	trail = {"variable": name, "source": source}
	warnings: list = []

	if source == SOURCE_CONSTANT:
		trail["value"] = spec["value"]
		return {"value": spec["value"], "trail": trail, "warnings": warnings}

	if source == SOURCE_REPORT:
		entry = windows.COMPUTERS.get(spec["report_name"])
		trail["report_name"] = spec["report_name"]
		trail["path"] = spec["path"]
		if entry is None:
			warnings.append(
				f"{name!r} reads the built-in computer {spec['report_name']!r}, which is not "
				"registered on this site, so the KPI has no value."
			)
			trail["value"] = None
			return {"value": None, "trail": trail, "warnings": warnings}
		if entry.get("available") and not entry["available"]():
			warnings.append(
				f"{name!r} reads the built-in computer {spec['report_name']!r}, whose doctypes are "
				"not installed on this site, so the KPI has no value. `bench migrate` is the fix."
			)
			trail["value"] = None
			return {"value": None, "trail": trail, "warnings": warnings}
		components = entry["computer"](company, period_start, period_end) or {}
		value = windows.dig(components, spec["path"])
		for sentence in components.get("computation_warnings") or []:
			warnings.append(f"[{name}] {sentence}")
		trail["value"] = value
		return {"value": value, "trail": trail, "warnings": warnings}

	if source == SOURCE_KPI:
		target = spec["kpi_id"]
		trail["kpi_id"] = target
		if target in seen:
			chain = " → ".join([*seen, target])
			warnings.append(
				f"{name!r} reads the KPI {target!r}, which is already being computed in this chain "
				f"({chain}). A cycle produces no value, so nothing was computed. Break the loop by "
				"pointing one of the definitions at its inputs directly."
			)
			trail["value"] = None
			return {"value": None, "trail": trail, "warnings": warnings}
		if len(seen) >= MAX_KPI_DEPTH:
			warnings.append(
				f"{name!r} reads the KPI {target!r} at depth {len(seen) + 1}, past the ceiling of "
				f"{MAX_KPI_DEPTH}. A KPI built on a KPI built on a KPI is one nobody can read the "
				"provenance of; flatten it."
			)
			trail["value"] = None
			return {"value": None, "trail": trail, "warnings": warnings}
		row = definition_row(target)
		if not row:
			warnings.append(
				f"{name!r} reads the KPI {target!r}, which is not a definition on this site, so "
				"nothing was computed."
			)
			trail["value"] = None
			return {"value": None, "trail": trail, "warnings": warnings}
		inner = compute_point(row, company, period_start, period_end, _seen=(*seen, target))
		for sentence in inner.get("computation_warnings") or []:
			warnings.append(f"[{name}] {sentence}")
		trail["value"] = inner.get("value")
		return {"value": inner.get("value"), "trail": trail, "warnings": warnings}

	# ── gl ──────────────────────────────────────────────────────────────
	# Read from GL Entry rather than off a report, for the reason
	# `sustainable_cf_per_acre` computes OCF from GL: every figure traces back to
	# rows somebody can open, which is what makes it defensible to a lender.
	trail.update(
		{
			"root_type": spec.get("root_type") or None,
			"account_type": spec.get("account_type") or None,
			"accounts": list(spec.get("accounts") or []) or None,
			"account_number_prefix": spec.get("account_number_prefix") or None,
			"sign": spec.get("sign"),
			"balance": bool(spec.get("balance")),
		}
	)
	if not compat.doctype_exists("GL Entry"):
		warnings.append(
			f"{name!r} reads GL Entry, which this site has not got, so nothing was computed. Zero "
			"here would be the absence of a ledger rather than a period with no activity."
		)
		trail["value"] = None
		return {"value": None, "trail": trail, "warnings": warnings}

	filters: dict = {"company": company, "is_group": 0}
	if spec.get("root_type"):
		filters["root_type"] = spec["root_type"]
	if spec.get("account_type"):
		filters["account_type"] = spec["account_type"]
	if spec.get("accounts"):
		filters["name"] = ("in", list(spec["accounts"]))
	if spec.get("account_number_prefix"):
		filters["account_number"] = ("like", f"{spec['account_number_prefix']}%")

	try:
		accounts = frappe.db.get_all(
			"Account",
			filters=_supported("Account", filters),
			fields=compat.existing_fields("Account", ("name", "account_name", "account_number", "root_type")),
			limit=2000,
		)
	except Exception as exc:  # pragma: no cover - a chart this query cannot be run against
		warnings.append(f"{name!r} could not read the chart of accounts: {type(exc).__name__}: {exc}.")
		trail["value"] = None
		return {"value": None, "trail": trail, "warnings": warnings}

	trail["account_count"] = len(accounts or [])
	if not accounts:
		warnings.append(
			f"{name!r} matched no ledger account at {company}, so there is nowhere for it to have "
			"been booked. That is a chart-of-accounts gap rather than a result — "
			"get_chart_of_accounts shows what the company does have — and the KPI has no value "
			"rather than a zero, because a zero here would be read as an answer."
		)
		trail["value"] = None
		return {"value": None, "trail": trail, "warnings": warnings}

	names = [row["name"] for row in accounts]
	date_filter = ("<=", period_end) if spec.get("balance") else ("between", [period_start, period_end])
	try:
		entries = frappe.db.get_all(
			"GL Entry",
			filters={
				"company": company,
				"account": ("in", names),
				"posting_date": date_filter,
				"is_cancelled": 0,
			},
			fields=compat.existing_fields("GL Entry", ("name", "account", "debit", "credit")),
			limit=GL_ROW_CAP,
		)
	except Exception as exc:  # pragma: no cover
		warnings.append(f"{name!r} could not read GL Entry: {type(exc).__name__}: {exc}.")
		trail["value"] = None
		return {"value": None, "trail": trail, "warnings": warnings}

	root_types = {str(row.get("root_type") or "") for row in accounts}
	credit_positive = _credit_positive(spec.get("sign"), root_types)
	total = 0.0
	for row in entries or []:
		debit = float(row.get("debit") or 0)
		credit = float(row.get("credit") or 0)
		total += (credit - debit) if credit_positive else (debit - credit)

	trail["entry_count"] = len(entries or [])
	trail["read_as"] = "credits less debits" if credit_positive else "debits less credits"
	trail["basis"] = (
		"cumulative balance through period_end" if spec.get("balance") else "movement across the window"
	)
	trail["value"] = round(total, 2)
	if spec.get("sign") == SIGN_NATURAL and len(root_types) > 1:
		warnings.append(
			f"{name!r} matched accounts across {len(root_types)} root types "
			f"({', '.join(sorted(root_types))}) and the sign is `natural`, which reads them all the "
			f"same way ({trail['read_as']}). Where the roots genuinely differ in which side they "
			"sit on, the figure is the difference between them rather than the sum — name the "
			"accounts explicitly, or set `sign` and mean it."
		)
	return {"value": trail["value"], "trail": trail, "warnings": warnings}


def _credit_positive(sign: str, root_types) -> bool:
	if sign == SIGN_CREDIT:
		return True
	if sign == SIGN_DEBIT:
		return False
	# Natural: the majority root wins, and the disagreement is warned about above.
	credit_roots = sum(1 for root in root_types if root in CREDIT_ROOTS)
	return credit_roots > (len(root_types) - credit_roots)


def _supported(doctype: str, filters: dict) -> dict:
	"""Drop filters on columns this site's doctype has not got.

	A site whose Account has no `account_number` is a site where narrowing by
	prefix cannot be done — and silently narrowing by nothing instead would be
	worse than either failing or dropping it, so the drop is reported by the
	caller through `account_count`.
	"""
	out = {}
	for field, condition in filters.items():
		try:
			if field in ("name",) or compat.has_field(doctype, field):
				out[field] = condition
		except Exception:  # pragma: no cover
			out[field] = condition
	return out


# ════════════════════════════════════════════════════════════════════════════
# THE RECORDS
# ════════════════════════════════════════════════════════════════════════════
def available() -> bool:
	"""Is the definition doctype on this site yet?"""
	try:
		return bool(compat.doctype_exists(DOCTYPE))
	except Exception:  # pragma: no cover
		return False


def rows(filters: dict | None = None, limit: int = DEFINITION_CAP) -> list:
	"""Definitions matching `filters`, in dashboard order."""
	if not available():
		return []
	try:
		return frappe.db.get_all(
			DOCTYPE,
			filters=filters or {},
			fields=compat.existing_fields(DOCTYPE, FIELDS),
			order_by="display_order asc, title asc",
			limit=limit,
		)
	except Exception:  # pragma: no cover - a site mid-migrate
		return []


def definition_row(reference: str) -> dict:
	"""One definition by `kpi_id` or by docname, or `{}`.

	BOTH SPELLINGS, because `kpi_id` is what a person and a model both use and
	the docname is what a link field holds, and a function that took only one of
	them would put the lookup at every call site.
	"""
	key = str(reference or "").strip()
	if not key or not available():
		return {}
	found = rows({"kpi_id": key}, limit=1)
	if found:
		return dict(found[0])
	found = rows({"name": key}, limit=1)
	return dict(found[0]) if found else {}


def enabled_rows(company: str = "", dashboard_only: bool = False) -> list:
	"""Every enabled definition that applies to `company`, in dashboard order.

	A definition with an EMPTY company applies to every company, which is the
	ordinary case — a current ratio is a current ratio wherever it is computed.
	One with a company set applies only there.
	"""
	found = [row for row in rows({"enabled": 1}) if _applies(row, company)]
	if dashboard_only:
		found = [row for row in found if int(row.get("dashboard_visible") or 0)]
	return found


def _applies(row: dict, company: str) -> bool:
	scoped = str(row.get("company") or "").strip()
	return not scoped or not company or scoped == company


def describe(row: dict) -> dict:
	"""One definition as every tool reports it."""
	row = dict(row or {})
	inputs_raw = row.get("expression_inputs")
	try:
		inputs = parse_inputs(inputs_raw)
		inputs_error = ""
	except ExpressionError as exc:
		inputs = {}
		inputs_error = str(exc)
	return {
		"name": row.get("name"),
		"kpi_id": row.get("kpi_id"),
		"title": row.get("title"),
		"description": row.get("description") or None,
		"enabled": bool(int(row.get("enabled") or 0)),
		"company": row.get("company") or None,
		"applies_to": row.get("company") or "every company",
		"category": row.get("category"),
		"unit": row.get("unit"),
		"formula_type": row.get("formula_type"),
		"builtin_function": row.get("builtin_function") or None,
		"expression": row.get("expression") or None,
		"expression_inputs": inputs or None,
		"expression_inputs_error": inputs_error or None,
		"default_window_type": row.get("default_window_type") or DEFAULT_WINDOW_TYPE,
		"default_window_months": int(row.get("default_window_months") or DEFAULT_WINDOW_MONTHS),
		"default_computation_step": row.get("default_computation_step") or DEFAULT_STEP,
		"historical_averaging_enabled": bool(int(row.get("historical_averaging_enabled") or 0)),
		"historical_lookback_years": int(row.get("historical_lookback_years") or DEFAULT_LOOKBACK_YEARS),
		"thresholds": thresholds_of(row),
		"dashboard_visible": bool(int(row.get("dashboard_visible") or 0)),
		"display_order": int(row.get("display_order") or 0),
	}


def thresholds_of(row: dict) -> dict:
	"""The four thresholds as numbers or `None`. EMPTY IS NOT ZERO.

	The distinction is the whole of reading a threshold correctly: an empty
	`threshold_warning_low` means low is not bad for this KPI, and a zero there
	means a negative value is a warning. On a cash-flow KPI those are two very
	different operations.
	"""
	out = {}
	for field in (
		"threshold_warning_low",
		"threshold_critical_low",
		"threshold_warning_high",
		"threshold_critical_high",
	):
		raw = row.get(field)
		out[field] = None if raw in (None, "") else float(raw)
	out["has_any"] = any(value is not None for value in out.values())
	return out


def threshold_status(row: dict, value) -> dict:
	"""Where one value sits against one definition's thresholds.

	CRITICAL BEATS WARNING AND BOTH BEAT OK, checked in that order, so a value
	past both lines reports the worse one. A value with no thresholds to check
	reports `No thresholds` rather than `OK` — "nobody set a line" and "inside the
	line" are different statements and a dashboard that showed them the same
	green would be claiming something nobody checked.
	"""
	limits = thresholds_of(row)
	out = {
		"status": STATUS_UNKNOWN,
		"breached": False,
		"threshold": None,
		"direction": None,
		"message": "",
		**{key: limits[key] for key in limits if key != "has_any"},
	}
	if value is None:
		out["message"] = (
			"there is no value for this window, so no threshold could be checked. That is not a "
			"pass — read the computation warnings, which say why the figure is missing."
		)
		return out
	if not limits["has_any"]:
		out["status"] = STATUS_NO_THRESHOLDS
		out["message"] = (
			"no thresholds are set on this definition, so nothing here says whether the value is "
			"good. That is a legitimate state for a KPI somebody watches by eye, and it is not the "
			"same as being inside a line."
		)
		return out

	number = float(value)
	for field, status, direction, word in (
		("threshold_critical_low", STATUS_CRITICAL, "low", "at or below the critical floor"),
		("threshold_critical_high", STATUS_CRITICAL, "high", "at or above the critical ceiling"),
		("threshold_warning_low", STATUS_WARNING, "low", "at or below the warning floor"),
		("threshold_warning_high", STATUS_WARNING, "high", "at or above the warning ceiling"),
	):
		limit = limits[field]
		if limit is None:
			continue
		if (direction == "low" and number <= limit) or (direction == "high" and number >= limit):
			out["status"] = status
			out["breached"] = True
			out["threshold"] = limit
			out["direction"] = direction
			out["message"] = f"{number} is {word} of {limit}."
			return out

	out["status"] = STATUS_OK
	out["message"] = f"{number} is inside every threshold set on this definition."
	return out


# ════════════════════════════════════════════════════════════════════════════
# COMPUTING
# ════════════════════════════════════════════════════════════════════════════
def computer_for(row: dict):
	"""The `(company, period_start, period_end) -> components` callable for one definition.

	THIS IS THE JOIN BETWEEN THE FRAMEWORK AND THE WINDOW STANDARD. Everything
	downstream — the boundaries, the fiscal anchoring, the cache, the history,
	the statistics — takes one of these and does not care which of the two
	formula types produced it.
	"""
	formula_type = str(row.get("formula_type") or FORMULA_BUILTIN)
	if formula_type == FORMULA_BUILTIN:
		entry = windows.COMPUTERS.get(str(row.get("builtin_function") or "").strip())
		if entry is None:
			raise ExpressionError(
				f"{row.get('kpi_id')} is a Built-in KPI naming {row.get('builtin_function')!r}, "
				f"which is not a computer this site has. It has: "
				f"{', '.join(sorted(windows.COMPUTERS)) or '<none>'}."
			)
		return entry["computer"]

	specs = parse_inputs(row.get("expression_inputs"))
	expression = str(row.get("expression") or "")
	check_expression(expression, specs.keys())

	def compute(company: str, period_start: str, period_end: str, _seen=()) -> dict:
		resolved = resolve_inputs(specs, company, period_start, period_end, _seen)
		outcome = evaluate(expression, resolved["values"])
		return {
			"company": company,
			"period_start": period_start,
			"period_end": period_end,
			"value": outcome["value"],
			"expression": expression,
			"inputs": resolved["resolved"],
			"input_values": resolved["values"],
			"computation_warnings": [*resolved["warnings"], *outcome["warnings"]],
		}

	return compute


def compute_point(row: dict, company: str, period_start: str, period_end: str, _seen=()) -> dict:
	"""One definition's components over one explicit period. No window, no cache.

	The inner call a `kpi` input makes, and the honest primitive underneath
	`compute_kpi`: the window machinery decides WHICH period, and this computes
	one. `_seen` is the chain of kpi_ids already being computed, which is how a
	cycle is caught on the first pass rather than by a worker running out of
	stack.
	"""
	try:
		computer = computer_for(row)
	except ExpressionError as exc:
		return {
			"company": company,
			"period_start": period_start,
			"period_end": period_end,
			"value": None,
			"computation_warnings": [str(exc)],
		}
	try:
		if str(row.get("formula_type") or FORMULA_BUILTIN) == FORMULA_EXPRESSION:
			return computer(company, period_start, period_end, _seen=_seen) or {}
		return computer(company, period_start, period_end) or {}
	except Exception as exc:  # pragma: no cover - one KPI must not take a dashboard down
		return {
			"company": company,
			"period_start": period_start,
			"period_end": period_end,
			"value": None,
			"computation_warnings": [
				f"{row.get('kpi_id')} could not be computed for this window: "
				f"{type(exc).__name__}: {exc}. Every other KPI still answered."
			],
		}


def _entry_for(row: dict) -> dict:
	"""The `windowed_reports.register`-shaped entry one definition computes through.

	Built per call rather than registered in `windows.COMPUTERS`, and that is
	deliberate: `COMPUTERS` is the registry of computers this app SHIPS, read by
	`get_windowed_report` and by the overnight history sweep, and populating it
	from the database at import time would make the tool surface depend on what
	somebody typed into a form. A definition reaches the same machinery by
	passing its entry in.

	A BUILT-IN KPI INHERITS ITS COMPUTER'S ENTRY, so its `sum_keys`,
	`weighted_keys` and `list_keys` are the shipped ones and its bucket
	aggregation behaves exactly as `get_windowed_report` does for the same
	figure. The framework changes which window is asked for, never the
	arithmetic.
	"""
	kpi_id = str(row.get("kpi_id") or "")
	label = str(row.get("title") or kpi_id)
	unit = str(row.get("unit") or "")
	formula_type = str(row.get("formula_type") or FORMULA_BUILTIN)

	if formula_type == FORMULA_BUILTIN:
		base = windows.COMPUTERS.get(str(row.get("builtin_function") or "").strip())
		if base is not None:
			return {**base, "report_name": kpi_id, "kpi_key": kpi_id, "label": label, "unit": unit}

	return {
		"report_name": kpi_id,
		"kpi_key": kpi_id,
		"label": label,
		"computer": None,
		"value_key": "value",
		"sum_keys": (),
		"weighted_keys": (),
		"list_keys": ("inputs",),
		# AN EXPRESSION KPI IS NEVER BUCKET-ADDITIVE. Most of them are ratios, and
		# a ratio assembled from twelve monthly ratios is the average of twelve
		# ratios rather than the ratio of twelve months — which is a different
		# number, always, and a worse one. The window is computed whole.
		"bucket_additive": False,
		"default_step": str(row.get("default_computation_step") or DEFAULT_STEP),
		"allow_daily": str(row.get("default_computation_step") or "") == windows.STEP_DAILY,
		"unit": unit,
		"available": None,
	}


def window_options(row: dict, overrides: dict | None = None) -> dict:
	"""The definition's window defaults, with a caller's overrides on top.

	The definition is the default and the caller is the override, which is the
	right way round: a KPI that says it is a TTM figure computed monthly is one
	whose dashboard, alerts and cache all agree without anybody passing anything,
	and a caller who wants last quarter can still ask for it.
	"""
	overrides = {key: value for key, value in (overrides or {}).items() if value not in (None, "")}
	window_type = str(overrides.get("window_type") or row.get("default_window_type") or DEFAULT_WINDOW_TYPE)
	step = str(overrides.get("computation_step") or row.get("default_computation_step") or DEFAULT_STEP)
	months = overrides.get("window_months") or row.get("default_window_months") or DEFAULT_WINDOW_MONTHS
	lookback = overrides.get("historical_lookback_years")
	if lookback in (None, ""):
		lookback = row.get("historical_lookback_years")
	if lookback in (None, ""):
		lookback = DEFAULT_LOOKBACK_YEARS
	averaging = overrides.get("historical_averaging_enabled")
	if averaging is None:
		averaging = bool(int(row.get("historical_averaging_enabled") or 0))
	return {
		"as_of": overrides.get("as_of") or frappe.utils.today(),
		"window_type": window_type,
		"window_months": int(months),
		"computation_step": step,
		"historical_lookback_years": int(lookback),
		"historical_averaging_enabled": bool(averaging),
	}


def compute_kpi(
	reference,
	company: str,
	use_cache: bool = True,
	**overrides,
) -> dict:
	"""ONE KPI, over its own window, with its history and its threshold status.

	`reference` is a kpi_id, a docname, or a definition row already read. The
	return is the `compute_windowed` payload — `point_in_time`, `window`,
	`historical_averages`, `computation_warnings` — with the definition and the
	threshold verdict added beside it.

	IT NEVER RAISES FOR A REASON THAT IS ABOUT ONE KPI. A definition pointing at
	a computer that does not exist, an expression that no longer parses, a query
	that matched no accounts: each of those produces a payload with a null value
	and a warning saying which, because `compute_all_kpis` walks a dashboard and
	one broken definition must not empty it. It DOES raise for a reference that
	names no definition at all, which is a caller error rather than a data one.
	"""
	row = reference if isinstance(reference, dict) else definition_row(reference)
	if not row:
		raise ExpressionError(
			f"no Financial KPI Definition called {reference!r} is on this site. "
			"list_financial_kpi_definitions has the register."
		)

	options = window_options(row, overrides)
	entry = _entry_for(row)
	warnings: list = []

	try:
		computer = computer_for(row)
	except ExpressionError as exc:
		warnings.append(str(exc))
		computer = None

	if computer is None:
		report = {
			"as_of": options["as_of"],
			"company": company,
			"report_name": row.get("kpi_id"),
			"kpi_key": row.get("kpi_id"),
			"label": row.get("title"),
			"unit": row.get("unit"),
			"window_type": options["window_type"],
			"window_months": options["window_months"],
			"computation_step": options["computation_step"],
			"point_in_time": {"value": None},
			"window": {"value": None},
			"historical_averages": {},
			"computation_warnings": warnings,
		}
	else:
		# An Expression computer takes an extra `_seen` keyword that the window
		# standard knows nothing about, so it is bound off here rather than being
		# a shape `compute_windowed` has to learn.
		if str(row.get("formula_type") or FORMULA_BUILTIN) == FORMULA_EXPRESSION:
			bound = computer

			def computer(company_arg, period_start, period_end, _bound=bound):
				return _bound(company_arg, period_start, period_end, _seen=(str(row.get("kpi_id")),))

		try:
			report = windows.compute_windowed(
				computer,
				company,
				entry=entry,
				kpi_key=str(row.get("kpi_id")),
				use_cache=use_cache,
				**options,
			)
		except Exception as exc:  # pragma: no cover - one KPI must not empty a dashboard
			report = {
				"as_of": options["as_of"],
				"company": company,
				"report_name": row.get("kpi_id"),
				"kpi_key": row.get("kpi_id"),
				"label": row.get("title"),
				"unit": row.get("unit"),
				"window_type": options["window_type"],
				"window_months": options["window_months"],
				"computation_step": options["computation_step"],
				"point_in_time": {"value": None},
				"window": {"value": None},
				"historical_averages": {},
				"computation_warnings": [
					f"{row.get('kpi_id')} could not be computed: {type(exc).__name__}: {exc}."
				],
			}
		report["computation_warnings"] = [*warnings, *(report.get("computation_warnings") or [])]

	value = (report.get("window") or {}).get("value")
	report["definition"] = describe(row)
	report["value"] = value
	report["threshold_status"] = threshold_status(row, value)
	return report


def compute_all_kpis(
	company: str,
	dashboard_only: bool = False,
	category: str = "",
	include_historical_averages=None,
	use_cache: bool = True,
	**overrides,
) -> dict:
	"""Every enabled KPI for one company, in dashboard order, with its thresholds.

	THE WHOLE DASHBOARD IN ONE CALL, and the reason it is one call rather than N
	is the same reason `get_windowed_report` is one tool rather than one per
	report: a framework whose every KPI costs a round trip is a framework with
	six KPIs in it.

	ONE BROKEN DEFINITION DOES NOT EMPTY THE DASHBOARD. Each KPI is computed
	independently and a failure becomes a null value with a warning on that row,
	which is the same promise the compliance sweep makes about one broken rule.
	"""
	definitions = enabled_rows(company, dashboard_only=dashboard_only)
	if category:
		definitions = [row for row in definitions if str(row.get("category") or "") == category]

	if include_historical_averages is not None:
		overrides = {**overrides, "historical_averaging_enabled": bool(include_historical_averages)}

	results = []
	for row in definitions[:DEFINITION_CAP]:
		try:
			results.append(compute_kpi(row, company, use_cache=use_cache, **overrides))
		except Exception as exc:  # pragma: no cover
			results.append(
				{
					"kpi_key": row.get("kpi_id"),
					"label": row.get("title"),
					"definition": describe(row),
					"value": None,
					"threshold_status": threshold_status(row, None),
					"computation_warnings": [f"{type(exc).__name__}: {exc}"],
				}
			)

	breached = [item for item in results if (item.get("threshold_status") or {}).get("breached")]
	warnings = []
	if len(definitions) > DEFINITION_CAP:
		warnings.append(
			f"{company} has {len(definitions)} enabled KPI definitions and this call computed the "
			f"first {DEFINITION_CAP} in dashboard order. The rest are not broken and are not "
			"missing; they were not reached. Narrow with `category` or `dashboard_only`."
		)
	if not definitions:
		warnings.append(
			f"no enabled Financial KPI Definition applies to {company}. That is an empty framework "
			"rather than an empty ledger — create_financial_kpi_definition adds one, and the "
			"install seeds Sustainable CF/Acre."
		)
	return {
		"company": company,
		"as_of": overrides.get("as_of") or frappe.utils.today(),
		"kpi_count": len(results),
		"definition_count": len(definitions),
		"kpis": results,
		"breached": [
			{
				"kpi_id": item.get("kpi_key"),
				"title": (item.get("definition") or {}).get("title"),
				"value": item.get("value"),
				"status": (item.get("threshold_status") or {}).get("status"),
				"message": (item.get("threshold_status") or {}).get("message"),
			}
			for item in breached
		],
		"breach_count": len(breached),
		"computation_warnings": warnings,
	}


# ════════════════════════════════════════════════════════════════════════════
# THE CACHE
# ════════════════════════════════════════════════════════════════════════════
def refresh_kpi_cache(
	kpi_id: str = "",
	company: str = "",
	back_years: int = DEFAULT_LOOKBACK_YEARS,
	force: bool = False,
) -> dict:
	"""Fill `Financial KPI History` for one KPI, or for every enabled one.

	THE ONLY THING IT CAN CHANGE IS A CACHE. Every row it writes is what the live
	computation would have produced for that window, and every row it deletes
	comes back on the next read or the next overnight run. Nothing here is the
	only copy of anything, so the worst outcome of running it at the wrong moment
	is time spent.

	INCREMENTAL UNLESS `force`. It walks back from the last completed boundary
	and computes only the snapshots that are missing, so a warm cache does almost
	nothing. `force` clears the KPI's series first, which is what a release that
	changed a built-in computer's arithmetic needs — an incremental fill would
	leave the old rows in place and the chart would hold two definitions of one
	number with nothing marking the join.
	"""
	report = {
		"kpi_id": kpi_id or None,
		"company": company or None,
		"back_years": int(back_years),
		"force": bool(force),
		"written": 0,
		"cleared": 0,
		"per_kpi": [],
		"skipped": [],
		"computation_warnings": [],
	}
	if not available():
		report["computation_warnings"].append(
			f"this site has no {DOCTYPE} doctype yet — it ships with erpnext_mcp and arrives with "
			"`bench --site <site> migrate`. Nothing was refreshed."
		)
		return report
	if not windows.cache_available():
		report["computation_warnings"].append(
			f"this site has no {HISTORY_DOCTYPE} doctype, so there is nowhere to cache anything. "
			"Every KPI still computes; it just recomputes on every call and says so."
		)
		return report

	try:
		companies = [company] if company else (frappe.db.get_all("Company", pluck="name", limit=200) or [])
	except Exception as exc:  # pragma: no cover
		report["computation_warnings"].append(f"the company list could not be read: {exc}")
		return report

	definitions = [definition_row(kpi_id)] if kpi_id else enabled_rows()
	definitions = [row for row in definitions if row]
	if kpi_id and not definitions:
		report["computation_warnings"].append(
			f"no Financial KPI Definition called {kpi_id!r} is on this site, so nothing was refreshed."
		)
		return report

	for row in definitions:
		for target in companies:
			if not _applies(row, str(target)):
				continue
			outcome = _refresh_one(row, str(target), int(back_years), bool(force))
			report["written"] += outcome["written"]
			report["cleared"] += outcome["cleared"]
			if outcome.get("skipped"):
				report["skipped"].append(outcome)
			else:
				report["per_kpi"].append(outcome)
	return report


def _refresh_one(row: dict, company: str, back_years: int, force: bool) -> dict:
	"""One definition on one company. Never raises — one KPI must not stop the rest."""
	kpi_key = str(row.get("kpi_id") or "")
	options = window_options(row)
	step = options["computation_step"]
	window_type = options["window_type"]
	window_months = options["window_months"]
	out = {
		"kpi_id": kpi_key,
		"company": company,
		"computation_step": step,
		"window_type": window_type,
		"window_months": window_months,
		"written": 0,
		"cleared": 0,
		"skipped": "",
	}

	try:
		computer = computer_for(row)
	except ExpressionError as exc:
		out["skipped"] = str(exc)
		return out

	if str(row.get("formula_type") or FORMULA_BUILTIN) == FORMULA_EXPRESSION:
		bound = computer

		def computer(company_arg, period_start, period_end, _bound=bound):
			return _bound(company_arg, period_start, period_end, _seen=(kpi_key,))

	entry = {**_entry_for(row), "computer": computer}

	if force:
		out["cleared"] = windows.clear(kpi_key, company, step)

	try:
		earliest = windows.earliest_posting(company)
	except Exception:  # pragma: no cover
		earliest = ""
	if not earliest:
		out["skipped"] = (
			f"{company} has no posted GL entry, so there is no ledger to compute a window over. "
			"That is an empty book rather than a broken KPI."
		)
		return out

	try:
		anchor_month = windows.fiscal_year_start_month(company)
		today = windows.as_date(frappe.utils.today())
		newest = windows.last_completed_boundary(today, step, anchor_month)
	except Exception as exc:  # pragma: no cover
		out["skipped"] = f"the window boundaries could not be computed: {type(exc).__name__}: {exc}"
		return out

	wanted = max(0, int(back_years)) * windows.STEPS_PER_YEAR[step]
	for index in range(0, wanted + 1):
		try:
			boundary = windows.step_back(newest, step, index, anchor_month)
			if windows.iso(boundary) < earliest:
				break
			if windows.cache_read(kpi_key, company, step, window_type, window_months, boundary):
				continue
			start = windows.window_start(boundary, window_months)
			windows.compute_snapshot(
				entry,
				company,
				start,
				boundary,
				step,
				use_cache=True,
				window_type=window_type,
				window_months=window_months,
				as_of=boundary,
			)
			out["written"] += 1
		except Exception as exc:  # pragma: no cover - one snapshot must not stop the series
			out["skipped"] = f"stopped at snapshot {index}: {type(exc).__name__}: {exc}"
			break
	return out


def refresh_all_kpi_caches() -> int:
	"""The overnight job. Every enabled definition, every company. NEVER RAISES.

	ONE JOB THAT ITERATES, not a cron entry per KPI — the same constraint
	`recompute_kpi_history_incremental` is written under, and it matters more
	here for the obvious reason: the whole point of the framework is that an
	operator can add a KPI without a code release, and a KPI that needed a
	scheduler entry would not be one they could add.

	IT RUNS AFTER THE SHIPPED-REPORT SWEEP AND BEFORE THE REGULATION FEED, at
	three in the morning, for the reason the two-o'clock job is at two: this is
	arithmetic over somebody's whole ledger and it should not be competing with
	the working day. It writes only this app's own `Financial KPI History`.

	Returns the number of snapshots written, which is what the scheduler log
	shows.
	"""
	try:
		if not available() or not windows.cache_available():
			return 0
		if not _sweep_enabled():
			return 0
		report = refresh_kpi_cache()
	except Exception:  # pragma: no cover - a site mid-migrate
		return 0
	try:
		frappe.db.commit()
	except Exception:  # pragma: no cover
		pass
	return int(report.get("written") or 0)


def _sweep_enabled() -> bool:
	"""The kill switch, shared with the shipped-report sweep.

	ONE SWITCH FOR BOTH JOBS, deliberately. They cache the same doctype for the
	same reason and an operator who turned one off to get their nights back would
	be surprised to find the other still running for minutes — and a second
	checkbox called something almost identical is how a setting stops being read.

	True on any failure to read it, which is the opposite of how a security
	control fails and the right way round for this one: it writes only a cache.
	"""
	try:
		from .. import settings

		return settings.kpi_history_sweep_enabled()
	except Exception:  # pragma: no cover
		return True


# ════════════════════════════════════════════════════════════════════════════
# THE SEED
# ════════════════════════════════════════════════════════════════════════════
#: The definitions the install writes. ONE, and that is the argument rather than
#: a starting point somebody has not got round to extending.
#:
#: A seeded KPI is a claim that this app knows what an operation should watch,
#: and it can only honestly make that claim about a metric it also ships the
#: computer for. Sustainable CF/Acre qualifies: `docs/kpi_sustainable_cf_per_acre.md`
#: is four pages on why headline operating cash flow is flattered in two
#: directions at once, the computer is tested, and the KPI is the reason v0.19.5
#: exists. A current ratio does not — not because it is a bad metric, but because
#: the accounts that make it up are named differently on every chart, and a
#: seeded definition pointing at the wrong ones would put a confident, precise,
#: wrong number on somebody's dashboard from the day they installed the app.
#:
#: THE `kpi_id` IS THE ONE THE CACHE ALREADY USES. Every Financial KPI History
#: row written since v0.19.6 carries `kpi_key = "sustainable_cf_per_acre"`, so
#: the seeded definition adopts that series rather than starting a second one
#: beside it under a new name — which is exactly the unmarked join this module's
#: docstring argues against.
SEED_DEFINITIONS = (
	{
		"kpi_id": "sustainable_cf_per_acre",
		"title": "Sustainable Cash Flow per Acre",
		"category": CATEGORY_PROFITABILITY,
		"unit": UNIT_CURRENCY,
		"formula_type": FORMULA_BUILTIN,
		"builtin_function": "sustainable_cf_per_acre",
		"default_window_type": windows.WINDOW_TTM,
		"default_window_months": 12,
		"default_computation_step": windows.STEP_MONTHLY,
		"historical_averaging_enabled": 1,
		"historical_lookback_years": 5,
		"dashboard_visible": 1,
		"display_order": 10,
		"description": (
			"(Normalized operating cash flow - maintenance capex) / productive acres. What is left "
			"per productive acre after the non-recurring items are taken back out and the "
			"replacement of what wore out is funded — the number that says whether the operation "
			"can pay its owners AND stay whole. Headline OCF is flattered in two directions at "
			"once: by money that will not come in again, and by maintenance that was not done. A "
			"farm running its irrigation to failure makes a year look good while destroying the "
			"thing the year was earned with, and the headline figure goes UP while it happens. "
			"NO THRESHOLDS ARE SEEDED, because a defensible floor is a number about one "
			"operation's own cost structure and debt service — set them from your own history, "
			"which list_financial_kpi_history has."
		),
	},
)


def seed_kpi_definitions() -> dict:
	"""One definition per entry in `SEED_DEFINITIONS`. Idempotent, and never raises.

	CHECKED BY `kpi_id`, AND AN EDITED DEFINITION IS LEFT ALONE — the same rule
	the compliance rule seeder follows and for the same reason. An operator who
	disabled Sustainable CF/Acre because their operation does not report it stays
	disabled; one who set thresholds on it keeps them; one who moved it down the
	dashboard does not find it back at the top after every migrate.
	"""
	report = {"created": [], "present": [], "failed": []}
	if not available():
		return report
	for spec in SEED_DEFINITIONS:
		kpi_id = spec["kpi_id"]
		try:
			if definition_row(kpi_id):
				report["present"].append(kpi_id)
				continue
			doc = frappe.new_doc(DOCTYPE)
			for field, value in {"enabled": 1, **spec}.items():
				setattr(doc, field, value)
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)
			report["created"].append(kpi_id)
		except Exception as exc:  # pragma: no cover - the seeder swallows its own
			report["failed"].append({"name": kpi_id, "reason": f"{type(exc).__name__}: {exc}"})
	return report


def builtin_names() -> list:
	"""The built-in computers a definition may name on this site."""
	return sorted(windows.COMPUTERS)
