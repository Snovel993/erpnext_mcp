# SPDX-License-Identifier: MIT
"""Controller for Farm Translation — one string, one language, one row.

THE DOCNAME IS DERIVED AND NOT TYPED. `translation_id` is `key::language`, so
the uniqueness of "one key in one language" is enforced by MariaDB rather than
by whichever caller remembered to check first. That is what makes
`install_translations` safe to run on every `bench migrate`: the seeder writes
the same identity it wrote last time and updates that row, instead of quietly
accumulating a second Spanish for `shift.status.open` every upgrade.

THE PLACEHOLDERS ARE CHECKED AGAINST THE ENGLISH. A string is rendered by
`str.format`, so a Spanish value naming `{bloque}` where the English names
`{block}` does not read slightly oddly — it raises a KeyError at the moment a
worker taps the screen, or (worse, once the renderer is defensive) prints a
literal brace to somebody standing in a block. This is the one invariant the
form can check that nobody can check by eye, because the two strings are in
different languages and one of them is one the reviewer may not read.

An English row with no counterpart is fine and is the normal state of a key
somebody has just added; the check only runs where an English row exists to
compare against, and it never blocks the ENGLISH row itself — refusing to save
the source string because a translation of it disagrees would make a bad Spanish
row hold the English hostage.

A DISABLED ROW IS NOT DELETED. `enabled` exists because a wrong translation a
worker acted on is evidence: the record of what somebody was shown has to
survive the correction, and a delete would take it with the fix.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

#: `{name}` — the substitution form `translations.render` uses. Deliberately
#: does NOT match `{{name}}` or `{0}`: this app's strings are named-only, and a
#: positional placeholder in one language and a named one in another is a bug
#: this check should surface rather than tolerate.
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

DEFAULT_LANGUAGE = "en"


class FarmTranslation(Document):
	def autoname(self):
		"""Build the derived docname BEFORE Frappe reads it off the field.

		THE ORDER MATTERS AND IT IS EASY TO GET WRONG. `set_new_name` runs
		before `validate`, so a `field:translation_id` rule reads whatever is on
		the column at that moment — and a controller that computed the id in
		`validate` would name every row from an EMPTY field and get a serial
		docname instead. The uniqueness of "one key in one language" would then
		be enforced by nothing at all, and `install_translations` would append a
		second copy of the whole catalogue on every migrate rather than updating
		it in place.
		"""
		self._normalise()

	def validate(self):
		# Re-run on every save, not only at insert: an operator correcting the
		# key or the language on an existing row has to leave the derived id
		# agreeing with them, or the seeder's lookup stops finding it.
		self._normalise()

		if not str(self.value or "").strip():
			frappe.throw(
				_(
					"A translation with an empty value is worse than no row at all: it is found, "
					"served, and shows a worker a blank where a sentence should be. Delete the row "
					"or untick Enabled instead."
				),
				title=_("Empty Translation"),
			)

		self._check_placeholders()

	def _normalise(self) -> None:
		"""Trim the key, lower-case the language, and derive the id from both."""
		key = str(self.translation_key or "").strip()
		language = str(self.language or "").strip().lower()
		if not key:
			frappe.throw(
				_(
					"A translation needs a key. It is the stable name an app looks this string up "
					"by — 'shift.status.open' — and it is deliberately not the English text, so "
					"rewording the English does not orphan the Spanish."
				),
				title=_("No Translation Key"),
			)
		if not language:
			frappe.throw(_("A translation needs a language."), title=_("No Language"))
		self.translation_key = key
		self.language = language
		self.translation_id = f"{key}::{language}"

	def _check_placeholders(self) -> None:
		"""Refuse a translation whose braces do not match the English row's.

		Skipped for the English row itself — see the module docstring on why a bad
		translation must not hold its own source string hostage — and skipped
		where no English row exists yet, which is every key on the day it is added.
		"""
		if self.language == DEFAULT_LANGUAGE:
			return
		source = f"{self.translation_key}::{DEFAULT_LANGUAGE}"
		try:
			english = frappe.db.get_value(self.doctype, source, "value")
		except Exception:  # pragma: no cover - a site mid-migrate
			return
		if not english:
			return
		wanted = set(PLACEHOLDER.findall(str(english)))
		got = set(PLACEHOLDER.findall(str(self.value or "")))
		if wanted == got:
			return
		missing = sorted(wanted - got)
		extra = sorted(got - wanted)
		detail = []
		if missing:
			detail.append(_("does not use {0}").format(_braced(missing)))
		if extra:
			detail.append(_("uses {0}, which the English does not").format(_braced(extra)))
		frappe.throw(
			_(
				"This {0} translation of {1} {2}. The caller fills placeholders by NAME, so a "
				"mismatch is not a wording difference — it is a brace printed to a worker, or a "
				"value silently missing from the sentence they act on. The English reads: {3}"
			).format(self.language, self.translation_key, _(" and ").join(detail), english),
			title=_("Placeholders Do Not Match"),
		)


def _braced(names) -> str:
	"""`['block', 'until']` → `"{block}, {until}"`.

	A helper rather than an inline comprehension because the braces are the
	subject of the sentence this renders into, and every way of writing them in
	place fights either the f-string or `str.format`.
	"""
	return ", ".join("{" + str(name) + "}" for name in names)
