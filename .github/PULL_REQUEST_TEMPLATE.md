<!-- SPDX-License-Identifier: MIT -->

## What this changes

<!-- One or two sentences. If it adds a tool, name it and say what it returns. -->

## Why

<!-- The question a user could not answer before, or the thing that was wrong. -->

## Checklist

- [ ] `python3 -m unittest discover -s tests_standalone -t .` passes
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Every new source file starts with `# SPDX-License-Identifier: MIT`
- [ ] `CHANGELOG.md` updated under `Unreleased`
- [ ] Docs updated — `docs/tool-catalog.md` for a tool change,
      `docs/security.md` for anything touching the gates, permissions or the
      audit log

### If this adds or changes a tool

- [ ] Registered in `erpnext_mcp/registry.py` with a description written for a
      model: what it returns, what it is for, and what it cannot do
- [ ] `allow_<tool_name>` added to the settings doctype JSON — read tools
      default `"1"`, mutating tools `"0"` — and to `field_order`, in the right
      section
- [ ] Given an `available` predicate and a `requires` sentence if it needs
      something not every site has
- [ ] Tests cover its refusals, not just its happy path

### If this mutates data

- [ ] Off by default, with its own switch
- [ ] The destructive verb is a separate tool from the one that prepares it
- [ ] Writes go through `frappe.get_doc().insert()` / `.submit()` / `.cancel()`
      or a framework operation — no raw SQL
- [ ] Validation happens before anything is written, and the message says what
      to send instead
- [ ] `docstatus_delta` is set on the `ToolResult`

## Tested on

<!--
Which Frappe/ERPNext version, and whether you ran the in-bench suite. "Standalone
only" is a fine answer — just say so.
-->
