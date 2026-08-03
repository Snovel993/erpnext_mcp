# SPDX-License-Identifier: MIT
"""Outbound integrations: the parts of this app that talk to somebody else.

ONE PACKAGE, ONE PROPERTY, AND IT IS THE REASON THE PACKAGE EXISTS. Everything
else in `erpnext_mcp` reads and writes the site it is installed on. A module in
here makes a network request to a third party, which is a different kind of code
with a different set of ways to fail: the far end is slow, the far end is down,
the far end has decided this address has asked enough questions for one hour, the
far end returns two hundred and a page of HTML. None of those is a bug in this
app and none of them may take a site's scheduler down.

So every module here holds to the same three rules, and `services/weather.py`
argues each one at the point it applies:

  * IT NEVER RAISES INTO ITS CALLER. A failed fetch returns None. The caller
    decides what a missing answer means, and for weather the answer is "this
    shift has one fewer reading than it might have had", which is a gap in
    evidence and not a reason to refuse to run.
  * IT HAS A TIMEOUT, AND THE TIMEOUT IS CONFIGURABLE. Code that runs on a
    scheduler is code holding a worker somebody else's job is queued behind.
  * IT BACKS OFF WHEN TOLD TO. A 429 is a request to stop asking, and an
    integration that answers a rate limit by retrying immediately is one whose
    site ends up blocked rather than throttled.

Nothing in here is imported at app load. `hooks.py` names the scheduled entry
point by dotted path and the tools import it when they run, so a bench without
`requests` — which does not exist, since Frappe depends on it — loses weather and
nothing else.
"""
