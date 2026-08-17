# SPDX-License-Identifier: MIT
"""Direct deposit and NACHA ACH file generation — v0.91.0.

TEN CLAIMS.

1. `RoutingNumbers` — the ABA check digit is enforced, and it catches the two
   mistakes people actually make.
2. `FieldFormatting` — alphanumeric, numeric and amount fields are the right
   width, the right justification and the right fill.
3. `RecordLayout` — every record is 94 characters and every field is at the
   offset the NACHA spec puts it at.
4. `FileStructure` — header, batch, entries, controls, and blocking to ten.
5. `Totals` — entry count, entry hash and credit total, in both control records.
6. `Allocation` — one net pay splits across accounts correctly, and refuses
   rather than shorting somebody.
7. `BankAccountTools` — CRUD, validation, and the account number never coming
   back out.
8. `NachaFileTool` — the generator against a real payroll entry.
9. `PrenoteTool` — zero-dollar entries with their own transaction codes.
10. `MobileBankAccounts` — a handset reaches the caller's OWN account and no
    colleague's, and the full number never comes back out.

WHY THE OFFSETS ARE ASSERTED BY SLICE. A NACHA record is 94 character positions,
not 94 delimited fields. A field written one character short does not produce a
short field — it shifts every field after it, and the file still looks like a
file to anything but the bank's parser. So these tests index the record the way
the spec does, `record[3:13]`, rather than splitting on anything.
"""
import unittest

from erpnext_mcp import nacha
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.farmops_api import routes

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import STORE
from .test_api_mobile import ON as MOBILE_ON
from .test_api_mobile import (
    OUTSIDER,
    WORKER,
    WORKER_EMPLOYEE,
    MobileAPITestCase,
)

ACH_TOOLS_ON = {
    f"allow_{name}": 1
    for name in (
        "get_employee_bank_account",
        "list_employee_bank_accounts",
        "create_employee_bank_account",
        "update_employee_bank_account",
        "generate_nacha_file",
        "generate_prenote_file",
    )
}

#: Two real, check-digit-valid routing numbers. Both pass the 3-7-1 test, which
#: is why they can be used at all — the validator would refuse an invented one.
ROUTING_A = "123456780"
ROUTING_B = "325070760"

ORIGINATOR = {
    "originating_dfi": ROUTING_A,
    "immediate_destination": ROUTING_A,
    "immediate_destination_name": "FIRST BANK",
    "immediate_origin": "1931234567",
    "immediate_origin_name": "ORCHARD MEADOW LLC",
    "company_name": "ORCHARD MEADOW",
    "company_identification": "1931234567",
    "entry_description": "PAYROLL",
    "discretionary_data": "",
    "settlement_days": 2,
}


def _entry(**overrides):
    base = {
        "routing_number": ROUTING_A,
        "account_number": "12345678",
        "account_type": "Checking",
        "amount": 1234.56,
        "individual_id": "HR-EMP-00001",
        "individual_name": "Test Worker",
    }
    base.update(overrides)
    return base


# ── Claim 1: routing numbers ──────────────────────────────────────────────


class RoutingNumbers(unittest.TestCase):
    def test_valid_routing_passes(self):
        self.assertEqual(nacha.normalize_routing(ROUTING_A), ROUTING_A)
        self.assertEqual(nacha.normalize_routing(ROUTING_B), ROUTING_B)

    def test_formatting_is_stripped(self):
        self.assertEqual(nacha.normalize_routing("123-456-780"), ROUTING_A)
        self.assertEqual(nacha.normalize_routing(" 123456780 "), ROUTING_A)

    def test_wrong_length_refused(self):
        with self.assertRaises(nacha.NachaError) as caught:
            nacha.normalize_routing("12345678")
        self.assertIn("8 digits", str(caught.exception))

    def test_single_digit_typo_is_caught(self):
        """Changing one digit always breaks the 3-7-1 sum."""
        bad = "123456781"
        self.assertFalse(nacha.routing_checksum_ok(bad))
        with self.assertRaises(nacha.NachaError):
            nacha.normalize_routing(bad)

    def test_adjacent_transposition_is_caught(self):
        """The other mistake people make copying off a cheque."""
        transposed = "213456780"
        self.assertFalse(nacha.routing_checksum_ok(transposed))

    def test_empty_refused(self):
        with self.assertRaises(nacha.NachaError):
            nacha.normalize_routing("")

    def test_prefix_and_check_digit_split(self):
        self.assertEqual(nacha.routing_prefix(ROUTING_A), "12345678")
        self.assertEqual(nacha.routing_check_digit(ROUTING_A), "0")


# ── Claim 2: field formatting ─────────────────────────────────────────────


class FieldFormatting(unittest.TestCase):
    def test_alphanumeric_is_left_justified_and_padded(self):
        self.assertEqual(nacha.alphanumeric("ABC", 6), "ABC   ")

    def test_alphanumeric_uppercases(self):
        self.assertEqual(nacha.alphanumeric("abc", 3), "ABC")

    def test_alphanumeric_truncates_from_the_right(self):
        """A too-long name keeps its front, which is what NACHA specifies."""
        self.assertEqual(nacha.alphanumeric("ABCDEFGH", 4), "ABCD")

    def test_accented_letters_fold_to_ascii(self):
        """A name keeps its LENGTH, which is what stops a column shifting."""
        out = nacha.alphanumeric("José Muñoz", 22)
        self.assertEqual(len(out), 22)
        self.assertTrue(out.startswith("JOSE MUNOZ"))

    def test_disallowed_characters_become_spaces_not_nothing(self):
        """Replacement, not deletion — deletion would re-align the field."""
        out = nacha.alphanumeric("A*B", 3)
        self.assertEqual(len(out), 3)
        self.assertEqual(out, "A B")

    def test_numeric_is_right_justified_zero_filled(self):
        self.assertEqual(nacha.numeric("42", 6), "000042")

    def test_numeric_strips_non_digits(self):
        self.assertEqual(nacha.numeric("12-34", 6), "001234")

    def test_numeric_empty_is_all_zeros(self):
        self.assertEqual(nacha.numeric("", 4), "0000")

    def test_amount_is_cents_zero_filled(self):
        self.assertEqual(nacha.amount_cents(1234.56, 10), "0000123456")

    def test_amount_zero(self):
        self.assertEqual(nacha.amount_cents(0, 10), "0000000000")

    def test_amount_rounds_to_the_cent(self):
        self.assertEqual(nacha.amount_cents(0.005, 10), "0000000001")

    def test_the_widest_amount_that_fits_is_accepted(self):
        """$99,999,999.99 is ten digits of cents — the last one that fits."""
        self.assertEqual(nacha.amount_cents(99999999.99, 10), "9999999999")

    def test_amount_refuses_to_truncate(self):
        """The one field where silent truncation would move money.

        Eleven digits of cents in a ten-digit field would keep the LOW ten and
        pay $23,456,789.00 instead of $123,456,789.00 — a wrong number that looks
        entirely well-formed.
        """
        with self.assertRaises(nacha.NachaError) as caught:
            nacha.amount_cents(123456789.00, 10)
        self.assertIn("silently drop", str(caught.exception))

    def test_amount_refuses_negative(self):
        with self.assertRaises(nacha.NachaError):
            nacha.amount_cents(-1, 10)

    def test_ten_char_id_routing_gets_a_leading_blank(self):
        self.assertEqual(nacha.ten_char_id(ROUTING_A), " 123456780")

    def test_ten_char_id_company_id_is_left_justified(self):
        self.assertEqual(nacha.ten_char_id("1931234567"), "1931234567")


# ── Claim 3: record layout, by offset ─────────────────────────────────────


class RecordLayout(unittest.TestCase):
    """Every field at the position the spec puts it. Offsets are 0-based slices."""

    def test_file_header_layout(self):
        r = nacha.file_header_record(
            immediate_destination=ROUTING_A,
            immediate_origin="1931234567",
            file_creation_date="250815",
            file_creation_time="0930",
            destination_name="FIRST BANK",
            origin_name="ORCHARD MEADOW LLC",
            file_id_modifier="A",
        )
        self.assertEqual(len(r), 94)
        self.assertEqual(r[0], "1")                       # record type
        self.assertEqual(r[1:3], "01")                    # priority code
        self.assertEqual(r[3:13], " 123456780")           # immediate destination
        self.assertEqual(r[13:23], "1931234567")          # immediate origin
        self.assertEqual(r[23:29], "250815")              # creation date
        self.assertEqual(r[29:33], "0930")                # creation time
        self.assertEqual(r[33], "A")                      # file id modifier
        self.assertEqual(r[34:37], "094")                 # record size
        self.assertEqual(r[37:39], "10")                  # blocking factor
        self.assertEqual(r[39], "1")                      # format code
        self.assertEqual(r[40:63], "FIRST BANK".ljust(23))
        self.assertEqual(r[63:86], "ORCHARD MEADOW LLC".ljust(23))
        self.assertEqual(r[86:94], " " * 8)               # reference code

    def test_batch_header_layout(self):
        r = nacha.batch_header_record(
            company_name="ORCHARD MEADOW",
            company_identification="1931234567",
            entry_description="PAYROLL",
            effective_entry_date="250817",
            originating_dfi="12345678",
            batch_number=1,
            descriptive_date="250815",
        )
        self.assertEqual(len(r), 94)
        self.assertEqual(r[0], "5")
        self.assertEqual(r[1:4], "220")                   # credits only
        self.assertEqual(r[4:20], "ORCHARD MEADOW".ljust(16))
        self.assertEqual(r[20:40], " " * 20)              # discretionary data
        self.assertEqual(r[40:50], "1931234567")          # company identification
        self.assertEqual(r[50:53], "PPD")                 # standard entry class
        self.assertEqual(r[53:63], "PAYROLL".ljust(10))
        self.assertEqual(r[63:69], "250815")              # descriptive date
        self.assertEqual(r[69:75], "250817")              # effective entry date
        self.assertEqual(r[75:78], "   ")                 # settlement — the operator's
        self.assertEqual(r[78], "1")                      # originator status
        self.assertEqual(r[79:87], "12345678")            # originating DFI
        self.assertEqual(r[87:94], "0000001")             # batch number

    def test_entry_detail_layout(self):
        r = nacha.entry_detail_record(
            transaction_code="22",
            receiving_routing=ROUTING_A,
            account_number="12345678",
            amount=1234.56,
            individual_id="HR-EMP-00001",
            individual_name="Test Worker",
            trace_prefix="12345678",
            trace_sequence=1,
        )
        self.assertEqual(len(r), 94)
        self.assertEqual(r[0], "6")
        self.assertEqual(r[1:3], "22")                    # checking credit
        self.assertEqual(r[3:11], "12345678")             # receiving DFI, 8 digits
        self.assertEqual(r[11], "0")                      # check digit, alone
        self.assertEqual(r[12:29], "12345678".ljust(17))  # account number
        self.assertEqual(r[29:39], "0000123456")          # amount in cents
        self.assertEqual(r[39:54], "HR-EMP-00001".ljust(15))
        self.assertEqual(r[54:76], "TEST WORKER".ljust(22))
        self.assertEqual(r[76:78], "  ")                  # discretionary
        self.assertEqual(r[78], "0")                      # addenda indicator
        self.assertEqual(r[79:94], "123456780000001")     # trace: 8 + 7

    def test_batch_control_layout(self):
        r = nacha.batch_control_record(
            entry_count=2,
            entry_hash=44852754,
            total_debit=0,
            total_credit=1734.56,
            company_identification="1931234567",
            originating_dfi="12345678",
            batch_number=1,
        )
        self.assertEqual(len(r), 94)
        self.assertEqual(r[0], "8")
        self.assertEqual(r[1:4], "220")
        self.assertEqual(r[4:10], "000002")               # entry/addenda count
        self.assertEqual(r[10:20], "0044852754")          # entry hash
        self.assertEqual(r[20:32], "000000000000")        # total debit
        self.assertEqual(r[32:44], "000000173456")        # total credit
        self.assertEqual(r[44:54], "1931234567")
        self.assertEqual(r[54:73], " " * 19)              # message auth code
        self.assertEqual(r[73:79], " " * 6)               # reserved
        self.assertEqual(r[79:87], "12345678")
        self.assertEqual(r[87:94], "0000001")

    def test_file_control_layout(self):
        r = nacha.file_control_record(
            batch_count=1,
            block_count=1,
            entry_count=2,
            entry_hash=44852754,
            total_debit=0,
            total_credit=1734.56,
        )
        self.assertEqual(len(r), 94)
        self.assertEqual(r[0], "9")
        self.assertEqual(r[1:7], "000001")                # batch count
        self.assertEqual(r[7:13], "000001")               # block count
        self.assertEqual(r[13:21], "00000002")            # entry count, 8 wide here
        self.assertEqual(r[21:31], "0044852754")
        self.assertEqual(r[31:43], "000000000000")
        self.assertEqual(r[43:55], "000000173456")
        self.assertEqual(r[55:94], " " * 39)              # reserved

    def test_padding_record_is_all_nines(self):
        self.assertEqual(nacha.padding_record(), "9" * 94)

    def test_a_short_field_is_caught_not_shipped(self):
        """The assertion that makes a width typo loud instead of invisible."""
        with self.assertRaises(nacha.NachaError) as caught:
            nacha._pack("1", "too short")
        self.assertIn("field-width bug", str(caught.exception))


# ── Claim 4: file structure ───────────────────────────────────────────────


class FileStructure(unittest.TestCase):
    def setUp(self):
        self.built = nacha.build_file(
            originator=ORIGINATOR,
            entries=[_entry(), _entry(routing_number=ROUTING_B, amount=500.0,
                                      account_type="Savings", account_number="998877",
                                      individual_id="HR-EMP-00002",
                                      individual_name="Ana Ruiz")],
            file_creation_date="250815",
            file_creation_time="0930",
            effective_entry_date="250817",
        )

    def test_record_types_in_order(self):
        types = [r[0] for r in self.built["records"][:6]]
        self.assertEqual(types, ["1", "5", "6", "6", "8", "9"])

    def test_every_record_is_94_characters(self):
        for i, record in enumerate(self.built["records"]):
            self.assertEqual(len(record), 94, f"record {i}")

    def test_padded_to_a_whole_block_of_ten(self):
        self.assertEqual(len(self.built["records"]) % 10, 0)
        self.assertEqual(len(self.built["records"]), 10)

    def test_padding_is_nine_records(self):
        self.assertTrue(all(r == "9" * 94 for r in self.built["records"][6:]))

    def test_savings_entry_gets_transaction_code_32(self):
        self.assertEqual(self.built["records"][3][1:3], "32")

    def test_checking_entry_gets_transaction_code_22(self):
        self.assertEqual(self.built["records"][2][1:3], "22")

    def test_trace_numbers_are_sequential(self):
        self.assertEqual(self.built["records"][2][79:94], "123456780000001")
        self.assertEqual(self.built["records"][3][79:94], "123456780000002")

    def test_content_is_newline_joined_and_terminated(self):
        lines = self.built["content"].split("\n")
        self.assertEqual(lines[-1], "")
        self.assertEqual(len(lines) - 1, len(self.built["records"]))

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(nacha.NachaError):
            nacha.build_file(ORIGINATOR, [], "250815", "0930", "250817")

    def test_unknown_account_type_refused(self):
        with self.assertRaises(nacha.NachaError) as caught:
            nacha.build_file(
                ORIGINATOR, [_entry(account_type="Brokerage")],
                "250815", "0930", "250817",
            )
        self.assertIn("transaction code", str(caught.exception))

    def test_block_count_covers_the_padding(self):
        """20 records is two blocks, and the file control has to say so."""
        entries = [_entry(amount=10.0) for _ in range(12)]
        built = nacha.build_file(ORIGINATOR, entries, "250815", "0930", "250817")
        self.assertEqual(len(built["records"]), 20)
        self.assertEqual(built["block_count"], 2)
        self.assertEqual(built["records"][15][7:13], "000002")


# ── Claim 5: totals ───────────────────────────────────────────────────────


class Totals(unittest.TestCase):
    def setUp(self):
        self.built = nacha.build_file(
            originator=ORIGINATOR,
            entries=[_entry(), _entry(routing_number=ROUTING_B, amount=500.0)],
            file_creation_date="250815",
            file_creation_time="0930",
            effective_entry_date="250817",
        )
        self.batch_control = self.built["records"][4]
        self.file_control = self.built["records"][5]

    def test_entry_hash_sums_eight_digit_prefixes(self):
        """NOT the nine-digit routing numbers — the check digit is excluded."""
        expected = int("12345678") + int("32507076")
        self.assertEqual(self.built["entry_hash"], str(expected))
        self.assertEqual(self.batch_control[10:20], str(expected).rjust(10, "0"))

    def test_credit_total(self):
        self.assertEqual(self.built["total_credit"], 1734.56)
        self.assertEqual(self.batch_control[32:44], "000000173456")

    def test_controls_agree_with_each_other(self):
        self.assertEqual(self.batch_control[10:20], self.file_control[21:31])
        self.assertEqual(self.batch_control[32:44], self.file_control[43:55])

    def test_entry_count_in_both_controls(self):
        self.assertEqual(self.batch_control[4:10], "000002")
        self.assertEqual(self.file_control[13:21], "00000002")

    def test_hash_truncates_to_rightmost_ten_digits(self):
        """Specified behaviour, and it looks exactly like a bug without warning."""
        self.assertEqual(nacha.truncate_hash(123456789012345), "6789012345")

    def test_block_count_rounds_up(self):
        self.assertEqual(nacha.block_count(1), 1)
        self.assertEqual(nacha.block_count(10), 1)
        self.assertEqual(nacha.block_count(11), 2)


# ── Claim 6: allocation ───────────────────────────────────────────────────


class Allocation(unittest.TestCase):
    """One net pay across several accounts. Pure — no site involved."""

    def _account(self, name, allocation_type, amount=0, priority=0):
        return {
            "name": name, "allocation_type": allocation_type,
            "allocation_amount": amount, "priority": priority,
        }

    def test_a_single_full_account_takes_everything(self):
        from erpnext_mcp.tools.ach import allocate
        splits = allocate(1500.00, [self._account("A", "Full")])
        self.assertEqual([a for _, a in splits], [1500.00])

    def test_fixed_then_full(self):
        from erpnext_mcp.tools.ach import allocate
        splits = allocate(1500.00, [
            self._account("A", "Full", priority=2),
            self._account("B", "Fixed Amount", 200, priority=1),
        ])
        self.assertEqual([(a["name"], amt) for a, amt in splits], [("B", 200.0), ("A", 1300.0)])

    def test_percentage_is_of_original_net_not_the_remainder(self):
        """20% of the cheque, not 20% of what survived the fixed transfer."""
        from erpnext_mcp.tools.ach import allocate
        splits = allocate(1000.00, [
            self._account("A", "Full", priority=3),
            self._account("B", "Fixed Amount", 500, priority=1),
            self._account("C", "Percentage", 20, priority=2),
        ])
        amounts = {a["name"]: amt for a, amt in splits}
        self.assertEqual(amounts["B"], 500.0)
        self.assertEqual(amounts["C"], 200.0)   # 20% of 1000, not of 500
        self.assertEqual(amounts["A"], 300.0)

    def test_splits_always_total_net_pay(self):
        from erpnext_mcp.tools.ach import allocate
        splits = allocate(1234.57, [
            self._account("A", "Full", priority=2),
            self._account("C", "Percentage", 33.33, priority=1),
        ])
        self.assertEqual(round(sum(a for _, a in splits), 2), 1234.57)

    def test_fixed_amount_larger_than_net_is_capped(self):
        from erpnext_mcp.tools.ach import allocate
        splits = allocate(100.00, [self._account("B", "Fixed Amount", 500)])
        self.assertEqual([a for _, a in splits], [100.0])

    def test_no_full_account_and_a_remainder_refuses(self):
        """The case that would silently short somebody."""
        from erpnext_mcp.errors import ToolError
        from erpnext_mcp.tools.ach import allocate
        with self.assertRaises(ToolError) as caught:
            allocate(1000.00, [self._account("B", "Fixed Amount", 200)])
        self.assertIn("not allocated", str(caught.exception))

    def test_two_full_accounts_refuse(self):
        from erpnext_mcp.errors import ToolError
        from erpnext_mcp.tools.ach import allocate
        with self.assertRaises(ToolError):
            allocate(1000.00, [self._account("A", "Full"), self._account("B", "Full")])

    def test_zero_amount_entries_are_dropped(self):
        """A $0.00 entry in a live batch is not a payment."""
        from erpnext_mcp.tools.ach import allocate
        splits = allocate(200.00, [
            self._account("A", "Full", priority=2),
            self._account("B", "Fixed Amount", 200, priority=1),
        ])
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0][0]["name"], "B")


# ── the site-shaped tests ─────────────────────────────────────────────────


class ACHTestCase(V12TestCase):
    def setUp(self):
        super().setUp()
        self.configure(enabled=1, **ACH_TOOLS_ON)
        install_hrms()
        self._seed_employees()
        self._seed_originator()

    def _seed_employees(self):
        STORE.seed("Employee", [
            {"name": "HR-EMP-00001", "employee_name": "Test Worker", "company": MAIN,
             "status": "Active", "date_of_joining": "2025-01-15"},
            {"name": "HR-EMP-00002", "employee_name": "Ana Ruiz", "company": MAIN,
             "status": "Active", "date_of_joining": "2025-03-01"},
            {"name": "HR-EMP-00003", "employee_name": "No Bank", "company": MAIN,
             "status": "Active", "date_of_joining": "2025-03-01"},
        ])

    def _seed_originator(self):
        STORE.seed("ACH Originator Configuration", [{
            "name": MAIN,
            "company": MAIN,
            "status": "Active",
            "bank_name": "First Bank",
            "originating_dfi": ROUTING_A,
            "immediate_destination": ROUTING_A,
            "immediate_destination_name": "FIRST BANK",
            "immediate_origin": "1931234567",
            "immediate_origin_name": "ORCHARD MEADOW LLC",
            "company_identification": "1931234567",
            "company_name": "ORCHARD MEADOW",
            "entry_description": "PAYROLL",
            "settlement_days": 2,
        }])

    def _add_account(self, employee="HR-EMP-00001", **overrides):
        args = {
            "employee": employee,
            "company": MAIN,
            "bank_name": "First Bank",
            "routing_number": ROUTING_A,
            "account_number": "12345678",
        }
        args.update(overrides)
        return self.tool_data("create_employee_bank_account", args)

    def _seed_payroll_entry(self, slips=None, status="Calculated"):
        slips = slips if slips is not None else [
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 1234.56},
            {"employee": "HR-EMP-00002", "employee_name": "Ana Ruiz", "net_pay": 500.00},
        ]
        STORE.seed("Farm Payroll Entry", [{
            "name": "PAY-2025-0001",
            "company": MAIN,
            "status": status,
            "pay_frequency": "Biweekly",
            "pay_period_start": "2025-08-01",
            "pay_period_end": "2025-08-15",
            "slips": slips,
        }])
        return "PAY-2025-0001"


# ── Claim 7: the bank account tools ───────────────────────────────────────


class BankAccountTools(ACHTestCase):
    def test_create_returns_masked_never_the_number(self):
        data = self._add_account()
        self.assertEqual(data["account_number_last_four"], "5678")
        self.assertEqual(data["account_number_masked"], "****5678")
        self.assertNotIn("account_number", data)

    def test_get_never_returns_the_number(self):
        created = self._add_account()
        data = self.tool_data("get_employee_bank_account", {"name": created["name"]})
        self.assertNotIn("account_number", data)
        self.assertEqual(data["account_number_masked"], "****5678")

    def test_list_never_returns_the_number(self):
        self._add_account()
        data = self.tool_data("list_employee_bank_accounts", {"company": MAIN})
        self.assertEqual(data["count"], 1)
        for row in data["accounts"]:
            self.assertNotIn("account_number", row)

    def test_bad_routing_refused(self):
        text = self.tool_error("create_employee_bank_account", {
            "employee": "HR-EMP-00001", "company": MAIN, "bank_name": "B",
            "routing_number": "123456789", "account_number": "1",
        })
        self.assertIn("check-digit", text)

    def test_short_routing_refused(self):
        text = self.tool_error("create_employee_bank_account", {
            "employee": "HR-EMP-00001", "company": MAIN, "bank_name": "B",
            "routing_number": "1234", "account_number": "1",
        })
        self.assertIn("9", text)

    def test_overlong_account_number_refused(self):
        text = self.tool_error("create_employee_bank_account", {
            "employee": "HR-EMP-00001", "company": MAIN, "bank_name": "B",
            "routing_number": ROUTING_A, "account_number": "1" * 18,
        })
        self.assertIn("17", text)

    def test_two_full_accounts_refused(self):
        self._add_account()
        text = self.tool_error("create_employee_bank_account", {
            "employee": "HR-EMP-00001", "company": MAIN, "bank_name": "B",
            "routing_number": ROUTING_B, "account_number": "999",
        })
        self.assertIn("already has a Full", text)

    def test_percentages_over_100_refused(self):
        self._add_account(allocation_type="Percentage", allocation_amount=60)
        text = self.tool_error("create_employee_bank_account", {
            "employee": "HR-EMP-00001", "company": MAIN, "bank_name": "B",
            "routing_number": ROUTING_B, "account_number": "999",
            "allocation_type": "Percentage", "allocation_amount": 50,
        })
        self.assertIn("110", text)

    def test_fixed_amount_needs_an_amount(self):
        text = self.tool_error("create_employee_bank_account", {
            "employee": "HR-EMP-00001", "company": MAIN, "bank_name": "B",
            "routing_number": ROUTING_A, "account_number": "1",
            "allocation_type": "Fixed Amount",
        })
        self.assertIn("above zero", text)

    def test_update_changes_bank_name(self):
        created = self._add_account()
        data = self.tool_data("update_employee_bank_account", {
            "name": created["name"], "bank_name": "Second Bank",
        })
        self.assertIn("bank_name", data["updated_fields"])
        self.assertEqual(data["bank_name"], "Second Bank")

    def test_changing_the_account_number_clears_the_prenote(self):
        created = self._add_account()
        STORE.rows("Employee Bank Account")[0]["prenote_sent"] = 1
        data = self.tool_data("update_employee_bank_account", {
            "name": created["name"], "account_number": "87654321",
        })
        self.assertEqual(int(data["prenote_sent"]), 0)
        self.assertEqual(data["account_number_last_four"], "4321")

    def test_update_of_an_unknown_account_refused(self):
        text = self.tool_error("update_employee_bank_account", {"name": "EBA-99999"})
        self.assertIn("no Employee Bank Account", text)

    def test_deactivating_frees_the_full_slot(self):
        first = self._add_account()
        self.tool_data("update_employee_bank_account", {
            "name": first["name"], "status": "Inactive",
        })
        second = self._add_account(routing_number=ROUTING_B, account_number="999")
        self.assertEqual(second["allocation_type"], "Full")


# ── Claim 8: the NACHA generator ──────────────────────────────────────────


class NachaFileTool(ACHTestCase):
    def test_generates_a_file_for_a_calculated_run(self):
        self._add_account("HR-EMP-00001")
        self._add_account("HR-EMP-00002", routing_number=ROUTING_B, account_number="998877")
        entry = self._seed_payroll_entry()
        data = self.tool_data("generate_nacha_file", {"payroll_entry": entry})
        self.assertEqual(data["entry_count"], 2)
        self.assertEqual(data["total_credit"], 1734.56)
        self.assertEqual(data["record_count"] % 10, 0)
        self.assertEqual(len(data["employees_paid"]), 2)

    def test_the_attachment_is_private(self):
        self._add_account("HR-EMP-00001")
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 100.0},
        ])
        data = self.tool_data("generate_nacha_file", {"payroll_entry": entry})
        self.assertTrue(data["attachment"]["is_private"])
        self.assertEqual(data["attached_to"]["doctype"], "Farm Payroll Entry")

    def test_a_draft_run_is_refused(self):
        self._add_account("HR-EMP-00001")
        entry = self._seed_payroll_entry(status="Draft")
        text = self.tool_error("generate_nacha_file", {"payroll_entry": entry})
        self.assertIn("Calculate the run first", text)

    def test_an_employee_with_no_account_is_skipped_not_fatal(self):
        self._add_account("HR-EMP-00001")
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 100.0},
            {"employee": "HR-EMP-00003", "employee_name": "No Bank", "net_pay": 90.0},
        ])
        data = self.tool_data("generate_nacha_file", {"payroll_entry": entry})
        self.assertEqual(data["entry_count"], 1)
        self.assertEqual(len(data["employees_skipped"]), 1)
        self.assertIn("cheque", data["employees_skipped"][0]["reason"])

    def test_an_unresolvable_allocation_refuses_the_whole_file(self):
        """The asymmetry: a wrong payment is worse than a missing one."""
        self._add_account("HR-EMP-00001", allocation_type="Fixed Amount",
                          allocation_amount=50)
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 900.0},
        ])
        text = self.tool_error("generate_nacha_file", {"payroll_entry": entry})
        self.assertIn("do not resolve", text)
        self.assertIn("Test Worker", text)

    def test_a_split_deposit_makes_two_entries(self):
        self._add_account("HR-EMP-00001", allocation_type="Fixed Amount",
                          allocation_amount=200, priority=1)
        self._add_account("HR-EMP-00001", routing_number=ROUTING_B,
                          account_number="998877", account_type="Savings", priority=2)
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 1000.0},
        ])
        data = self.tool_data("generate_nacha_file", {"payroll_entry": entry})
        self.assertEqual(data["entry_count"], 2)
        self.assertEqual(data["total_credit"], 1000.0)
        self.assertEqual(data["employees_paid"][0]["entries"], 2)

    def test_warns_about_a_never_prenoted_account(self):
        self._add_account("HR-EMP-00001")
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 100.0},
        ])
        data = self.tool_data("generate_nacha_file", {"payroll_entry": entry})
        self.assertTrue(any("never been prenoted" in w for w in data["warnings"]))

    def test_missing_originator_config_refuses_naming_the_company(self):
        # Deactivated rather than deleted, which is also the likelier real
        # shape: a company whose origination agreement has lapsed still has the
        # row. `_load_originator` filters on Active, so both read the same.
        for row in STORE.rows("ACH Originator Configuration"):
            row["status"] = "Inactive"
        self._add_account("HR-EMP-00001")
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 100.0},
        ])
        text = self.tool_error("generate_nacha_file", {"payroll_entry": entry})
        self.assertIn(MAIN, text)
        self.assertIn("ACH Originator Configuration", text)

    def test_nobody_payable_refuses(self):
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00003", "employee_name": "No Bank", "net_pay": 90.0},
        ])
        text = self.tool_error("generate_nacha_file", {"payroll_entry": entry})
        self.assertIn("paid by cheque", text)

    def test_unknown_payroll_entry_refused(self):
        text = self.tool_error("generate_nacha_file", {"payroll_entry": "PAY-NOPE"})
        self.assertIn("no Farm Payroll Entry", text)

    def test_the_file_content_parses_back_to_94_char_records(self):
        self._add_account("HR-EMP-00001")
        entry = self._seed_payroll_entry(slips=[
            {"employee": "HR-EMP-00001", "employee_name": "Test Worker", "net_pay": 100.0},
        ])
        self.tool_data("generate_nacha_file", {"payroll_entry": entry})
        content = list(STORE.file_contents.values())[-1]
        text = content.decode("ascii") if isinstance(content, bytes) else content
        lines = [line for line in text.split("\n") if line]
        self.assertTrue(lines)
        for line in lines:
            self.assertEqual(len(line), 94)
        self.assertEqual(lines[0][0], "1")
        self.assertEqual(lines[-1], "9" * 94)


# ── Claim 9: prenotes ─────────────────────────────────────────────────────


class PrenoteTool(ACHTestCase):
    def test_generates_zero_dollar_entries(self):
        self._add_account("HR-EMP-00001")
        data = self.tool_data("generate_prenote_file", {"company": MAIN})
        self.assertEqual(data["entry_count"], 1)
        self.assertEqual(data["total_credit"], 0.0)

    def test_checking_prenote_uses_code_23(self):
        self._add_account("HR-EMP-00001")
        self.tool_data("generate_prenote_file", {"company": MAIN})
        content = list(STORE.file_contents.values())[-1]
        text = content.decode("ascii") if isinstance(content, bytes) else content
        entry = [line for line in text.split("\n") if line.startswith("6")][0]
        self.assertEqual(entry[1:3], "23")
        self.assertEqual(entry[29:39], "0000000000")

    def test_savings_prenote_uses_code_33(self):
        self._add_account("HR-EMP-00001", account_type="Savings")
        self.tool_data("generate_prenote_file", {"company": MAIN})
        content = list(STORE.file_contents.values())[-1]
        text = content.decode("ascii") if isinstance(content, bytes) else content
        entry = [line for line in text.split("\n") if line.startswith("6")][0]
        self.assertEqual(entry[1:3], "33")

    def test_marks_the_accounts_sent(self):
        created = self._add_account("HR-EMP-00001")
        data = self.tool_data("generate_prenote_file", {"company": MAIN})
        self.assertIn(created["name"], data["marked_sent"])
        row = self.tool_data("get_employee_bank_account", {"name": created["name"]})
        self.assertEqual(int(row["prenote_sent"]), 1)

    def test_already_prenoted_accounts_are_not_resent(self):
        self._add_account("HR-EMP-00001")
        self.tool_data("generate_prenote_file", {"company": MAIN})
        text = self.tool_error("generate_prenote_file", {"company": MAIN})
        self.assertIn("awaiting a prenote", text)

    def test_resend_includes_them(self):
        self._add_account("HR-EMP-00001")
        self.tool_data("generate_prenote_file", {"company": MAIN})
        data = self.tool_data("generate_prenote_file", {"company": MAIN, "resend": True})
        self.assertEqual(data["entry_count"], 1)

    def test_a_nonzero_amount_in_a_prenote_is_refused(self):
        """The builder's own guard, reached directly."""
        with self.assertRaises(nacha.NachaError):
            nacha.build_file(
                ORIGINATOR, [_entry(amount=5.0)], "250815", "0930", "250817", prenote=True,
            )

    def test_the_accounts_reported_are_masked(self):
        self._add_account("HR-EMP-00001")
        data = self.tool_data("generate_prenote_file", {"company": MAIN})
        for row in data["accounts"]:
            self.assertNotIn("account_number", row)
            self.assertEqual(row["account_number_last_four"], "5678")


# ── Claim 10: the handset reaches the caller's own account and nobody else's ─


class MobileBankAccounts(MobileAPITestCase):
    """The three mobile routes, and the boundary that makes them safe.

    THIS IS THE CLAIM THE MOBILE HALF TURNS ON. Every other write on that
    transport that names a person takes an Employee docname from the body and
    checks it against the caller's COMPANY scope — correct for onboarding, where
    a foreman acts on somebody else's record deliberately. It is wrong for a bank
    account: company scope is shared by everybody enrolled, so an `employee`
    argument checked that way would let any picker with a handset repoint a
    colleague's wages into their own account.

    So these three take no `employee` argument at all, and the tests below prove
    both halves — that the argument is ABSENT FROM THE SIGNATURE, which is what
    `routes.bind` filters on, and that a docname belonging to somebody else is
    refused when one is passed anyway.
    """

    def setUp(self):
        super().setUp()
        self.configure(
            enabled=1,
            public_url="https://umbrel.tail4a2b.ts.net",
            **{**MOBILE_ON, **ACH_TOOLS_ON},
        )
        # The base fixture enrols only the worker. The colleague has to be
        # enrolled too, because the interesting test is one ENROLLED account
        # failing to reach another's bank details — an unenrolled caller is
        # stopped by the credential gate long before the ownership check, and
        # would prove nothing about the ownership check.
        self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[OTHER])

    def _add(self, user, routing=ROUTING_A, account="12345678", bank="First Bank"):
        self.be(user)
        return mobile_api.add_my_bank_account(
            bank_name=bank, routing_number=routing, account_number=account,
        )

    def test_the_endpoints_do_not_accept_an_employee_argument(self):
        """Absent from the signature, so `routes.bind` drops it at the transport."""
        for handler in (
            mobile_api.list_my_bank_accounts,
            mobile_api.add_my_bank_account,
            mobile_api.update_my_bank_account,
        ):
            accepted = routes.accepted_arguments(handler)
            self.assertNotIn("employee", accepted, handler.farm_ops_method)
            self.assertNotIn("company", accepted, handler.farm_ops_method)

    def test_create_does_not_accept_a_status(self):
        """A phone that could set an account Inactive could switch one off."""
        self.assertNotIn("status", routes.accepted_arguments(mobile_api.add_my_bank_account))

    def test_a_worker_adds_their_own_account(self):
        data = self._add(WORKER)
        self.assertEqual(data["employee"], WORKER_EMPLOYEE)
        self.assertEqual(data["account_number_last_four"], "5678")

    def test_the_full_number_never_comes_back_to_the_handset(self):
        created = self._add(WORKER)
        self.assertNotIn("account_number", created)
        listed = mobile_api.list_my_bank_accounts()
        for row in listed["accounts"]:
            self.assertNotIn("account_number", row)
            self.assertEqual(row["account_number_masked"], "****5678")

    def test_the_list_is_only_the_callers_own(self):
        self._add(WORKER, account="11112222")
        self._add(OUTSIDER, routing=ROUTING_B, account="33334444")
        self.be(WORKER)
        listed = mobile_api.list_my_bank_accounts()
        self.assertEqual(listed["employee"], WORKER_EMPLOYEE)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["accounts"][0]["account_number_last_four"], "2222")

    def test_a_worker_cannot_update_a_colleagues_account(self):
        """The one that would redirect somebody else's wages."""
        victim = self._add(OUTSIDER, routing=ROUTING_B, account="33334444")
        self.be(WORKER)
        with self.assertRaises(Exception) as caught:
            mobile_api.update_my_bank_account(
                name=victim["name"], routing_number=ROUTING_A, account_number="99998888",
            )
        self.assertIn("belongs to you", str(caught.exception))

    def test_the_colleagues_account_is_untouched_after_the_refusal(self):
        """Read off the store rather than through a second call.

        The refusal path in `guard.endpoint` rolls the request back, and in this
        double a rollback discards rows inserted earlier in the same test — so
        asking the API again would be measuring the double's transaction
        emulation rather than whether the row changed. The store is the thing
        the claim is actually about.
        """
        victim = self._add(OUTSIDER, routing=ROUTING_B, account="33334444")
        before = [
            dict(r) for r in STORE.rows("Employee Bank Account")
            if r.get("name") == victim["name"]
        ]
        self.assertEqual(len(before), 1)
        self.be(WORKER)
        with self.assertRaises(Exception):
            mobile_api.update_my_bank_account(name=victim["name"], account_number="99998888")
        after = [
            dict(r) for r in STORE.rows("Employee Bank Account")
            if r.get("name") == victim["name"]
        ]
        if after:
            self.assertEqual(after[0]["account_number_last_four"], "4444")
            self.assertEqual(after[0]["routing_number"], ROUTING_B)

    def test_a_refusal_reads_the_same_as_a_row_that_does_not_exist(self):
        """So the register cannot be mapped by watching which error comes back."""
        victim = self._add(OUTSIDER, routing=ROUTING_B, account="33334444")
        self.be(WORKER)
        with self.assertRaises(Exception) as theirs:
            mobile_api.update_my_bank_account(name=victim["name"], bank_name="X")
        with self.assertRaises(Exception) as absent:
            mobile_api.update_my_bank_account(name="EBA-99999", bank_name="X")
        self.assertEqual(
            str(theirs.exception).replace(victim["name"], "EBA-99999"),
            str(absent.exception),
        )

    def test_a_worker_updates_their_own(self):
        created = self._add(WORKER)
        data = mobile_api.update_my_bank_account(
            name=created["name"], bank_name="Second Bank",
        )
        self.assertEqual(data["bank_name"], "Second Bank")

    def test_a_bad_routing_number_is_refused_on_the_handset_too(self):
        self.be(WORKER)
        with self.assertRaises(Exception) as caught:
            mobile_api.add_my_bank_account(
                bank_name="First Bank", routing_number="123456789",
                account_number="12345678",
            )
        self.assertIn("check-digit", str(caught.exception))

    def test_the_three_routes_are_on_the_table(self):
        paths = {r.path: r.mutating for r in routes.ROUTES}
        self.assertIs(paths.get("/mobile/list_my_bank_accounts"), False)
        self.assertIs(paths.get("/mobile/add_my_bank_account"), True)
        self.assertIs(paths.get("/mobile/update_my_bank_account"), True)


if __name__ == "__main__":
    unittest.main()
