# SPDX-License-Identifier: MIT
"""The compliance alert engine. Import a rule module and it is registered.

Same auto-discovery as `erpnext_mcp.packets` and `erpnext_mcp.charts`: every
sibling module is imported at load time, and each one ends its rules with
`register(...)`. There is no list to maintain, which is the entire reason for the
indirection — a list is a thing somebody forgets to add to.
"""

from . import rules  # noqa: F401 - imported for its side effect: every rule registers itself
from .base import (  # noqa: F401
	ALERT_DOCTYPE,
	REGIME_FIELD,
	RULE_CAP,
	RULES,
	SEVERITY_CRITICAL,
	SEVERITY_INFO,
	SEVERITY_ORDER,
	SEVERITY_WARNING,
	Observation,
	Rule,
	alert_key,
	alert_matches_regime,
	days_since,
	days_until,
	names,
	refresh_compliance_alerts,
	regimes_for_alerts,
	regimes_of,
	register,
	severity_at_least,
	sweep,
)


def get(key: str):
	"""One rule by its alert_type, or None."""
	return RULES.get(str(key or "").strip())


def describe() -> list:
	"""Every rule, with its kairotic gate. The docs and the calendar both read this."""
	return [RULES[key].describe() for key in names()]
