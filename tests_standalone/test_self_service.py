# SPDX-License-Identifier: MIT
"""Employee self-service on the mobile surface — the caller's own records.

Five methods that answer a question about the PERSON HOLDING THE PHONE: what
they elected on their W-4, what they were paid and the statement for it, what
they have been trained in, and where their I-9 stands. Every one of those
registers already had a route; every one of those routes was HR-gated or took an
`employee` docname, so the worker the record is about was the one person who
could not read it from a handset.

SEVEN CLAIMS.

1. `TheSelfServiceReadsAreAddressed` — none of the five declares an `employee`
   or a `company` argument, so `routes.bind` has nothing to deliver and there is
   no body that can point one of them at a colleague. This is the claim the
   whole set turns on and it is asserted on the SIGNATURE, which is what the
   transport actually filters against, rather than on behaviour alone.

2. `TheSelfServiceReadsNeedNoHRRole` — a plain Field Worker gets an answer. The
   HR-gated routes over the same registers are unchanged and still refuse them.

3. `MyW4` — the caller's active elections, the sensitive columns absent, and
   "you have not filed one" answered as a state rather than as an error.

4. `MyPayStubs` — one person's slips off runs that carry a whole crew's, drafts
   excluded, and a colleague's figures nowhere in the payload.

5. `MyPayStubPdf` — an attached statement comes back rather than being redrawn,
   an unrendered one is drawn, and a run the caller is not on reads exactly like
   a run that does not exist.

6. `MyTrainings` — the caller's own card list with the status computed against
   today, and nobody else's records in it.

7. `MyI9` — status, dates and reverification, WITHOUT the document numbers or
   the scanned pages. The projection is asserted by what is missing, because a
   read like this leaks by growing a field rather than by returning the wrong
   record.

WHY A COLLEAGUE AT THE SAME COMPANY IS IN EVERY FIXTURE. An outsider at another
entity proves nothing here: `guard.require_scope` and `require_scoped_doc`
already stop them, and they would stop them even if these methods took an
`employee` argument. The interesting case — the one an `employee` argument would
actually have opened — is TWO ENROLLED WORKERS AT THE SAME COMPANY, because
company scope is what they share. Cara exists in this file for that reason.
"""

import unittest

import frappe

from erpnext_mcp import pay_stub_pdf
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.farmops_api import routes
from erpnext_mcp.tools import payroll as payroll_tools

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import STORE
from .test_api_mobile import ON as MOBILE_ON
from .test_api_mobile import (
	OUTSIDER,
	WORKER,
	WORKER_EMPLOYEE,
	MobileAPITestCase,
)
from .test_payroll_register import entry, slip

#: A second enrolled worker AT THE CALLER'S OWN COMPANY. See the module header:
#: the outsider at another entity is stopped by scoping that predates this
#: release, and proves nothing about it.
COLLEAGUE = "cara@example.test"
COLLEAGUE_EMPLOYEE = "EMP-CARA"

NEEDS_REPORTLAB = unittest.skipUnless(
	pay_stub_pdf.available(),
	"reportlab is not installed on this bench — the stub tool goes quietly unavailable, which "
	"`test_pay_stub_pdf.StubAvailability` asserts without it",
)

#: The five methods this file is about, in the order the routes table lists them.
SELF_SERVICE = (
	"get_my_w4",
	"list_my_pay_stubs",
	"get_my_pay_stub_pdf",
	"list_my_trainings",
	"get_my_i9",
)


class SelfServiceTestCase(MobileAPITestCase):
	"""Ana, Cara beside her at the same company, and Ben at another."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **MOBILE_ON)
		install_hrms()
		STORE.seed(
			"Employee",
			[
				{
					"name": COLLEAGUE_EMPLOYEE,
					"employee_name": "Cara Diaz",
					"user_id": COLLEAGUE,
					"company": MAIN,
					"status": "Active",
				}
			],
		)
		# Both colleagues are ENROLLED. An unenrolled account is refused by the
		# credential gate long before any of this runs, so a fixture that left
		# Cara unenrolled would prove the enrolment gate works and nothing about
		# whether Ana can reach Cara's wages.
		self.enrol(email=COLLEAGUE, name="Cara Diaz", entities=[MAIN])
		self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[OTHER])

	# ── the registers these five read ───────────────────────────────────────
	def a_w4(self, employee=WORKER_EMPLOYEE, name="W4-2026-ANA", **overrides):
		payload = {
			"doctype": "W-4 Form",
			"name": name,
			"employee": employee,
			"employee_name": "Ana Ramos",
			"company": MAIN,
			"tax_year": 2026,
			"status": "Active",
			"effective_date": "2026-01-15",
			"filing_status": "Single or Married Filing Separately",
			"multiple_jobs": 0,
			"dependents_under_17_count": 2,
			"dependents_under_17_amount": 4000,
			"other_dependents_count": 0,
			"other_dependents_amount": 0,
			"total_dependents_credit": 4000,
			"other_income": 0,
			"deductions": 0,
			"extra_withholding_per_period": 25,
			"signed_at": "2026-01-15 09:14:00",
			"signed_ip": "100.64.0.7",
			"generated_pdf": "/private/files/W4-2026-ANA.pdf",
		}
		payload.update(overrides)
		STORE.seed("W-4 Form", [payload])
		return name

	def an_i9(self, employee=WORKER_EMPLOYEE, name="I9-2026-ANA", **overrides):
		payload = {
			"doctype": "I-9 Form",
			"name": name,
			"employee": employee,
			"employee_name": "Ana Ramos",
			"company": MAIN,
			"status": "Complete",
			"hire_date": "2026-03-02",
			"legal_first_name": "Ana",
			"legal_last_name": "Ramos",
			"date_of_birth": "1990-04-11",
			"ssn_last_four": "4321",
			"address_street": "12 Orchard Lane",
			"address_city": "Hood River",
			"address_state": "OR",
			"address_zip": "97031",
			"citizenship_status": "Alien Authorized to Work",
			"alien_registration_number": "A123456789",
			"alien_work_authorization_expiry": "2027-03-01",
			"section_1_signed_at": "2026-03-02 08:02:00",
			"section_1_signed_ip": "100.64.0.7",
			"section_2_signed_at": "2026-03-04 16:40:00",
			"section_2_signed_ip": "100.64.0.9",
			"verification_date": "2026-03-04",
			"list_a_doc_title": "Employment Authorization Document",
			"list_a_doc_authority": "USCIS",
			"list_a_doc_number": "SRC1234567890",
			"list_a_doc_expiry": "2027-03-01",
			"verifier_name": "Dana Foreman",
			"verifier_title": "Crew Lead",
			"retention_until": "2029-03-04",
			"destruction_eligible_date": "2029-03-05",
			"generated_pdf": "/private/files/I9-2026-ANA.pdf",
			"signed_pdf": "/private/files/I9-2026-ANA-signed.pdf",
			"document_path": "/private/files/ana-ead-scan.pdf",
		}
		payload.update(overrides)
		STORE.seed("I-9 Form", [payload])
		return name

	def a_reverification(self, i9="I9-2026-ANA", **overrides):
		"""One Supplement B row, nested on its parent where a child row lives.

		Seeded onto the parent rather than beside it: `CHILD_TABLE_SOURCES` is
		what makes `get_all("I-9 Reverification", ...)` find it, and a standalone
		row would be invisible to the document and to the read alike.
		"""
		row = {
			"name": f"I9REV-{i9}-1",
			"doctype": "I-9 Reverification",
			"parent": i9,
			"parenttype": "I-9 Form",
			"parentfield": "reverifications",
			"idx": 1,
			"reverification_date": "2027-02-20",
			"reason": "Work Authorization Expired",
			"document_title": "Employment Authorization Document",
			"document_number": "SRC9999999999",
			"document_expiry": "2029-02-19",
			"verifier_name": "Dana Foreman",
			"signed_ip": "100.64.0.9",
		}
		row.update(overrides)
		form = STORE.get_raw("I-9 Form", i9)
		form.setdefault("reverifications", []).append(row)
		return row["name"]

	def a_training(self, name, employee=WORKER_EMPLOYEE, **overrides):
		payload = {
			"doctype": "Employee Training Record",
			"name": name,
			"employee": employee,
			"employee_name": "Ana Ramos",
			"company": MAIN,
			"training_type": "WPS Handler Training",
			"training_source": "External",
			"provider": "OSU Extension",
			"completed_date": "2026-04-01",
			"expires_date": "2027-04-01",
			"regimes": "WPS",
			"person_performed_signature": "sig-token",
			"status": "Active",
		}
		payload.update(overrides)
		STORE.seed("Employee Training Record", [payload])
		return name

	def two_runs(self):
		"""Two submitted runs. Ana is on both; Cara is on the first beside her."""
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-0001",
					"2026-06-01",
					"2026-06-14",
					[
						slip(WORKER_EMPLOYEE, name="Ana Ramos"),
						slip(COLLEAGUE_EMPLOYEE, name="Cara Diaz", gross=2000.0, federal=200.0, state=120.0),
					],
				),
				entry(
					"PAY-2026-0002",
					"2026-06-15",
					"2026-06-28",
					[slip(WORKER_EMPLOYEE, name="Ana Ramos", gross=1500.0)],
				),
			],
		)

	def a_stub_file(self, run="PAY-2026-0001", employee=WORKER_EMPLOYEE, period_end="2026-06-14"):
		"""A pay stub already attached to a run, as `render_pay_stub` leaves one."""
		file_name = pay_stub_pdf.file_name_for(
			{"payroll_entry": run, "employee": employee, "pay_period_end": period_end}
		)
		STORE.seed(
			"File",
			[
				{
					"name": f"FILE-{run}-{employee}",
					"file_name": file_name,
					"file_url": f"/private/files/{file_name}",
					"attached_to_doctype": payroll_tools.PAYROLL_ENTRY,
					"attached_to_name": run,
					"is_private": 1,
				}
			],
		)
		return file_name


# ── 1. the subject is the login, and there is no argument that widens it ────
class TheSelfServiceReadsAreAddressed(SelfServiceTestCase):
	"""No `employee`, no `company`, on any of the five.

	THIS IS THE CLAIM THE SET TURNS ON. `routes.bind` keeps the body keys that
	match the wrapper's signature and drops the rest, so an argument that is not
	declared is not merely refused — it is undeliverable. Asserting the signature
	is asserting the thing the transport actually enforces.
	"""

	def handlers(self):
		return [getattr(mobile_api, name) for name in SELF_SERVICE]

	def test_none_of_them_accepts_an_employee(self):
		for handler in self.handlers():
			self.assertNotIn("employee", routes.accepted_arguments(handler), handler.farm_ops_method)

	def test_none_of_them_accepts_a_company(self):
		"""Naming an entity could only narrow the caller's own scope."""
		for handler in self.handlers():
			self.assertNotIn("company", routes.accepted_arguments(handler), handler.farm_ops_method)

	def test_the_stub_route_cannot_be_asked_to_overwrite(self):
		"""Replacing a statement somebody was handed is a correction, not a fetch."""
		accepted = routes.accepted_arguments(mobile_api.get_my_pay_stub_pdf)
		self.assertEqual(accepted, {"payroll_entry"})

	def test_the_five_are_on_the_route_table_with_the_right_write_flags(self):
		paths = {route.path: route.mutating for route in routes.ROUTES}
		self.assertIs(paths.get("/mobile/get_my_w4"), False)
		self.assertIs(paths.get("/mobile/list_my_pay_stubs"), False)
		self.assertIs(paths.get("/mobile/list_my_trainings"), False)
		self.assertIs(paths.get("/mobile/get_my_i9"), False)
		# The one that can write. A period whose stub was never drawn is drawn
		# here, which attaches a File — the flag describes what the call can do
		# to the site rather than who it is for.
		self.assertIs(paths.get("/mobile/get_my_pay_stub_pdf"), True)

	def test_a_body_naming_a_colleague_is_dropped_at_the_transport(self):
		"""What `bind` does with the key an attacker would actually send."""
		for name in SELF_SERVICE:
			route = routes.BY_PATH[f"/mobile/{name}"]
			bound = routes.bind(route, {"employee": COLLEAGUE_EMPLOYEE, "company": OTHER})
			self.assertEqual(bound, {}, name)


# ── 2. no HR role, and the HR-gated routes are unchanged ────────────────────
class TheSelfServiceReadsNeedNoHRRole(SelfServiceTestCase):
	"""A picker reads their own record. The crew registers still refuse them."""

	def test_a_field_worker_reads_their_own_w4(self):
		self.a_w4()
		self.be(WORKER)
		self.assertTrue(mobile_api.get_my_w4()["on_file"])

	def test_a_field_worker_reads_their_own_i9(self):
		self.an_i9()
		self.be(WORKER)
		self.assertTrue(mobile_api.get_my_i9()["on_file"])

	def test_a_field_worker_reads_their_own_trainings_and_stubs(self):
		self.a_training("TRN-ANA-1")
		self.two_runs()
		self.be(WORKER)
		self.assertEqual(mobile_api.list_my_trainings()["count"], 1)
		self.assertEqual(mobile_api.list_my_pay_stubs()["count"], 2)

	def test_the_same_worker_still_cannot_read_the_payroll_register(self):
		"""The crew read is unchanged. One person versus everybody is the line."""
		self.be(WORKER)
		with self.assertRaises(Exception) as caught:
			mobile_api.get_payroll_register(company=MAIN)
		self.assertNotIn("was not found", str(caught.exception))

	def test_the_same_worker_still_cannot_read_the_training_matrix(self):
		self.be(WORKER)
		with self.assertRaises(Exception):
			mobile_api.get_training_compliance_report(company=MAIN)

	def test_an_unenrolled_login_is_still_refused_by_the_credential_gate(self):
		"""The seven gates run first. Self-service is not a way past them."""
		self.a_w4()
		self.be("Guest")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.get_my_w4()


# ── 3. the W-4 ──────────────────────────────────────────────────────────────
class MyW4(SelfServiceTestCase):
	def test_the_elections_come_back(self):
		self.a_w4()
		self.be(WORKER)
		data = mobile_api.get_my_w4()
		self.assertEqual(data["employee"], WORKER_EMPLOYEE)
		self.assertTrue(data["on_file"])
		self.assertEqual(data["w4"]["filing_status"], "Single or Married Filing Separately")
		self.assertEqual(str(data["w4"]["extra_withholding_per_period"]), "25")
		self.assertEqual(str(data["w4"]["total_dependents_credit"]), "4000")
		self.assertEqual(str(data["w4"]["tax_year"]), "2026")

	def test_no_form_on_file_is_a_state_and_not_an_error(self):
		"""A phone cannot tell a validation error from a broken server."""
		self.be(WORKER)
		data = mobile_api.get_my_w4()
		self.assertFalse(data["on_file"])
		self.assertIsNone(data["w4"])
		self.assertIn("submit_w4", data["note"])

	def test_a_colleagues_form_is_never_the_answer(self):
		"""Cara is enrolled at Ana's own company, which is the shared scope."""
		self.a_w4(employee=COLLEAGUE_EMPLOYEE, name="W4-2026-CARA", filing_status="Head of Household")
		self.be(WORKER)
		self.assertFalse(mobile_api.get_my_w4()["on_file"])

		self.be(COLLEAGUE)
		mine = mobile_api.get_my_w4()
		self.assertEqual(mine["employee"], COLLEAGUE_EMPLOYEE)
		self.assertEqual(mine["w4"]["filing_status"], "Head of Household")

	def test_the_signing_ip_and_the_pdf_url_do_not_come_back(self):
		"""Evidence about a signature is not an election."""
		self.a_w4()
		self.be(WORKER)
		form = mobile_api.get_my_w4()["w4"]
		for absent in ("signed_ip", "generated_pdf", "generated_pdf_on", "employer_signer_name"):
			self.assertNotIn(absent, form)

	def test_a_superseded_form_is_not_the_one_reported(self):
		"""What a worker means by "my W-4" is what payroll withholds against."""
		self.a_w4(
			name="W4-2025-ANA",
			tax_year=2025,
			status="Superseded",
			filing_status="Married Filing Jointly",
		)
		self.be(WORKER)
		self.assertFalse(mobile_api.get_my_w4()["on_file"])

		self.a_w4()
		self.assertEqual(
			mobile_api.get_my_w4()["w4"]["filing_status"], "Single or Married Filing Separately"
		)


# ── 4. the pay stubs ────────────────────────────────────────────────────────
class MyPayStubs(SelfServiceTestCase):
	def test_only_the_callers_own_slip_comes_off_a_shared_run(self):
		"""PAY-2026-0001 carries Cara as well. Ana gets one row off it."""
		self.two_runs()
		self.be(WORKER)
		data = mobile_api.list_my_pay_stubs()
		self.assertEqual(data["employee"], WORKER_EMPLOYEE)
		self.assertEqual(data["count"], 2)
		self.assertEqual(
			{row["payroll_entry"] for row in data["pay_stubs"]}, {"PAY-2026-0001", "PAY-2026-0002"}
		)
		for row in data["pay_stubs"]:
			self.assertEqual(row["employee"], WORKER_EMPLOYEE)

	def test_a_colleagues_figures_are_nowhere_in_the_payload(self):
		"""Cara's gross is 2000 and it must not appear anywhere Ana can read."""
		self.two_runs()
		self.be(WORKER)
		data = mobile_api.list_my_pay_stubs()
		self.assertNotIn("Cara Diaz", str(data))
		self.assertNotIn(COLLEAGUE_EMPLOYEE, str(data))
		for row in data["pay_stubs"]:
			self.assertNotEqual(row["gross_pay"], 2000.0)

	def test_the_newest_period_leads(self):
		self.two_runs()
		self.be(WORKER)
		periods = [row["pay_period_end"] for row in mobile_api.list_my_pay_stubs()["pay_stubs"]]
		self.assertEqual(periods, sorted(periods, reverse=True))

	def test_a_draft_run_is_not_a_pay_stub(self):
		"""A figure somebody is still working on is not a statement."""
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-0003",
					"2026-06-29",
					"2026-07-12",
					[slip(WORKER_EMPLOYEE, name="Ana Ramos")],
					status="Draft",
				)
			],
		)
		self.be(WORKER)
		data = mobile_api.list_my_pay_stubs()
		self.assertEqual(data["count"], 0)
		self.assertEqual(data["statuses_counted"], list(payroll_tools.REGISTER_STATUSES))

	def test_a_run_at_another_entity_is_not_scanned(self):
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-OTHER",
					"2026-06-01",
					"2026-06-14",
					[slip(WORKER_EMPLOYEE, name="Ana Ramos")],
					company=OTHER,
				)
			],
		)
		self.be(WORKER)
		self.assertEqual(mobile_api.list_my_pay_stubs()["count"], 0)

	def test_the_year_filter_moves_the_window(self):
		self.two_runs()
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2025-0009",
					"2025-06-01",
					"2025-06-14",
					[slip(WORKER_EMPLOYEE, name="Ana Ramos")],
				)
			],
		)
		self.be(WORKER)
		self.assertEqual(mobile_api.list_my_pay_stubs()["count"], 3)
		last_year = mobile_api.list_my_pay_stubs(year=2025)
		self.assertEqual(last_year["count"], 1)
		self.assertEqual(last_year["year"], "2025")
		self.assertEqual(last_year["pay_stubs"][0]["payroll_entry"], "PAY-2025-0009")

	def test_a_year_that_is_not_a_year_is_refused(self):
		self.be(WORKER)
		with self.assertRaises(Exception) as caught:
			mobile_api.list_my_pay_stubs(year="last")
		self.assertIn("four-digit", str(caught.exception))

	def test_the_employers_own_contributions_are_not_on_a_workers_row(self):
		"""The same decision `render_pay_stub` makes about its own argument."""
		self.two_runs()
		self.be(WORKER)
		row = mobile_api.list_my_pay_stubs()["pay_stubs"][0]
		for absent in (
			"social_security_employer",
			"medicare_employer",
			"futa",
			"state_unemployment",
			"total_employer_taxes",
		):
			self.assertNotIn(absent, row)

	def test_an_attached_stub_is_reported_with_its_url(self):
		self.two_runs()
		file_name = self.a_stub_file()
		self.be(WORKER)
		rows = {row["payroll_entry"]: row for row in mobile_api.list_my_pay_stubs()["pay_stubs"]}
		self.assertTrue(rows["PAY-2026-0001"]["stub_attached"])
		self.assertEqual(rows["PAY-2026-0001"]["file_url"], f"/private/files/{file_name}")
		self.assertFalse(rows["PAY-2026-0002"]["stub_attached"])
		self.assertIsNone(rows["PAY-2026-0002"]["file_url"])

	def test_a_colleagues_attached_stub_is_not_mistaken_for_the_callers(self):
		"""Cara's stub is attached to a run Ana is also on.

		The lookup is keyed on the file NAME `pay_stub_pdf.file_name_for` gives
		the CALLER's stub, not merely on the run: a query that asked only which
		files hang off these runs would hand Ana whatever the office rendered
		last, which on a shared run is a colleague's statement.
		"""
		self.two_runs()
		self.a_stub_file(employee=COLLEAGUE_EMPLOYEE)
		self.be(WORKER)
		for row in mobile_api.list_my_pay_stubs()["pay_stubs"]:
			self.assertFalse(row["stub_attached"])
			self.assertIsNone(row["file_url"])

	def test_a_file_matching_one_runs_name_on_another_run_is_not_taken(self):
		"""The pair is re-checked after the fetch, and this is why.

		`_attached_stub_urls` filters on two `in` lists, which is a PRODUCT and
		not a set of pairs — every combination of the runs asked about and the
		names asked about comes back. This seeds the combination that is wrong:
		the file Ana's FIRST period would have produced, hanging off her second.
		Taking the query's word for it would report the June 1–14 statement as
		the June 15–28 one.
		"""
		self.two_runs()
		crossed = pay_stub_pdf.file_name_for(
			{
				"payroll_entry": "PAY-2026-0001",
				"employee": WORKER_EMPLOYEE,
				"pay_period_end": "2026-06-14",
			}
		)
		STORE.seed(
			"File",
			[
				{
					"name": "FILE-CROSSED",
					"file_name": crossed,
					"file_url": f"/private/files/{crossed}",
					"attached_to_doctype": payroll_tools.PAYROLL_ENTRY,
					"attached_to_name": "PAY-2026-0002",
					"is_private": 1,
				}
			],
		)
		self.be(WORKER)
		rows = {row["payroll_entry"]: row for row in mobile_api.list_my_pay_stubs()["pay_stubs"]}
		self.assertFalse(rows["PAY-2026-0002"]["stub_attached"])
		self.assertIsNone(rows["PAY-2026-0002"]["file_url"])


# ── 5. the statement itself ─────────────────────────────────────────────────
class MyPayStubPdf(SelfServiceTestCase):
	def test_an_already_attached_statement_comes_back_rather_than_being_redrawn(self):
		"""`render_pay_stub` REFUSES a second render, so calling it blindly here
		would fail on every period that had been through the office."""
		self.two_runs()
		file_name = self.a_stub_file()
		self.be(WORKER)
		data = mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-0001")
		self.assertFalse(data["rendered"])
		self.assertEqual(data["file_name"], file_name)
		self.assertEqual(data["file_url"], f"/private/files/{file_name}")
		self.assertEqual(data["employee"], WORKER_EMPLOYEE)
		# 1000 gross less 100 federal, 60 state, 62 FICA and 14.50 Medicare.
		self.assertEqual(data["net_pay"], 763.5)

	@NEEDS_REPORTLAB
	def test_a_period_never_drawn_is_drawn_on_demand(self):
		self.two_runs()
		self.be(WORKER)
		data = mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-0002")
		self.assertTrue(data["rendered"])
		self.assertTrue(str(data["file_url"]).endswith(".pdf"))
		self.assertEqual(data["employee"], WORKER_EMPLOYEE)

	@NEEDS_REPORTLAB
	def test_the_second_call_returns_the_first_call_s_file(self):
		"""And does not refuse, which is what an unguarded delegation would do."""
		self.two_runs()
		self.be(WORKER)
		first = mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-0002")
		second = mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-0002")
		self.assertTrue(first["rendered"])
		self.assertFalse(second["rendered"])
		self.assertEqual(second["file_url"], first["file_url"])

	def test_a_run_the_caller_is_not_on_reads_as_not_found(self):
		"""Cara's run at Ana's own company. `require_scoped_doc` lets it through."""
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-CARA",
					"2026-06-01",
					"2026-06-14",
					[slip(COLLEAGUE_EMPLOYEE, name="Cara Diaz")],
				)
			],
		)
		self.be(WORKER)
		with self.assertRaises(Exception) as theirs:
			mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-CARA")
		with self.assertRaises(Exception) as absent:
			mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-NOPE")
		self.assertEqual(
			str(theirs.exception).replace("PAY-2026-CARA", "PAY-2026-NOPE"),
			str(absent.exception),
		)

	def test_the_refusal_does_not_name_who_is_on_the_run(self):
		"""The wrapper's own check is what keeps the tool's refusal out of reach.

		`payroll._stub_slip` answers "no slip for X" by LISTING THE RUN'S OWN
		EMPLOYEES — correct for a desk that has mistyped a name, and a crew's
		payroll roster handed to whoever guessed a docname if it ever reaches a
		handset. The wrapper refuses first, in words that name nobody.
		"""
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-CARA",
					"2026-06-01",
					"2026-06-14",
					[slip(COLLEAGUE_EMPLOYEE, name="Cara Diaz")],
				)
			],
		)
		self.be(WORKER)
		with self.assertRaises(Exception) as caught:
			mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-CARA")
		self.assertNotIn("Cara Diaz", str(caught.exception))
		self.assertNotIn(COLLEAGUE_EMPLOYEE, str(caught.exception))

	def test_nothing_is_attached_to_a_run_the_caller_is_not_on(self):
		"""Nothing is drawn or filed, whichever layer does the refusing.

		The wrapper refuses first and the tool would refuse behind it, so this
		does not discriminate between them — it holds the property both are
		there for. What separates the two layers is the WORDING, which
		`test_the_refusal_does_not_name_who_is_on_the_run` asserts.
		"""
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-CARA",
					"2026-06-01",
					"2026-06-14",
					[slip(COLLEAGUE_EMPLOYEE, name="Cara Diaz")],
				)
			],
		)
		self.be(WORKER)
		with self.assertRaises(Exception):
			mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-CARA")
		attached = [
			row
			for row in STORE.rows("File")
			if str(row.get("attached_to_name") or "") == "PAY-2026-CARA"
		]
		self.assertEqual(attached, [])

	def test_a_run_at_another_entity_is_refused_before_the_slip_is_looked_at(self):
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-OTHER",
					"2026-06-01",
					"2026-06-14",
					[slip(WORKER_EMPLOYEE, name="Ana Ramos")],
					company=OTHER,
				)
			],
		)
		self.be(WORKER)
		with self.assertRaises(Exception) as caught:
			mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-OTHER")
		self.assertIn("was not found", str(caught.exception))

	def test_a_draft_run_the_caller_is_on_is_refused_in_words(self):
		"""They ARE on it, so the refusal can say what is wrong with it."""
		STORE.seed(
			"Farm Payroll Entry",
			[
				entry(
					"PAY-2026-0003",
					"2026-06-29",
					"2026-07-12",
					[slip(WORKER_EMPLOYEE, name="Ana Ramos")],
					status="Draft",
				)
			],
		)
		self.be(WORKER)
		with self.assertRaises(Exception) as caught:
			mobile_api.get_my_pay_stub_pdf(payroll_entry="PAY-2026-0003")
		self.assertIn("Draft", str(caught.exception))
		self.assertIn("Nothing was changed", str(caught.exception))

	def test_a_missing_docname_is_refused(self):
		self.be(WORKER)
		with self.assertRaises(Exception) as caught:
			mobile_api.get_my_pay_stub_pdf()
		self.assertIn("payroll_entry", str(caught.exception))


# ── 6. the training card list ───────────────────────────────────────────────
class MyTrainings(SelfServiceTestCase):
	def test_the_callers_own_records_come_back(self):
		self.a_training("TRN-ANA-1")
		self.a_training("TRN-ANA-2", training_type="Food Safety Refresher", regimes="FSMA")
		self.be(WORKER)
		data = mobile_api.list_my_trainings()
		self.assertEqual(data["employee"], WORKER_EMPLOYEE)
		self.assertEqual(data["count"], 2)
		self.assertEqual({row["name"] for row in data["trainings"]}, {"TRN-ANA-1", "TRN-ANA-2"})

	def test_a_colleagues_record_is_not_in_it(self):
		self.a_training("TRN-CARA-1", employee=COLLEAGUE_EMPLOYEE, employee_name="Cara Diaz")
		self.be(WORKER)
		self.assertEqual(mobile_api.list_my_trainings()["count"], 0)

		self.be(COLLEAGUE)
		mine = mobile_api.list_my_trainings()
		self.assertEqual(mine["employee"], COLLEAGUE_EMPLOYEE)
		self.assertEqual(mine["count"], 1)

	def test_the_status_is_computed_against_today_and_not_read_off_the_column(self):
		"""A record saved in March carries March's answer."""
		self.a_training(
			"TRN-ANA-LAPSED",
			completed_date="2020-01-01",
			expires_date="2021-01-01",
			status="Active",
		)
		self.be(WORKER)
		data = mobile_api.list_my_trainings()
		row = data["trainings"][0]
		self.assertEqual(row["status_now"], "Expired")
		self.assertLess(row["days_until_expiry"], 0)
		self.assertEqual(data["expired"], ["TRN-ANA-LAPSED"])

	def test_a_one_time_training_says_so_rather_than_expiring(self):
		self.a_training("TRN-ANA-ONCE", expires_date=None)
		self.be(WORKER)
		row = mobile_api.list_my_trainings()["trainings"][0]
		self.assertTrue(row["one_time"])
		self.assertIsNone(row["expires_date"])
		self.assertIsNone(row["days_until_expiry"])

	def test_the_supervisor_review_columns_are_not_a_workers_answer(self):
		"""Whether the EMPLOYER completed its §112.161(b) review is the matrix."""
		self.a_training("TRN-ANA-1")
		self.be(WORKER)
		row = mobile_api.list_my_trainings()["trainings"][0]
		for absent in ("supervisor_reviewed", "supervisor_reviewed_by", "supervisor_signed", "notes"):
			self.assertNotIn(absent, row)

	def test_a_record_written_against_an_unreachable_entity_is_dropped(self):
		"""`guard.scoped` is the belt to the employee filter's brace."""
		self.a_training("TRN-ANA-ELSEWHERE", company=OTHER)
		self.be(WORKER)
		self.assertEqual(mobile_api.list_my_trainings()["count"], 0)


# ── 7. the I-9 ──────────────────────────────────────────────────────────────
class MyI9(SelfServiceTestCase):
	def test_the_status_and_the_dates_come_back(self):
		self.an_i9()
		self.be(WORKER)
		data = mobile_api.get_my_i9()
		self.assertEqual(data["employee"], WORKER_EMPLOYEE)
		self.assertTrue(data["on_file"])
		form = data["i9"]
		self.assertEqual(form["status"], "Complete")
		self.assertEqual(form["hire_date"], "2026-03-02")
		self.assertEqual(form["verification_date"], "2026-03-04")
		self.assertEqual(form["alien_work_authorization_expiry"], "2027-03-01")
		self.assertEqual(form["list_a_doc_title"], "Employment Authorization Document")
		self.assertEqual(form["list_a_doc_expiry"], "2027-03-01")

	def test_the_document_numbers_and_the_scanned_pages_do_not_come_back(self):
		"""What a stolen handset must not be carrying."""
		self.an_i9()
		self.be(WORKER)
		form = mobile_api.get_my_i9()["i9"]
		for absent in (
			"list_a_doc_number",
			"list_a_doc_authority",
			"alien_registration_number",
			"generated_pdf",
			"signed_pdf",
			"document_path",
			"ssn_last_four",
			"date_of_birth",
			"address_street",
			"section_1_signed_ip",
			"section_2_signed_ip",
			"verifier_name",
		):
			self.assertNotIn(absent, form)
		self.assertNotIn("SRC1234567890", str(form))
		self.assertNotIn("A123456789", str(form))

	def test_no_form_on_file_is_a_state_and_not_an_error(self):
		self.be(WORKER)
		data = mobile_api.get_my_i9()
		self.assertFalse(data["on_file"])
		self.assertIsNone(data["i9"])
		self.assertIn("create_i9_form", data["note"])

	def test_a_colleagues_form_is_never_the_answer(self):
		self.an_i9(employee=COLLEAGUE_EMPLOYEE, name="I9-2026-CARA", status="Section 1 Complete")
		self.be(WORKER)
		self.assertFalse(mobile_api.get_my_i9()["on_file"])

		self.be(COLLEAGUE)
		mine = mobile_api.get_my_i9()
		self.assertEqual(mine["employee"], COLLEAGUE_EMPLOYEE)
		self.assertEqual(mine["i9"]["status"], "Section 1 Complete")

	def test_reverification_needed_is_derived_from_the_status_the_workflow_moves(self):
		self.an_i9(status="Reverification Needed")
		self.be(WORKER)
		self.assertTrue(mobile_api.get_my_i9()["i9"]["reverification_needed"])

	def test_a_complete_form_is_not_flagged(self):
		self.an_i9()
		self.be(WORKER)
		self.assertFalse(mobile_api.get_my_i9()["i9"]["reverification_needed"])

	def test_the_reverification_history_is_projected_too(self):
		self.an_i9()
		self.a_reverification()
		self.be(WORKER)
		form = mobile_api.get_my_i9()["i9"]
		self.assertEqual(form["reverification_count"], 1)
		row = form["reverifications"][0]
		self.assertEqual(row["reason"], "Work Authorization Expired")
		self.assertEqual(row["document_expiry"], "2029-02-19")
		for absent in ("document_number", "verifier_name", "signed_ip"):
			self.assertNotIn(absent, row)
		self.assertNotIn("SRC9999999999", str(row))

	def test_reading_your_own_form_is_logged_like_any_other_read(self):
		"""The log's question is who looked, not whether they were entitled to."""
		self.an_i9()
		self.be(WORKER)
		mobile_api.get_my_i9()
		viewed = [
			row
			for row in STORE.rows("I-9 Audit Log")
			if str(row.get("action") or "") == "Viewed" and str(row.get("employee") or "") == WORKER_EMPLOYEE
		]
		self.assertTrue(viewed)


if __name__ == "__main__":
	unittest.main()
