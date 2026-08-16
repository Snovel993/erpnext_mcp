# SPDX-License-Identifier: MIT
"""The closed list of routes, and the argument filter Frappe was doing.

THERE IS NO DISPATCHER AND NO METHOD-NAME ARGUMENT, for the same reason
`api/mobile.py` has neither: a route exists in the table below or its path 404s,
so the entire reachable surface of this service is the lines an auditor can
read in one screen. This app has two hundred-odd MCP tools; `create_journal_entry`,
`convey_parcel`, `import_chart_of_accounts` and `disable_je_gate` are among them
and NONE of them is reachable from here at any path, under any argument, by any
caller.

`test_farmops_api.py` asserts that table against `api/mobile.py` and
`api/files.py` in both directions — every route resolves to a real guarded
function, and every guarded function has a route — so another method cannot
arrive quietly and a route cannot come to point at something ungated. v0.45.0
added nine at once (onboarding, the crew clock and the bucket sync) and that
assertion is what made adding them a two-file change rather than a 404 in a
field. v0.46.0 added the three the wizard reaches BEFORE any of those nine —
`create_employee`, `search_employees` and `reactivate_employee`, which were
still being asked for at Frappe's own `/api/resource/Employee` and are the
reason the onboarding flow got no further than its Identity step. v0.46.2 added
the fourth of that set, `get_employee`: the same 404 at the same doctype's
detail path, on the branch a returning seasonal worker takes.

────────────────────────────────────────────────────────────────────────────
WHY THE ARGUMENT FILTER IS HERE
────────────────────────────────────────────────────────────────────────────

Frappe's own handler binds a request body to a whitelisted method by KEEPING THE
KEYS THAT MATCH THE SIGNATURE AND DROPPING THE REST. Every one of the wrappers
was written against that behaviour — `api/mobile.py` says so at length, because
naming every accepted argument instead of taking `**kwargs` is what makes
`record_data`, `worker_id`, `foreman` and a W-4's `status` unreachable from a
phone.

This transport does not go through Frappe's handler, so that filter is not
happening any more, and `function(**body)` would be two different bugs at once:
a phone sending a key the server does not know would get a 500 instead of an
answer, and a wrapper that ever did grow a `**kwargs` would silently start
accepting whatever the body carried. Reproducing the filter keeps every
signature meaning exactly what it meant on the old path — which is also what
lets the byte-identical cross-check in the test suite be a real comparison
rather than a coincidence.

`user` is dropped even though it IS in every signature: `guard.endpoint` injects
the authenticated caller there, and an account that can name somebody else in a
request body is not scoped to anything. `guard` drops it too. Two locks on the
one argument that would matter.
"""

from __future__ import annotations

import inspect

from ..api import fallback_auth
from ..api import files as files_api
from ..api import mobile as mobile_api

#: The path prefix every route lives under. NOT under `/api/…`: the whole point
#: of this transport is to be somewhere Frappe's request handler is not, and a
#: prefix that collided with Frappe's would be handed straight back to it by any
#: proxy in front that routes on path before it forwards.
PREFIX = "/farmops/api"


class Route:
	"""One reachable path, and the guarded function behind it.

	`path` is under `PREFIX` — `/mobile/get_task`. `handler` is the
	`@guard.endpoint`-wrapped function in `api/mobile.py` or `api/files.py`, gates
	and all. `mutating` is read off the wrapper's own `farm_ops_mutating`
	attribute rather than restated here, so the route table and the endpoint
	cannot come to disagree about whether a call writes.

	The NAME comes off the wrapper too, for the same reason: `guard.endpoint` is
	given the method name the app calls it by, so a route cannot be spelled
	differently from the method it reaches.
	"""

	__slots__ = ("handler", "mutating", "path")

	def __init__(self, prefix, handler):
		self.path = f"{prefix}/{handler.farm_ops_method}"
		self.handler = handler
		self.mutating = bool(getattr(handler, "farm_ops_mutating", False))

	def __repr__(self):  # pragma: no cover - diagnostics
		return f"<Route {self.path}{' (mutating)' if self.mutating else ''}>"


#: The mobile methods and the two file methods. ORDER IS THE APP'S ORDER —
#: the caller, lists, detail, lifecycle, field reports, compliance, upload —
#: because this table is the first thing somebody reads to learn what a phone
#: can do.
ROUTES = (
	Route("/mobile", mobile_api.get_current_user_context),
	Route("/mobile", mobile_api.list_my_tasks),
	Route("/mobile", mobile_api.list_available_tasks),
	Route("/mobile", mobile_api.get_task),
	Route("/mobile", mobile_api.claim_task),
	Route("/mobile", mobile_api.start_task),
	Route("/mobile", mobile_api.complete_task_via_mobile),
	Route("/mobile", mobile_api.reject_task),
	Route("/mobile", mobile_api.report_field_task),
	Route("/mobile", mobile_api.list_compliance_alerts),
	# v0.57.0. The compliance tab stops being a noticeboard. `API_CONTRACT.md`
	# §8.2 — a row may now be closed from a handset, but only where the alert
	# itself says so, which is a box somebody ticks per alert and which defaults
	# off. The gate lives in the wrapper rather than in the app, because a
	# refusal that only exists in a client is not one.
	Route("/mobile", mobile_api.dismiss_compliance_alert),
	Route("/mobile", mobile_api.scan_asset),
	Route("/mobile", mobile_api.get_asset_detail),
	Route("/mobile", mobile_api.log_asset_state_change),
	Route("/mobile", mobile_api.get_available_actions),
	Route("/mobile", mobile_api.report_asset_issue),
	# v0.46.0. The Identity step, which comes before all of it and which the
	# wizard 404'd on: the foreman searches for somebody who has worked here
	# before, and either puts that record back on the payroll or creates a new
	# one. Every path below this line was unreachable in practice until these
	# three existed, because the flow stops at step 1.
	Route("/mobile", mobile_api.create_employee),
	Route("/mobile", mobile_api.search_employees),
	# v0.46.2. The step between the search and the rehire, and the last of the
	# Identity step's four calls to be answering at Frappe's own
	# `/api/resource/Employee/<name>` — which the funnel does not publish. It is
	# what tells the wizard which of its five steps a returning picker has already
	# done, and in tree fruit the returning picker is the common case.
	Route("/mobile", mobile_api.get_employee),
	Route("/mobile", mobile_api.reactivate_employee),
	# v0.45.0. Onboarding, in the order `OnboardingFlow` walks it: the I-9 is
	# opened, the worker fills their half, the employer verifies the documents,
	# the W-4 is signed, and the badge that will carry their piece-rate is mapped
	# to them last.
	Route("/mobile", mobile_api.create_i9_form),
	Route("/mobile", mobile_api.submit_i9_section_1),
	Route("/mobile", mobile_api.submit_i9_section_2),
	# v0.47.0. The two the I-9 half was missing. `list_i9_document_types` is the
	# only READ on the onboarding path and it replaces a Swift array: the server
	# has seeded all 24 USCIS-accepted documents since v0.27.0 and no phone could
	# ask for them. `reverify_i9` is Section 3 — the branch the wizard has been
	# able to SEE since v0.46.2, when `get_employee` began reporting a returning
	# picker's expired I-9 as expired, and has had no call to take.
	Route("/mobile", mobile_api.list_i9_document_types),
	Route("/mobile", mobile_api.reverify_i9),
	# v0.47.1. The I-9 as a document rather than as a doctype. `get_i9_form` is
	# the read the wizard never had — every other I-9 call hands back the record
	# it just wrote, so a foreman opening the flow on somebody already verified
	# could be told `Verified` and nothing else. `generate_i9_pdf` fills the
	# government's own fillable form and hands back a URL to print from, and
	# `upload_signed_i9` files the photograph of the signed sheet back against
	# the record. The three together are what turn four sprints of collected
	# fields into the thing an inspection under §1324a(b)(3) actually asks for.
	Route("/mobile", mobile_api.get_i9_form),
	Route("/mobile", mobile_api.generate_i9_pdf),
	Route("/mobile", mobile_api.upload_signed_i9),
	# v0.48.0. Who may put their name on Section 2, and the three calls that
	# maintain that list. `list_authorized_signers` is the one the wizard needs
	# on the Section 2 screen — v0.48.0 turned the verifier from a free-text box
	# into a roster lookup, and an app that could not read the roster would have
	# to learn whether its own account may sign by submitting a form in an
	# orchard and being refused. The other three are here because a roster that
	# can only be edited in the Desk is one nobody fixes at 6am on a hire day.
	Route("/mobile", mobile_api.list_authorized_signers),
	Route("/mobile", mobile_api.add_authorized_signer),
	Route("/mobile", mobile_api.update_authorized_signer),
	Route("/mobile", mobile_api.remove_authorized_signer),
	Route("/mobile", mobile_api.submit_w4),
	# v0.48.0. The W-4's own federal form, and the last artefact the onboarding
	# flow was missing: the wizard has collected withholding elections since
	# v0.45.0 and had nothing printable to show for them. The employer block it
	# fills needs nothing from the phone — see the wrapper.
	Route("/mobile", mobile_api.generate_w4_pdf),
	Route("/mobile", mobile_api.link_badge_to_employee),
	# v0.50.0. The read between a scan and a name. `add_worker_to_shift` takes
	# an Employee docname and a camera produces a badge string; until this route
	# existed the crew clock could scan a whole crew and roster none of it, and
	# the bucket loop could show a foreman a code but never the picker.
	Route("/mobile", mobile_api.resolve_badge),
	# v0.51.0. The other half of that gap. `generate_employee_badge_qr` has
	# minted readable `CF-0001` badges since v0.50.0 and was published only on
	# the MCP tool registry, which a handset does not speak — so the wizard's
	# badge step could map a card printed elsewhere and could not issue one, on
	# a hire day, to a worker standing there waiting for their number.
	# `set_employee_photo` is what makes the printed card carry a face: the
	# template reads `Employee.image` and nothing on this surface wrote it.
	Route("/mobile", mobile_api.generate_employee_badge_qr),
	Route("/mobile", mobile_api.set_employee_photo),
	# v0.53.0. The same badge, delivered to the wallet the worker already has.
	# `generate_employee_badge_qr` hands back a PNG somebody has to print and
	# laminate; this hands back a `.pkpass` the foreman AirDrops off the handset,
	# which opens straight into Apple Wallet on a device with nothing installed
	# on it — plus the Google Wallet save link for the Android half. The bytes
	# travel in the answer rather than as a `file_url`, because this door
	# authenticates with `X-FarmOps-Token` and a private File is a login page to
	# it.
	Route("/mobile", mobile_api.get_employee_badge_pass),
	# The capture queue and the crew clock.
	Route("/mobile", mobile_api.sync_bucket_entries),
	Route("/mobile", mobile_api.start_shift),
	Route("/mobile", mobile_api.add_worker_to_shift),
	Route("/mobile", mobile_api.end_shift),
	# v0.48.3. The second half of an onboarding upload, and the route whose
	# absence was sending the wizard's six photographs to Frappe's own
	# `/api/method/upload_file` — a path this app's auth hook does not look at, so
	# the funnel-stripped request arrived as Guest and got 200 and a login page.
	# `finalize_staged_file` commits evidence unattached on purpose; this is the
	# scoped call that files it against the Employee it belongs to.
	Route("/mobile", mobile_api.attach_onboarding_document),
	# v0.52.0. Models served from ERPNext, not Volume Vision directly:
	# `get_active_model` is what BucketLog/Farm Ops asks to find out which
	# model is deployed, `get_model_file_chunk` is how it then reads the
	# binary back — through the same door and the same credential as every
	# other call on this surface, rather than a second connection to Volume
	# Vision.
	Route("/mobile", mobile_api.get_active_model),
	Route("/mobile", mobile_api.get_model_file_chunk),
	# v0.54.0. The hiring wizard's Assignment and Housing steps.
	# `list_onboarding_reference_data` is the four dropdowns — Branch,
	# Department, Designation, Employment Type — read off the site instead of
	# compiled into the app, which is the same staleness `list_i9_document_types`
	# removed from the I-9's document picker. The camp pair is the read and the
	# write of one question: which cabin has a bed free, and put this person in
	# it. Routed here rather than left on `/api/method/…` alone for the reason
	# every method above is: the funnel publishes `/farmops/api/…` and nothing
	# else, and a method with no route is a 404 in somebody's hands.
	Route("/mobile", mobile_api.list_onboarding_reference_data),
	Route("/mobile", mobile_api.list_available_housing),
	Route("/mobile", mobile_api.assign_housing),
	# v0.55.0. The other end of the missing-signature rules. A Farm Task raised
	# by `i9_section_2_unsigned` carries the form that needs signing, the app
	# opens a signature pad over it, and this is where the pad posts. It is the
	# one call on this surface that takes IMAGE BYTES in the body rather than a
	# `file_token` — see `tools/signatures.py` for why a capture and a scan take
	# different doors — and it is routed here for the same reason everything
	# above it is: the funnel publishes `/farmops/api/…` and nothing else.
	Route("/mobile", mobile_api.collect_signature),
	# v0.57.0. The same write under the name and the argument spellings
	# `API_CONTRACT.md` §14.2 posts, and the reason both exist is this table's
	# own argument filter: the app sends `signature_field` and `signature_image`,
	# `collect_signature` declares `field` and `signature_base64`, and `bind`
	# drops what a signature does not name — so the contract's body arrived at
	# the v0.55.0 method with no field name and no picture in it. The older
	# spelling keeps its route; handsets already in the field are not asked to
	# change to get an answer.
	Route("/mobile", mobile_api.submit_form_signature),
	# v0.58.0. The break methods: log a break, end it, and read the policy that
	# says what is owed. `get_break_policy` is the read the handset's break coach
	# computes its countdown from — the schedule itself, fetched at shift start.
	Route("/mobile", mobile_api.log_shift_break),
	Route("/mobile", mobile_api.end_shift_break),
	Route("/mobile", mobile_api.get_break_policy),
	# v0.59.0. The foreman's day: clock somebody out, see production, read the
	# shift. `clock_out_worker` is the mobile name for `remove_worker_from_shift`.
	Route("/mobile", mobile_api.clock_out_worker),
	Route("/mobile", mobile_api.get_shift_production),
	Route("/mobile", mobile_api.get_shift),
	# v0.62.0. THE SEVEN `MobileAPI.swift` NAMES AND THIS TABLE DID NOT CARRY.
	# Audited against v0.61.0 on 2026-08-12; see the block comment in
	# `api/mobile.py` that opens this set for the argument each one settles.
	#
	# THE FIRST THREE ARE THE SAME ACT AS THE ROUTE ABOVE THEM UNDER THE NAME AND
	# THE ARGUMENT SPELLINGS THE APP ACTUALLY POSTS, which is exactly why they are
	# routes of their own rather than renames of `list_onboarding_reference_data`,
	# `list_available_housing` and `assign_housing`. This table's own argument
	# filter is the reason a rename would not have been enough: `bind` keeps only
	# the keys a signature names, so `assignable_only` at a method declaring
	# `include_full` is a filter that vanishes and `unit`/`assigned_date` at one
	# declaring `housing_unit`/`check_in_date` is an assignment with no cabin and
	# no date in it. The older spellings keep their routes — a handset already in
	# an orchard is not asked to change to get an answer, which is the same
	# promise `collect_signature` kept when `submit_form_signature` arrived.
	#
	# v0.63.1: BOTH SPELLINGS NOW REACH BOTH DOORS, and this filter is still the
	# reason. Declaring one spelling per method fixed the crossing the app makes
	# and left the opposite crossing dropping arguments the same silent way — so
	# each of these two methods and each of the two above it declares its own
	# spelling AND the other's, reconciled in one place in `api/mobile.py`, with
	# each door keeping the default it answers for.
	Route("/mobile", mobile_api.list_org_reference_data),
	Route("/mobile", mobile_api.list_housing_units),
	Route("/mobile", mobile_api.create_housing_assignment),
	# The two writes the hiring wizard's Assignment and Contact steps have never
	# had. `list_onboarding_reference_data` has served the four dropdowns since
	# v0.54.0 with nowhere to put the answers, and the contact step collects a
	# number and an email a returning picker is reached on in October.
	Route("/mobile", mobile_api.set_employee_org_fields),
	Route("/mobile", mobile_api.set_employee_contact_fields),
	# Reading the folder back. Six routes above file documents against an Employee
	# and until now none could ask what was already there — so a badge issued on a
	# hire day was invisible from a handset ever after, and every private file this
	# app writes was unreachable from the device that collected it (the funnel
	# strips the credential a `/private/files/…` link would want, and answers with
	# a login page).
	Route("/mobile", mobile_api.list_attachments),
	Route("/mobile", mobile_api.get_attachment_content),
	# v0.63.0. THE TWO ENDS OF THE SIGNING FLOW THE SERVER NEVER SERVED.
	#
	# `get_document_preview` is step 1 of the evidence chain — the signer saw the
	# form — and `API_CONTRACT.md` §17.5 is the whole argument for it: both
	# renderers answer with a private `file_url`, this door authenticates with
	# `X-FarmOps-Token` rather than to Frappe, so the app could show the COMPLETED
	# page after signing and not the blank one before. §17.5 called that a
	# server-side gap and said the fix is one route. The bytes travel in the
	# answer, exactly as the signed page and the `.pkpass` do.
	#
	# `seal_signed_document` is step 5, published so a handset can take it for the
	# two cases `submit_form_signature` cannot — a form signed before v0.63.0, and
	# one whose second signature came through the Desk. The ordinary flow never
	# calls it: the signature route seals what it just signed.
	Route("/mobile", mobile_api.get_document_preview),
	Route("/mobile", mobile_api.seal_signed_document),
	# v0.65.0. The scanner screen's one call. Five routes above it — `scan_asset`,
	# `get_asset_detail`, `resolve_badge`, `list_housing_units` and the field
	# reads — each answer for one register and refuse everything else, so a phone
	# pointed at an unknown QR had to be told what it was looking at before it
	# could ask. This resolves the string first and answers second, and it is the
	# same code behind each branch: a badge read here and a badge read at
	# `resolve_badge` get the same refusals, and an asset scanned here gets the
	# same `last_scan_at` stamp `scan_asset` leaves.
	Route("/mobile", mobile_api.universal_scan),
	# v0.67.0. Receipt capture: one screen, four kinds of paper, and the branch
	# between them published as a route of its own. `classify_receipt` reads no
	# doctype and could have shipped as a keyword table inside the app — it is
	# here so that the table exists once rather than once per platform, because
	# two copies of a classifier are two answers to one question.
	#
	# Three of the nine receipt tools are deliberately absent. `submit_scale_ticket`
	# freezes a third party's weight record, and `create_settlement_statement`
	# and `submit_settlement_statement` are a multi-page document that arrives at
	# an office rather than a thing anybody photographs at a tailgate. A method
	# with no route 404s, which is the whole design of this table.
	Route("/mobile", mobile_api.classify_receipt),
	Route("/mobile", mobile_api.create_expense_receipt),
	Route("/mobile", mobile_api.create_scale_ticket),
	Route("/mobile", mobile_api.list_scale_tickets),
	# The three reads `create_expense_receipt`'s pickers and detail view need:
	# a cost center to code the receipt to, a supplier to link it against, and
	# the receipts already filed, for the screen that opens one back up.
	Route("/mobile", mobile_api.list_cost_centers),
	Route("/mobile", mobile_api.list_suppliers),
	Route("/mobile", mobile_api.list_expense_receipts),
	Route("/mobile", mobile_api.update_expense_receipt),
	# Sprint 3 (v0.68.0): compliance alert rectification — see api/rectify.py.
	# Five direct fixes, and the one route every task-shaped fix shares.
	Route("/mobile", mobile_api.renew_certification),
	Route("/mobile", mobile_api.record_training),
	Route("/mobile", mobile_api.sign_training_supervisor_review),
	Route("/mobile", mobile_api.update_regulatory_filing),
	Route("/mobile", mobile_api.advance_policy_review),
	Route("/mobile", mobile_api.rectify_alert),
	# Sprint 4 (v0.69.0): document intelligence. The phone reads a pesticide
	# label at a chemical shed; `validate_document` is what decides whether to
	# believe what it read, and `get_document_validation` reads one back.
	#
	# BOTH PATHS DIFFER FROM THE SPRINT 4 CONTRACT'S, AND THE WRAPPERS SAY SO AT
	# LENGTH. The contract wrote `POST /farmops/api/validate-document` and
	# `GET /farmops/api/document-validation/<name>`; this transport builds every
	# path from the method's own name under `/mobile` (see `Route`), takes POST
	# only, and matches whole paths rather than patterns. A hyphen cannot be a
	# Python method name and a path parameter has nowhere to land, so honouring
	# the contract's spelling would mean forking the router — and the closed,
	# readable-in-one-screen table is the entire design of this file. The bodies
	# and the answers are the contract's, unchanged.
	#
	# THE OTHER THREE TOOLS ARE DELIBERATELY ABSENT. `list_document_validations`
	# and `list_revalidation_due` are an office's registers rather than anything
	# a phone at a shed reads, and `revalidate_document` re-decides a stored
	# status — which is a supervisor's call at a desk. A method with no route
	# 404s, which is the whole design of this table.
	Route("/mobile", mobile_api.validate_document),
	Route("/mobile", mobile_api.get_document_validation),
	# Sprint 7 (v0.72.0): the foreman's crew-task dashboard. Five tools that have
	# existed since Sprint 8 and have never been reachable from a handset — the
	# board for somebody else's work, the dispatch that moves it, the task raised
	# on the spot, and the two ends of the template register.
	#
	# THESE FIVE ARE THE FIRST ROUTES ON THIS TABLE THAT A FIELD WORKER CANNOT
	# CALL. Every path above is a worker's own work and is gated on
	# `guard.FARM_OPS_ROLES`, which admits a picker; each of these calls
	# `guard.require_dispatch_role` in its own body — Foreman or Farm Manager,
	# the same two names `dispatch.py` already draws the line between for
	# Critical urgency. The gate is in the wrapper rather than in the tools
	# because the tools have none: on the MCP transport what stands in front of
	# them is the operator's enablement switch, and a phone does not go through
	# it.
	#
	# `list_dispatched_tasks` IS THE ONE WHOSE ARGUMENT CHANGED SHAPE. The tool
	# takes `worker_id` and will read anybody's board; the wrapper does not
	# declare that key at all — it computes the crew off the caller's own open
	# shifts and lets `employee` narrow that set and nothing else. This table's
	# own argument filter is what makes the undeclared key unreachable rather
	# than merely unused.
	#
	# `get_farm_task_template`, `create_farm_task_template` and
	# `update_farm_task_template` are deliberately absent. Reading one template
	# in full is what the list already carries enough of, and AUTHORING the shape
	# of a recurring job — its evidence contract, the record it builds, its
	# checklist — is a decision made at a desk with the regulation open, not at a
	# tailgate. A method with no route 404s, which is the whole design of this
	# table.
	Route("/mobile", mobile_api.list_dispatched_tasks),
	Route("/mobile", mobile_api.assign_farm_task),
	Route("/mobile", mobile_api.create_farm_task),
	Route("/mobile", mobile_api.list_farm_task_templates),
	Route("/mobile", mobile_api.create_task_from_template),
	# Sprint 8 (v0.78.0): field asset registration, three routes. The iOS
	# screens are built and the flow they perform — photograph the plate,
	# register the asset, print the tag, file the photograph — stopped at step
	# two, because `register_asset` and `generate_asset_qr` have been MCP tools
	# since v0.25.0 with no route and a phone does not speak that transport.
	#
	# `attach_file_to_document` IS THE ONE THAT CHANGED SHAPE ON THE WAY HERE,
	# and this table's own argument filter is half of how. The tool takes any
	# doctype on the site; the wrapper carries an allowlist of the registers a
	# field device already writes into and refuses the rest by name — and it
	# does not DECLARE `allow_cancelled`, so `bind` cannot deliver it and the
	# tool's own refusal of a cancelled parent stands whatever a body says.
	# Personnel evidence keeps its own door: `attach_onboarding_document` above
	# checks the HR role, and `Employee` is deliberately off the allowlist.
	Route("/mobile", mobile_api.register_asset),
	Route("/mobile", mobile_api.generate_asset_qr),
	Route("/mobile", mobile_api.attach_file_to_document),
	# Sprint 9 (v0.79.0): what the day actually looks like. Nineteen routes in
	# four groups, and the gate is different on each group for a stated reason.
	#
	# THE PAUSE PAIR IS A WORKER'S OWN WORK and is gated on nothing more than
	# enrolment, like `start_task` above it. `worker_id` is not on either
	# signature, so this table's argument filter is what stops an account
	# stopping a stranger's clock from across the farm.
	Route("/mobile", mobile_api.pause_task_via_mobile),
	Route("/mobile", mobile_api.resume_task_via_mobile),
	# LINKING IS AN OBSERVATION AND MERGING IS A DECISION, which is why they are
	# gated differently. Noticing that your job and somebody else's are the same
	# valve is what a worker in a block sees and a foreman at a desk does not;
	# folding one away takes somebody's work off the board under another name,
	# and that is Foreman-and-above.
	Route("/mobile", mobile_api.link_tasks_via_mobile),
	Route("/mobile", mobile_api.merge_task_via_mobile),
	# The narrative trio. `attach_audio_note` is the one this sprint exists for:
	# a foreman at an accident scene has a phone in one hand and somebody on the
	# ground, and typing is not what happens next. The handset transcribes
	# on-device and posts the words; the recording travels the chunked upload
	# path and is linked here.
	Route("/mobile", mobile_api.add_task_note_via_mobile),
	Route("/mobile", mobile_api.attach_audio_note),
	Route("/mobile", mobile_api.list_task_notes),
	# THE FIVE DISCIPLINE ROUTES CARRY AN HR GATE IN THEIR OWN BODIES, not the
	# field-role gate every route above them uses. A discipline record is a
	# personnel document; a picker holding a perfectly good field credential has
	# no business reading one, let alone writing one. `expire_discipline_record`
	# is deliberately absent — ageing a step out of a chain is a policy decision
	# made at a desk with the handbook open, and a method with no route 404s.
	Route("/mobile", mobile_api.create_discipline_record),
	Route("/mobile", mobile_api.acknowledge_discipline_record),
	Route("/mobile", mobile_api.get_discipline_record),
	Route("/mobile", mobile_api.list_discipline_history),
	Route("/mobile", mobile_api.get_discipline_report),
	# THE ACCIDENT ROUTES SPLIT, and this is the most deliberate gate on the
	# table. `create_accident_report` and `get_accident_report` are open to any
	# enrolled worker: the person who finds somebody on the ground is whoever
	# finds them, and a server that refused their report because they are not a
	# foreman is a server people work around at the exact moment that matters.
	# Updating, closing and listing the register are the INVESTIGATION, and an
	# investigation is somebody's job — those take the dispatch role.
	Route("/mobile", mobile_api.create_accident_report),
	Route("/mobile", mobile_api.get_accident_report),
	Route("/mobile", mobile_api.update_accident_investigation),
	Route("/mobile", mobile_api.close_accident_investigation),
	Route("/mobile", mobile_api.list_accident_reports),
	# The wizard registry. Both reads, both gated on enrolment alone, and both
	# answer in the caller's own language off `Employee.preferred_language`.
	Route("/mobile", mobile_api.get_wizard_definition),
	Route("/mobile", mobile_api.list_wizard_definitions),
	# v0.80.0. Trade documentation: four reads and one write. The write is a
	# DRIVER'S CONFIRMATION and not the desk's tool — `confirm_shipment_movement`
	# takes 'departed' or 'delivered' and its signature carries neither `status`
	# nor `override_reason`, so `bind` cannot pass either. Releasing a shipment
	# and cancelling one stay on the MCP surface, where the trade-role gate is.
	Route("/mobile", mobile_api.list_shipments),
	Route("/mobile", mobile_api.get_shipment),
	Route("/mobile", mobile_api.get_shipment_readiness),
	Route("/mobile", mobile_api.list_trade_documents),
	Route("/mobile", mobile_api.confirm_shipment_movement),
	Route("/files", files_api.stage_file_chunk),
	Route("/files", files_api.finalize_staged_file),
)

#: Path → Route. Built once at import; there is nothing dynamic about it.
BY_PATH = {route.path: route for route in ROUTES}


def accepted_arguments(handler) -> set:
	"""The body keys this method will accept. Everything else is dropped.

	Read off the signature with `inspect`, which follows `functools.wraps`
	through `guard.endpoint` to the wrapper's own parameter list — so the answer
	is always what the function actually declares, and a signature change cannot
	leave a stale allowlist behind it.

	A `**kwargs` in one of them would make this return the empty set and take
	every argument away, which is the safe direction to fail and is asserted as
	a property of the surface in `test_farmops_api.py`. None of them has one, on
	purpose.
	"""
	try:
		parameters = inspect.signature(handler).parameters
	except (TypeError, ValueError):  # pragma: no cover - a builtin, not one of ours
		return set()
	if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
		return set()
	return {
		name
		for name, parameter in parameters.items()
		if name != "user"
		and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
	}


def bind(route, body: dict) -> dict:
	"""The request body, reduced to the keys this method declares.

	`_auth` goes whatever else happens: it is envelope rather than argument, it
	is the one key in a mobile body that carries a live credential, and a call
	that reached this transport did not need it — but the app sends it on every
	request (v0.17.2 carries the pair three ways and does not know which door it
	came in), so it arrives here on every single call.
	"""
	accepted = accepted_arguments(route.handler)
	return {
		key: value for key, value in (body or {}).items() if key in accepted and key != fallback_auth.BODY_KEY
	}
