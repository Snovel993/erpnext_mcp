#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Mount the public paths on the Tailscale funnel. v0.58.1.
#
# ────────────────────────────────────────────────────────────────────────────
# `--set-path` STRIPS THE PATH IT MATCHED. EVERY MOUNT BELOW ENDS IN ITS OWN
# PATH FOR THAT REASON, AND A MOUNT THAT DOES NOT IS THE BUG THIS FILE FIXES
# ────────────────────────────────────────────────────────────────────────────
#
# Every version of this script before this one mounted like this:
#
#     tailscale funnel --set-path=/farmops/api/health --https=443 \
#         http://127.0.0.1:5250
#
# which reads as "publish that path" and is not what it does. Tailscale removes
# the matched prefix before it forwards, so the request that arrived as
# `/farmops/api/health` reached gunicorn as `/`. Fifty-six mounts, all of them
# correct-looking in `tailscale serve status`, every one of them forwarding to
# `/`.
#
# What came back is `farmops_api/app.py`'s own refusal — dispatch() takes the
# path apart and answers a JSON 404 for anything that is not under `/farmops/api`:
#
#     {"error": "/ is not a Farm Ops API path."}
#
# The phone showed that as its generic miss. Note the shape of the failure and
# how it differs from v0.57.1's: the request DID arrive, so this one is visible
# from the server — an access-log line for `/` on every call the farm made. The
# unmounted-route failure of v0.57.1 left nothing at all.
#
# THE FIX IS THE TARGET URL, NOT THE MOUNT PATH. Give the proxy target the same
# path the mount matched and the two cancel out:
#
#     --set-path=/farmops  →  http://127.0.0.1:5250/farmops
#     GET /farmops/api/health
#       → strip "/farmops"            → "/api/health"
#       → append to the target's path → "/farmops/api/health"      ✓
#
# `mount_path()` builds the target from the path so the two cannot drift, and
# `test_farmops_api.py` runs this script under `--dry-run` and asserts that no
# emitted mount forwards to a bare origin.
#
# ────────────────────────────────────────────────────────────────────────────
# ONE PREFIX FOR /farmops, EXACT PATHS FOR THE OTHER TWO
# ────────────────────────────────────────────────────────────────────────────
#
# The old header argued against a prefix mount: "`--set-path=/farmops/api/`
# would publish whatever ends up behind that prefix later". That argument was
# written when the prefix pointed at ERPNext, and it does not survive contact
# with what is actually behind this one. `farmops-api` is a dedicated process
# whose entire surface is `routes.ROUTES` — dispatch() answers 404 to every
# other path under the prefix, and every route it does answer runs
# `@guard.endpoint` before it does anything. The mount is not the boundary; the
# route table is, and the route table is asserted in both directions by
# `TheSurfaceIsClosed`. So the prefix publishes exactly what the table holds,
# and a route added to `routes.py` is now published BY having been added —
# which is the whole of v0.57.1's failure mode, gone.
#
# THAT REASONING IS SPECIFIC TO 5250 AND DOES NOT TRANSFER. The other two ports
# are mounted one exact path at a time, and it is not a style preference:
#
#   * `/api/method` on 5300 is Frappe's whitelisted-method router — every
#     `@frappe.whitelist()` in ERPNext, in every installed app. A prefix mount
#     there publishes the lot to the internet. Only `erpnext_mcp.mcp.handle` is
#     meant to be public, and it is the only one listed.
#
#   * `/bankbridge` on 5202 serves an admin UI that is UNAUTHENTICATED BY
#     DESIGN, plus four unauthenticated Plaid write endpoints. Bank Bridge's own
#     SECURITY.md is explicit that only the OAuth callback may be published.
#     One path is listed and it is that callback.
#
# ────────────────────────────────────────────────────────────────────────────
# RUNNING IT
# ────────────────────────────────────────────────────────────────────────────
#
#   sudo sh mount_farmops_funnel.sh              # unmount the stale, mount the new
#   sudo sh mount_farmops_funnel.sh --dry-run    # print the commands only
#
# On the Umbrel HOST, not in the bench container: the Tailscale container is
# what holds the funnel config, and `127.0.0.1:5250` is the host's loopback —
# see the long note in `fafo-erpnext/docker-compose.yml` about why the port is
# published there at all.
#
# IT UNMOUNTS BEFORE IT MOUNTS, and on a box that has ever run an older version
# of this script that step is the one that matters. Tailscale routes on the
# longest matching path, so the fifty-six stale `/farmops/api/<method>` mounts
# would each out-specify the new `/farmops` prefix and keep forwarding to `/`.
# Adding the correct mount is not enough on its own; the wrong ones have to go.
# They are discovered from the live config rather than listed here, so a path
# somebody mounted by hand is cleaned up too.
#
# It is otherwise IDEMPOTENT and re-running it after every upgrade costs
# nothing. `--set-path` on a path already mounted at the same target is a no-op,
# and at a DIFFERENT target it replaces — which is how the two exact paths below
# get corrected without a teardown of their own.

set -eu

CONTAINER="${TAILSCALE_CONTAINER:-tailscale_web_1}"

# Origins only — no path. `mount_path` appends the mount path to these, and a
# path here would be doubled. `FARMOPS_TARGET` is the pre-v0.58.1 name.
FARMOPS_ORIGIN="${FARMOPS_ORIGIN:-${FARMOPS_TARGET:-http://127.0.0.1:5250}}"
ERPNEXT_ORIGIN="${ERPNEXT_ORIGIN:-http://127.0.0.1:5300}"
BANKBRIDGE_ORIGIN="${BANKBRIDGE_ORIGIN:-http://127.0.0.1:5202}"

DRY_RUN=""
case "${1:-}" in
	--dry-run) DRY_RUN=1 ;;
	"") ;;
	*) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

# ── what gets published ─────────────────────────────────────────────────────

# 5250. One prefix, covering `/farmops/api/health` and every route in
# `routes.ROUTES`. Deliberately `/farmops` and not `/farmops/api`: the service
# owns the whole `/farmops` namespace and a future sibling under it would
# otherwise need a second mount and a second release nobody applied.
FARMOPS_PREFIX="/farmops"

# 5300. ERPNext's nginx. EXACT PATHS — see the header. The MCP endpoint is what
# an AI client calls and the only whitelisted method meant to be public.
#
# The legacy mobile transport at `/api/method/erpnext_mcp.api.mobile.*` is NOT
# here on purpose. It is still live on the site, but it has never worked through
# this proxy — it comes back as the Desk's HTML login page, which is the failure
# v0.18.0 built `farmops_api` to route around. Publishing it would add public
# surface that cannot answer.
ERPNEXT_PATHS="
/api/method/erpnext_mcp.mcp.handle
"

# 5202. Bank Bridge. The OAuth callback and nothing else — a bank redirects the
# operator's browser here after login, which is the only reason any of that app
# needs to be reachable from outside the LAN.
BANKBRIDGE_PATHS="
/bankbridge/plaid/oauth_return
"

# ── the two operations ──────────────────────────────────────────────────────

mount_path() {
	path="$1"
	origin="$2"
	# The target carries the path. This is the whole fix; see the header.
	if [ -n "$DRY_RUN" ]; then
		printf 'docker exec %s tailscale funnel --bg --https=443 --set-path=%s %s%s\n' \
			"$CONTAINER" "$path" "$origin" "$path"
		return
	fi
	docker exec "$CONTAINER" tailscale funnel --bg --https=443 \
		--set-path="$path" "$origin$path"
}

unmount_path() {
	path="$1"
	if [ -n "$DRY_RUN" ]; then
		printf 'docker exec %s tailscale funnel --https=443 --set-path=%s off\n' \
			"$CONTAINER" "$path"
		return
	fi
	# `off` on a path that is not mounted is an error, and an expected one when
	# the list came from a config that has already been cleaned. Not fatal.
	docker exec "$CONTAINER" tailscale funnel --https=443 --set-path="$path" off \
		>/dev/null 2>&1 || echo "  (already gone: $path)"
}

# Every handler currently mounted UNDER `/farmops/` — which is every stale
# per-route mount, and never the `/farmops` prefix itself, because the pattern
# requires the trailing slash. Read off the live config so that a path mounted
# by hand, or by a version of this script older than the one in the repository,
# is found too.
#
# Parsed with sed rather than jq: the Tailscale image does not ship jq, and the
# host's may not either. `tr` splits the object into one token per handler key
# so the match can be anchored, which is what keeps it from matching a path that
# appears inside a proxy TARGET rather than as a key.
stale_farmops_paths() {
	docker exec "$CONTAINER" tailscale serve status --json 2>/dev/null \
		| tr '{},' '\n\n\n' \
		| sed -n 's/^[[:space:]]*"\(\/farmops\/[^"]*\)"[[:space:]]*:[[:space:]]*$/\1/p' \
		| sort -u
}

# ── run ─────────────────────────────────────────────────────────────────────

echo "Removing stale per-route mounts under $FARMOPS_PREFIX/ …"
stale=$(stale_farmops_paths || true)
if [ -z "$stale" ]; then
	echo "  none found."
	echo "  If this box HAS been mounted by an older version of this script, that"
	echo "  is a finding rather than good news — the readback below is what says"
	echo "  which. A stale mount left in place still forwards to '/'."
else
	for path in $stale; do
		echo "  off: $path"
		unmount_path "$path"
	done
fi

echo
echo "Mounting …"
mount_path "$FARMOPS_PREFIX" "$FARMOPS_ORIGIN"
for path in $ERPNEXT_PATHS; do mount_path "$path" "$ERPNEXT_ORIGIN"; done
for path in $BANKBRIDGE_PATHS; do mount_path "$path" "$BANKBRIDGE_ORIGIN"; done

[ -n "$DRY_RUN" ] && exit 0

echo
echo "Mounted. Read back what is actually published — and read the TARGETS, not"
echo "just the paths. A target with no path on it is this script's own bug back:"
echo "  docker exec $CONTAINER tailscale serve status"
echo
echo "Then prove it from OUTSIDE the tailnet. A mount that exists in the config"
echo "and does not carry is the failure this script is about, and it looks"
echo "identical to a working one from in here:"
echo "  validate_public_endpoint(probe_routes=true)"
echo
echo "The two exact paths are not covered by that probe. Check them by hand:"
echo "  curl -si https://<host>.<tailnet>.ts.net/api/method/erpnext_mcp.mcp.handle"
echo "      -X POST -H 'Content-Type: application/json' -d '{}'   # expect 401 JSON"
echo "  curl -si https://<host>.<tailnet>.ts.net/bankbridge/plaid/oauth_return"
echo "      # expect Bank Bridge, NOT Tailscale's plain-text 404"
