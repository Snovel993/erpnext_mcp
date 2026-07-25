---
name: Feature request
about: A tool or capability this app should have
title: ''
labels: enhancement
assignees: ''
---

<!-- SPDX-License-Identifier: MIT -->

## The question you want an AI client to be able to answer

<!--
Start here rather than with the tool. "Which purchase orders has nobody approved
in a fortnight" tells us more than "add a tool for the Purchase Order doctype",
because it says what the return shape has to contain.
-->

## What you do today instead

<!-- The report you open, the query you run, the person you ask. -->

## Does it read or write?

- [ ] Read-only
- [ ] It changes data

<!--
Write tools ship off and stay off until an operator ticks a box, and the
destructive verb gets its own switch — `create_journal_entry` cannot submit,
because that is a separate tool. If what you want writes, say what the narrowest
useful version is.
-->

## What does it need on the site?

<!--
An app (hrms?), a doctype that only exists on some versions, a customisation? A
tool with a prerequisite gets an availability predicate so it is simply absent
where it cannot work.
-->

## Is there already a report for this?

<!--
`run_report` can run any Query, Script or Report Builder report the site has,
and a report somebody already trusts usually beats a new tool assembling the
same figure out of primitives. Worth checking `list_reports` first.
-->

## Anything else
