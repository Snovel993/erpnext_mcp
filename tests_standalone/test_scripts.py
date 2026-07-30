# SPDX-License-Identifier: MIT
"""`scripts/seed_related_parties.py` — the parts that run before Frappe does.

WHY A SEED SCRIPT GETS TESTS AT ALL. Every failure this script has ever had was
in the thirty lines before `frappe.connect()`: a docstring naming a flag
`argparse` did not register, a sites path it could not find, a log directory it
assumed existed. Those are exactly the parts that need no bench, so they are
exactly the parts a standalone suite can hold to account.

THE FLAG TEST IS THE ONE THAT EARNED ITS PLACE. A previous release shipped a
script whose docstring said `--include-submitted` and whose parser registered
`--list-submitted`. The test below reads both and compares them, so the two
cannot drift again.
"""

import json
import os
import re
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "seed_related_parties.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import seed_related_parties as seed  # noqa: E402


class Flags(unittest.TestCase):
	def documented_flags(self) -> set:
		"""Every `--flag` the module docstring's flag list names."""
		block = seed.__doc__.split("FLAGS, WHICH MATCH THIS DOCSTRING EXACTLY", 1)[1]
		block = block.split("THE INPUT FORMAT", 1)[0]
		return set(re.findall(r"^\s{4}(--[a-z-]+)", block, re.M))

	def test_the_documented_flags_and_the_registered_flags_are_the_same_set(self):
		"""The exact discrepancy a previous release shipped, in both directions —
		a documented flag that does not exist AND a flag nobody documented."""
		documented = self.documented_flags()
		self.assertIn("--input", documented)
		self.assertIn("--apply", documented)
		self.assertEqual(documented, seed.registered_flags())

	def test_input_is_required(self):
		with self.assertRaises(SystemExit):
			seed.parse_args([])

	def test_apply_defaults_to_off(self):
		"""A register of who is related to whom is not something to get wrong
		quietly, so the default writes nothing."""
		self.assertFalse(seed.parse_args(["--input", "x"]).apply)

	def test_the_dashed_flag_becomes_the_underscored_attribute(self):
		self.assertEqual(seed.parse_args(["--input", "x", "--sites-path", "/s"]).sites_path, "/s")


class SitesPath(unittest.TestCase):
	def bench(self):
		root = tempfile.mkdtemp(prefix="fake-bench-")
		sites = os.path.join(root, "sites")
		os.makedirs(os.path.join(sites, "erp.local"))
		with open(os.path.join(sites, "common_site_config.json"), "w") as handle:
			handle.write("{}")
		return root, sites

	def test_an_explicit_path_is_used_as_given(self):
		_root, sites = self.bench()
		self.assertEqual(seed.find_sites_path(sites), sites)

	def test_an_explicit_path_that_is_not_a_directory_is_refused(self):
		with self.assertRaises(seed.PlanError) as caught:
			seed.find_sites_path("/definitely/not/here")
		self.assertIn("not a directory", str(caught.exception))

	def test_it_finds_sites_by_walking_up_from_the_working_directory(self):
		_root, sites = self.bench()
		here = os.getcwd()
		try:
			os.chdir(os.path.join(sites, "erp.local"))
			self.assertEqual(os.path.realpath(seed.find_sites_path()), os.path.realpath(sites))
		finally:
			os.chdir(here)

	def test_a_directory_holding_apps_txt_counts_as_a_bench(self):
		root = tempfile.mkdtemp(prefix="fake-bench-")
		sites = os.path.join(root, "sites")
		os.makedirs(sites)
		with open(os.path.join(sites, "apps.txt"), "w") as handle:
			handle.write("frappe\n")
		self.assertTrue(seed._is_sites_dir(sites))

	def test_the_site_comes_from_currentsite_when_no_flag_is_given(self):
		_root, sites = self.bench()
		with open(os.path.join(sites, "currentsite.txt"), "w") as handle:
			handle.write("erp.local\n")
		self.assertEqual(seed.find_site(sites), "erp.local")

	def test_a_missing_currentsite_is_refused_with_the_flag_to_pass(self):
		_root, sites = self.bench()
		with self.assertRaises(seed.PlanError) as caught:
			seed.find_site(sites)
		self.assertIn("--site", str(caught.exception))

	def test_the_flag_wins_over_currentsite(self):
		_root, sites = self.bench()
		with open(os.path.join(sites, "currentsite.txt"), "w") as handle:
			handle.write("other.local\n")
		self.assertEqual(seed.find_site(sites, "erp.local"), "erp.local")


class LogDirectories(unittest.TestCase):
	def test_it_names_the_bench_log_dir_and_the_sites_own(self):
		"""`frappe.connect()` opens loggers, and a logger whose directory does not
		exist raises before anything useful has happened."""
		paths = seed.log_dirs("/home/frappe/frappe-bench/sites", "erp.local")
		self.assertIn("/home/frappe/frappe-bench/logs", paths)
		self.assertIn("/home/frappe/frappe-bench/sites/erp.local/logs", paths)

	def test_it_creates_them(self):
		root = tempfile.mkdtemp(prefix="fake-bench-")
		sites = os.path.join(root, "sites")
		os.makedirs(os.path.join(sites, "erp.local"))
		seed.ensure_log_dirs(sites, "erp.local")
		self.assertTrue(os.path.isdir(os.path.join(root, "logs")))
		self.assertTrue(os.path.isdir(os.path.join(sites, "erp.local", "logs")))

	def test_creating_them_twice_is_fine(self):
		root = tempfile.mkdtemp(prefix="fake-bench-")
		sites = os.path.join(root, "sites")
		os.makedirs(sites)
		seed.ensure_log_dirs(sites, "erp.local")
		self.assertEqual(seed.ensure_log_dirs(sites, "erp.local"), [])

	def test_an_uncreatable_directory_is_reported_rather_than_raised(self):
		unwritable = seed.ensure_log_dirs("/proc/nonexistent/sites", "erp.local")
		self.assertTrue(unwritable)


class Plan(unittest.TestCase):
	def written(self, payload) -> str:
		handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
		json.dump(payload, handle)
		handle.close()
		return handle.name

	def record(self, **overrides):
		out = {
			"party_name": "Tim Polehn",
			"party_type": "Individual",
			"relationship_to_company": "Manager",
			"effective_date": "2020-06-15",
			"company": "Orchard Meadow LLC",
		}
		out.update(overrides)
		return out

	def test_a_bare_list_is_accepted(self):
		self.assertEqual(len(seed.load_plan(self.written([self.record()]))), 1)

	def test_an_object_with_a_parties_key_is_accepted(self):
		self.assertEqual(len(seed.load_plan(self.written({"parties": [self.record()]}))), 1)

	def test_a_missing_file_is_refused_by_name(self):
		with self.assertRaises(seed.PlanError) as caught:
			seed.load_plan("/no/such/plan.json")
		self.assertIn("no such input file", str(caught.exception))

	def test_broken_json_is_refused_with_the_parser_error(self):
		handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
		handle.write("{not json")
		handle.close()
		with self.assertRaises(seed.PlanError) as caught:
			seed.load_plan(handle.name)
		self.assertIn("not valid JSON", str(caught.exception))

	def test_an_empty_plan_is_refused(self):
		with self.assertRaises(seed.PlanError) as caught:
			seed.load_plan(self.written([]))
		self.assertIn("no records", str(caught.exception))

	def test_a_valid_record_passes(self):
		self.assertEqual(seed.validate(self.record(), 1)["party_name"], "Tim Polehn")

	def test_the_company_default_fills_in(self):
		record = self.record()
		record.pop("company")
		self.assertEqual(seed.validate(record, 1, "Highland LLC")["company"], "Highland LLC")

	def test_no_company_and_no_default_is_refused(self):
		record = self.record()
		record.pop("company")
		with self.assertRaises(seed.PlanError) as caught:
			seed.validate(record, 1)
		self.assertIn("--company", str(caught.exception))

	def test_a_missing_required_field_is_refused_by_name(self):
		record = self.record()
		record.pop("effective_date")
		with self.assertRaises(seed.PlanError) as caught:
			seed.validate(record, 4)
		self.assertIn("record 4", str(caught.exception))
		self.assertIn("effective_date", str(caught.exception))

	def test_an_unknown_field_is_refused_rather_than_ignored(self):
		"""A typo silently dropped is a field somebody thinks they set."""
		with self.assertRaises(seed.PlanError) as caught:
			seed.validate(self.record(taxid="6789"), 1)
		self.assertIn("does not know", str(caught.exception))
		self.assertIn("taxid", str(caught.exception))

	def test_nine_digits_are_refused_before_frappe_is_even_started(self):
		with self.assertRaises(seed.PlanError) as caught:
			seed.validate(self.record(tax_id_type="SSN", tax_id_last4="123456789"), 1)
		self.assertIn("nine digits", str(caught.exception))
		self.assertIn("'6789'", str(caught.exception))

	def test_a_hyphenated_full_id_is_caught_too(self):
		with self.assertRaises(seed.PlanError):
			seed.validate(self.record(tax_id_type="SSN", tax_id_last4="123-45-6789"), 1)

	def test_four_digits_are_normalised_and_kept(self):
		self.assertEqual(
			seed.validate(self.record(tax_id_type="EIN", tax_id_last4="4411"), 1)["tax_id_last4"],
			"4411",
		)

	def test_an_end_date_before_the_start_is_refused(self):
		with self.assertRaises(seed.PlanError) as caught:
			seed.validate(self.record(end_date="2019-01-01"), 1)
		self.assertIn("before effective_date", str(caught.exception))

	def test_the_whole_plan_is_validated_before_anything_is_written(self):
		"""A plan of forty records should be refused whole, not half-applied."""
		with self.assertRaises(seed.PlanError) as caught:
			seed.validate_plan([self.record(), self.record(party_name="")], "")
		self.assertIn("record 2", str(caught.exception))


class Header(unittest.TestCase):
	def test_the_script_carries_its_spdx_header(self):
		"""CI checks this across every tracked file; failing here says which."""
		with open(SCRIPT_PATH) as handle:
			head = "".join(handle.readlines()[:3])
		self.assertIn("SPDX-License-Identifier: MIT", head)

	def test_it_documents_the_container_copy_sequence(self):
		"""A script that has to be copied into a container and cannot say how is a
		script somebody works out again every time."""
		self.assertIn("docker cp", seed.__doc__)
		self.assertIn("--apply", seed.__doc__)
		self.assertIn("rm -f", seed.__doc__)
