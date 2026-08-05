# SPDX-License-Identifier: MIT
"""State Tax Engines (Oregon + Washington) — v0.29.0.

TEN CLAIMS.

1. `OregonCalcEngine` — Oregon withholding is correct: income tax from brackets,
   transit tax, paid leave, workers comp.
2. `WashingtonCalcEngine` — Washington withholding is correct: PFML, WA Cares,
   L&I. No income tax.
3. `CrossStatePeriod` — an employee with shifts in both OR and WA in the same
   pay period gets each shift's gross under its own state's rules.
4. `CombinedFederalState` — the combined engine runs federal + state together
   and grand totals are correct.
5. `OregonBrackets` — the seeded Oregon 2025 brackets produce the right tax at
   boundary values.
6. `SmallEmployer` — an Oregon small employer (under 25) pays no employer share
   of Paid Leave.
7. `EdgeCases` — zero gross, missing brackets, unsupported state.
8. `ReadTools` — the read-only MCP tools return the right data.
9. `MutatingTools` — create, update, and import tools work correctly.
10. `CombinedPreview` — the preview_total_payroll_taxes tool runs federal + state.
"""
from erpnext_mcp.state_withholding import (
    OR_ANNUAL_BRACKETS,
    PERIODS_PER_YEAR,
    calculate_all_payroll_taxes,
    calculate_oregon_withholding,
    calculate_state_withholding,
    calculate_washington_withholding,
    seed_or_brackets,
)
from erpnext_mcp.withholding import ANNUAL_BRACKETS, calculate_federal_withholding

from .fixtures import MAIN, MAIN_ABBR, V12TestCase, install_hrms
from .harness import STORE

STATE_TAX_TOOLS_ON = {
    f"allow_{name}": 1
    for name in (
        "get_state_tax_config",
        "list_state_tax_configs",
        "get_state_tax_table",
        "preview_state_withholding",
        "preview_total_payroll_taxes",
        "list_employees_by_work_state",
        "create_state_tax_config",
        "update_state_tax_config",
        "import_state_tax_table",
        # Federal tools needed for combined preview
        "get_w4",
        "list_w4_forms",
        "get_fica_config",
        "get_federal_tax_table",
        "preview_federal_withholding",
        "list_employees_missing_w4",
        "calculate_payroll_taxes",
        "submit_w4",
        "update_fica_config",
        "import_federal_tax_table",
    )
}


class StateTaxTestCase(V12TestCase):
    def setUp(self):
        super().setUp()
        self.configure(enabled=1, **STATE_TAX_TOOLS_ON)
        install_hrms()
        self._seed_fica()
        self._seed_federal_brackets()
        self._seed_employees()
        self._seed_or_config()
        self._seed_wa_config()
        self._seed_or_brackets()

    def _seed_fica(self):
        STORE.singles["FICA Configuration"] = {
            "doctype": "FICA Configuration",
            "tax_year": "2025",
            "social_security_rate_employee": "6.2",
            "social_security_rate_employer": "6.2",
            "social_security_wage_base": "176100",
            "medicare_rate_employee": "1.45",
            "medicare_rate_employer": "1.45",
            "additional_medicare_threshold": "200000",
            "additional_medicare_rate": "0.9",
            "futa_rate": "6.0",
            "futa_wage_base": "7000",
            "futa_state_credit_max": "5.4",
        }

    def _seed_federal_brackets(self):
        from erpnext_mcp.withholding import ANNUAL_BRACKETS as FED_BRACKETS
        brackets = []
        for filing_status, annual in FED_BRACKETS.items():
            for period_name, periods in PERIODS_PER_YEAR.items():
                for bracket in annual:
                    floor = bracket["bracket_floor"] / periods
                    ceiling = bracket["bracket_ceiling"] / periods if bracket["bracket_ceiling"] else None
                    base = bracket["base_tax"] / periods
                    brackets.append({
                        "name": f"FTB-{filing_status[:3]}-{period_name[:3]}-{floor:.0f}",
                        "tax_year": 2025,
                        "filing_status": filing_status,
                        "payroll_period": period_name,
                        "bracket_floor": round(floor, 2),
                        "bracket_ceiling": round(ceiling, 2) if ceiling else None,
                        "base_tax": round(base, 2),
                        "marginal_rate": bracket["marginal_rate"],
                    })
        STORE.seed("Federal Tax Table", brackets)

    def _seed_employees(self):
        STORE.seed(
            "Employee",
            [
                {
                    "name": "HR-EMP-00001",
                    "employee_name": "Test Worker",
                    "company": MAIN,
                    "status": "Active",
                    "date_of_joining": "2025-01-15",
                },
                {
                    "name": "HR-EMP-00002",
                    "employee_name": "WA Worker",
                    "company": MAIN,
                    "status": "Active",
                    "date_of_joining": "2025-03-01",
                },
            ],
        )

    def _seed_or_config(self):
        STORE.seed("State Tax Configuration", [{
            "name": "STC-OR-2025",
            "company": MAIN,
            "state": "OR",
            "tax_year": 2025,
            "status": "Active",
            "or_income_tax_enabled": 1,
            "or_transit_tax_rate": 0.1,
            "or_paid_leave_rate": 1.0,
            "or_paid_leave_employee_share": 60,
            "or_paid_leave_employer_share": 40,
            "or_paid_leave_small_employer": 0,
            "or_workers_comp_rate": 1.5,
        }])

    def _seed_wa_config(self):
        STORE.seed("State Tax Configuration", [{
            "name": "STC-WA-2025",
            "company": MAIN,
            "state": "WA",
            "tax_year": 2025,
            "status": "Active",
            "wa_pfml_rate": 0.92,
            "wa_pfml_employee_share": 72.76,
            "wa_pfml_employer_share": 27.24,
            "wa_pfml_wage_base": 176100,
            "wa_cares_rate": 0.58,
            "wa_cares_employee_only": 1,
            "wa_cares_exempt_employees": "",
            "wa_li_rate_employee": 0.25,
            "wa_li_rate_employer": 0.35,
        }])

    def _seed_or_brackets(self):
        brackets = []
        for filing_status, annual in OR_ANNUAL_BRACKETS.items():
            for bracket in annual:
                brackets.append({
                    "name": f"STB-OR-{filing_status[:3]}-{bracket['bracket_floor']:.0f}",
                    "state": "OR",
                    "tax_year": 2025,
                    "filing_status": filing_status,
                    "bracket_floor": bracket["bracket_floor"],
                    "bracket_ceiling": bracket["bracket_ceiling"],
                    "base_tax": bracket["base_tax"],
                    "marginal_rate": bracket["marginal_rate"],
                })
        STORE.seed("State Tax Table", brackets)

    def _default_or_config(self):
        return {
            "or_income_tax_enabled": 1,
            "or_transit_tax_rate": 0.1,
            "or_paid_leave_rate": 1.0,
            "or_paid_leave_employee_share": 60,
            "or_paid_leave_employer_share": 40,
            "or_paid_leave_small_employer": 0,
            "or_workers_comp_rate": 1.5,
        }

    def _default_wa_config(self):
        return {
            "wa_pfml_rate": 0.92,
            "wa_pfml_employee_share": 72.76,
            "wa_pfml_employer_share": 27.24,
            "wa_pfml_wage_base": 176100,
            "wa_cares_rate": 0.58,
            "wa_cares_employee_only": 1,
            "wa_li_rate_employee": 0.25,
            "wa_li_rate_employer": 0.35,
        }

    def _default_w4_data(self):
        return {
            "filing_status": "Single",
            "multiple_jobs": False,
            "additional_income_from_other_jobs": 0,
            "dependents_under_17_count": 0,
            "other_dependents_count": 0,
            "total_dependents_credit": 0,
            "other_income": 0,
            "deductions": 0,
            "extra_withholding_per_period": 0,
        }

    def _default_fica(self):
        return {
            "social_security_rate_employee": 6.2,
            "social_security_rate_employer": 6.2,
            "social_security_wage_base": 176100,
            "medicare_rate_employee": 1.45,
            "medicare_rate_employer": 1.45,
            "additional_medicare_threshold": 200000,
            "additional_medicare_rate": 0.9,
            "futa_rate": 6.0,
            "futa_wage_base": 7000,
            "futa_state_credit_max": 5.4,
        }

    def _submit_w4(self, employee="HR-EMP-00001", filing_status="Single or Married Filing Separately",
                   tax_year=2025, **kwargs):
        args = {
            "employee": employee,
            "company": MAIN,
            "tax_year": tax_year,
            "filing_status": filing_status,
            **kwargs,
        }
        return self.tool_data("submit_w4", args)


# ── Claim 1: Oregon calc engine ────────────────────────────────────────


class OregonCalcEngine(StateTaxTestCase):
    """Oregon withholding is correct."""

    def test_basic_or_biweekly(self):
        """Oregon withholding on $2,500 biweekly produces all components."""
        config = self._default_or_config()
        brackets = OR_ANNUAL_BRACKETS["Single"]

        result = calculate_oregon_withholding(
            gross_pay=2500,
            pay_frequency="Biweekly",
            filing_status="Single",
            state_config=config,
            state_tax_table=brackets,
        )

        self.assertIn("or_income_tax", result)
        self.assertIn("or_transit_tax", result)
        self.assertIn("or_paid_leave_employee", result)
        self.assertIn("or_paid_leave_employer", result)
        self.assertIn("or_workers_comp", result)
        self.assertIn("total_or_employee", result)
        self.assertIn("total_or_employer", result)
        self.assertGreater(result["or_income_tax"], 0)
        self.assertEqual(result["state"], "OR")

    def test_transit_tax(self):
        """Transit tax is 0.1% of gross, no cap."""
        config = self._default_or_config()
        result = calculate_oregon_withholding(
            1000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertEqual(result["or_transit_tax"], round(1000 * 0.001, 2))

    def test_paid_leave_split(self):
        """Paid leave is 1% split 60/40 employee/employer."""
        config = self._default_or_config()
        result = calculate_oregon_withholding(
            1000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        total_paid_leave = 1000 * 0.01
        self.assertEqual(result["or_paid_leave_employee"], round(total_paid_leave * 0.60, 2))
        self.assertEqual(result["or_paid_leave_employer"], round(total_paid_leave * 0.40, 2))

    def test_workers_comp(self):
        """Workers' comp is the employer-entered rate on gross."""
        config = self._default_or_config()
        result = calculate_oregon_withholding(
            1000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertEqual(result["or_workers_comp"], round(1000 * 0.015, 2))

    def test_totals_add_up(self):
        """Employee and employer totals are the sum of their parts."""
        config = self._default_or_config()
        result = calculate_oregon_withholding(
            3000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        expected_ee = round(
            result["or_income_tax"] + result["or_transit_tax"] + result["or_paid_leave_employee"], 2
        )
        expected_er = round(result["or_paid_leave_employer"] + result["or_workers_comp"], 2)
        self.assertEqual(result["total_or_employee"], expected_ee)
        self.assertEqual(result["total_or_employer"], expected_er)

    def test_mfj_lower_tax(self):
        """Married Filing Jointly pays less Oregon income tax than Single."""
        config = self._default_or_config()
        single = calculate_oregon_withholding(
            5000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        mfj = calculate_oregon_withholding(
            5000, "Biweekly", "Married Filing Jointly", config,
            OR_ANNUAL_BRACKETS["Married Filing Jointly"],
        )
        self.assertGreater(single["or_income_tax"], mfj["or_income_tax"])


# ── Claim 2: Washington calc engine ────────────────────────────────────


class WashingtonCalcEngine(StateTaxTestCase):
    """Washington withholding is correct — no income tax."""

    def test_basic_wa(self):
        """Washington withholding produces PFML, Cares, L&I — no income tax."""
        config = self._default_wa_config()
        result = calculate_washington_withholding(2500, config)

        self.assertIn("wa_pfml_employee", result)
        self.assertIn("wa_pfml_employer", result)
        self.assertIn("wa_cares_employee", result)
        self.assertIn("wa_li_employee", result)
        self.assertIn("wa_li_employer", result)
        self.assertNotIn("income_tax", result)
        self.assertEqual(result["state"], "WA")

    def test_pfml_split(self):
        """PFML is 0.92% split ~72.76/27.24 employee/employer."""
        config = self._default_wa_config()
        result = calculate_washington_withholding(1000, config)
        total_pfml = 1000 * 0.0092
        self.assertEqual(result["wa_pfml_employee"], round(total_pfml * 0.7276, 2))
        self.assertEqual(result["wa_pfml_employer"], round(total_pfml * 0.2724, 2))

    def test_cares_employee_only(self):
        """WA Cares is 0.58%, employee-only."""
        config = self._default_wa_config()
        result = calculate_washington_withholding(1000, config)
        self.assertEqual(result["wa_cares_employee"], round(1000 * 0.0058, 2))

    def test_li_rates(self):
        """L&I uses employer-entered rates for both employee and employer."""
        config = self._default_wa_config()
        result = calculate_washington_withholding(1000, config)
        self.assertEqual(result["wa_li_employee"], round(1000 * 0.0025, 2))
        self.assertEqual(result["wa_li_employer"], round(1000 * 0.0035, 2))

    def test_totals_add_up(self):
        """WA totals are the sum of their parts."""
        config = self._default_wa_config()
        result = calculate_washington_withholding(3000, config)
        expected_ee = round(
            result["wa_pfml_employee"] + result["wa_cares_employee"] + result["wa_li_employee"], 2
        )
        expected_er = round(result["wa_pfml_employer"] + result["wa_li_employer"], 2)
        self.assertEqual(result["total_wa_employee"], expected_ee)
        self.assertEqual(result["total_wa_employer"], expected_er)


# ── Claim 3: Cross-state pay period ────────────────────────────────────


class CrossStatePeriod(StateTaxTestCase):
    """An employee with shifts in both OR and WA gets each state's calc."""

    def test_separate_calcs_per_state(self):
        """OR and WA produce different tax shapes on the same gross."""
        or_config = self._default_or_config()
        wa_config = self._default_wa_config()

        or_result = calculate_state_withholding(
            2000, "Biweekly", "OR", "Single", or_config, OR_ANNUAL_BRACKETS["Single"],
        )
        wa_result = calculate_state_withholding(
            1000, "Biweekly", "WA", "Single", wa_config, None,
        )

        self.assertEqual(or_result["state"], "OR")
        self.assertEqual(wa_result["state"], "WA")
        self.assertIn("or_income_tax", or_result)
        self.assertNotIn("or_income_tax", wa_result)
        self.assertIn("wa_pfml_employee", wa_result)
        self.assertNotIn("wa_pfml_employee", or_result)

    def test_summed_totals_for_pay_period(self):
        """Both states' employee totals sum correctly for a split pay period."""
        or_config = self._default_or_config()
        wa_config = self._default_wa_config()

        or_result = calculate_state_withholding(
            2000, "Biweekly", "OR", "Single", or_config, OR_ANNUAL_BRACKETS["Single"],
        )
        wa_result = calculate_state_withholding(
            1000, "Biweekly", "WA", "Single", wa_config, None,
        )

        combined_ee = or_result["total_or_employee"] + wa_result["total_wa_employee"]
        combined_er = or_result["total_or_employer"] + wa_result["total_wa_employer"]
        self.assertGreater(combined_ee, 0)
        self.assertGreater(combined_er, 0)


# ── Claim 4: Combined federal + state ──────────────────────────────────


class CombinedFederalState(StateTaxTestCase):
    """The combined engine runs federal + state together."""

    def test_combined_or(self):
        """Federal + Oregon combined calc produces grand totals."""
        result = calculate_all_payroll_taxes(
            gross_pay=2500,
            pay_frequency="Biweekly",
            work_state="OR",
            filing_status="Single",
            w4_data=self._default_w4_data(),
            ytd_gross=0,
            ytd_ss_withheld=0,
            fica_config=self._default_fica(),
            federal_tax_table=ANNUAL_BRACKETS["Single"],
            state_config=self._default_or_config(),
            state_tax_table=OR_ANNUAL_BRACKETS["Single"],
        )

        self.assertIn("federal", result)
        self.assertIn("state", result)
        self.assertEqual(result["work_state"], "OR")
        self.assertEqual(
            result["grand_total_employee"],
            round(result["federal"]["total_employee_tax"] + result["state"]["total_or_employee"], 2),
        )
        self.assertEqual(
            result["grand_total_employer"],
            round(result["federal"]["total_employer_tax"] + result["state"]["total_or_employer"], 2),
        )

    def test_combined_wa(self):
        """Federal + Washington combined calc produces grand totals."""
        result = calculate_all_payroll_taxes(
            gross_pay=2500,
            pay_frequency="Biweekly",
            work_state="WA",
            filing_status="Single",
            w4_data=self._default_w4_data(),
            ytd_gross=0,
            ytd_ss_withheld=0,
            fica_config=self._default_fica(),
            federal_tax_table=ANNUAL_BRACKETS["Single"],
            state_config=self._default_wa_config(),
            state_tax_table=None,
        )

        self.assertIn("federal", result)
        self.assertIn("state", result)
        self.assertEqual(result["work_state"], "WA")
        self.assertEqual(
            result["grand_total_employee"],
            round(result["federal"]["total_employee_tax"] + result["state"]["total_wa_employee"], 2),
        )

    def test_grand_total_all(self):
        """grand_total_all = grand_total_employee + grand_total_employer."""
        result = calculate_all_payroll_taxes(
            3000, "Biweekly", "OR", "Single",
            self._default_w4_data(), 0, 0, self._default_fica(),
            ANNUAL_BRACKETS["Single"], self._default_or_config(),
            OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertEqual(
            result["grand_total_all"],
            round(result["grand_total_employee"] + result["grand_total_employer"], 2),
        )


# ── Claim 5: Oregon bracket boundaries ────────────────────────────────


class OregonBrackets(StateTaxTestCase):
    """Oregon 2025 brackets produce the right tax at boundary values."""

    def test_lowest_bracket_single(self):
        """Income entirely in the 4.75% bracket."""
        config = self._default_or_config()
        # $3,000 annual → entirely in first bracket (under $4,050)
        result = calculate_oregon_withholding(
            3000, "Annual", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        expected = round(3000 * 0.0475, 2)
        self.assertEqual(result["or_income_tax"], expected)

    def test_second_bracket_single(self):
        """Income crossing into the 6.75% bracket."""
        config = self._default_or_config()
        # $8,000 annual → $4,050 at 4.75% + ($8,000-$4,050) at 6.75%
        result = calculate_oregon_withholding(
            8000, "Annual", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        expected = round(192.38 + (8000 - 4050) * 0.0675, 2)
        self.assertEqual(result["or_income_tax"], expected)

    def test_top_bracket_single(self):
        """Income in the 9.9% top bracket."""
        config = self._default_or_config()
        # $150,000 annual → into the top bracket
        result = calculate_oregon_withholding(
            150000, "Annual", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        expected = round(10657.50 + (150000 - 125000) * 0.099, 2)
        self.assertEqual(result["or_income_tax"], expected)


# ── Claim 6: Small employer ────────────────────────────────────────────


class SmallEmployer(StateTaxTestCase):
    """Oregon small employer pays no employer share of Paid Leave."""

    def test_small_employer_no_er_share(self):
        config = self._default_or_config()
        config["or_paid_leave_small_employer"] = 1

        result = calculate_oregon_withholding(
            2000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertEqual(result["or_paid_leave_employer"], 0.0)
        self.assertGreater(result["or_paid_leave_employee"], 0)

    def test_normal_employer_has_er_share(self):
        config = self._default_or_config()
        config["or_paid_leave_small_employer"] = 0

        result = calculate_oregon_withholding(
            2000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertGreater(result["or_paid_leave_employer"], 0)


# ── Claim 7: Edge cases ────────────────────────────────────────────────


class EdgeCases(StateTaxTestCase):
    """Zero gross, missing brackets, unsupported state."""

    def test_zero_gross_or(self):
        result = calculate_oregon_withholding(
            0, "Biweekly", "Single", self._default_or_config(), OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertEqual(result["total_or_employee"], 0)
        self.assertEqual(result["total_or_employer"], 0)

    def test_zero_gross_wa(self):
        result = calculate_washington_withholding(0, self._default_wa_config())
        self.assertEqual(result["total_wa_employee"], 0)
        self.assertEqual(result["total_wa_employer"], 0)

    def test_no_brackets_or(self):
        """Oregon with no brackets produces zero income tax."""
        config = self._default_or_config()
        result = calculate_oregon_withholding(
            3000, "Biweekly", "Single", config, [],
        )
        self.assertEqual(result["or_income_tax"], 0)
        self.assertGreater(result["or_transit_tax"], 0)

    def test_income_tax_disabled(self):
        """Disabling Oregon income tax zeroes it out."""
        config = self._default_or_config()
        config["or_income_tax_enabled"] = 0
        result = calculate_oregon_withholding(
            3000, "Biweekly", "Single", config, OR_ANNUAL_BRACKETS["Single"],
        )
        self.assertEqual(result["or_income_tax"], 0)

    def test_unsupported_state(self):
        result = calculate_state_withholding(
            1000, "Biweekly", "CA", "Single", {}, [],
        )
        self.assertIn("error", result)

    def test_deterministic(self):
        """Same inputs produce the same outputs."""
        config = self._default_or_config()
        brackets = OR_ANNUAL_BRACKETS["Single"]
        kwargs = dict(gross_pay=3000, pay_frequency="Biweekly", filing_status="Single",
                      state_config=config, state_tax_table=brackets)
        r1 = calculate_oregon_withholding(**kwargs)
        r2 = calculate_oregon_withholding(**kwargs)
        self.assertEqual(r1["or_income_tax"], r2["or_income_tax"])
        self.assertEqual(r1["total_or_employee"], r2["total_or_employee"])

    def test_all_periods_or(self):
        """Oregon engine works for every pay frequency."""
        config = self._default_or_config()
        brackets = OR_ANNUAL_BRACKETS["Single"]
        for period in PERIODS_PER_YEAR:
            result = calculate_oregon_withholding(5000, period, "Single", config, brackets)
            self.assertIn("or_income_tax", result, f"failed for {period}")

    def test_seed_or_brackets(self):
        """seed_or_brackets returns rows for all filing statuses and periods."""
        rows = seed_or_brackets()
        self.assertGreater(len(rows), 0)
        statuses = {r["filing_status"] for r in rows}
        self.assertIn("Single", statuses)
        self.assertIn("Married Filing Jointly", statuses)
        self.assertIn("Head of Household", statuses)
        self.assertTrue(all(r["state"] == "OR" for r in rows))


# ── Claim 8: Read tools ───────────────────────────────────────────────


class ReadTools(StateTaxTestCase):
    """Read-only MCP tools return the right data."""

    def test_get_state_tax_config_or(self):
        data = self.tool_data("get_state_tax_config", {
            "company": MAIN, "state": "OR", "tax_year": 2025,
        })
        self.assertEqual(data["state"], "OR")
        self.assertEqual(data["company"], MAIN)

    def test_get_state_tax_config_wa(self):
        data = self.tool_data("get_state_tax_config", {
            "company": MAIN, "state": "WA", "tax_year": 2025,
        })
        self.assertEqual(data["state"], "WA")

    def test_list_state_tax_configs(self):
        data = self.tool_data("list_state_tax_configs", {"company": MAIN})
        self.assertEqual(data["count"], 2)

    def test_get_state_tax_table_or(self):
        data = self.tool_data("get_state_tax_table", {
            "state": "OR", "tax_year": 2025, "filing_status": "Single",
        })
        self.assertGreater(data["count"], 0)

    def test_get_state_tax_table_wa_empty(self):
        data = self.tool_data("get_state_tax_table", {
            "state": "WA", "tax_year": 2025, "filing_status": "Single",
        })
        self.assertEqual(data["count"], 0)
        self.assertIn("note", data)

    def test_missing_config_errors(self):
        text = self.tool_error("get_state_tax_config", {
            "company": MAIN, "state": "OR", "tax_year": 2030,
        })
        self.assertIn("no active", text)


# ── Claim 9: Mutating tools ───────────────────────────────────────────


class MutatingTools(StateTaxTestCase):
    """Create, update, and import tools work correctly."""

    def test_create_state_tax_config(self):
        data = self.tool_data("create_state_tax_config", {
            "company": MAIN, "state": "OR", "tax_year": 2026,
            "or_transit_tax_rate": 0.1,
        })
        self.assertEqual(data["state"], "OR")
        self.assertEqual(data["tax_year"], 2026)

    def test_create_duplicate_fails(self):
        text = self.tool_error("create_state_tax_config", {
            "company": MAIN, "state": "OR", "tax_year": 2025,
        })
        self.assertIn("already exists", text)

    def test_update_state_tax_config(self):
        data = self.tool_data("update_state_tax_config", {
            "company": MAIN, "state": "OR", "tax_year": 2025,
            "or_transit_tax_rate": 0.15,
        })
        self.assertIn("or_transit_tax_rate", data["updated_fields"])

    def test_import_state_tax_table(self):
        data = self.tool_data("import_state_tax_table", {
            "state": "OR", "tax_year": 2026,
            "brackets": [
                {"filing_status": "Single", "bracket_floor": 0, "bracket_ceiling": 5000,
                 "base_tax": 0, "marginal_rate": 5.0},
                {"filing_status": "Single", "bracket_floor": 5000, "bracket_ceiling": None,
                 "base_tax": 250, "marginal_rate": 9.0},
            ],
        })
        self.assertEqual(data["created"], 2)


# ── Claim 10: Combined preview tool ───────────────────────────────────


class CombinedPreview(StateTaxTestCase):
    """The preview_total_payroll_taxes tool runs federal + state."""

    def test_preview_total_or(self):
        self._submit_w4()
        data = self.tool_data("preview_total_payroll_taxes", {
            "employee": "HR-EMP-00001",
            "gross_pay": 2500,
            "pay_frequency": "Biweekly",
            "work_state": "OR",
        })
        self.assertIn("federal", data)
        self.assertIn("state", data)
        self.assertEqual(data["work_state"], "OR")
        self.assertGreater(data["grand_total_employee"], 0)
        self.assertGreater(data["grand_total_employer"], 0)

    def test_preview_total_wa(self):
        self._submit_w4(employee="HR-EMP-00002")
        data = self.tool_data("preview_total_payroll_taxes", {
            "employee": "HR-EMP-00002",
            "gross_pay": 2500,
            "pay_frequency": "Biweekly",
            "work_state": "WA",
        })
        self.assertEqual(data["work_state"], "WA")
        self.assertGreater(data["grand_total_employee"], 0)

    def test_preview_state_only_or(self):
        self._submit_w4()
        data = self.tool_data("preview_state_withholding", {
            "employee": "HR-EMP-00001",
            "gross_pay": 2500,
            "pay_frequency": "Biweekly",
            "work_state": "OR",
        })
        self.assertEqual(data["state"], "OR")
        self.assertGreater(data["total_or_employee"], 0)
