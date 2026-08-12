# SPDX-License-Identifier: MIT
"""One conversion: an ISO 8601 timestamp into what a MariaDB DATETIME takes.

PURE. No frappe import, no database, no side effects — the same contract
`model_registry.py` and `bucket_bridge.py` keep, and the reason this can be
called from a whitelisted endpoint, a tool, or a test without dragging anything
in behind it.

WHY IT IS ITS OWN MODULE. v0.59.1 put `as_mariadb_datetime` in
`model_registry.py`, because the bug that produced it was Volume Vision writing
`training_completed_at` as `2026-07-08T02:38:43Z`. That was never a fact about
ML models. It is a fact about EVERY boundary where something that speaks JSON
writes a timestamp into a Frappe `Datetime` column, and this app has two of
them: the trainer, and an iPhone. `api/mobile.py` importing `model_registry` to
convert a bucket capture's timestamp would have read as a dependency on the
model registry that is not one, so the rule moved here and `model_registry`
re-exports it for its existing callers.

THE SECOND BOUNDARY, AND WHAT IT COST. `BadgeAPI.payload` stamps every bucket
capture with an `ISO8601DateFormatter` set to `.withInternetDateTime` in UTC —
`2026-08-11T07:12:00Z`. `bucket_bridge.validate_bucket_entry` READS that
happily (`_parse_dt` splits the `T` and drops the `Z`), so every entry passed
validation and then died at the insert, where the column would not take the
string the validator had just approved. Not one bucket entry ever synced from a
handset. See `api/mobile._bucket_entries`.
"""

from __future__ import annotations

import datetime
import re

#: An ISO 8601 timestamp as a JSON producer writes one, and as a `Datetime`
#: column will not take one: `2026-07-08T02:38:43Z`. Date and time are matched
#: separately from the offset so the offset can be applied rather than
#: discarded. Written out rather than left to `datetime.fromisoformat` because
#: that function did not accept a trailing `Z` before Python 3.11 and this app
#: supports 3.10, which is exactly the input that produced the bug.
_ISO_DATETIME_PATTERN = re.compile(
	r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
	r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?:\.\d+)?)?"
	r"\s*(?P<offset>[Zz]|[+-]\d{2}:?\d{2})?$"
)

#: What MariaDB's DATETIME takes, and what every Frappe `Datetime` column is
#: written in.
MARIADB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def as_mariadb_datetime(value) -> str:
	"""`value` as `YYYY-MM-DD HH:MM:SS`, or `""` when it is not a timestamp.

	THIS IS WHY IT EXISTS. Volume Vision writes `training_completed_at` into a
	manifest the way every JSON producer does — ISO 8601, `2026-07-08T02:38:43Z`
	— and MariaDB answers a `Datetime` column set to that string with
	`OperationalError (1292, "Incorrect datetime value")`, which surfaces as a
	failed pull with the model already downloaded and nothing to show for it.
	The `T` and the zone designator are the whole problem; the instant is fine.
	An iPhone's `ISO8601DateFormatter` writes the identical shape, which is why
	the bucket capture queue hit the identical wall.

	AN OFFSET IS APPLIED, NOT DISCARDED. `2026-07-08T04:38:43+02:00` becomes
	`2026-07-08 02:38:43`, so the column holds one zone (UTC) for every value
	rather than whichever zone the sender happened to be in. A `Datetime`
	column has nowhere to put a zone, and the alternative — keeping the wall
	clock and dropping the offset — stores two timestamps three hours apart as
	if they were the same moment. A value with NO offset is taken as written,
	because there is nothing else it could mean.

	RETURNS `""` RATHER THAN RAISING for anything unreadable. This module is
	pure and its callers have all already done work they should not lose:
	`reconcile_bundle_manifest` turns the empty string into a warning and
	leaves the field alone, and `_bucket_entries` hands the original string
	back to the validator, which names the field and the value rather than
	reporting a timestamp that has silently gone missing.
	"""
	if isinstance(value, datetime.datetime):
		value = value.replace(microsecond=0)
		return (
			value.astimezone(datetime.timezone.utc).replace(tzinfo=None) if value.tzinfo else value
		).strftime(MARIADB_DATETIME_FORMAT)
	if isinstance(value, datetime.date):
		return datetime.datetime(value.year, value.month, value.day).strftime(MARIADB_DATETIME_FORMAT)

	text = str(value or "").strip()
	if not text:
		return ""
	match = _ISO_DATETIME_PATTERN.match(text)
	if not match:
		return ""

	parts = match.groupdict()
	try:
		stamp = datetime.datetime(
			int(parts["year"]),
			int(parts["month"]),
			int(parts["day"]),
			int(parts["hour"] or 0),
			int(parts["minute"] or 0),
			int(parts["second"] or 0),
		)
	except ValueError:
		# A well-shaped string naming a day that does not exist — 2026-02-30,
		# or hour 25. The pattern cannot catch those and the calendar can.
		return ""

	offset = parts["offset"] or ""
	if offset and offset not in ("Z", "z"):
		sign = -1 if offset[0] == "-" else 1
		digits = offset[1:].replace(":", "")
		stamp -= sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
	return stamp.strftime(MARIADB_DATETIME_FORMAT)
