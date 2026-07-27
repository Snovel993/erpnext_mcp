# SPDX-License-Identifier: MIT
"""Chart-of-accounts templates, auto-discovered from this package.

Every module here that calls `register(ChartTemplate(...))` becomes a template
`propose_clean_chart` can offer. Discovery walks the package at import time, so
adding one is a file drop with no list to update — the same arrangement as
`erpnext_mcp.packets`, and for the same reason: the roadmap for this family is
long (`us_c_corp`, `us_s_corp`, `us_partnership` at least) and each will be
written by somebody who should not have to edit the dispatcher.

Templates are pure data. Nothing in this package reads the database.
"""

import importlib
import pkgutil

from .base import (  # noqa: F401  (re-exported for template modules and tools)
	ACCOUNT_TYPE_FALLBACKS,
	ACCOUNT_TYPES_BY_ROOT,
	MAX_ACCOUNTS,
	MAX_DEPTH,
	NODE_FIELDS,
	ROOT_TYPES,
	TEMPLATES,
	ChartTemplate,
	account_docname,
	account_type_conflict,
	parse_accounts_json,
	register,
	resolve_account_type,
	site_account_types,
	validate_tree,
	walk,
)


def _discover() -> None:
	"""Import every sibling module so its `register(...)` call runs.

	Failures are not swallowed, for the same reason they are not swallowed in the
	packet package: a template that quietly fails to import is a template that
	quietly vanishes from the catalogue, and nobody notices an absence.
	"""
	for module in pkgutil.iter_modules(__path__):
		if module.name.startswith("_") or module.name == "base":
			continue
		importlib.import_module(f"{__name__}.{module.name}")


_discover()


def get(key: str):
	"""A registered ChartTemplate, or None."""
	return TEMPLATES.get(key)


def names() -> tuple:
	"""Every registered template key, in a stable order."""
	return tuple(sorted(TEMPLATES))
