# SPDX-License-Identifier: MIT
"""The catalogue's own counts, checked against the registry it documents.

WHY THIS MODULE EXISTS. `docs/tool-catalog.md` opens by telling the reader how
many tools there are, and says it again at the head of the read-only section.
Both numbers are written by hand and neither had a test. They have drifted
twice: v0.19.0 found the total saying 206 against a catalogue of 210, and
v0.19.1 found the read count saying 85 against a registry of 95 — a gap of ten
that had been there long enough that nobody could say which release opened it.

The failure is quiet, which is what makes it worth a test. A wrong count does
not break a call; it makes the document that is supposed to be the map of the
tool surface disagree with the surface, and a reader who notices has no way to
know which of the two is stale. `test_protocol.py` asserts the registry's own
counts — that a tool was added deliberately — and this asserts the documentation
kept up, which is the half that was unguarded.

The numbers are READ OUT OF THE PROSE rather than kept in a constant here. A
constant would be a third place to update, and a third place to forget.
"""

import pathlib
import re
import unittest

from erpnext_mcp import registry

#: The catalogue, found relative to this file so the test does not depend on
#: which directory `unittest discover` was started from.
CATALOG = pathlib.Path(__file__).resolve().parent.parent / "docs" / "tool-catalog.md"

#: "All 214 tools `erpnext_mcp` exposes …" — the intro line.
TOTAL_PATTERN = re.compile(r"^All (\d+) tools\b", re.MULTILINE)

#: "All 95 read tools are **on** by default …" — the read-only section head.
READ_PATTERN = re.compile(r"^All (\d+) read tools\b", re.MULTILINE)


def _sole_count(pattern: re.Pattern, text: str, what: str) -> int:
	"""The one number the pattern matches, or a failure naming what went wrong.

	More than one match means the document grew a second claim about the same
	count, and a test that silently took the first would go on passing while the
	other one rotted. That is the exact failure this module exists to catch, so
	it is an error rather than a first-match.
	"""
	found = pattern.findall(text)
	if not found:
		raise AssertionError(
			f"docs/tool-catalog.md no longer states {what} in a form this test can read "
			f"(expected a line matching {pattern.pattern!r}). If the wording changed on "
			f"purpose, update the pattern in tests_standalone/test_tool_catalog_count.py; "
			f"if the sentence was dropped, put the count back — it is how a reader knows "
			f"the document is complete."
		)
	if len(found) > 1:
		raise AssertionError(
			f"docs/tool-catalog.md states {what} in {len(found)} places ({', '.join(found)}). "
			f"Two hand-written copies of one number is how the count drifts in the first "
			f"place — keep one."
		)
	return int(found[0])


class CatalogueCountsMatchTheRegistry(unittest.TestCase):
	def setUp(self):
		self.text = CATALOG.read_text(encoding="utf-8")

	def test_the_intro_states_the_registry_total(self):
		documented = _sole_count(TOTAL_PATTERN, self.text, "the total tool count")
		self.assertEqual(
			documented,
			len(registry.TOOLS),
			f"docs/tool-catalog.md says {documented} tools; erpnext_mcp/registry.py TOOLS has "
			f"{len(registry.TOOLS)}. registry.py is authoritative — the catalogue documents it. "
			f"Fix the count in the first paragraph of docs/tool-catalog.md, and check the tool "
			f"you added has a section there too: the number is a proxy for whether it does.",
		)

	def test_the_read_section_states_the_read_tool_count(self):
		documented = _sole_count(READ_PATTERN, self.text, "the read-only tool count")
		self.assertEqual(
			documented,
			len(registry.READ_TOOLS),
			f"docs/tool-catalog.md says {documented} read tools; erpnext_mcp/registry.py "
			f"READ_TOOLS has {len(registry.READ_TOOLS)}. registry.py is authoritative. Fix the "
			f"count under the '# Read-only tools' heading in docs/tool-catalog.md.",
		)

	def test_the_two_documented_counts_are_consistent_with_each_other(self):
		"""A total that is not reads plus writes is wrong even if both halves match.

		Cheap, and it catches the case where somebody updates one number by hand
		to make a failing assertion above go green without looking at why.
		"""
		total = _sole_count(TOTAL_PATTERN, self.text, "the total tool count")
		reads = _sole_count(READ_PATTERN, self.text, "the read-only tool count")
		self.assertEqual(
			total - reads,
			len(registry.MUTATING_TOOLS),
			f"the catalogue's {total} total minus its {reads} reads leaves {total - reads} "
			f"mutating tools; registry.MUTATING_TOOLS has {len(registry.MUTATING_TOOLS)}.",
		)


if __name__ == "__main__":
	unittest.main()
