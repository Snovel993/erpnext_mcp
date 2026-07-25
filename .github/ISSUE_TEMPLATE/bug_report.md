---
name: Bug report
about: Something behaves differently from how it is documented
title: ''
labels: bug
assignees: ''
---

<!-- SPDX-License-Identifier: MIT -->

## What happened

<!-- The tool you called, the arguments, and what came back. -->

## What you expected

## Versions

| | |
| --- | --- |
| erpnext_mcp | <!-- e.g. 0.2.0 --> |
| Frappe | <!-- bench version, e.g. 15.115.0 --> |
| ERPNext | |
| Other apps | <!-- hrms? anything that customises the doctypes involved? --> |
| Python | |

## Reproducing it

<!--
A curl against the endpoint is the most useful form. Redact the token — it is
read access to your whole ledger, and there is no version of this issue that
needs it.

curl -sS -X POST https://your-site/api/method/erpnext_mcp.mcp.handle \
  -H 'Content-Type: application/json' -H "X-MCP-Token: $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"<tool>","arguments":{}}}'
-->

## The audit row

<!--
Every call lands in MCP Action Log (/app/mcp-action-log). The row for the failed
call — result_status, result_summary, docstatus_delta — usually says more than
the client-side error does. Paste it here.
-->

## Anything in the Error Log?

<!--
An unexpected failure also writes a traceback to /app/error-log titled
"erpnext_mcp: tool <name> failed". Paste it if there is one.
-->

## Checklist

- [ ] The tool is enabled in **ERPNext MCP Settings** (a disabled tool is
      refused by name, and a tool whose site prerequisite is missing is not
      advertised at all)
- [ ] I have redacted the auth token from everything above
- [ ] I checked whether `docs/security.md` describes this as intended behaviour
      — several refusals are deliberate
