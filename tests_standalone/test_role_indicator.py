# SPDX-License-Identifier: MIT
"""The role badge: which one word describes somebody on a screen this size.

WHAT THE PHONE WAS DOING INSTEAD. `get_current_user_context` returns `roles` —
every role the account holds, this app's six and the site's own — and the app
was intersecting that array with a list of role names compiled into Swift and
drawing whichever came out first. That is this app's role vocabulary living in a
binary somebody has to ship through review, and it goes wrong two ways: a role
added here does not exist there until the next build, and the ORDER the app
picks in is its own invention.

WHY THE OBVIOUS FIX WAS WRONG, which is the whole reason this file has a
precedence class in it. `capability_of` reported two fields that looked like the
answer and neither was:

  * `primary_role` is `held[0]` in `ROLE_SPECS` order, the LEAST of what
    somebody holds. A foreman who is also a field worker — which every foreman
    enrolled through `create_mobile_user` is — badges "Field Worker". It is
    still reported, because it predates all of this by ninety releases, and it
    is asserted below as the negative control it is.
  * `senior_role` was `held[-1]`, and `ROLE_SPECS` ends with `Family Member` and
    `Advisor` because that is the order they were written in, not a ranking. It
    was DELETED in v0.109.0 — nothing server-side or in the iOS client ever read
    it, and a field whose only test was a warning not to use it should not be a
    field. The two cases it got wrong are still pinned below, on
    `role_indicator`, which is what a caller reaching for it would reach for now.

The surviving control matters because a badge that is right for the
single-role accounts in a fixture and wrong for the people who
actually hold two is exactly the bug that ships.
"""

from erpnext_mcp import roles
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.api import shape

from .fixtures import SeededTestCase
from .harness import STORE, set_roles
from .test_api_mobile import WORKER, WORKER_EMPLOYEE, MobileAPITestCase

MANAGER = "meg@example.test"


def a_user(login: str, *held: str) -> str:
	"""One User with roles on its own `roles` child table, where Frappe keeps them."""
	STORE.seed(
		"User",
		[{"name": login, "enabled": 1, "full_name": login, "roles": [{"role": name} for name in held]}],
	)
	set_roles(login, list(held))
	return login


class TheBadgePicksOneRole(SeededTestCase):
	def badge(self, *held: str) -> dict:
		return roles.role_indicator(a_user("person@example.test", *held))

	def test_one_role_badges_as_itself(self):
		self.assertEqual(self.badge("Field Worker")["key"], "field_worker")
		self.assertEqual(self.badge("Foreman")["label"], "Foreman")

	def test_a_foreman_who_is_also_a_field_worker_badges_as_a_foreman(self):
		"""THE CASE `primary_role` GETS WRONG, and it is not an edge case: every
		account `create_mobile_user` enrols as a Foreman also carries Field
		Worker, so this is what the majority of dispatchers look like."""
		self.assertEqual(self.badge("Field Worker", "Foreman")["key"], "foreman")

	def test_a_farm_manager_who_also_advises_badges_as_a_farm_manager(self):
		"""THE CASE THE DELETED `senior_role` GOT WRONG. `ROLE_SPECS` ends with Advisor
		because that is when it was written, and `roles.py` calls Advisor the
		narrowest role in the app."""
		self.assertEqual(self.badge("Farm Manager", "Advisor")["key"], "farm_manager")

	def test_a_foreman_who_also_leads_a_crew_badges_as_a_foreman(self):
		"""THE SECOND CASE IT GOT WRONG, and it is not about the
		holding roles at all. `Crew Leader` was added after `Foreman` and sits
		after it in `ROLE_SPECS`, so it reads as the more senior of the two —
		while `roles.py` describes it as the board-less half of a Foreman that
		cannot raise, assign or cancel work."""
		self.assertEqual(self.badge("Foreman", "Crew Leader")["key"], "foreman")

	def test_the_two_existing_answers_really_are_wrong_here(self):
		"""The negative control for the three claims above. Without it they all
		pass on any implementation that happens to agree today.

		BOTH `ROLE_SPECS` INVERSIONS ARE PINNED, not just the one that prompted
		this. v0.106.0's comment on these fields called the ordering "ascending
		capability", which is what made it a trap rather than a wart: it read as
		a deliberate invariant, so the next person builds on it — and the next
		person did, which is how `senior_role` came to exist at all. That field
		is gone; `primary_role` stays and is asserted here, because it predates
		the false claim and removing it would be a wider change than the defect
		justified. `role_indicator` is checked beside it every time, so the wrong
		answer and the right one are pinned against each other rather than the
		wrong one merely being described.
		"""
		login = a_user("both@example.test", "Field Worker", "Foreman", "Advisor")
		capability = roles.capability_of(login)
		self.assertEqual(capability["primary_role"], "Field Worker")
		self.assertEqual(roles.role_indicator(login)["key"], "foreman")

		crew = a_user("crew@example.test", "Foreman", "Crew Leader")
		self.assertEqual(roles.role_indicator(crew)["key"], "foreman")

	def test_the_spec_order_really_is_the_order_those_claims_rest_on(self):
		"""Guards the two tests above against `ROLE_SPECS` being reordered under
		them — which would leave them asserting nothing while staying green."""
		order = list(roles.ROLE_NAMES)
		self.assertLess(order.index("Farm Manager"), order.index("Advisor"))
		self.assertLess(order.index("Foreman"), order.index("Crew Leader"))

	def test_a_system_manager_outranks_every_farm_role(self):
		"""A badge saying "Field Worker" over an account that can do anything is
		the badge lying."""
		badge = self.badge("Field Worker", "System Manager")
		self.assertEqual(badge["key"], "administrator")
		self.assertTrue(badge["is_administrator"])

	def test_a_family_member_who_holds_nothing_else_badges_as_one(self):
		"""The reason the non-operational roles are in the table at all, rather
		than left to fall through to "No Role"."""
		self.assertEqual(self.badge("Family Member")["key"], "family_member")

	def test_a_login_with_no_role_of_ours_says_so(self):
		badge = self.badge("Sales User")
		self.assertEqual(badge["key"], "none")
		self.assertTrue(badge["has_login"])
		self.assertFalse(badge["is_administrator"])

	def test_no_login_at_all_is_a_different_answer(self):
		""" "Nobody has given this person an account" and "this person's account
		holds no role" send a foreman to two different places."""
		badge = roles.role_indicator("")
		self.assertEqual(badge["key"], "none")
		self.assertFalse(badge["has_login"])

	def test_it_never_raises(self):
		"""It is folded into the call that doubles as credential validation, so a
		throw here reads on the handset as "this token is dead, sign out"."""
		for value in (None, "", "  ", "nobody@example.test", 0):
			with self.subTest(value=value):
				self.assertTrue(roles.role_indicator(value)["key"])


class TheBadgeCarriesWhatAViewNeeds(SeededTestCase):
	def test_it_says_can_dispatch_and_agrees_with_the_gate(self):
		"""Computed off `DISPATCH_ROLES`, the same frozenset
		`guard.require_dispatch_role` refuses on — not a second list."""
		for role in roles.ROLE_NAMES:
			with self.subTest(role=role):
				badge = roles.role_indicator(a_user("x@example.test", role))
				self.assertEqual(badge["can_dispatch"], role in roles.DISPATCH_ROLES)

	def test_it_agrees_with_capability_of(self):
		login = a_user("y@example.test", "Foreman")
		self.assertEqual(
			roles.role_indicator(login)["can_dispatch"], roles.capability_of(login)["can_dispatch"]
		)

	def test_every_badge_has_a_short_form_for_a_narrow_screen(self):
		for indicator in (*roles.ROLE_INDICATORS, roles.NO_ROLE_INDICATOR):
			with self.subTest(badge=indicator.key):
				self.assertTrue(indicator.short_label)
				self.assertLessEqual(len(indicator.short_label), 4)
				self.assertGreater(len(indicator.description), 30)


class TheTableIsWellFormed(SeededTestCase):
	def test_every_role_this_app_creates_has_a_badge(self):
		"""A role with no badge is a person the app cannot describe. The check
		that stops the eighth role shipping with the phone drawing nothing."""
		missing = [name for name in roles.ROLE_NAMES if name not in roles.INDICATOR_BY_ROLE]
		self.assertEqual(missing, [], f"roles with no badge: {missing}")

	def test_every_badge_names_a_real_role(self):
		for indicator in roles.ROLE_INDICATORS:
			with self.subTest(badge=indicator.key):
				self.assertTrue(
					indicator.role in roles.ROLE_NAMES or indicator.role == "System Manager",
					f"{indicator.role} is not a role this app knows",
				)

	def test_the_keys_and_the_precedences_are_unique(self):
		keys = [item.key for item in roles.ROLE_INDICATORS]
		ranks = [item.precedence for item in roles.ROLE_INDICATORS]
		self.assertEqual(len(keys), len(set(keys)))
		self.assertEqual(len(ranks), len(set(ranks)))

	def test_no_role_sorts_last(self):
		self.assertGreater(
			roles.NO_ROLE_INDICATOR.precedence, max(item.precedence for item in roles.ROLE_INDICATORS)
		)

	def test_the_operational_ladder_outranks_the_holding_roles(self):
		"""Stated as an assertion because it is a judgement, not an accident:
		somebody holding both is holding a phone in an orchard."""
		operational = max(
			roles.INDICATOR_BY_ROLE[name].precedence
			for name in ("Farm Manager", "Compliance Officer", "Foreman", "Crew Leader", "Field Worker")
		)
		for name in ("Family Member", "Advisor"):
			with self.subTest(role=name):
				self.assertGreater(roles.INDICATOR_BY_ROLE[name].precedence, operational)


class ThePhoneReceivesIt(MobileAPITestCase):
	"""End to end, over the transport the app actually speaks."""

	def test_the_user_context_carries_the_badge(self):
		self.be()
		badge = mobile_api.get_current_user_context()["role_indicator"]
		self.assertEqual(badge["key"], "field_worker")
		self.assertEqual(badge["label"], "Field Worker")
		self.assertFalse(badge["can_dispatch"])

	def test_the_badge_moves_when_the_role_does(self):
		"""The negative control for the test above: a fixture with one role would
		pass on an implementation that returned a constant."""
		STORE.seed("User", [{"name": WORKER, "enabled": 1, "roles": [{"role": "Farm Manager"}]}])
		set_roles(WORKER, ["Farm Manager"])
		self.be()
		badge = mobile_api.get_current_user_context()["role_indicator"]
		self.assertEqual(badge["key"], "farm_manager")
		self.assertTrue(badge["can_dispatch"])

	def test_the_raw_role_array_is_still_there(self):
		"""Additive. A build of the app that has not been updated yet keeps
		working off `roles` exactly as it did."""
		self.be()
		context = mobile_api.get_current_user_context()
		self.assertIn("roles", context)
		self.assertIn("Field Worker", context["roles"])

	def test_the_badge_carries_no_credential(self):
		self.be()
		badge = mobile_api.get_current_user_context()["role_indicator"]
		for key in ("api_key", "api_secret", "token", "password", "user_id"):
			self.assertNotIn(key, badge)

	def test_a_roster_row_carries_the_same_badge_as_the_person_it_names(self):
		"""`search_employees` is the roster half of every "who should hold this"
		screen. A picker that badged somebody one way and their own account
		screen another would be two answers to one question."""
		self.be()
		row = mobile_api._capability(WORKER)
		self.assertEqual(row["role_indicator"], mobile_api.get_current_user_context()["role_indicator"])

	def test_a_worker_with_no_login_is_reported_as_such(self):
		"""Most of a picking crew. `_capability` is handed `Employee.user_id`,
		which is empty for anybody who has never been enrolled — and `""` must
		not read as "we could not work it out"."""
		badge = mobile_api._capability("")["role_indicator"]
		self.assertEqual(badge["key"], "none")
		self.assertFalse(badge["has_login"])

	def test_the_shaper_reads_the_context_it_is_given(self):
		"""`shape.user_context` is what every caller of the mobile surface goes
		through, and it resolves the badge off the identity in the payload rather
		than off whoever is logged in."""
		a_user(MANAGER, "Farm Manager")
		shaped = shape.user_context({"user": MANAGER}, MANAGER)
		self.assertEqual(shaped["role_indicator"]["key"], "farm_manager")

	def test_the_employee_fixture_is_still_the_one_it_names(self):
		"""Guards the two tests above against a fixture rename quietly turning
		them into assertions about nobody."""
		self.be()
		self.assertEqual(mobile_api.get_current_user_context()["employee"], WORKER_EMPLOYEE)
