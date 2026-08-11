#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Mount every farmops-api route on the Tailscale funnel. v0.57.1.
#
# ────────────────────────────────────────────────────────────────────────────
# WHY THIS FILE EXISTS
# ────────────────────────────────────────────────────────────────────────────
#
# `tailscale funnel --set-path` IS AN EXACT MOUNT POINT, so the funnel carries
# one line per method and a route added to `farmops_api/routes.py` is NOT
# published by having been added. It is published by somebody running this.
#
# Nobody ran it. v0.54.0 added the housing pair and the onboarding dropdowns,
# v0.55.0 added `collect_signature`, v0.57.0 added `dismiss_compliance_alert`
# and `submit_form_signature` — six routes, three releases, all of them 404 in
# a foreman's hand while every test in the suite passed, because the tests call
# the service and the phone calls the funnel. The failure is silent from the
# server's side: the request never arrives, so there is no log line, no audit
# row and no traceback. What the worker sees is Tailscale's own plain-text
# `404 page not found`, which the app cannot parse into a sentence, so it shows
# its generic miss — "That task no longer exists — someone may have taken it."
#
# THE LIST BELOW IS ASSERTED AGAINST `routes.ROUTES` by `test_farmops_api.py`,
# in both directions. A route with no line here fails the suite, which is the
# only mechanism that would have caught any of the three releases above.
#
# ────────────────────────────────────────────────────────────────────────────
# RUNNING IT
# ────────────────────────────────────────────────────────────────────────────
#
#   sudo sh mount_farmops_funnel.sh              # mount everything
#   sudo sh mount_farmops_funnel.sh --dry-run    # print the commands only
#
# On the Umbrel HOST, not in the bench container: the Tailscale container is
# what holds the funnel config, and `127.0.0.1:5250` is the host's loopback —
# see the long note in `fafo-erpnext/docker-compose.yml` about why the port is
# published there at all.
#
# It is IDEMPOTENT. `--set-path` on a path already mounted at the same target
# is a no-op, so re-running it after every upgrade is the intended use and
# costs nothing.
#
# NOT A PREFIX MOUNT, on purpose. `--set-path=/farmops/api/` would publish
# whatever ends up behind that prefix later, including a route added by an
# upgrade nobody reviewed. Explicit paths publish exactly what is listed, and
# the list is reviewable in a diff.

set -eu

CONTAINER="${TAILSCALE_CONTAINER:-tailscale_web_1}"
TARGET="${FARMOPS_TARGET:-http://127.0.0.1:5250}"
DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# The health path. Unauthenticated and deliberately incurious — it proves a
# path reaches gunicorn and says nothing else, which is what makes a failed
# check diagnostic rather than binary.
HEALTH="health"

# ── /farmops/api/mobile/… ───────────────────────────────────────────────────
# ORDER IS `routes.py`'s ORDER: the caller, lists, detail, lifecycle, field
# reports, compliance, onboarding, badges, the clock. Keeping the two files in
# the same order is what makes the diff between them readable by eye when the
# test says they disagree.
MOBILE="
get_current_user_context
list_my_tasks
list_available_tasks
get_task
claim_task
start_task
complete_task_via_mobile
reject_task
report_field_task
list_compliance_alerts
dismiss_compliance_alert
scan_asset
get_asset_detail
log_asset_state_change
get_available_actions
report_asset_issue
create_employee
search_employees
get_employee
reactivate_employee
create_i9_form
submit_i9_section_1
submit_i9_section_2
list_i9_document_types
reverify_i9
get_i9_form
generate_i9_pdf
upload_signed_i9
list_authorized_signers
add_authorized_signer
update_authorized_signer
remove_authorized_signer
submit_w4
generate_w4_pdf
link_badge_to_employee
resolve_badge
generate_employee_badge_qr
set_employee_photo
get_employee_badge_pass
sync_bucket_entries
start_shift
add_worker_to_shift
end_shift
attach_onboarding_document
get_active_model
get_model_file_chunk
list_onboarding_reference_data
list_available_housing
assign_housing
collect_signature
submit_form_signature
log_shift_break
end_shift_break
get_break_policy
clock_out_worker
get_shift_production
get_shift
"

# ── /farmops/api/files/… ────────────────────────────────────────────────────
FILES="
stage_file_chunk
finalize_staged_file
"

mount_path() {
	path="$1"
	if [ -n "$DRY_RUN" ]; then
		printf 'docker exec %s tailscale funnel --bg --https=443 --set-path=%s %s\n' \
			"$CONTAINER" "$path" "$TARGET"
		return
	fi
	docker exec "$CONTAINER" tailscale funnel --bg --https=443 \
		--set-path="$path" "$TARGET"
}

mount_path "/farmops/api/$HEALTH"
for method in $MOBILE; do mount_path "/farmops/api/mobile/$method"; done
for method in $FILES; do mount_path "/farmops/api/files/$method"; done

[ -n "$DRY_RUN" ] && exit 0

echo
echo "Mounted. Read back what is actually published:"
echo "  docker exec $CONTAINER tailscale serve status"
echo
echo "Then prove it from OUTSIDE the tailnet — a mount that exists in the"
echo "config and does not answer is the failure this script is about:"
echo "  validate_public_endpoint(probe_routes=true)"
