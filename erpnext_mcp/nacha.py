# SPDX-License-Identifier: MIT
"""NACHA ACH file construction — PURE FUNCTIONS.

No database reads, no side effects, fully testable with deterministic inputs.
Same contract as `state_withholding.py`, and for a sharper reason: the thing
this module produces is handed to a bank, and the bank's parser is a fixed-width
column reader written decades ago. It does not negotiate.

────────────────────────────────────────────────────────────────────────────
WHY EVERY FIELD IS A SLICE AND NOT A JOIN
────────────────────────────────────────────────────────────────────────────

A NACHA record is 94 characters. Not 94 fields separated by something — 94
character positions, each belonging to exactly one field, every one of them
occupied. An amount is ten digits with leading zeros; a name is twenty-two
characters with trailing spaces. There is no delimiter to get wrong and no
delimiter to save you: a field written one character short does not produce a
short field, it shifts every field after it and the file still looks like a file.

So the records here are built by concatenating fixed-width pieces, and
`_pack` asserts the total is 94 before returning it. That assertion is the
whole safety net. A typo in a width is otherwise invisible until a bank rejects
a payroll — or worse, does not reject it.

────────────────────────────────────────────────────────────────────────────
THE FIVE RECORD TYPES
────────────────────────────────────────────────────────────────────────────

    1  File Header      one per file
    5  Batch Header     one per batch (this app writes one batch per company)
    6  Entry Detail     one per payment — NOT one per employee, see below
    8  Batch Control    one per batch, totals and hash
    9  File Control     one per file, totals of the totals

and then the file is padded with all-9 records until the record count is a
multiple of ten, because ACH files are transmitted in blocks of ten. The padding
is not optional and it is not decorative — a file whose final block is short is
malformed.

ONE ENTRY PER PAYMENT, NOT PER EMPLOYEE. A worker who splits their cheque
between checking and savings produces two Entry Detail records with two
different account numbers and two different amounts that sum to their net pay.
The batch's credit total is the sum of the entries, so a split that does not add
up to net pay is a file that is internally consistent and still wrong — which is
why the allocation arithmetic happens in the caller, against the slip, and this
module only ever formats what it is given.

────────────────────────────────────────────────────────────────────────────
THE ENTRY HASH IS NOT A CHECKSUM OF THE FILE
────────────────────────────────────────────────────────────────────────────

It is the sum of the EIGHT-DIGIT routing prefixes of every entry — the check
digit is deliberately excluded — truncated to its rightmost ten digits. Not
wrapped, not modulo'd in the arithmetic sense anybody would guess: summed as a
full-width integer and then the low ten digits kept. A file with enough entries
to overflow ten digits keeps the bottom ten and discards the top, which is the
specified behaviour and looks exactly like a bug if you meet it without warning.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

#: Every record in a NACHA file is exactly this wide.
RECORD_LENGTH = 94

#: Records are transmitted in blocks of ten. A file whose record count is not a
#: multiple of ten is padded with all-9 records until it is.
BLOCKING_FACTOR = 10

#: Record type codes, in the order they appear in a file.
FILE_HEADER = "1"
BATCH_HEADER = "5"
ENTRY_DETAIL = "6"
BATCH_CONTROL = "8"
FILE_CONTROL = "9"

#: Service class: 200 mixed debits and credits, 220 credits only, 225 debits
#: only. Payroll direct deposit moves money one way, so it is 220 — and a batch
#: that declared 200 with no debits in it is a batch some ODFIs reject.
SERVICE_CLASS_CREDITS_ONLY = "220"

#: Standard Entry Class. PPD is Prearranged Payment and Deposit — a credit to a
#: consumer account under a standing authorisation, which is what a direct
#: deposit authorisation form is. (CCD is the business-account equivalent; a
#: payroll file that used it would be miscategorising an employee as a company.)
STANDARD_ENTRY_CLASS = "PPD"

#: Transaction codes, by (account_type, is_prenote). A prenote is a zero-dollar
#: entry that asks the receiving bank to confirm the account exists before real
#: money is sent, and it gets its OWN code — a $0.00 entry with an ordinary
#: credit code is not a prenote, it is a payment of nothing.
TRANSACTION_CODES = {
    ("Checking", False): "22",
    ("Checking", True): "23",
    ("Savings", False): "32",
    ("Savings", True): "33",
}

#: NACHA's character set is upper-case alphanumeric plus a short punctuation
#: list. Anything else is replaced rather than dropped, so a name keeps its
#: length and its shape: "José Muñoz" becomes "JOSE MUNOZ" where the accents
#: decompose and "JOS  MU OZ" where they do not — both readable, neither shifting
#: a column. Dropping characters instead would silently re-align the field.
_ALLOWED = re.compile(r"[^A-Z0-9 .,()&'\-/]")

#: Latin-1 letters that have an obvious ASCII fold. Not a general transliteration
#: table — just the ones that turn up in the names this app actually carries.
_FOLD = str.maketrans({
    "Á": "A", "À": "A", "Â": "A", "Ä": "A", "Ã": "A", "Å": "A",
    "É": "E", "È": "E", "Ê": "E", "Ë": "E",
    "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
    "Ó": "O", "Ò": "O", "Ô": "O", "Ö": "O", "Õ": "O",
    "Ú": "U", "Ù": "U", "Û": "U", "Ü": "U",
    "Ñ": "N", "Ç": "C", "Ý": "Y",
})


class NachaError(ValueError):
    """A file that cannot be built correctly. Raised instead of writing one wrong."""


# ── field formatting ───────────────────────────────────────────────────────


def alphanumeric(value, width: int) -> str:
    """Left-justified, space-padded, upper-cased, truncated to `width`.

    Truncation is silent by design at this level — a 30-character name in a
    22-character field has to become 22 characters somehow, and NACHA's answer is
    to keep the front. Callers that care (the individual name field does) warn
    about it before getting here.
    """
    text = "" if value is None else str(value)
    text = text.upper().translate(_FOLD)
    text = _ALLOWED.sub(" ", text)
    return text[:width].ljust(width)


def numeric(value, width: int) -> str:
    """Right-justified, zero-padded, truncated from the LEFT to `width`.

    Left truncation is the specified behaviour for an overlong numeric field and
    it is genuinely dangerous — an amount too wide for its field loses its most
    significant digits rather than its least. `amount_cents` refuses instead of
    relying on this, and this function keeps the behaviour only for fields whose
    width no realistic value can exceed.
    """
    digits = re.sub(r"\D", "", "" if value is None else str(value))
    return digits[-width:].rjust(width, "0") if digits else "0" * width


def amount_cents(dollars, width: int = 10) -> str:
    """A dollar amount as zero-padded cents. Refuses to truncate.

    An amount that does not fit is the one field where silent truncation would
    move money: $1,234,567.89 in a 10-digit field keeps its low ten digits and
    pays out a different number. So this raises, and the caller names the
    employee.

    HALF A CENT ROUNDS UP, NOT TO EVEN. Python's `round` is banker's rounding —
    `round(0.5)` is 0 — which is the right default for statistics and the wrong
    one for wages: it is unfamiliar to anybody checking the arithmetic by hand,
    and it makes the cent land differently depending on whether the figure before
    it happened to be odd. Decimal with ROUND_HALF_UP is the payroll convention
    and it is what the split allocations are reconciled against.
    """
    cents = int(
        Decimal(str(float(dollars))).scaleb(2).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    if cents < 0:
        raise NachaError(f"a negative amount ({dollars}) cannot be written as an ACH credit.")
    text = str(cents)
    if len(text) > width:
        raise NachaError(
            f"amount {dollars} is {len(text)} digits in cents and the field is {width}. "
            "Writing it would silently drop the leading digits and pay a different amount."
        )
    return text.rjust(width, "0")


def ten_char_id(value) -> str:
    """The ten-character Immediate Origin / Company Identification field.

    Nine digits are read as a routing number and get the conventional leading
    blank; anything else is taken as the identifier the bank issued and is
    left-justified. The two conventions look identical in the file and mean
    different things to the ODFI, which is exactly why neither is inferred from
    the other.
    """
    text = str(value or "").strip().upper()
    digits = re.sub(r"\D", "", text)
    if len(text) == len(digits) and len(digits) == 9:
        return digits.rjust(10)
    return alphanumeric(text, 10)


def round_money(value) -> float:
    """Two decimal places, half a cent rounding UP. See `amount_cents`.

    Exported because the allocation arithmetic in `tools/ach.py` has to agree
    with this module to the cent — the amounts it computes are the amounts this
    module writes, and two different rounding rules across that boundary would
    put the odd cent in one place and report it in another.
    """
    return float(Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pack(*pieces: str) -> str:
    """Join fixed-width pieces and assert the record is exactly 94 characters."""
    record = "".join(pieces)
    if len(record) != RECORD_LENGTH:
        raise NachaError(
            f"built a {len(record)}-character record; every NACHA record is exactly "
            f"{RECORD_LENGTH}. This is a field-width bug in the record builder, not bad input."
        )
    return record


# ── routing numbers ────────────────────────────────────────────────────────


def normalize_routing(routing) -> str:
    """Nine digits, or a NachaError explaining which rule was broken."""
    digits = re.sub(r"\D", "", "" if routing is None else str(routing))
    if not digits:
        raise NachaError("a routing number is required.")
    if len(digits) != 9:
        raise NachaError(
            f"routing number {routing!r} has {len(digits)} digits; an ABA routing number has 9."
        )
    if not routing_checksum_ok(digits):
        raise NachaError(
            f"routing number {digits} fails the ABA check-digit test, so it is not a real "
            "routing number — most likely two digits are transposed."
        )
    return digits


def routing_checksum_ok(digits: str) -> bool:
    """The ABA check digit: 3-7-1 weights across nine digits, sum divisible by 10.

    Catches every single-digit error and every transposition of adjacent digits,
    which between them are almost all the ways somebody mistypes one off a cheque.
    """
    if len(digits) != 9 or not digits.isdigit():
        return False
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    total = sum(int(d) * w for d, w in zip(digits, weights))
    return total % 10 == 0


def routing_check_digit(digits: str) -> str:
    """The ninth digit — carried in its own one-character field on entry records."""
    return digits[8]


def routing_prefix(digits: str) -> str:
    """The first eight digits. What the entry hash sums and what trace numbers use."""
    return digits[:8]


# ── record builders ────────────────────────────────────────────────────────


def file_header_record(
    immediate_destination: str,
    immediate_origin: str,
    file_creation_date: str,
    file_creation_time: str,
    destination_name: str,
    origin_name: str,
    file_id_modifier: str = "A",
) -> str:
    """Record type 1. One per file.

    `immediate_destination` is ten characters and the leading position is a blank
    rather than a digit — a nine-digit routing number sits in positions 2-10 of
    its field. Right-justifying the digits into ten characters produces exactly
    that, which is why this looks like it is ignoring the blank.

    `immediate_origin` is ten characters too and is NOT necessarily a routing
    number: many ODFIs want the originator's ten-character company identification
    there — a "1" followed by the nine-digit EIN — while others want the routing
    number of the originating bank. Which one is a fact about the bank agreement,
    so it is passed through as given rather than validated into one shape.
    """
    return _pack(
        FILE_HEADER,                                   # 1
        "01",                                          # 2-3    priority code
        numeric(immediate_destination, 9).rjust(10),   # 4-13
        ten_char_id(immediate_origin),                 # 14-23
        numeric(file_creation_date, 6),                # 24-29  YYMMDD
        numeric(file_creation_time, 4),                # 30-33  HHMM
        alphanumeric(file_id_modifier or "A", 1),      # 34
        "094",                                         # 35-37  record size
        "10",                                          # 38-39  blocking factor
        "1",                                           # 40     format code
        alphanumeric(destination_name, 23),            # 41-63
        alphanumeric(origin_name, 23),                 # 64-86
        " " * 8,                                       # 87-94  reference code
    )


def batch_header_record(
    company_name: str,
    company_identification: str,
    entry_description: str,
    effective_entry_date: str,
    originating_dfi: str,
    batch_number: int,
    descriptive_date: str = "",
    discretionary_data: str = "",
    service_class: str = SERVICE_CLASS_CREDITS_ONLY,
) -> str:
    """Record type 5. One per batch.

    `effective_entry_date` is the day the money should land, and it is the only
    date in the file the receiving bank acts on. Settlement date (76-78) is left
    blank on purpose: the ACH operator fills it in, and a value there from the
    originator is ignored at best.
    """
    return _pack(
        BATCH_HEADER,                                  # 1
        service_class,                                 # 2-4
        alphanumeric(company_name, 16),                # 5-20
        alphanumeric(discretionary_data, 20),          # 21-40
        alphanumeric(company_identification, 10),      # 41-50
        STANDARD_ENTRY_CLASS,                          # 51-53
        alphanumeric(entry_description, 10),           # 54-63
        alphanumeric(descriptive_date, 6),             # 64-69
        numeric(effective_entry_date, 6),              # 70-75
        " " * 3,                                       # 76-78  settlement date
        "1",                                           # 79     originator status
        numeric(originating_dfi, 8),                   # 80-87
        numeric(batch_number, 7),                      # 88-94
    )


def entry_detail_record(
    transaction_code: str,
    receiving_routing: str,
    account_number: str,
    amount: float,
    individual_id: str,
    individual_name: str,
    trace_prefix: str,
    trace_sequence: int,
    discretionary_data: str = "",
    addenda_indicator: str = "0",
) -> str:
    """Record type 6. One per payment.

    The routing number is split across two fields — eight digits in 4-11 and the
    check digit alone in 12 — which is not a formatting quirk. Position 12 is
    what the receiving bank validates first, and the entry hash in the batch
    control deliberately sums only the eight.

    `individual_id` is the employee number, not a Social Security number. It
    travels unencrypted through the ACH network and is printed on the receiving
    bank's statement.
    """
    routing = normalize_routing(receiving_routing)
    return _pack(
        ENTRY_DETAIL,                                  # 1
        transaction_code,                              # 2-3
        routing_prefix(routing),                       # 4-11
        routing_check_digit(routing),                  # 12
        alphanumeric(account_number, 17),              # 13-29
        amount_cents(amount, 10),                      # 30-39
        alphanumeric(individual_id, 15),               # 40-54
        alphanumeric(individual_name, 22),             # 55-76
        alphanumeric(discretionary_data, 2),           # 77-78
        alphanumeric(addenda_indicator, 1),            # 79
        numeric(trace_prefix, 8) + numeric(trace_sequence, 7),  # 80-94
    )


def batch_control_record(
    entry_count: int,
    entry_hash: int,
    total_debit: float,
    total_credit: float,
    company_identification: str,
    originating_dfi: str,
    batch_number: int,
    service_class: str = SERVICE_CLASS_CREDITS_ONLY,
) -> str:
    """Record type 8. The batch's own totals, which the RDFI re-computes and compares."""
    return _pack(
        BATCH_CONTROL,                                 # 1
        service_class,                                 # 2-4
        numeric(entry_count, 6),                       # 5-10
        numeric(truncate_hash(entry_hash), 10),        # 11-20
        amount_cents(total_debit, 12),                 # 21-32
        amount_cents(total_credit, 12),                # 33-44
        alphanumeric(company_identification, 10),      # 45-54
        " " * 19,                                      # 55-73  message auth code
        " " * 6,                                       # 74-79  reserved
        numeric(originating_dfi, 8),                   # 80-87
        numeric(batch_number, 7),                      # 88-94
    )


def file_control_record(
    batch_count: int,
    block_count: int,
    entry_count: int,
    entry_hash: int,
    total_debit: float,
    total_credit: float,
) -> str:
    """Record type 9. Totals of the batch totals, and the block count."""
    return _pack(
        FILE_CONTROL,                                  # 1
        numeric(batch_count, 6),                       # 2-7
        numeric(block_count, 6),                       # 8-13
        numeric(entry_count, 8),                       # 14-21
        numeric(truncate_hash(entry_hash), 10),        # 22-31
        amount_cents(total_debit, 12),                 # 32-43
        amount_cents(total_credit, 12),                # 44-55
        " " * 39,                                      # 56-94  reserved
    )


def padding_record() -> str:
    """An all-9 record. Written until the file is a whole number of blocks."""
    return "9" * RECORD_LENGTH


# ── totals ─────────────────────────────────────────────────────────────────


def truncate_hash(total: int) -> str:
    """The rightmost ten digits of the entry hash sum. See the module docstring."""
    return str(int(total))[-10:]


def block_count(record_count: int) -> int:
    """Blocks of ten, rounded up."""
    return -(-record_count // BLOCKING_FACTOR)


def pad_to_block(records: list[str]) -> list[str]:
    """Append all-9 records until the count is a multiple of ten."""
    remainder = len(records) % BLOCKING_FACTOR
    if remainder:
        records = records + [padding_record()] * (BLOCKING_FACTOR - remainder)
    return records


# ── the whole file ─────────────────────────────────────────────────────────


def build_file(
    originator: dict,
    entries: list[dict],
    file_creation_date: str,
    file_creation_time: str,
    effective_entry_date: str,
    entry_description: str = "PAYROLL",
    file_id_modifier: str = "A",
    descriptive_date: str = "",
    batch_number: int = 1,
    prenote: bool = False,
) -> dict:
    """Assemble one single-batch NACHA file and return it with its own totals.

    `entries` are dicts of routing_number, account_number, account_type, amount,
    individual_id, individual_name. `originator` carries the company's bank
    identity — see `ach_originator` in `tools/ach.py` for where it comes from.

    Returns the file content plus the counts and totals a caller needs to report
    and to reconcile against, rather than making the caller re-parse what it just
    built.

    A PRENOTE FILE IS THIS FILE WITH DIFFERENT TRANSACTION CODES AND ZERO
    AMOUNTS, which is why it is a flag rather than a second function: everything
    that could be wrong about the header, the hash, the blocking and the trace
    numbers is wrong in both, and one code path means one set of tests proves
    both. The zero is asserted below rather than assumed.
    """
    if not entries:
        raise NachaError("an ACH file needs at least one entry. Nothing was built.")

    odfi = normalize_routing(originator["originating_dfi"])
    destination = normalize_routing(originator["immediate_destination"])
    # Blank falls back to the company identification rather than to the routing
    # number: it is the more common of the two conventions and the one an
    # operator who left the field empty is likelier to have meant.
    origin = (
        str(originator.get("immediate_origin") or "").strip()
        or originator["company_identification"]
    )

    records = [
        file_header_record(
            immediate_destination=destination,
            immediate_origin=origin,
            file_creation_date=file_creation_date,
            file_creation_time=file_creation_time,
            destination_name=originator.get("immediate_destination_name", ""),
            origin_name=originator.get("immediate_origin_name", ""),
            file_id_modifier=file_id_modifier,
        ),
        batch_header_record(
            company_name=originator.get("company_name", ""),
            company_identification=originator["company_identification"],
            entry_description=entry_description,
            effective_entry_date=effective_entry_date,
            originating_dfi=routing_prefix(odfi),
            batch_number=batch_number,
            descriptive_date=descriptive_date,
        ),
    ]

    hash_total = 0
    credit_total = 0.0
    prefix = routing_prefix(odfi)

    for sequence, entry in enumerate(entries, start=1):
        routing = normalize_routing(entry["routing_number"])
        account_type = str(entry.get("account_type") or "Checking").strip().title()
        code = TRANSACTION_CODES.get((account_type, bool(prenote)))
        if not code:
            raise NachaError(
                f"account_type {entry.get('account_type')!r} is neither Checking nor Savings, "
                "so there is no ACH transaction code for it. Nothing was built."
            )
        amount = 0.0 if prenote else float(entry.get("amount") or 0)
        if prenote and float(entry.get("amount") or 0):
            raise NachaError("a prenote entry carries no money; its amount must be zero.")

        records.append(entry_detail_record(
            transaction_code=code,
            receiving_routing=routing,
            account_number=entry.get("account_number", ""),
            amount=amount,
            individual_id=entry.get("individual_id", ""),
            individual_name=entry.get("individual_name", ""),
            trace_prefix=prefix,
            trace_sequence=sequence,
        ))
        hash_total += int(routing_prefix(routing))
        credit_total += amount

    entry_count = len(entries)
    credit_total = round(credit_total, 2)

    records.append(batch_control_record(
        entry_count=entry_count,
        entry_hash=hash_total,
        total_debit=0,
        total_credit=credit_total,
        company_identification=originator["company_identification"],
        originating_dfi=prefix,
        batch_number=batch_number,
    ))

    # THE BLOCK COUNT COUNTS THE PADDING IT IS ABOUT TO CAUSE. The file control
    # record is itself a record, so the count it reports has to include the
    # header, the batch, every entry, both controls AND the 9-records appended
    # after it — which is why the arithmetic happens before the record is built
    # and the padding is applied after.
    total_records = len(records) + 1
    blocks = block_count(total_records)

    records.append(file_control_record(
        batch_count=1,
        block_count=blocks,
        entry_count=entry_count,
        entry_hash=hash_total,
        total_debit=0,
        total_credit=credit_total,
    ))
    records = pad_to_block(records)

    return {
        "content": "\n".join(records) + "\n",
        "records": records,
        "record_count": len(records),
        "entry_count": entry_count,
        "batch_count": 1,
        "block_count": blocks,
        "entry_hash": truncate_hash(hash_total),
        "total_credit": credit_total,
        "total_debit": 0.0,
        "prenote": bool(prenote),
    }
