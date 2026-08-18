# SPDX-License-Identifier: MIT
"""The mock recall, in both directions — v0.93.0.

WHAT IS ACTUALLY BEING TESTED is a join, and a join is only worth what its
weakest hop is worth. Every class below is one hop or one break:

    THE WALK JOINS            a shipment id on a bucket capture reaches the
                              block, the block reaches the spray, and the spray
                              carries its EPA registration number. Five
                              registers, four joins, one answer.

    THE DATE BOUND HOLDS      a forward trace from a spray takes the fruit
                              picked AFTER it and not the fruit picked before.
                              This is the difference between a recall somebody
                              can act on and one that names three seasons.

    THE BREAKS ARE NAMED      a capture with no bin id is fruit this system
                              cannot place. `unlinked_counts` says how many, per
                              column, which is what turns "our traceability is
                              fine" into a number an auditor can argue with.

    A REFERENCE TO NOTHING    `shipment_id` is free text and `Trade Shipment` is
    IS A FINDING              a register with its own names. An id matching no
                              shipment is reported, never dropped — the silent
                              version is how a chain looks complete and is not.

`NobodyToTelephone` IS THE ONE TO READ IF YOU ONLY READ ONE. A forward trace
that reaches no customer must say so as a BREAK rather than return an empty
list, because an empty `customers_to_notify` and a complete `customers_to_notify`
look identical to anybody skimming, and one of them means the recall cannot be
executed at all.
"""

from .fixtures import MAIN, V12TestCase, seed_masters, seed_stock
from .harness import STORE

from erpnext_mcp import compliance_fields

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"trace_backward",
		"trace_forward",
		"create_parcel",
		"create_field",
		"create_irrigation_zone",
		"create_spray_application",
		"create_water_test",
		"list_bucket_entries",
	)
}

BLOCK = "Yellow Camp Block 3 - MC"
OTHER_BLOCK = "Yellow Camp Block 4 - MC"
CAPTAN = "CAPTAN-80WDG"

#: The harness's "today". Every date here is placed relative to it.
TODAY = "2026-07-24"


class TraceTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		seed_masters()
		seed_stock()
		self.configure(enabled=1, **ALL_ON)
		compliance_fields.install_compliance_fields()
		STORE.seed("UOM", [{"name": "Lb", "enabled": 1}])
		STORE.seed(
			"Item",
			[
				{
					"name": CAPTAN,
					"item_code": CAPTAN,
					"item_name": "Captan 80 WDG",
					"stock_uom": "Lb",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [],
					"reorder_levels": [],
					"rei_hours": 24,
					"phi_days": 14,
				}
			],
		)
		self.tool_data(
			"create_parcel",
			{"owning_entity": MAIN, "parcel_name": "Mill Creek", "acreage": 131.43},
		)
		for name in ("Yellow Camp Block 3", "Yellow Camp Block 4"):
			self.tool_data(
				"create_field",
				{"parcel": "Mill Creek", "field_name": name, "acreage": 12.5, "crop": "Cherry"},
			)

	# ── fixtures ────────────────────────────────────────────────────────────
	def a_capture(self, index=1, **overrides):
		payload = {
			"name": f"BLE-2026-{index:04d}",
			"entry_uuid": f"uuid-{index:04d}",
			"session_uuid": "S-1",
			"company": MAIN,
			"worker_badge": "QR-0001",
			"employee": "HR-EMP-0001",
			"timestamp": f"2026-06-{index:02d} 08:00:00",
			"verdict": "Accepted",
			"container_type": "bucket",
			"crew_id": "CREW-A",
			"block_id": BLOCK,
			"bin_id": "BIN-17",
			"shipment_id": "TSHIP-2026-0001",
		}
		payload.update(overrides)
		STORE.seed("Bucket Log Entry", [payload])
		return payload["name"]

	def a_spray(self, blocks=(BLOCK,), **overrides):
		payload = {
			"company": MAIN,
			"blocks": [{"block": block, "acres": 12.5} for block in blocks],
			"products": [
				{
					"item_code": CAPTAN,
					"rate_per_acre": 5,
					"rate_uom": "Lb",
					"epa_reg_number": "66222-242",
				}
			],
			"applicator_license": "OR-PA-88213",
			"started_at": "2026-05-20 07:00:00",
			"completed_at": "2026-05-20 11:30:00",
		}
		payload.update(overrides)
		return self.tool_data("create_spray_application", payload)

	def a_shipment(self, name="TSHIP-2026-0001", **overrides):
		payload = {
			"name": name,
			"status": "Delivered",
			"ship_date": "2026-06-10",
			"company": MAIN,
			"customer": "Northwest Packing Co",
			"customer_name": "Northwest Packing Co",
			"commodity": "Cherries",
			"destination_tier": "Domestic",
			"destination_name": "Yakima DC",
			"destination_state": "WA",
			"carrier": "Cold Chain Freight",
			"documents": [],
		}
		payload.update(overrides)
		STORE.seed("Trade Shipment", [payload])
		return name

	def a_ticket(self, name="ST-2026-0001", **overrides):
		payload = {
			"name": name,
			"ticket_number": "T-9911",
			"date": "2026-06-05",
			"company": MAIN,
			"customer": "Northwest Packing Co",
			"field": BLOCK,
			"block": BLOCK,
			"variety": "Bing",
			"grade": "Fancy",
			"net_weight": 18400,
			"weight_uom": "Lb",
			"status": "Matched",
			"truck_id": "TRK-4",
		}
		payload.update(overrides)
		STORE.seed("Scale Ticket", [payload])
		return name


# ── the walk joins ──────────────────────────────────────────────────────────
class OneLotBackToTheGround(TraceTestCase):
	def test_a_shipment_reaches_the_blocks_the_crews_and_the_pickers(self):
		self.a_capture(1)
		self.a_capture(2, employee="HR-EMP-0002", crew_id="CREW-B")
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		self.assertEqual(data["bucket_count"], 2)
		self.assertEqual(data["blocks"], [BLOCK])
		self.assertEqual(sorted(data["crews"]), ["CREW-A", "CREW-B"])
		self.assertEqual(sorted(data["pickers"]), ["HR-EMP-0001", "HR-EMP-0002"])
		self.assertEqual(data["harvest_days"], ["2026-06-01", "2026-06-02"])

	def test_a_bin_reaches_the_same_ground(self):
		self.a_capture(1)
		data = self.tool_data("trace_backward", {"bin": "BIN-17"})
		self.assertEqual(data["anchor"], {"kind": "bin", "id": "BIN-17"})
		self.assertEqual(data["blocks"], [BLOCK])

	def test_the_block_reaches_the_spray_and_its_registration_number(self):
		"""THE HOP THIS TOOL EXISTS FOR. A residue detection is a question about
		the spray register, and answering it meant collecting block ids off the
		captures by hand and filtering the sprays on paper."""
		self.a_spray()
		self.a_capture(1)
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		sprays = next(hop for hop in data["chain"] if hop["hop"] == "sprays")
		self.assertEqual(sprays["count"], 1)
		row = sprays["rows"][0]
		self.assertEqual(row["blocks_reached"], [BLOCK])
		self.assertEqual(row["applicator_license"], "OR-PA-88213")
		self.assertEqual(row["products"][0]["epa_reg_number"], "66222-242")

	def test_a_pass_made_after_the_fruit_came_off_is_not_in_the_answer(self):
		"""It did not reach that fruit. Naming it sends somebody to investigate a
		tank that was never on this crop."""
		self.a_capture(1)  # picked 2026-06-01
		self.a_spray(started_at="2026-06-20 07:00:00", completed_at="2026-06-20 11:30:00")
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		sprays = next(hop for hop in data["chain"] if hop["hop"] == "sprays")
		self.assertEqual(sprays["count"], 0)

	def test_a_planned_pass_is_not_in_the_answer_either(self):
		"""Nothing went on the ground, so nothing reached the fruit."""
		self.a_spray(status="Planned")
		self.a_capture(1)
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		self.assertEqual(next(hop for hop in data["chain"] if hop["hop"] == "sprays")["count"], 0)

	def test_a_scale_ticket_widens_to_its_block_on_its_day(self):
		self.a_capture(5)  # 2026-06-05
		self.a_ticket()
		data = self.tool_data("trace_backward", {"scale_ticket": "ST-2026-0001"})
		self.assertEqual(data["bucket_count"], 1)
		self.assertTrue(any("may include fruit that went out on another truck" in note for note in data["notes"]))

	def test_a_settlement_reaches_the_ground_through_its_tickets(self):
		STORE.seed(
			"Settlement Statement",
			[
				{
					"name": "SS-2026-0001",
					"statement_number": "NWP-4417",
					"date": "2026-06-30",
					"company": MAIN,
					"customer": "Northwest Packing Co",
					"status": "Posted",
					"net_proceeds": 41200.0,
					"line_items": [],
					"deductions": [],
				}
			],
		)
		self.a_ticket(settlement="SS-2026-0001")
		self.a_capture(5)
		data = self.tool_data("trace_backward", {"settlement": "SS-2026-0001"})
		self.assertEqual(data["anchor"]["kind"], "settlement")
		self.assertEqual(data["blocks"], [BLOCK])

	def test_a_settlement_with_no_tickets_is_refused_and_says_why(self):
		STORE.seed(
			"Settlement Statement",
			[
				{
					"name": "SS-2026-0002",
					"statement_number": "NWP-4418",
					"date": "2026-06-30",
					"company": MAIN,
					"customer": "Northwest Packing Co",
					"status": "Posted",
					"line_items": [],
					"deductions": [],
				}
			],
		)
		error = self.tool_error("trace_backward", {"settlement": "SS-2026-0002"})
		self.assertIn("no Scale Ticket is linked", error)
		self.assertIn("through its tickets", error)

	def test_naming_nothing_to_start_from_is_refused_with_the_options(self):
		error = self.tool_error("trace_backward", {})
		for option in ("shipment", "bin", "scale_ticket", "settlement", "bucket_entry"):
			self.assertIn(option, error)
		self.assertIn("trace_forward", error)


# ── the forward walk, and the bound that makes it usable ────────────────────
class WhichLotsCarryThis(TraceTestCase):
	def test_a_block_reaches_the_bins_the_shipments_and_the_customer(self):
		self.a_capture(1)
		self.a_shipment()
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertEqual(data["bins"], ["BIN-17"])
		shipments = next(hop for hop in data["chain"] if hop["hop"] == "shipments")
		self.assertEqual(shipments["count"], 1)
		self.assertEqual(
			[row["customer"] for row in data["customers_to_notify"]], ["Northwest Packing Co"]
		)

	def test_the_customer_list_says_which_register_reached_them(self):
		"""Four registers can name a customer and they can disagree. A recall
		telephones all of them, so the route is reported rather than picked."""
		self.a_capture(1)
		self.a_shipment()
		self.a_ticket()
		data = self.tool_data("trace_forward", {"block": BLOCK})
		row = next(
			entry for entry in data["customers_to_notify"] if entry["customer"] == "Northwest Packing Co"
		)
		self.assertIn("shipment", row["reached_via"])
		self.assertIn("scale ticket", row["reached_via"])

	def test_a_spray_bounds_the_trace_at_the_pass(self):
		"""THE WHOLE VALUE OF A FORWARD TRACE. Fruit picked before the tank went
		out did not carry it, and a recall naming three seasons over one
		application is one nobody can act on."""
		before = self.a_capture(1, timestamp="2026-05-01 08:00:00", bin_id="BIN-EARLY")
		after = self.a_capture(2, timestamp="2026-06-01 08:00:00", bin_id="BIN-LATE")
		spray = self.a_spray()  # completed 2026-05-20
		data = self.tool_data("trace_forward", {"spray_application": spray["name"]})
		self.assertEqual(data["bounded_from"], "2026-05-20")
		self.assertEqual(data["bins"], ["BIN-LATE"])
		self.assertEqual(data["bucket_count"], 1)
		self.assertNotIn(before, [row["entry"] for row in data["chain"][0]["rows"]])
		self.assertIn(after, [row["entry"] for row in data["chain"][0]["rows"]])

	def test_a_spray_trace_calls_out_the_preharvest_interval(self):
		"""Fruit picked inside the PHI is a different and more serious finding
		than fruit merely treated."""
		self.a_capture(1, timestamp="2026-06-01 08:00:00")
		spray = self.a_spray()
		data = self.tool_data("trace_forward", {"spray_application": spray["name"]})
		self.assertTrue(any("pre-harvest interval" in note for note in data["notes"]))

	def test_a_bare_block_is_unbounded_and_says_so(self):
		"""An unbounded trace is a legitimate question and a different one. It
		must not be silently confused with a bounded one."""
		self.a_capture(1)
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertIsNone(data["bounded_from"])
		self.assertTrue(any("UNBOUNDED" in note for note in data["notes"]))

	def test_date_from_bounds_a_bare_block(self):
		self.a_capture(1, timestamp="2026-05-01 08:00:00")
		self.a_capture(2, timestamp="2026-06-01 08:00:00")
		data = self.tool_data("trace_forward", {"block": BLOCK, "date_from": "2026-05-15"})
		self.assertEqual(data["bucket_count"], 1)
		self.assertNotIn("UNBOUNDED", " ".join(data["notes"]))

	def test_another_block_is_not_in_the_answer(self):
		self.a_capture(1)
		self.a_capture(2, block_id=OTHER_BLOCK, bin_id="BIN-99")
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertEqual(data["bins"], ["BIN-17"])

	def test_a_water_test_reaches_the_block_through_its_zone(self):
		self.tool_data(
			"create_irrigation_zone",
			{"field": BLOCK, "zone_name": "YC3-Zone2", "owning_entity": MAIN},
		)
		zone = STORE.rows("Irrigation Zone")[0]["name"]
		self.tool_data(
			"create_water_test",
			{"source": zone, "test_date": "2026-05-10", "ecoli_result": "Detected"},
		)
		test = STORE.rows("Water Test")[0]["name"]
		self.a_capture(1, timestamp="2026-06-01 08:00:00")
		data = self.tool_data("trace_forward", {"water_test": test})
		self.assertEqual(data["blocks_traced"], [BLOCK])
		self.assertEqual(data["bounded_from"], "2026-05-10")
		self.assertEqual(data["bucket_count"], 1)

	def test_naming_nothing_to_start_from_is_refused_with_the_options(self):
		error = self.tool_error("trace_forward", {})
		for option in ("block", "spray_application", "water_test"):
			self.assertIn(option, error)
		self.assertIn("trace_backward", error)


# ── the breaks, which are the point of the read ─────────────────────────────
class TheBreaksAreNamed(TraceTestCase):
	def test_captures_with_no_bin_are_counted_per_column(self):
		"""The number that turns "our traceability is fine" into a fact."""
		self.a_capture(1)
		self.a_capture(2, bin_id="")
		self.a_capture(3, bin_id="", shipment_id="")
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertEqual(data["unlinked_counts"]["bin_id"], 2)
		self.assertEqual(data["unlinked_counts"]["shipment_id"], 1)
		self.assertEqual(data["unlinked_counts"]["block_id"], 0)
		self.assertTrue(
			any(entry["missing"] == "bin_id" for entry in data["breaks"]), data["breaks"]
		)

	def test_a_missing_shipment_id_is_flagged_as_the_hop_that_reaches_a_customer(self):
		self.a_capture(1, shipment_id="")
		data = self.tool_data("trace_forward", {"block": BLOCK})
		note = next(entry for entry in data["breaks"] if entry["missing"] == "shipment_id")["note"]
		self.assertIn("THIS IS THE HOP THAT REACHES A CUSTOMER", note)

	def test_a_shipment_id_matching_no_register_row_is_reported_not_dropped(self):
		"""`shipment_id` is free text and Trade Shipment has its own names. An id
		pointing at nothing is a real data fault, and the silent version of it is
		how a chain looks complete and is not."""
		self.a_capture(1, shipment_id="TSHIP-NOT-A-THING")
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertEqual(data["unresolved_shipment_ids"], ["TSHIP-NOT-A-THING"])
		self.assertTrue(
			any(entry["missing"] == "Trade Shipment records" for entry in data["breaks"])
		)

	def test_a_block_id_matching_no_field_is_reported_on_the_way_back(self):
		"""The spray and water history covers only the blocks that resolved, so
		the answer is INCOMPLETE and has to say so."""
		self.a_capture(1, block_id="YC3")
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		self.assertEqual(data["unresolved_block_ids"], ["YC3"])
		note = next(entry for entry in data["breaks"] if entry["missing"] == "Field records")["note"]
		self.assertIn("INCOMPLETE", note)

	def test_a_lot_with_no_captures_says_there_is_no_trace_at_all(self):
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0009"})
		self.assertEqual(data["bucket_count"], 0)
		note = next(entry for entry in data["breaks"] if entry["missing"] == "bucket captures")["note"]
		self.assertIn("WITHOUT THIS HOP THERE IS NO TRACE", note)

	def test_a_treated_block_with_no_spray_on_file_is_reported(self):
		self.a_capture(1)
		data = self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		self.assertTrue(
			any(entry["missing"] == "spray applications" for entry in data["breaks"])
		)

	def test_a_capture_with_no_traceability_columns_at_all_is_refused_as_a_start(self):
		entry = self.a_capture(1, block_id="", bin_id="", shipment_id="")
		error = self.tool_error("trace_backward", {"bucket_entry": entry})
		self.assertIn("carries no block, bin or shipment id", error)
		self.assertIn("which is the finding", error)


class NobodyToTelephone(TraceTestCase):
	"""A forward trace that reaches no customer says so as a BREAK, never as an
	empty list. An empty `customers_to_notify` and a complete one look identical
	to anybody skimming, and one of them means the recall cannot be executed."""

	def test_reaching_no_customer_is_a_break_in_its_own_right(self):
		self.a_capture(1, shipment_id="")
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertEqual(data["customers_to_notify"], [])
		note = next(entry for entry in data["breaks"] if entry["missing"] == "customers")["note"]
		self.assertIn("NOBODY TO TELEPHONE", note)

	def test_reaching_one_leaves_no_such_break(self):
		self.a_capture(1)
		self.a_shipment()
		data = self.tool_data("trace_forward", {"block": BLOCK})
		self.assertTrue(data["customers_to_notify"])
		self.assertFalse([entry for entry in data["breaks"] if entry["missing"] == "customers"])


class TheseWriteNothing(TraceTestCase):
	def test_neither_direction_writes_a_row(self):
		"""EVERY register except the audit log. `MCP Action Log` grows by one per
		call by design — that is this app recording that somebody ran a trace,
		which is itself evidence, and excluding it here is the difference between
		"writes nothing" and "writes nothing operational"."""
		self.a_capture(1)
		self.a_shipment()
		counted = lambda: {
			doctype: len(STORE.rows(doctype))
			for doctype in STORE.tables
			if doctype != "MCP Action Log"
		}
		before = counted()
		self.tool_data("trace_forward", {"block": BLOCK})
		self.tool_data("trace_backward", {"shipment": "TSHIP-2026-0001"})
		self.assertEqual(before, counted())
