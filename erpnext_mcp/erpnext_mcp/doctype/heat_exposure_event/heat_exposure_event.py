# SPDX-License-Identifier: MIT
"""Controller for Heat Exposure Event — one shift, one heat record, one signature.

ONE PER SHIFT, ENFORCED HERE AS WELL AS BY THE UNIQUE INDEX. Two records about
one exposure period will disagree, and the one an inspector finds will be
whichever was filed second. The `unique` flag on the link catches it at the
database; this catches it with a sentence that says which record already exists,
because "Duplicate entry for key farm_shift" is not an answer anybody can act on.

WHAT IT DERIVES rather than takes: the foreman, the company and the date all come
off the shift, on first save, and are never rewritten. A heat record whose
foreman was resolved through a link at report time would answer with whoever the
shift's foreman is today, and the entire point of this doctype is to say who was
responsible on the day.

────────────────────────────────────────────────────────────────────────────
WHY THE VALIDATIONS ARE ON SUBMIT AND NOT ON SAVE
────────────────────────────────────────────────────────────────────────────

A draft is a record in progress. On the morning of a heat wave the foreman may
have the shift open, the water break logged and no idea yet whether anybody will
show signs — and a controller that refused to save until every question was
answered would push the whole record to the end of the day, written from memory,
which is precisely the shape of record an investigator discounts.

Submitting is the attestation. THREE THINGS ARE REFUSED AT THAT MOMENT:

  1. No supervisor signature. An unsigned heat record is a claim with nobody
     behind it, and this is the record a serious-injury investigation is read
     from.
  2. Signs of heat illness observed and no emergency response activated, with
     nothing in the notes. Signs seen and nothing done is the sequence that
     kills people; there ARE legitimate versions of it — the worker recovered in
     shade within minutes and declined further help — and every one of them is a
     sentence somebody can write. What is refused is the silence.
  3. `training_verified` ticked where the crew's own training register does not
     support it. That one is checked in `tools/heat.py` rather than here,
     because it needs the shift's crew and the training records together, and a
     controller that went looking for them would run four queries on every save
     of a draft.

`supervisor_signed_on` is WRITTEN AT SUBMIT, never taken as input. A signature
date somebody can set is a signature date somebody can set to the day of the
shift, which is exactly the thing the timestamp exists to prove.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import shifts


class HeatExposureEvent(Document):
	def autoname(self):
		"""HEAT-YYYY-0001, where YYYY is the year of the SHIFT.

		Not the year somebody filed the record. A shift on 31 December documented
		on 2 January is a December record, and a series keyed off `today()` would
		file it under the following year for ever — which matters here more than
		on most doctypes, because a heat record is looked up by season.
		"""
		year = str(self.event_date or frappe.utils.today())[:4]
		self.name = shifts.next_in_series(shifts.HEAT_DOCTYPE, "HEAT", year)

	def validate(self):
		self._require_the_shift()
		self._fill_from_the_shift()
		self._fill_from_the_weather_timeline()
		self._refuse_a_second_record_for_the_shift()
		if not str(self.regulation_citation or "").strip():
			self.regulation_citation = shifts.CITATION

	def before_submit(self):
		self._require_the_signature()
		self._require_a_response_to_signs()
		self.supervisor_signed_on = self.supervisor_signed_on or frappe.utils.now()

	# ── the parts ───────────────────────────────────────────────────────────
	def _require_the_shift(self) -> None:
		if not self.farm_shift:
			frappe.throw(
				_(
					"A Heat Exposure Event documents a SHIFT. Without one it is a page of "
					"checkboxes about no particular crew on no particular day, which is not what "
					"OAR 437-004-1131 asks for — the rule asks whether the obligations were met "
					"during an exposure period, and the period is the shift."
				),
				title=_("Not Anchored to a Shift"),
			)

	def _fill_from_the_shift(self) -> None:
		row = (
			frappe.db.get_value(
				shifts.DOCTYPE,
				self.farm_shift,
				["foreman", "company", "start_datetime"],
				as_dict=True,
			)
			or {}
		)
		if not self.foreman:
			self.foreman = row.get("foreman")
		if not self.company:
			self.company = row.get("company")
		if not self.event_date:
			self.event_date = str(row.get("start_datetime") or "")[:10] or None

	def _fill_from_the_weather_timeline(self) -> None:
		"""The maxima and the first crossing, computed off the shift's own readings.

		v0.19.4, and it is the reason the timeline was worth building. Before the
		fetch existed a foreman filled these three fields in from memory or from
		the truck thermometer, and "about ninety-five" is what an investigator
		discounts. Computed from the shift's own timeline they are arithmetic over
		stored evidence, and the evidence is one click away on the shift behind
		this record.

		MANUAL ENTRY ALWAYS WINS. A field somebody typed is never recomputed, and
		this is not deference for its own sake: an on-site reading at the block
		beats a modelled figure for a grid square measured in kilometres, and the
		foreman who wrote 103 °F on a record they are about to sign is asserting
		something they saw. The computed value fills a BLANK; it never corrects an
		answer.

		`threshold_crossed_at` IS THE EARLIEST CROSSING AND NOT THE HOTTEST
		MOMENT. Every obligation -1131 adds runs from the instant the shift passed
		the threshold, so the exposure period starts at the first reading over it;
		the peak is what `max_heat_index_f` is for. Read against this company's
		own thresholds, so an entity that set a lower trigger gets the earlier
		time its own policy implies.
		"""
		if not self.farm_shift:
			return
		try:
			readings = shifts.weather_of(self.farm_shift)
		except Exception:  # pragma: no cover - a site mid-migrate
			return
		if not readings:
			return

		def peak(fieldname):
			values = []
			for row in readings:
				value = row.get(fieldname)
				if value in (None, ""):
					continue
				try:
					values.append(float(value))
				except (TypeError, ValueError):
					continue
			return max(values) if values else None

		if self.max_temp_f in (None, ""):
			self.max_temp_f = peak("temp_f")
		if self.max_heat_index_f in (None, ""):
			self.max_heat_index_f = peak("heat_index_f")
		if self.threshold_crossed_at in (None, ""):
			self.threshold_crossed_at = self._first_crossing(readings)

	def _first_crossing(self, readings: list):
		from erpnext_mcp.services import weather

		limits = weather.thresholds_for(str(self.company or ""))
		for row in sorted(readings, key=lambda entry: str(entry.get("reading_datetime") or "")):
			when = str(row.get("reading_datetime") or "")
			if not when:
				continue
			for fieldname, limit in (
				("temp_f", limits["heat_threshold_temp_f"]),
				("heat_index_f", limits["heat_threshold_heat_index_f"]),
			):
				value = row.get(fieldname)
				if value in (None, ""):
					continue
				try:
					if float(value) >= limit:
						return when
				except (TypeError, ValueError):
					continue
		return None

	def _refuse_a_second_record_for_the_shift(self) -> None:
		existing = frappe.db.get_all(
			shifts.HEAT_DOCTYPE,
			filters={"farm_shift": self.farm_shift, "name": ("!=", self.name or "")},
			pluck="name",
			limit=2,
		)
		if existing:
			frappe.throw(
				_(
					"{0} already documents shift {1}. One shift has at most one heat record: two "
					"records about one exposure period will disagree, and the one an inspector "
					"finds will be whichever was filed second. Amend {0} instead."
				).format(existing[0], self.farm_shift),
				title=_("Already Documented"),
			)

	def _require_the_signature(self) -> None:
		if not str(self.supervisor_signature or "").strip():
			frappe.throw(
				_(
					"This record cannot be submitted without the supervisor's signature. "
					"Submitting is the attestation — an unsigned heat record is a claim with "
					"nobody behind it, and this is the record a serious-injury investigation is "
					"read from. Save it as a draft until the signature is captured."
				),
				title=_("Unsigned"),
			)

	def _require_a_response_to_signs(self) -> None:
		"""Signs observed and no response activated is refused unless explained.

		Not refused outright, because there are legitimate versions: the worker
		recovered in shade within minutes and declined further help; the signs
		were mild and the person was moved to lighter work and watched. Every one
		of those is a sentence somebody can write. What is refused is the SILENCE
		— a ticked observation beside an unticked response and an empty notes
		field is a record that raises the question and answers nothing.
		"""
		if not frappe.utils.cint(self.heat_illness_signs_observed):
			return
		if frappe.utils.cint(self.emergency_response_activated):
			return
		if str(self.notes or "").strip():
			return
		frappe.throw(
			_(
				"This record says heat illness signs were observed and no emergency response was "
				"activated, and the notes are empty. Signs seen and nothing done is the sequence "
				"that kills people. There are legitimate versions of it — the worker recovered in "
				"shade within minutes and declined further help is one — and every one of them is "
				"a sentence somebody can write. Tick Emergency Response Activated, or write what "
				"happened instead."
			),
			title=_("Signs Observed, No Response Recorded"),
		)
