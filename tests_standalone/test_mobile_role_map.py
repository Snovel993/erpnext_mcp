# SPDX-License-Identifier: MIT
"""Which mobile role a farm job title gets, and the one job title that IS a role.

THE QUESTION THIS ANSWERS was asked in the wrong shape three times: "we need a
Checker role", "we need a Tractor Driver role", "we need a Crew Leader role".
Two of those are not requests for a role and one of them is, and the test that
tells them apart is not "is it a distinct job" — it is **does it touch a
different set of records**.

FOUR CLAIMS.

1. `CrewLeaderIsARole` — because forming and closing a Farm Shift writes a
   register no Field Worker may touch, and because this app already NAMED the
   role in two gates and a shipped iOS build while `roles.py` created nothing.
   `employee.SHIFT_ROLES` has listed it since v0.19.3 and `create_mobile_user`
   refused to enrol one; both halves are asserted here.

2. `CheckerAndTractorDriverAreDesignations` — they hold Field Worker, and the
   one thing a checker's job needed that the role did not carry is the container
   fill threshold. Granting that read to the role is the alternative to
   inventing a role for one column, and it is asserted in both directions: the
   checker may READ the band and may NOT move it.

3. `TheMappingIsMachineReadable` — `list_mobile_users` returns it, because "we
   hired a checker, what do we give them" should not be a paragraph in a release
   note. It names the DESIGNATION and the ROLE, which are two fields set by two
   different tools.

4. `TheDesignationsExist` — the installer seeds them, create-only, so
   `list_pending_threshold_acknowledgments` filtering Active Employees on
   `designation == "Checker"` is a query against a master that is actually there
   rather than one nothing ever created.
"""

import frappe

from erpnext_mcp import install, roles

from .fixtures import MAIN, SeededTestCase
from .harness import STORE

CUSTOM = roles.CUSTOM_DOCPERM

THRESHOLD = "Container Fill Threshold"
SHIFT = "Farm Shift"
BUCKET_SESSION = "Bucket Log Session"

ON = {
	f"allow_{name}": 1
	for name in ("list_mobile_users", "create_mobile_user", "list_designations")
}


def custom_perms(doctype: str) -> dict:
	return {str(row.get("role")): row for row in STORE.rows(CUSTOM) if str(row.get("parent")) == doctype}


class RoleMapTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		roles.install_roles()

	def may(self, role: str, doctype: str, flag: str = "read") -> bool:
		row = custom_perms(doctype).get(role)
		return bool(row and int(row.get(flag) or 0))


class CrewLeaderIsARole(RoleMapTestCase):
	def test_the_installer_creates_it(self):
		self.assertIn("Crew Leader", roles.ROLE_NAMES)
		self.assertTrue(frappe.db.exists("Role", "Crew Leader"))

	def test_the_app_already_named_it_in_the_shift_gate(self):
		"""The gap this closes, stated as the two facts that made it a gap.

		`employee.SHIFT_ROLES` has admitted a Crew Leader since v0.19.3 and the
		iOS ShiftToolsToolbar offers Crew Clock to one. `roles.py` created no
		such role and granted it nothing, so a site that wanted one had to build
		it by hand and could not enrol one through this app at all.
		"""
		from erpnext_mcp.tools import employee as employee_tool

		self.assertIn("Crew Leader", employee_tool.SHIFT_ROLES)
		self.assertNotIn("Crew Leader", employee_tool.HR_ROLES)

	def test_create_mobile_user_can_now_enrol_one(self):
		data = self.tool_data(
			"create_mobile_user",
			{
				"email": "lead@example.test",
				"full_name": "Beto Cruz",
				"role": "Crew Leader",
				"entity_access": [MAIN],
			},
		)
		self.assertEqual(data["role"], "Crew Leader")
		self.assertIn("Crew Leader", roles.roles_of("lead@example.test"))

	def test_the_shift_is_theirs_and_the_board_is_not(self):
		"""THE SEPARATION THAT MAKES IT NOT A SECOND FOREMAN, both directions.

		OAR 437-004-1131 puts the water, shade, rest-cycle and observation
		obligations on the supervisor standing on the block, and `end_shift`
		writes one submitted Attendance per crew member — so a crew lead who
		cannot close is a crew with no wage record for the day. Raising and
		sending work stays the Foreman's.
		"""
		self.assertTrue(self.may("Crew Leader", SHIFT, "write"))
		self.assertTrue(self.may("Crew Leader", SHIFT, "create"))
		self.assertTrue(self.may("Crew Leader", "Farm Task", "read"))
		self.assertFalse(self.may("Crew Leader", "Farm Task", "create"))
		self.assertFalse(self.may("Crew Leader", "Farm Task", "write"))

	def test_a_field_worker_still_cannot_write_the_shift(self):
		"""The negative control for the claim above. A role that could write
		nothing would pass the Foreman half of it by accident."""
		self.assertTrue(self.may("Field Worker", SHIFT, "read"))
		self.assertFalse(self.may("Field Worker", SHIFT, "write"))

	def test_it_is_a_phone_role(self):
		self.assertFalse(roles.spec_for("Crew Leader").desk_access)

	def test_it_does_not_reach_the_personnel_register_or_accounting(self):
		targets = {doctype for doctype, _flags in roles.spec_for("Crew Leader").permissions}
		for doctype in ("Employee", "I-9 Form", "W-4 Form", "Journal Entry", "Compliance Policy"):
			self.assertNotIn(doctype, targets)


class CheckerAndTractorDriverAreDesignations(RoleMapTestCase):
	def test_neither_is_a_role(self):
		self.assertNotIn("Checker", roles.ROLE_NAMES)
		self.assertNotIn("Tractor Driver", roles.ROLE_NAMES)

	def test_both_map_onto_field_worker(self):
		self.assertEqual(roles.ROLE_FOR_JOB_TITLE["Checker"], "Field Worker")
		self.assertEqual(roles.ROLE_FOR_JOB_TITLE["Tractor Driver"], "Field Worker")

	def test_a_field_worker_can_read_the_band_a_checker_enforces(self):
		"""The one thing the job needed that the role did not carry.

		`update_fill_threshold` is Foreman-and-above, which is right — and
		nothing granted the person APPLYING the band so much as a read, so a
		handset showed a number it had no permission to fetch and the
		acknowledgment loop asked people to confirm a number they could not see.
		"""
		self.assertTrue(self.may("Field Worker", THRESHOLD, "read"))
		self.assertTrue(self.may("Field Worker", "Fill Threshold Change Log", "read"))

	def test_a_field_worker_cannot_move_it(self):
		"""The other direction, and the reason the read was granted instead of a
		role: a checker who could move the number they are asked to trust is the
		exact shape `fill_pipeline.FOREMAN_ROLES` exists to prevent."""
		self.assertFalse(self.may("Field Worker", THRESHOLD, "write"))
		self.assertTrue(self.may("Foreman", THRESHOLD, "write"))
		self.assertFalse(self.may("Crew Leader", THRESHOLD, "write"))
		# The Farm Manager's write comes from the doctype's own standard DocPerm
		# rather than from `roles.py` — see the bucket-log test below.
		self.assertTrue(self.may("Farm Manager", THRESHOLD, "write"))

	def test_a_field_worker_cannot_read_the_bucket_log(self):
		"""Deliberate in the direction that looks unhelpful.

		A Bucket Log Entry is a piece-rate count, which is somebody's pay — and a
		User Permission scopes by COMPANY rather than by person, so a picker
		granted read here could read the whole crew's day. The three roles that
		run the crew get it, read-only.
		"""
		self.assertFalse(self.may("Field Worker", BUCKET_SESSION))
		for role in ("Foreman", "Crew Leader"):
			with self.subTest(role=role):
				self.assertTrue(self.may(role, BUCKET_SESSION, "read"))
				self.assertFalse(self.may(role, BUCKET_SESSION, "write"))
		# A Farm Manager already had r/w/c from the doctype's OWN standard
		# DocPerm, which the installer's mirror copies — so `roles.py` grants it
		# nothing here on purpose. A read-only grant would have been a silent
		# no-op that `describe_role` then advertised as the truth.
		self.assertTrue(self.may("Farm Manager", BUCKET_SESSION, "write"))
		self.assertNotIn(
			BUCKET_SESSION,
			{doctype for doctype, _flags in roles.spec_for("Farm Manager").permissions},
		)

	def test_the_checker_designation_is_what_the_app_actually_filters_on(self):
		from erpnext_mcp.tools import fill_pipeline

		self.assertEqual(fill_pipeline.CHECKER_DESIGNATION, "Checker")
		self.assertIn("Checker", roles.ROLE_FOR_JOB_TITLE)


class TheMappingIsMachineReadable(RoleMapTestCase):
	def test_list_mobile_users_returns_it(self):
		data = self.tool_data("list_mobile_users")
		titles = {entry["designation"]: entry for entry in data["job_titles"]}
		self.assertEqual(titles["Checker"]["mobile_role"], "Field Worker")
		self.assertEqual(titles["Tractor Driver"]["mobile_role"], "Field Worker")
		self.assertEqual(titles["Crew Leader"]["mobile_role"], "Crew Leader")
		self.assertEqual(titles["Foreman"]["mobile_role"], "Foreman")

	def test_every_mapped_role_is_a_real_role(self):
		"""A mapping naming a role that does not exist is worse than no mapping:
		it reads as an instruction and refuses when followed."""
		for entry in roles.JOB_TITLES:
			with self.subTest(designation=entry["designation"]):
				self.assertIn(entry["mobile_role"], roles.ROLE_NAMES)
				self.assertTrue(entry["why"].strip())

	def test_it_reports_whether_each_half_is_actually_installed(self):
		"""The mapping is only actionable if both the role and the master are
		there — a site whose Checker designation was renamed should see that here
		rather than discover it when the acknowledgment loop returns nobody."""
		rows = {entry["designation"]: entry for entry in roles.job_titles()}
		self.assertTrue(rows["Crew Leader"]["role_installed"])
		self.assertFalse(rows["Checker"]["designation_exists"])
		STORE.seed("Designation", [{"name": "Checker", "designation_name": "Checker"}])
		self.assertTrue({e["designation"]: e for e in roles.job_titles()}["Checker"]["designation_exists"])

	def test_the_role_catalogue_still_lists_every_role(self):
		data = self.tool_data("list_mobile_users")
		self.assertEqual([entry["role"] for entry in data["roles"]], list(roles.ROLE_NAMES))

	def test_an_unknown_role_refusal_names_them_all(self):
		error = self.tool_error(
			"create_mobile_user",
			{"email": "x@example.test", "full_name": "X Y", "role": "Checker", "entity_access": [MAIN]},
		)
		self.assertIn("Crew Leader", error)
		self.assertIn(str(len(roles.ROLE_NAMES)), error)
		# The refusal has to say what a Checker actually gets, or it is a "no"
		# with no next step.
		self.assertIn("Designation", error)


class TheDesignationsExist(RoleMapTestCase):
	def test_the_seeder_creates_the_titles_this_app_reads(self):
		install._farm_designations()
		for name in roles.ROLE_FOR_JOB_TITLE:
			with self.subTest(designation=name):
				self.assertTrue(frappe.db.exists("Designation", name))

	def test_it_is_create_only_and_never_overwrites(self):
		STORE.seed(
			"Designation",
			[{"name": "Checker", "designation_name": "Checker", "description": "ours, not yours"}],
		)
		install._farm_designations()
		self.assertEqual(
			frappe.db.get_value("Designation", "Checker", "description"), "ours, not yours"
		)

	def test_running_it_twice_creates_nothing_the_second_time(self):
		install._farm_designations()
		before = len(STORE.rows("Designation"))
		install._farm_designations()
		self.assertEqual(len(STORE.rows("Designation")), before)

	# The seeder reads its list FROM the mapping rather than restating it, so a
	# title in one and not the other cannot happen.
	def test_the_seeded_list_is_the_mapping(self):
		self.assertEqual(
			sorted(install.FARM_DESIGNATIONS), sorted(roles.ROLE_FOR_JOB_TITLE)
		)


class EveryPhoneOnlyRoleHasADoor(RoleMapTestCase):
	"""The invariant behind F1, stated so it cannot rot back.

	A role with `desk_access=0` has been told, by its own spec, that the phone is
	the only way in. `api/guard.FARM_OPS_ROLES` is that way in — the enrolment
	gate every field method runs before anything else. So a phone-only role
	missing from that frozenset is not a narrow permission, it is an account with
	no door at all: every DocPerm `roles.py` grants it is unreachable, and the
	handset gets a refusal that names enrolment rather than the grant it lacks.

	CREW LEADER WAS EXACTLY THAT for four releases. `employee.SHIFT_ROLES` listed
	it, `roles.py` granted it the Farm Shift, `create_mobile_user` enrolled it —
	and `FARM_OPS_ROLES` did not have the name, so none of it was reachable.
	Reverting the guard line turns this red naming Crew Leader, which is the only
	evidence that it tests anything.

	WHAT THE STANDALONE DOUBLE CAN PROVE HERE: this reads two module-level
	constants and compares them. No store, no session, no transport — so it is
	one of the few assertions in this suite that means exactly as much here as it
	does on the bench.
	"""

	def test_every_phone_only_role_can_reach_the_field_api(self):
		from erpnext_mcp.api import guard

		for spec in roles.ROLE_SPECS:
			if spec.desk_access:
				continue
			with self.subTest(role=spec.name):
				self.assertIn(
					spec.name,
					guard.FARM_OPS_ROLES,
					f"{spec.name} has desk_access=0 and is not in FARM_OPS_ROLES, so it "
					"has no door: the Desk is closed to it by its own spec and the field "
					"API refuses it at enrolment. Add the name to FARM_OPS_ROLES or give "
					"the spec desk_access=1 — the one thing it may not be is neither.",
				)

	def test_the_negative_control(self):
		"""A role WITH desk access is deliberately not required to be in the set.

		Without this, the assertion above would read as "every role must be in
		FARM_OPS_ROLES", which is false and would have been satisfied by adding
		all seven. Compliance Officer is the case: it holds a real login, reads
		the register in the Desk, and is kept off the field API on purpose.
		"""
		self.assertTrue(roles.spec_for("Compliance Officer").desk_access)
		from erpnext_mcp.api import guard

		self.assertNotIn("Compliance Officer", guard.FARM_OPS_ROLES)
