# SPDX-License-Identifier: MIT
"""Short keys for parcels, and the docnames the farm registers hang off them.

WHY A PARCEL NEEDS AN ABBREVIATION AT ALL. A Parcel's own docname already
carries the company — `"Mill Creek - HLD"` — and suffixing a field with that
gives `"Yellow Camp Block 3 - Mill Creek - HLD"`, which is a docname nobody
types twice. Fields, irrigation zones and housing units are the records an
operator names most often and refers to in the field, so they get the short key:
`"Yellow Camp Block 3 - MC"`, `"YC3-Zone2 - MC"`, `"MC-Cabin-01 - MC"`.

THE SUFFIX IS THE PARCEL'S, ALL THREE LEVELS DOWN. An irrigation zone hangs off
a field which hangs off a parcel, and the obvious reading — suffix the zone with
the *field's* abbreviation — produces `"YC3-Zone2 - YC3"`, which says the same
thing twice and tells you nothing about where the water is. The zone name
already carries its field (`YC3-`); what it does not carry is the ground. So one
short key per parcel, used at every level, and the registers enforce uniqueness
of the name *within the parcel* so the docname cannot collide.

DERIVATION IS A CONVENIENCE, NOT A RULE. An operator who types an abbreviation
gets theirs. One who does not gets initials — "Mill Creek" → `MC`, "Red Camp" →
`RC`, "Olney Road 64" → `OR6` — and a derived key that collides with one already
in use is disambiguated with a digit rather than refused, because the operator
never asked for that key and cannot be expected to defend it. An abbreviation
somebody *did* type and that collides is refused: that one they meant.
"""

import re

import frappe

#: Longest abbreviation this will derive or accept. Eight characters is enough
#: for "MILLCRK" and short enough that `"MC-Cabin-01 - MC"` still reads as a
#: name rather than a path.
MAX_ABBR = 8

#: How many characters a single-word name contributes. "Packinghouse" → "PAC":
#: one letter would collide with everything, and the whole word is not an
#: abbreviation.
SINGLE_WORD_CHARS = 3


def derive_abbr(name: str) -> str:
	"""An abbreviation for a parcel name, from its initials.

	Multi-word names give initials (`"Mill Creek"` → `"MC"`, `"Olney Road 64"` →
	`"OR6"`). A single word gives its first three characters (`"Packinghouse"` →
	`"PAC"`). A name with no alphanumerics at all gives `""`, and the caller
	decides what to do about that — this function never invents a key.
	"""
	words = [word for word in re.split(r"[^A-Za-z0-9]+", str(name or "")) if word]
	if not words:
		return ""
	if len(words) == 1:
		return words[0][:SINGLE_WORD_CHARS].upper()
	return "".join(word[0] for word in words)[:MAX_ABBR].upper()


def normalise_abbr(abbr: str) -> str:
	"""An operator-supplied abbreviation, cleaned to what a docname can carry.

	Uppercased, stripped of everything but letters, digits and the hyphen a site
	may want for `"MC-1"`, and truncated. Deliberately not refused when it is
	unusual — a key is the operator's to choose, and this only removes what would
	make the resulting docname ambiguous.
	"""
	cleaned = re.sub(r"[^A-Za-z0-9-]+", "", str(abbr or "")).upper()
	return cleaned[:MAX_ABBR]


def unique_abbr(base: str, taken) -> str:
	"""`base`, or `base` with the lowest digit suffix that is not in `taken`.

	Only ever called on a *derived* abbreviation. Two parcels called "Mill Creek"
	and "Meadow Creek" both derive `MC`, and refusing the second is refusing an
	operator a key they never asked for — so the second becomes `MC2`. A suffix
	that would exceed `MAX_ABBR` eats into the base rather than overflowing it.
	"""
	taken = {str(entry).upper() for entry in (taken or ())}
	if base and base not in taken:
		return base
	for suffix in range(2, 100):
		tail = str(suffix)
		head = base[: MAX_ABBR - len(tail)] or "P"
		candidate = f"{head}{tail}"
		if candidate not in taken:
			return candidate
	return base  # pragma: no cover - 98 parcels sharing one derivation


def parcel_abbr(parcel: str) -> str:
	"""The short key a parcel's fields, zones and housing units are filed under.

	Falls back to deriving it from the parcel name, so a Parcel registered before
	v0.12.0 — which carries no stored abbreviation until something saves it —
	still produces the docname it will keep once it does. That fallback is why
	this release needs no data patch: the derivation is deterministic, so the
	name a field gets today is the name it would get after the backfill.
	"""
	if not parcel:
		return ""
	row = frappe.db.get_value("Parcel", parcel, ["abbr", "parcel_name"], as_dict=True) or {}
	return str(row.get("abbr") or "").strip() or derive_abbr(row.get("parcel_name") or parcel)


def suffixed(name: str, abbr: str) -> str:
	"""`"<name> - <abbr>"`, or just the name when there is no abbreviation.

	The same shape `Parcel`, `Lease` and every ERPNext Account docname use, so a
	reader who knows one knows all of them.
	"""
	name = str(name or "").strip()
	abbr = str(abbr or "").strip()
	return f"{name} - {abbr}" if abbr else name
