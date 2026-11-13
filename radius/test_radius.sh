#!/usr/bin/env bash
# Checks the RADIUS rules end to end: single-use tokens, device binding, one session
# per device, and two devices online at once.
#
# Usage:
#   ./test_radius.sh <email> <token> <device-MAC> <other-MAC> <radius-host> <secret> \
#                    [<second-token-same-device>] [<token-other-device>]
#
#   <token>                 a token the backend issued for <device-MAC>
#   <other-MAC>             a MAC different from <device-MAC>
#   <second-token-same>     optional: a fresh token for <device-MAC>
#   <token-other-device>    optional: a token for <other-MAC>
#
# Each device signs in with its own RADIUS username: <email>|<MAC>, so the two devices
# do not affect each other. Needs freeradius-utils (radclient). Use a test account only,
# because the sequence consumes tokens.

set -u

EMAIL="$1"; TOKEN="$2"; MAC="$3"; OTHER="$4"; HOST="$5"; SECRET="$6"
SECOND_SAME="${7:-}"; TOKEN_OTHER="${8:-}"

radius_mac() { echo "$1" | tr ':' '-' | tr 'a-f' 'A-F'; }
MAC_R=$(radius_mac "$MAC")
OTHER_R=$(radius_mac "$OTHER")
USER_A="$EMAIL|$MAC_R"
USER_B="$EMAIL|$OTHER_R"
SESSION_A="test-a-$(date +%s)"
SESSION_B="test-b-$(date +%s)"
FAILED=0

auth() {  # auth <radius-username> <calling-station> <password>
  echo "User-Name = \"$1\", User-Password = \"$3\", Calling-Station-Id = \"$2\", NAS-IP-Address = 10.0.0.1, NAS-Port = 1" \
    | radclient -x "$HOST:1812" auth "$SECRET" 2>/dev/null
}

acct() {  # acct <Start|Stop> <radius-username> <calling-station> <session-id>
  echo "Acct-Status-Type = $1, User-Name = \"$2\", Acct-Session-Id = \"$4\", Calling-Station-Id = \"$3\", NAS-IP-Address = 10.0.0.1, NAS-Port = 1" \
    | radclient -x "$HOST:1813" acct "$SECRET" >/dev/null 2>&1
}

expect() {  # expect <label> <Access-Accept|Access-Reject> <output>
  if echo "$3" | grep -q "Received $2"; then
    echo "PASS  $1"
  else
    echo "FAIL  $1  (wanted $2)"; echo "$3" | sed 's/^/      /'; FAILED=1
  fi
}

# 1. Token from the wrong MAC is rejected, and the token is not consumed.
expect "token rejected from another MAC" Access-Reject "$(auth "$USER_A" "$OTHER_R" "$TOKEN")"

# 2. Token from its own MAC is accepted, and then consumed.
expect "token accepted from its own MAC" Access-Accept "$(auth "$USER_A" "$MAC_R" "$TOKEN")"
acct Start "$USER_A" "$MAC_R" "$SESSION_A"

# 3. The consumed token is rejected on reuse.
expect "consumed token rejected on reuse" Access-Reject "$(auth "$USER_A" "$MAC_R" "$TOKEN")"

# 4. One session per device: a second session from the same device is refused.
if [ -n "$SECOND_SAME" ]; then
  expect "second session on the same device refused" Access-Reject "$(auth "$USER_A" "$MAC_R" "$SECOND_SAME")"
else
  echo "SKIP  same-device second session (pass a 7th argument)"
fi

# 5. Two devices at once: the other device is accepted while the first is online.
if [ -n "$TOKEN_OTHER" ]; then
  expect "second device accepted while first is online" Access-Accept "$(auth "$USER_B" "$OTHER_R" "$TOKEN_OTHER")"
  acct Start "$USER_B" "$OTHER_R" "$SESSION_B"
  acct Stop "$USER_B" "$OTHER_R" "$SESSION_B"
else
  echo "SKIP  two devices at once (pass an 8th argument)"
fi

# 6. After the first session stops, a fresh token for that device is accepted again.
acct Stop "$USER_A" "$MAC_R" "$SESSION_A"
if [ -n "$SECOND_SAME" ]; then
  expect "same device accepted again after its session stops" Access-Accept "$(auth "$USER_A" "$MAC_R" "$SECOND_SAME")"
fi

exit $FAILED
