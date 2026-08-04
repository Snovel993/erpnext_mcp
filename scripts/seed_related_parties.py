#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Seed the Related Party register from a JSON file. Idempotent, dry-run by default.

WHY THIS EXISTS AND WHY IT IS EMPTY. `Related Party` is the one doctype in this
app whose useful content is a list of people's names — members, managers,
trustees, family — and this repository is public. So the script ships with no
data in it: the names live in a JSON file the operator keeps outside the repo,
and this reads that file. A seed script with a family's names committed into it
would be a privacy leak with a git history.

RUNNING IT. This runs OUTSIDE `bench execute`, so it configures Frappe itself
rather than assuming a configured one:

    python3 seed_related_parties.py --input parties.json --site erp.local
    python3 seed_related_parties.py --input parties.json --site erp.local --apply

Without `--apply` it reports exactly what it WOULD create and writes nothing.
That is the default because a register of who is related to whom is not
something to get wrong quietly.

FLAGS, WHICH MATCH THIS DOCSTRING EXACTLY (a previous release shipped a script
whose docstring and `argparse` disagreed, and it cost twenty minutes of somebody
else's evening):

    --input PATH        the JSON file of party records. Required.
    --site SITE         the Frappe site. Defaults to `currentsite.txt`.
    --sites-path PATH   the bench's `sites` directory. Auto-detected by walking
                        up from this file, then from the working directory.
    --company NAME      default company for records that omit one.
    --apply             actually write. Without it, nothing is created.
    --verbose           print each record as it is considered.

THE INPUT FORMAT is a JSON list of objects, or an object with a `parties` key
holding one. Each record takes the same fields `create_related_party` does:

    [
      {"party_name": "...", "party_type": "Individual", "company": "...",
       "relationship_to_company": "Manager", "effective_date": "2020-06-15",
       "tax_id_type": "SSN", "tax_id_last4": "6789",
       "address": "...", "notes": "...", "supplier": "...",
       "cap_table_entry": "...", "governing_document": "...", "end_date": "..."}
    ]

`tax_id_last4` is four digits and this refuses nine before Frappe is even
started — see `validate`. The full number belongs on the signed W-9.

COPYING THE PLAN INTO A CONTAINER. The JSON file lives on the host and the
script runs where the bench is, so both have to be copied in. In order:

    docker cp parties.json <container>:/home/frappe/frappe-bench/parties.json
    docker cp scripts/seed_related_parties.py <container>:/home/frappe/frappe-bench/
    docker exec -w /home/frappe/frappe-bench <container> \\
        python3 seed_related_parties.py --input parties.json --site <site>
    # ...read the plan, then re-run the last line with --apply

Do NOT leave the JSON in the container afterwards:

    docker exec <container> rm -f /home/frappe/frappe-bench/parties.json

EXIT CODES. 0 when everything asked for is now true (including a dry run that
found no problems), 1 for a bad plan or a refused record, 2 for a Frappe or site
problem. Nothing is half-written: the whole plan is validated before the first
insert, and a failure part-way rolls the transaction back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

#: Fields a record may carry. Anything else is a typo, and a typo silently
#: ignored is a field somebody thinks they set.
ALLOWED_FIELDS = frozenset(
	{
		"party_name",
		"company",
		"party_type",
		"relationship_to_company",
		"effective_date",
		"end_date",
		"tax_id_type",
		"tax_id_last4",
		"address",
		"notes",
		"cap_table_entry",
		"supplier",
		"governing_document",
	}
)

REQUIRED_FIELDS = ("party_name", "party_type", "relationship_to_company", "effective_date")

DOCTYPE = "Related Party"


class PlanError(Exception):
	"""A problem with the input file. Reported before Frappe is started."""


# ── the parts that need no bench ────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
	"""The parser, separately from parsing, so a test can read what it registers.

	The whole point: the module docstring lists the flags and a test compares the
	two lists. A previous release shipped a docstring saying `--include-submitted`
	against a parser registering `--list-submitted`, and it cost twenty minutes of
	somebody else's evening.
	"""
	parser = argparse.ArgumentParser(
		prog="seed_related_parties.py",
		description="Seed the Related Party register from a JSON file. Dry-run by default.",
	)
	parser.add_argument("--input", required=True, help="the JSON file of party records")
	parser.add_argument("--site", default="", help="the Frappe site; defaults to currentsite.txt")
	parser.add_argument("--sites-path", default="", help="the bench's sites directory")
	parser.add_argument("--company", default="", help="default company for records that omit one")
	parser.add_argument("--apply", action="store_true", help="actually write; without it nothing is created")
	parser.add_argument("--verbose", action="store_true", help="print each record as it is considered")
	return parser


def registered_flags() -> set:
	"""Every long option `build_parser` registers, `--help` excluded."""
	out = set()
	for action in build_parser()._actions:
		out.update(option for option in action.option_strings if option.startswith("--"))
	return out - {"--help"}


def parse_args(argv=None) -> argparse.Namespace:
	return build_parser().parse_args(argv)


def find_sites_path(explicit: str = "") -> str:
	"""The bench's `sites` directory, from the flag or by looking for one.

	A sites directory is recognised by holding `common_site_config.json` or
	`apps.txt` — the two files every bench has and nothing else does. Searched
	from this file's location outwards first, because the script is usually
	copied into the bench, then from the working directory.
	"""
	if explicit:
		path = os.path.abspath(explicit)
		if not os.path.isdir(path):
			raise PlanError(f"--sites-path {explicit!r} is not a directory")
		return path

	seeds = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
	for seed in seeds:
		current = seed
		while True:
			for candidate in (os.path.join(current, "sites"), current):
				if _is_sites_dir(candidate):
					return candidate
			parent = os.path.dirname(current)
			if parent == current:
				break
			current = parent
	raise PlanError(
		"could not find the bench's sites directory. Pass --sites-path, or run this from "
		"inside the bench (the directory holding `sites/common_site_config.json`)."
	)


def _is_sites_dir(path: str) -> bool:
	return os.path.isfile(os.path.join(path, "common_site_config.json")) or os.path.isfile(
		os.path.join(path, "apps.txt")
	)


def find_site(sites_path: str, explicit: str = "") -> str:
	if explicit:
		return explicit
	current = os.path.join(sites_path, "currentsite.txt")
	if os.path.isfile(current):
		with open(current) as handle:
			name = handle.read().strip()
		if name:
			return name
	raise PlanError(f"no --site given and {current} is missing or empty. Pass --site <site>.")


def log_dirs(sites_path: str, site: str) -> list[str]:
	"""Every directory Frappe may try to write a log into during `connect`.

	`frappe.connect()` opens the framework's loggers, and a logger whose
	directory does not exist raises before anything useful has happened. In a
	container built from the official image these are usually there; in one where
	the bench was assembled by hand, or where `logs` was never mounted, they are
	not — which is how a working script became a twenty-minute mystery in a
	previous release.
	"""
	bench = os.path.dirname(sites_path.rstrip(os.sep))
	return [
		os.path.join(bench, "logs"),
		os.path.join(sites_path, site, "logs"),
		# The path the official image's frappe user actually has, named
		# explicitly because it is not always the bench's parent.
		os.path.join(os.path.expanduser("~"), "logs"),
	]


def ensure_log_dirs(sites_path: str, site: str, verbose: bool = False) -> list[str]:
	"""Create the log directories, and report which ones this could not.

	A directory this cannot create is not fatal on its own — the framework may
	never write there — so it is reported rather than raised. The failure this
	prevents is the one where the directory is simply absent.
	"""
	unwritable = []
	for path in log_dirs(sites_path, site):
		try:
			os.makedirs(path, exist_ok=True)
			if verbose:
				print(f"  log directory ready: {path}")
		except OSError as error:
			unwritable.append(f"{path} ({error.strerror})")
	return unwritable


def load_plan(path: str) -> list[dict]:
	"""Read the JSON file into a list of records. Raises PlanError, never IOError."""
	if not os.path.isfile(path):
		raise PlanError(f"no such input file: {path}")
	try:
		with open(path) as handle:
			payload = json.load(handle)
	except json.JSONDecodeError as error:
		raise PlanError(f"{path} is not valid JSON: {error}") from None

	if isinstance(payload, dict):
		payload = payload.get("parties")
	if not isinstance(payload, list):
		raise PlanError(
			f"{path} must hold a JSON list of party records, or an object with a `parties` key holding one."
		)
	if not payload:
		raise PlanError(f"{path} holds no records. Nothing to do.")
	return payload


def validate(record, index: int, default_company: str = "") -> dict:
	"""Check one record and return it with the default company filled in.

	Every check here is one `create_related_party` also makes. Duplicating them
	is deliberate: a plan of forty records should be refused whole, before the
	first insert, rather than half-applied and then stopped.
	"""
	where = f"record {index}"
	if not isinstance(record, dict):
		raise PlanError(f"{where} is not an object")

	unknown = sorted(set(record) - ALLOWED_FIELDS)
	if unknown:
		raise PlanError(
			f"{where} has field(s) this script does not know: {', '.join(unknown)}. "
			f"Known fields: {', '.join(sorted(ALLOWED_FIELDS))}."
		)

	out = {key: value for key, value in record.items() if value not in (None, "")}
	if default_company:
		out.setdefault("company", default_company)

	missing = [field for field in REQUIRED_FIELDS if not str(out.get(field) or "").strip()]
	if missing:
		raise PlanError(f"{where} is missing required field(s): {', '.join(missing)}")
	if not str(out.get("company") or "").strip():
		raise PlanError(f"{where} has no company, and no --company default was given")

	digits = str(out.get("tax_id_last4") or "").replace("-", "").replace(" ", "")
	if digits:
		if not digits.isdigit():
			raise PlanError(f"{where}: tax_id_last4 must be four digits, got {out['tax_id_last4']!r}")
		if len(digits) == 9:
			raise PlanError(
				f"{where}: tax_id_last4 is nine digits — that is a whole taxpayer id, not the "
				f"last four of one. Send {digits[-4:]!r}. The full number belongs on the signed "
				"W-9, not in this file and not on the site."
			)
		if len(digits) != 4:
			raise PlanError(f"{where}: tax_id_last4 must be exactly four digits, got {out['tax_id_last4']!r}")
		out["tax_id_last4"] = digits

	if out.get("end_date") and out["end_date"] < out["effective_date"]:
		raise PlanError(
			f"{where}: end_date {out['end_date']} is before effective_date {out['effective_date']}"
		)
	return out


def validate_plan(records, default_company: str = "") -> list[dict]:
	return [validate(record, index, default_company) for index, record in enumerate(records, start=1)]


# ── the parts that need a bench ─────────────────────────────────────────────
def connect(site: str, sites_path: str, verbose: bool = False):
	"""Start Frappe against this site, having made the log directories first."""
	unwritable = ensure_log_dirs(sites_path, site, verbose=verbose)
	if unwritable:
		print(f"warning: could not create log director(y/ies): {'; '.join(unwritable)}")

	import frappe

	frappe.init(site=site, sites_path=sites_path)
	frappe.connect()
	return frappe


def existing_name(frappe, record: dict) -> str:
	return (
		frappe.db.get_value(
			DOCTYPE,
			{
				"party_name": record["party_name"],
				"relationship_to_company": record["relationship_to_company"],
				"company": record["company"],
			},
			"name",
		)
		or ""
	)


def seed(frappe, records: list[dict], apply_changes: bool, verbose: bool = False) -> dict:
	"""Create the records that do not exist. Idempotent: re-running creates nothing."""
	created, skipped = [], []
	for record in records:
		existing = existing_name(frappe, record)
		if existing:
			skipped.append((record["party_name"], record["relationship_to_company"], existing))
			if verbose:
				print(
					f"  exists:  {record['party_name']} as {record['relationship_to_company']} → {existing}"
				)
			continue
		if verbose:
			print(f"  create:  {record['party_name']} as {record['relationship_to_company']}")
		if not apply_changes:
			created.append((record["party_name"], record["relationship_to_company"], "(not written)"))
			continue
		doc = frappe.new_doc(DOCTYPE)
		for field, value in record.items():
			doc.set(field, value)
		doc.insert(ignore_permissions=True)
		created.append((record["party_name"], record["relationship_to_company"], doc.name))
	return {"created": created, "skipped": skipped}


def main(argv=None) -> int:
	args = parse_args(argv)
	try:
		sites_path = find_sites_path(args.sites_path)
		site = find_site(sites_path, args.site)
		records = validate_plan(load_plan(args.input), args.company)
	except PlanError as error:
		print(f"error: {error}", file=sys.stderr)
		return 1

	print(f"site:       {site}")
	print(f"sites path: {sites_path}")
	print(f"plan:       {len(records)} record(s) from {args.input}")
	print(
		f"mode:       {'APPLY — records will be created' if args.apply else 'DRY RUN — nothing will be written'}"
	)

	frappe = None
	try:
		frappe = connect(site, sites_path, verbose=args.verbose)
	# Any exception here is a site problem — a bad site name, a database that is
	# not up, a bench whose config this cannot read — and the useful answer is
	# the type and the message rather than a traceback.
	except Exception as error:
		print(f"error: could not connect to {site}: {type(error).__name__}: {error}", file=sys.stderr)
		return 2

	try:
		if not frappe.db.exists("DocType", DOCTYPE):
			print(
				f"error: the {DOCTYPE!r} DocType is not on this site. Install erpnext_mcp 0.11.0 "
				"or later and run `bench --site <site> migrate`.",
				file=sys.stderr,
			)
			return 2
		result = seed(frappe, records, args.apply, verbose=args.verbose)
		if args.apply:
			frappe.db.commit()
	# Report and roll back. A seed run that half-applied is worse than one that
	# did nothing, so the transaction goes back either way.
	except Exception as error:
		frappe.db.rollback()
		print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
		print("nothing was written — the transaction was rolled back.", file=sys.stderr)
		return 1
	finally:
		try:
			frappe.destroy()
		except Exception:  # pragma: no cover - teardown must not mask a real error
			pass

	print(f"\n{len(result['created'])} to create, {len(result['skipped'])} already present")
	for party_name, relationship, name in result["created"]:
		print(f"  + {party_name} — {relationship} — {name}")
	for party_name, relationship, name in result["skipped"]:
		print(f"  = {party_name} — {relationship} — {name}")
	if not args.apply and result["created"]:
		print("\nNothing was written. Re-run with --apply to create the records above.")
	return 0


if __name__ == "__main__":  # pragma: no cover
	sys.exit(main())
