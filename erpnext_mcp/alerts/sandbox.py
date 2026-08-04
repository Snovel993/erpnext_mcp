# SPDX-License-Identifier: MIT
"""The `custom_python` evaluator: a restricted interpreter, not an `exec`.

THIS FILE IS THE ONE PLACE IN THE APP WHERE TEXT SOMEBODY TYPED INTO A FIELD
BECOMES BEHAVIOUR, WHICH IS WHY IT IS WRITTEN THE WAY IT IS. A Compliance Rule
is data; `custom_python` is the one column on it that is not merely read. So it
is not handed to `exec`, it is not handed to `eval`, and it never touches the
real builtins. It is parsed to an AST, every node is checked against an
allowlist, and the allowed nodes are then walked by an interpreter in this
module that can only reach the names it was given.

WHY NOT RestrictedPython OR asteval. Both are good libraries and neither is on
this bench. `pyproject.toml` has three runtime dependencies, each argued for,
each imported defensively so a bench missing one loses a named feature rather
than the app — and adding a fourth for a field that ships used by ZERO of the
thirteen rules would be the tail wagging the dog. The subset actually needed
here is small and closed: read some rows, compare some dates, build some
observations. An interpreter for that subset is a few hundred lines, has no
supply chain, and refuses by construction rather than by configuration.

WHAT IS REFUSED, AND WHY EACH ONE IS ON THE LIST:

  * `import` / `__import__` — the whole game. One import is `os`, and `os` is
    the filesystem.
  * `exec`, `eval`, `compile`, `open`, `globals`, `locals`, `vars`, `getattr`,
    `setattr`, `delattr`, `input`, `__build_class__` — each of them is a way to
    get back to the interpreter the allowlist just took away.
  * EVERY DUNDER ATTRIBUTE, and every attribute starting with an underscore.
    `x.__class__.__bases__[0].__subclasses__()` is the standard escape from
    every sandbox that forgot this, and it needs no imports at all.
  * `while` — unbounded by construction. `for` over a finite sequence is
    bounded by the sequence, and the step counter bounds it again.
  * `def`, `class`, `lambda`, `yield` — a rule that needs to define a function
    is a rule that has outgrown this field and should be a shipped scanner.
  * `try` / `except` — a rule that swallows its own errors is a rule that goes
    quiet, and a compliance rule going quiet is the failure mode this whole
    app is written against. Errors here are reported, loudly, in the sweep.
  * `with`, `del`, `global`, `nonlocal`, `assert`, `await` — no use case, and
    each is a surface.

WHAT IS BOUNDED. A step counter (every node visit costs one) and a wall clock,
both checked in the same place, so neither a tight loop nor a slow query can
hold the sweep. Exceeding either raises `SandboxError`, which the engine turns
into a reported failure on that ONE rule; the other rules still run, because
`base.refresh_compliance_alerts` has never let one rule take the sweep down.

WHAT `frappe` IS HERE. Not the module. A facade with four readers on it
(`get_all`, `get_value`, `get_doc`, `exists`, `count`) and a `utils` namespace,
and `get_doc` HANDS BACK A PLAIN DICT rather than a Document — because a
Document has `.save()` on it, and a read-only sandbox that returns a live
document is a read-only sandbox in the same sense a locked door with the key in
it is locked.
"""

from __future__ import annotations

import ast
import time

#: Wall-clock ceiling for one rule's program, in seconds. The sweep runs on
#: somebody's scheduler beside their real work; a rule that wants six seconds is
#: a rule with a query in a loop, and that is worth being told about rather than
#: worth waiting for.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: Node visits before the interpreter gives up. Generous for anything this field
#: is meant to hold — the whole of the most complex shipped rule is a few
#: thousand — and far short of a loop that will not finish.
DEFAULT_MAX_STEPS = 200_000

#: How often to look at the clock, in steps. Checking `time.monotonic()` on
#: every node visit is most of the cost of visiting one.
_CLOCK_EVERY = 512


class SandboxError(Exception):
	"""A program that was refused, or that ran away. Never silent."""


# ── the grammar ─────────────────────────────────────────────────────────────
#: Statement nodes the interpreter will execute. Everything else is refused by
#: name at check time, before a single step runs.
_ALLOWED_STATEMENTS = (
	ast.Module,
	ast.Expr,
	ast.Assign,
	ast.AugAssign,
	ast.For,
	ast.If,
	ast.Return,
	ast.Pass,
	ast.Break,
	ast.Continue,
)

#: Expression nodes the interpreter will evaluate.
_ALLOWED_EXPRESSIONS = (
	ast.Constant,
	ast.Name,
	ast.Load,
	ast.Store,
	ast.BinOp,
	ast.UnaryOp,
	ast.BoolOp,
	ast.Compare,
	ast.Call,
	ast.keyword,
	ast.Attribute,
	ast.Subscript,
	ast.Slice,
	ast.List,
	ast.Tuple,
	ast.Dict,
	ast.Set,
	ast.ListComp,
	ast.SetComp,
	ast.DictComp,
	ast.GeneratorExp,
	ast.comprehension,
	ast.IfExp,
	ast.JoinedStr,
	ast.FormattedValue,
	# operators and comparators, which are leaf marker nodes
	ast.Add,
	ast.Sub,
	ast.Mult,
	ast.Div,
	ast.FloorDiv,
	ast.Mod,
	ast.Pow,
	ast.USub,
	ast.UAdd,
	ast.Not,
	ast.And,
	ast.Or,
	ast.Eq,
	ast.NotEq,
	ast.Lt,
	ast.LtE,
	ast.Gt,
	ast.GtE,
	ast.In,
	ast.NotIn,
	ast.Is,
	ast.IsNot,
)

_ALLOWED_NODES = _ALLOWED_STATEMENTS + _ALLOWED_EXPRESSIONS

#: Nodes refused with a sentence rather than with "unsupported node". A refusal
#: an author cannot act on is a refusal that gets worked around.
_REFUSALS = {
	ast.Import: (
		"`import` is refused. One import is `os` and `os` is the filesystem — a compliance "
		"rule reads records and compares dates, and everything it needs for that is already "
		"in scope (frappe, today, datetime, timedelta, days_until, days_since)."
	),
	ast.ImportFrom: (
		"`from ... import ...` is refused, for the same reason `import` is. Everything a rule "
		"needs is already in scope."
	),
	ast.FunctionDef: (
		"`def` is refused. A rule that needs to define a function has outgrown this field: "
		"either the declarative fields want a new primitive, or the logic wants to be a shipped "
		"scanner that somebody reviewed."
	),
	ast.AsyncFunctionDef: "`async def` is refused. The sweep is synchronous.",
	ast.ClassDef: "`class` is refused. A rule is a program that returns observations, not a type.",
	ast.Lambda: (
		"`lambda` is refused. Use a comprehension — `[x for x in rows if ...]` covers every "
		"place a rule wanted one."
	),
	ast.While: (
		"`while` is refused because it is unbounded by construction. Loop over a sequence with "
		"`for`, which is bounded by the sequence."
	),
	ast.Try: (
		"`try` / `except` is refused. A rule that swallows its own errors is a rule that goes "
		"quiet, and a compliance rule going quiet is the failure this app is written against. "
		"An error here is reported against this rule and the rest of the sweep still runs."
	),
	ast.With: "`with` is refused — there is no resource in scope to manage.",
	ast.Raise: (
		"`raise` is refused. Report a problem by putting it in the observation's message, which "
		"is a thing somebody reads, rather than by throwing, which is a thing somebody greps for."
	),
	ast.Assert: "`assert` is refused — see `raise`.",
	ast.Delete: "`del` is refused.",
	ast.Global: "`global` is refused — there is no module scope to reach.",
	ast.Nonlocal: "`nonlocal` is refused — there are no nested scopes.",
	ast.Yield: "`yield` is refused. Build a list and return it.",
	ast.YieldFrom: "`yield from` is refused. Build a list and return it.",
	ast.Await: "`await` is refused. The sweep is synchronous.",
	ast.NamedExpr: (
		"the walrus operator is refused. It is an assignment hidden inside an expression, and "
		"the point of this field being readable is that assignments are visible."
	),
}

#: Names an author may not bind or read. Reaching any one of them is reaching
#: back to the interpreter this module exists to keep out of scope.
_FORBIDDEN_NAMES = frozenset(
	{
		"__import__",
		"__builtins__",
		"__loader__",
		"__spec__",
		"__name__",
		"__file__",
		"__class__",
		"__subclasses__",
		"__bases__",
		"__mro__",
		"__globals__",
		"__code__",
		"__closure__",
		"__dict__",
		"__getattribute__",
		"__reduce__",
		"__reduce_ex__",
		"exec",
		"eval",
		"compile",
		"open",
		"globals",
		"locals",
		"vars",
		"dir",
		"getattr",
		"setattr",
		"delattr",
		"hasattr",
		"input",
		"breakpoint",
		"exit",
		"quit",
		"help",
		"memoryview",
		"object",
		"type",
		"super",
		"classmethod",
		"staticmethod",
		"property",
	}
)

#: Types whose bound methods may be called. A `str.upper` is a method on a value
#: the program already legitimately holds; it cannot reach anything the program
#: could not already reach, and refusing it would make the field unusable.
_METHOD_SAFE_TYPES = (str, list, dict, set, tuple, frozenset, int, float)


class _Break(Exception):
	pass


class _Continue(Exception):
	pass


class _Return(Exception):
	def __init__(self, value):
		self.value = value


def check(source: str) -> ast.Module:
	"""Parse and vet a program. Raises SandboxError with a sentence, or returns the tree.

	CALLED AT AUTHORING TIME AS WELL AS AT SWEEP TIME. The Compliance Rule
	controller runs this on save, so a rule with `import os` in it is refused
	while the person who typed it is present — rather than at two in the morning,
	in a sweep report nobody is reading.
	"""
	text = str(source or "").strip()
	if not text:
		raise SandboxError("the program is empty.")
	if len(text) > 20000:
		raise SandboxError(
			f"the program is {len(text)} characters, past the 20000 cap. A rule this long is a "
			"scanner that wants reviewing and shipping, not a field."
		)
	try:
		tree = ast.parse(text, filename="<compliance rule>", mode="exec")
	except SyntaxError as exc:
		raise SandboxError(f"syntax error on line {exc.lineno}: {exc.msg}") from None

	for node in ast.walk(tree):
		for refused, why in _REFUSALS.items():
			if isinstance(node, refused):
				raise SandboxError(f"line {getattr(node, 'lineno', '?')}: {why}")
		if not isinstance(node, _ALLOWED_NODES):
			raise SandboxError(
				f"line {getattr(node, 'lineno', '?')}: {type(node).__name__} is not in the "
				"restricted grammar. What is allowed is assignment, if/elif/else, for, "
				"comprehensions, arithmetic, comparisons, calls to the names in scope, and return."
			)
		if isinstance(node, ast.Attribute) and _is_private(node.attr):
			raise SandboxError(
				f"line {getattr(node, 'lineno', '?')}: attribute {node.attr!r} is refused. Every "
				"underscore-prefixed attribute is, because `x.__class__.__bases__` is how a "
				"sandbox that allowed them stops being one."
			)
		if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
			raise SandboxError(
				f"line {getattr(node, 'lineno', '?')}: the name {node.id!r} is refused. It is one "
				"of the handful of builtins that lead back out of the sandbox."
			)
		if isinstance(node, ast.keyword) and node.arg and _is_private(node.arg):
			raise SandboxError(f"line {getattr(node, 'lineno', '?')}: keyword {node.arg!r} is refused.")
	return tree


def _is_private(name) -> bool:
	return str(name or "").startswith("_")


def run(
	source: str, names: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_steps: int = DEFAULT_MAX_STEPS
):
	"""Vet, then execute, then return what the program returned.

	`names` is the ENTIRE world the program can see. There is no fallback lookup
	behind it: a name the caller did not provide is a NameError with a sentence
	listing what is in scope, which is a far more useful answer than the value
	`None` some other sandbox would have handed back.
	"""
	tree = check(source)
	return _Interpreter(names, timeout=timeout, max_steps=max_steps).execute(tree)


#: The name `evaluate` assigns its answer to. Never in `names`, so a caller
#: cannot shadow it and an expression cannot read a previous call's answer.
RESULT_NAME = "answer"


def evaluate(
	source: str, names: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_steps: int = DEFAULT_MAX_STEPS
):
	"""Evaluate ONE expression against `names` and return its value.

	`run` is for a program that produces observations; this is for the case where
	the answer is a VALUE — `producer_assigned_to_expression`, which turns the row
	an alert is about into the person the work belongs to.

	WRITTEN AS AN ASSIGNMENT RATHER THAN PARSED IN `eval` MODE, deliberately.
	`check` is the one place the grammar is enforced, and it parses in `exec`
	mode; an expression compiled down a second path would be an expression the
	checker had never seen the tree of. Wrapping it in `answer = (...)` means the
	tree that is vetted is exactly the tree that runs, with the same refusals, the
	same step budget and the same clock.

	An empty expression is None rather than an error: "this rule has no assignee
	expression" is the ordinary case for twelve of the fourteen shipped rules.
	"""
	text = str(source or "").strip()
	if not text:
		return None
	tree = check(f"{RESULT_NAME} = ({text})")
	interpreter = _Interpreter(
		{key: value for key, value in (names or {}).items() if key != RESULT_NAME},
		timeout=timeout,
		max_steps=max_steps,
	)
	interpreter.execute(tree)
	return interpreter.scope.get(RESULT_NAME)


class _Interpreter:
	def __init__(self, names: dict, timeout: float, max_steps: int):
		self.scope = dict(names or {})
		self.callables = {id(value) for value in self.scope.values() if callable(value)}
		self.receivers = _receivers(self.scope)
		self.timeout = float(timeout)
		self.max_steps = int(max_steps)
		self.steps = 0
		self.started = 0.0

	# ── budget ──────────────────────────────────────────────────────────────
	def _tick(self) -> None:
		self.steps += 1
		if self.steps > self.max_steps:
			raise SandboxError(
				f"the program ran past {self.max_steps} steps. That is a loop that is not going "
				"to finish, or a scan of far more rows than a compliance rule should be reading "
				"— narrow it with scope_filters."
			)
		if self.steps % _CLOCK_EVERY == 0 and (time.monotonic() - self.started) > self.timeout:
			raise SandboxError(
				f"the program ran past its {self.timeout:g}s budget. The sweep runs beside "
				"somebody's real work; a rule that wants longer is usually one with a query "
				"inside a loop."
			)

	# ── entry ───────────────────────────────────────────────────────────────
	def execute(self, tree: ast.Module):
		self.started = time.monotonic()
		try:
			self._body(tree.body)
		except _Return as done:
			return done.value
		except (_Break, _Continue):
			raise SandboxError("`break` or `continue` outside a loop.") from None
		# A program that fell off the end. `observations` is the conventional
		# accumulator and reading it is a kindness; anything else is an empty
		# answer, which is different from an error and is reported as such.
		return self.scope.get("observations", [])

	def _body(self, statements) -> None:
		for statement in statements:
			self._statement(statement)

	# ── statements ──────────────────────────────────────────────────────────
	def _statement(self, node) -> None:
		self._tick()
		if isinstance(node, ast.Expr):
			self._eval(node.value)
		elif isinstance(node, ast.Assign):
			value = self._eval(node.value)
			for target in node.targets:
				self._assign(target, value)
		elif isinstance(node, ast.AugAssign):
			current = self._eval(node.target)
			self._assign(node.target, _binop(node.op, current, self._eval(node.value)))
		elif isinstance(node, ast.If):
			if _truthy(self._eval(node.test)):
				self._body(node.body)
			else:
				self._body(node.orelse)
		elif isinstance(node, ast.For):
			self._for(node)
		elif isinstance(node, ast.Return):
			raise _Return(self._eval(node.value) if node.value is not None else None)
		elif isinstance(node, ast.Pass):
			return
		elif isinstance(node, ast.Break):
			raise _Break()
		elif isinstance(node, ast.Continue):
			raise _Continue()
		else:  # pragma: no cover - `check` refused everything else already
			raise SandboxError(f"{type(node).__name__} is not executable here.")

	def _for(self, node) -> None:
		for item in self._iterate(self._eval(node.iter)):
			self._assign(node.target, item)
			try:
				self._body(node.body)
			except _Break:
				return
			except _Continue:
				continue
		self._body(node.orelse)

	def _iterate(self, value):
		if isinstance(value, (list, tuple, set, frozenset, str, range)):
			return value
		if isinstance(value, dict):
			return list(value.keys())
		# A generator from a comprehension, or a frappe result list subclass.
		try:
			return list(value)
		except Exception:
			raise SandboxError(f"{type(value).__name__} cannot be looped over here.") from None

	def _assign(self, target, value) -> None:
		self._tick()
		if isinstance(target, ast.Name):
			if target.id in _FORBIDDEN_NAMES:
				raise SandboxError(f"cannot assign to {target.id!r}.")
			self.scope[target.id] = value
			return
		if isinstance(target, (ast.Tuple, ast.List)):
			items = list(self._iterate(value))
			if len(items) != len(target.elts):
				raise SandboxError(f"cannot unpack {len(items)} value(s) into {len(target.elts)} name(s).")
			for element, item in zip(target.elts, items, strict=True):
				self._assign(element, item)
			return
		if isinstance(target, ast.Subscript):
			container = self._eval(target.value)
			if not isinstance(container, (dict, list)):
				raise SandboxError("only a dict or a list may be assigned into.")
			container[self._eval_slice(target.slice)] = value
			return
		raise SandboxError(
			"the left-hand side of an assignment must be a name, a tuple of names, or a "
			"dict/list subscript. Assigning to an attribute is refused — a rule reads records, "
			"it does not edit them."
		)

	# ── expressions ─────────────────────────────────────────────────────────
	def _eval(self, node):
		self._tick()
		if isinstance(node, ast.Constant):
			return node.value
		if isinstance(node, ast.Name):
			if node.id in self.scope:
				return self.scope[node.id]
			raise SandboxError(
				f"there is no name {node.id!r} in scope. What is in scope: {', '.join(sorted(self.scope))}."
			)
		if isinstance(node, ast.BinOp):
			return _binop(node.op, self._eval(node.left), self._eval(node.right))
		if isinstance(node, ast.UnaryOp):
			return _unaryop(node.op, self._eval(node.operand))
		if isinstance(node, ast.BoolOp):
			return self._boolop(node)
		if isinstance(node, ast.Compare):
			return self._compare(node)
		if isinstance(node, ast.IfExp):
			return self._eval(node.body) if _truthy(self._eval(node.test)) else self._eval(node.orelse)
		if isinstance(node, ast.Call):
			return self._call(node)
		if isinstance(node, ast.Attribute):
			return self._attribute(node)
		if isinstance(node, ast.Subscript):
			return self._subscript(node)
		if isinstance(node, ast.List):
			return [self._eval(element) for element in node.elts]
		if isinstance(node, ast.Tuple):
			return tuple(self._eval(element) for element in node.elts)
		if isinstance(node, ast.Set):
			return {self._eval(element) for element in node.elts}
		if isinstance(node, ast.Dict):
			return {
				self._eval(key) if key is not None else None: self._eval(value)
				for key, value in zip(node.keys, node.values, strict=True)
			}
		if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
			return self._comprehension(node)
		if isinstance(node, ast.JoinedStr):
			return "".join(str(self._eval(part)) for part in node.values)
		if isinstance(node, ast.FormattedValue):
			return _format(self._eval(node.value), node)
		raise SandboxError(f"{type(node).__name__} cannot be evaluated here.")

	def _boolop(self, node):
		if isinstance(node.op, ast.And):
			value = True
			for element in node.values:
				value = self._eval(element)
				if not _truthy(value):
					return value
			return value
		value = False
		for element in node.values:
			value = self._eval(element)
			if _truthy(value):
				return value
		return value

	def _compare(self, node):
		left = self._eval(node.left)
		for operator, right_node in zip(node.ops, node.comparators, strict=True):
			right = self._eval(right_node)
			if not _compare(operator, left, right):
				return False
			left = right
		return True

	def _subscript(self, node):
		container = self._eval(node.value)
		key = self._eval_slice(node.slice)
		try:
			return container[key]
		except (KeyError, IndexError):
			# A missing key on a record row is the ordinary case on a site whose
			# doctype does not have the column, and a rule that died on it would
			# be a rule nobody could write portably.
			return None
		except TypeError:
			raise SandboxError(f"{type(container).__name__} cannot be subscripted.") from None

	def _eval_slice(self, node):
		if isinstance(node, ast.Slice):
			return slice(
				self._eval(node.lower) if node.lower is not None else None,
				self._eval(node.upper) if node.upper is not None else None,
				self._eval(node.step) if node.step is not None else None,
			)
		return self._eval(node)

	def _attribute(self, node):
		if _is_private(node.attr):  # pragma: no cover - `check` refused it already
			raise SandboxError(f"attribute {node.attr!r} is refused.")
		target = self._eval(node.value)
		try:
			return getattr(target, node.attr)
		except AttributeError:
			# `.get` on something that is not a dict, most often. Answering None
			# rather than dying keeps a rule portable across sites whose rows
			# carry different columns.
			if isinstance(target, dict):
				return target.get(node.attr)
			raise SandboxError(
				f"{type(target).__name__} has no attribute {node.attr!r} that this sandbox will hand over."
			) from None

	def _comprehension(self, node):
		saved = dict(self.scope)
		try:
			collected = []
			self._comprehend(node, node.generators, 0, collected)
		finally:
			self.scope = saved
		if isinstance(node, ast.SetComp):
			return set(collected)
		if isinstance(node, ast.DictComp):
			return dict(collected)
		return collected

	def _comprehend(self, node, generators, index: int, collected: list) -> None:
		if index >= len(generators):
			if isinstance(node, ast.DictComp):
				collected.append((self._eval(node.key), self._eval(node.value)))
			else:
				collected.append(self._eval(node.elt))
			return
		generator = generators[index]
		for item in self._iterate(self._eval(generator.iter)):
			self._tick()
			self._assign(generator.target, item)
			if all(_truthy(self._eval(condition)) for condition in generator.ifs):
				self._comprehend(node, generators, index + 1, collected)

	def _call(self, node):
		function = self._eval(node.func)
		self._refuse_unsafe_callable(function, node)
		args = [self._eval(argument) for argument in node.args]
		kwargs = {}
		for keyword in node.keywords:
			if keyword.arg is None:
				raise SandboxError("`**kwargs` unpacking in a call is refused.")
			kwargs[keyword.arg] = self._eval(keyword.value)
		try:
			return function(*args, **kwargs)
		except SandboxError:
			raise
		except Exception as exc:
			raise SandboxError(
				f"{_callable_name(function)}(...) raised {type(exc).__name__}: {exc}"
			) from None

	def _refuse_unsafe_callable(self, function, node) -> None:
		if not callable(function):
			raise SandboxError(f"{_callable_name(function)} is not callable.")
		if id(function) in self.callables:
			return
		bound = getattr(function, "__self__", None)
		if bound is not None:
			# A method on a plain value the program already legitimately holds —
			# `"x".upper()`, `rows.append(...)`. It cannot reach anything the
			# program could not already reach, and refusing it would make the
			# field unusable.
			if isinstance(bound, _METHOD_SAFE_TYPES):
				return
			# A method on a FACADE the caller handed in — `frappe.get_all`,
			# `frappe.utils.today`. Matched on the RECEIVER rather than on the
			# function, because `getattr` builds a fresh bound-method object on
			# every access and an identity test against the function would never
			# be true. `check` has already refused every underscore-prefixed
			# attribute, so what is reachable this way is exactly the public
			# surface whoever built the facade meant to expose.
			if id(bound) in self.receivers:
				return
		if id(function) in self.receivers:
			# A class handed in as a value and called as a constructor —
			# `datetime.date(2026, 1, 1)`.
			return
		raise SandboxError(
			f"line {getattr(node, 'lineno', '?')}: {_callable_name(function)} is not one of the "
			"callables this sandbox will run. Callable here: the names given to the rule, their "
			"public attributes, and methods on plain strings, lists, dicts and numbers."
		)


def _receivers(scope: dict, depth: int = 2) -> set:
	"""Object ids whose PUBLIC methods a program may call.

	Everything in scope, plus its public attributes, two levels deep — which is
	exactly `frappe`, `frappe.utils`, and the date classes on the `datetime`
	stand-in, and stops well short of walking an object graph. Anything deeper
	than the facade is something the facade did not mean to expose, and
	`check()` has already made every dunder unreachable, so this cannot be walked
	out of.
	"""
	found = set()
	layer = list(scope.values())
	for _level in range(depth):
		nxt = []
		for holder in layer:
			if holder is None or isinstance(holder, _METHOD_SAFE_TYPES):
				continue
			found.add(id(holder))
			for name in dir(holder):
				if name.startswith("_"):
					continue
				try:
					nxt.append(getattr(holder, name))
				except Exception:
					continue
		layer = nxt
	return found


def _callable_name(function) -> str:
	return str(getattr(function, "__name__", None) or type(function).__name__)


def _truthy(value) -> bool:
	return bool(value)


def _format(value, node) -> str:
	conversion = getattr(node, "conversion", -1)
	if conversion == 114:
		value = repr(value)
	elif conversion == 97:
		value = ascii(value)
	spec = ""
	if node.format_spec is not None and isinstance(node.format_spec, ast.JoinedStr):
		spec = "".join(str(part.value) for part in node.format_spec.values if isinstance(part, ast.Constant))
	try:
		return format(value, spec)
	except Exception:
		return str(value)


_BINOPS = {
	ast.Add: lambda a, b: a + b,
	ast.Sub: lambda a, b: a - b,
	ast.Mult: lambda a, b: a * b,
	ast.Div: lambda a, b: a / b,
	ast.FloorDiv: lambda a, b: a // b,
	ast.Mod: lambda a, b: a % b,
}


def _binop(operator, left, right):
	if isinstance(operator, ast.Pow):
		# Bounded: 2 ** 10**9 is a memory exhaustion with no loop in sight.
		if isinstance(right, (int, float)) and right > 64:
			raise SandboxError(
				"an exponent above 64 is refused — it is a way to exhaust memory without a loop."
			)
		return left**right
	if isinstance(operator, ast.Mult) and isinstance(left, (str, list, tuple)) and isinstance(right, int):
		if right > 10000:
			raise SandboxError("repeating a sequence more than 10000 times is refused.")
	handler = _BINOPS.get(type(operator))
	if handler is None:
		raise SandboxError(f"the {type(operator).__name__} operator is refused here.")
	try:
		return handler(left, right)
	except ZeroDivisionError:
		raise SandboxError("division by zero.") from None
	except TypeError as exc:
		raise SandboxError(f"{type(operator).__name__}: {exc}") from None


def _unaryop(operator, value):
	if isinstance(operator, ast.USub):
		return -value
	if isinstance(operator, ast.UAdd):
		return +value
	if isinstance(operator, ast.Not):
		return not _truthy(value)
	raise SandboxError(f"the {type(operator).__name__} operator is refused here.")


def _compare(operator, left, right) -> bool:
	try:
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
		if isinstance(operator, ast.In):
			return left in right
		if isinstance(operator, ast.NotIn):
			return left not in right
		if isinstance(operator, ast.Is):
			return left is right
		if isinstance(operator, ast.IsNot):
			return left is not right
	except TypeError as exc:
		raise SandboxError(
			f"cannot compare {type(left).__name__} with {type(right).__name__}: {exc}"
		) from None
	raise SandboxError(f"the {type(operator).__name__} comparison is refused here.")
