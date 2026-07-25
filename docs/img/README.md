<!-- SPDX-License-Identifier: MIT -->
# Screenshots

The README links two images from this directory. They are placeholders until
somebody drops real captures in:

| File | What to capture |
| --- | --- |
| `settings-form.png` | **ERPNext MCP Settings** (`/app/erpnext-mcp-settings`) with the endpoint enabled and at least one write tool on, so the red "MCP is live with N write tool(s)" banner is visible. Scroll so the Connection and Accounting Write Tools sections both show. |
| `action-log.png` | **MCP Action Log** (`/app/mcp-action-log`) list view, ideally with a mix of `Success`, `Blocked` and `Unauthorized` rows and a populated `Docstatus Delta`. |

Before committing either: the token is never rendered on the form, but the
**site name in the URL bar, real company names, real account numbers and real
customer names are**. Crop or use a demo site.
