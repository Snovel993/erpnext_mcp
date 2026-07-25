# Contributing

Thanks for looking. This is a small app with a narrow job — expose an ERPNext
site over MCP, safely — and the bar for a change is that it makes that job
better without widening it.

## Running the tests

```bash
# Logic. No bench, no database, no third-party packages. Run this constantly.
python3 -m unittest discover -s tests_standalone -t .

# Framework contract. Needs a dev bench with this app installed.
bench --site yoursite.localhost run-tests --app erpnext_mcp
```

The standalone suite installs an in-memory `frappe` into `sys.modules` before
the app is imported (`tests_standalone/harness.py`). It is a test double, not an
emulator: reaching for a framework function it does not implement raises an
`AttributeError` naming the function, rather than quietly returning `None`. If
you hit that, add the function to the harness deliberately.

CI runs the standalone suite on Python 3.10 and 3.11, plus `ruff check`,
`ruff format --check` and an SPDX-header check. Run those locally before opening
a PR:

```bash
pip install ruff
ruff check .
ruff format .
```

`docs/development.md` goes into more detail, including how to stand up a dev
bench from scratch.

## Code style

Frappe's, because that is what a reviewer of a Frappe app will be reading.

- **Tabs** for indentation in Python and JavaScript; spaces in JSON, TOML and
  Markdown. `.editorconfig` and `pyproject.toml` both say so, and `ruff format`
  enforces it.
- `# SPDX-License-Identifier: MIT` as the first line of every source file,
  including tests and workflow YAML. CI fails without it.
- Line length 110.
- Docstrings and comments explain **why**, not what. If a decision would look
  arbitrary to somebody reading it cold — gating on the rightmost
  `X-Forwarded-For` hop, casting `"0"` to `False`, delegating to
  `apply_workflow` instead of writing the state field — the reason belongs next
  to the code. Comments that restate the line above are noise; delete them.

## Adding a tool

Everything a tool needs lives in three places. `docs/development.md` has the
long version; the short one:

1. **Write the handler** in the right module under `erpnext_mcp/tools/`
   (`read`, `mutate`, `workflow`, `reports`, `files`, `collab`, `hr`, `trade`,
   `meta`). Signature is `(args: dict) -> ToolResult`. Raise `ToolError` for
   anything a caller could fix; let genuine bugs propagate — `dispatch` logs a
   traceback to the site's Error Log and returns the exception type.

   Write the `summary` for an operator scanning the audit log in a month, not
   for the model. Set `docstatus_delta` on anything that changes document state.

2. **Register it** in `TOOLS` in `erpnext_mcp/registry.py`. The `description` is
   the entire basis on which a model decides to call your tool: say what it
   returns, what it is *for*, and — if it mutates — what it cannot do. Spell out
   "Read-only." or "MUTATING (default OFF)." in the text as well as in the
   annotations, because clients that ignore annotations still show the model the
   description.

   If the tool needs something not every site has, give it an `available`
   predicate and a `requires` sentence. A tool that is advertised and always
   fails is worse than one that is absent.

3. **Add the switch** to the settings doctype:
   `erpnext_mcp/erpnext_mcp/doctype/erpnext_mcp_settings/erpnext_mcp_settings.json`,
   as `allow_<tool_name>`, fieldtype `Check`, default `"1"` for a read tool and
   `"0"` for a mutating one, with a `description` an operator can act on. Add it
   to `field_order` and put it in the right section.

Then `bench --site <site> migrate`. Existing installs get the field seeded by
`install.after_migrate`, so no bespoke patch is needed.

Tests will tell you if you skipped step 3:
`ShippedDefaults.test_every_tool_has_a_switch` fails when a tool has no switch,
and `test_every_switch_has_a_tool` fails when a switch outlives its tool.

### If the tool mutates

- Make the destructive verb its own tool with its own switch. `create` and
  `submit` are separate for a reason: an operator can grant "propose" without
  granting "post", which is what most of them actually want.
- Validate before writing, with a message the model can act on. `"debits
  (1450.0) do not equal credits (1400.0); difference 50.0. Nothing was
  created."` beats a Frappe traceback.
- Go through `frappe.get_doc(...).insert()` / `.submit()` / `.cancel()`, or
  through the framework's own operation (`apply_workflow`). **No raw SQL** — the
  standalone harness raises on `frappe.db.sql` to keep it that way.

### Things that do not belong

- Hardcoded company, account, fiscal-year or report names. Everything is
  discovered at call time; that is the difference between an app anyone can
  install and a script for one site.
- A field selected without checking it exists. Use `compat.existing_fields()` /
  `compat.first_field()` — selecting a missing column is a hard SQL error.
- Anything that logs, echoes or returns the auth token.
- A second copy of the tool catalogue. The JavaScript asks the server; so should
  anything else.

## Pull requests

- One change per PR. A tool addition and a refactor of the dispatcher are two
  PRs.
- Update `CHANGELOG.md` under an `Unreleased` heading.
- Update `docs/tool-catalog.md` if you added or changed a tool, and
  `docs/security.md` if you changed anything about the gates, permissions or the
  audit log.
- Say in the PR description what you tested and on which Frappe version. "Ran
  the standalone suite" and "ran the in-bench suite on v15.115" are different
  claims and both are useful.

The PR template has a short checklist covering the same ground.

## Reporting a security issue

Anything already public: open an issue. Anything not: email the address in
`pyproject.toml` rather than filing publicly. `docs/security.md` describes the
threat model this app is built against, which is a good place to check whether
what you found is in scope.

## License

MIT. By contributing you agree your work ships under it.
