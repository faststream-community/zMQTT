#!/bin/sh
# Grant the Dynamic Security ACLs the broker tests rely on, on every platform
# that runs them: the Mosquitto container, the macOS runner and the Windows one.
#
# MOSQUITTO_CTRL  path to mosquitto_ctrl (default: whatever is on PATH)
# MOSQUITTO_PORT  port the broker listens on (default: 1883)
set -eu

ctrl=${MOSQUITTO_CTRL:-mosquitto_ctrl}
port=${MOSQUITTO_PORT:-1883}

dynsec() {
    "$ctrl" -h 127.0.0.1 -p "$port" -u admin -P admin dynsec "$@" 2>&1
}

# Read-back is authoritative: mosquitto_ctrl can exit 0 after a rejected
# command. Keep the last response for diagnostics, without printing arguments.
ensure() {
    stage=$1
    check=$2
    apply=$3
    attempt=0
    last_response="not applied"
    until readback=$("$check") && [ -n "$readback" ]; do
        if [ "$attempt" -ge 10 ]; then
            echo "Dynamic Security stage failed: $stage" >&2
            printf '%s\n' "$last_response" "$readback" >&2
            return 1
        fi
        attempt=$((attempt + 1))
        last_response=$("$apply") || :
        if readback=$("$check") && [ -n "$readback" ]; then
            return 0
        fi
        sleep 1
    done
}

check_field() {
    pattern=$1
    shift
    response=$(dynsec "$@") || { printf '%s\n' "$response"; return 1; }
    printf '%s\n' "$response"
    printf '%s\n' "$response" | grep -q "$pattern"
}

check_group() { check_field '^Groupname:' getGroup zmqtt-anonymous-clients; }
create_group() { dynsec createGroup zmqtt-anonymous-clients; }
check_anonymous_role() { check_field '^Rolename:' getRole zmqtt-anonymous-role; }
create_anonymous_role() { dynsec createRole zmqtt-anonymous-role; }
check_test_role() { check_field '^Rolename:' getRole zmqtt-tests; }
create_test_role() { dynsec createRole zmqtt-tests; }
check_client() { check_field '^Username:' getClient zmqtt-mosquitto; }
create_client() { dynsec createClient zmqtt-mosquitto -p zmqtt-mosquitto; }

check_links() {
    check_field zmqtt-anonymous-clients getAnonymousGroup || return 1
    check_field zmqtt-anonymous-role getGroup zmqtt-anonymous-clients || return 1
    check_field zmqtt-tests getClient zmqtt-mosquitto || return 1
}
apply_links() {
    dynsec addGroupRole zmqtt-anonymous-clients zmqtt-anonymous-role 10
    dynsec setAnonymousGroup zmqtt-anonymous-clients
    dynsec addClientRole zmqtt-mosquitto zmqtt-tests 10
}
apply_acls() {
    dynsec setDefaultACLAccess publishClientSend allow
    dynsec setDefaultACLAccess subscribe allow
    dynsec addRoleACL zmqtt-anonymous-role subscribePattern '#' allow 10
    dynsec addRoleACL zmqtt-anonymous-role publishClientSend '#' allow 10
    dynsec addRoleACL zmqtt-tests subscribePattern '#' allow 10
    dynsec addRoleACL zmqtt-tests publishClientSend '#' allow 10
    dynsec addRoleACL zmqtt-tests publishClientSend 'zmqtt/e2e/denied' deny 20
    dynsec addRoleACL zmqtt-tests unsubscribePattern '#' allow 10
    dynsec addRoleACL zmqtt-tests unsubscribePattern 'zmqtt/unsuback/denied/#' deny 20
}

has_acls() {
    acls=$(dynsec getRole "$1" | tr -s ' ')
    printf '%s\n' "$acls"
    shift
    for acl in "$@"; do
        printf '%s\n' "$acls" | grep -qF "$acl" || return 1
    done
}

check_acls() {
    defaults=$(dynsec getDefaultACLAccess | tr -s ' ')
    printf '%s\n' "$defaults"
    printf '%s\n' "$defaults" | grep -qF 'publishClientSend : allow' || return 1
    printf '%s\n' "$defaults" | grep -qF 'subscribe : allow' || return 1
    has_acls zmqtt-anonymous-role \
        'subscribePattern : allow : #' \
        'publishClientSend : allow : #' || return 1
    has_acls zmqtt-tests \
        'subscribePattern : allow : #' \
        'publishClientSend : allow : #' \
        'publishClientSend : deny : zmqtt/e2e/denied' \
        'unsubscribePattern : allow : #' \
        'unsubscribePattern : deny : zmqtt/unsuback/denied/#' || return 1
    echo 'ACLs verified'
}

attempt=0
until dynsec getClient admin | grep -q '^Username:'; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Mosquitto Dynamic Security did not become ready" >&2
        exit 1
    fi
    sleep 1
done

ensure anonymous-group check_group create_group
ensure anonymous-role check_anonymous_role create_anonymous_role
ensure test-role check_test_role create_test_role
ensure test-client check_client create_client
ensure role-bindings check_links apply_links
ensure acls check_acls apply_acls
if ! verification=$(check_links && check_acls); then
    echo "Dynamic Security final verification failed:" >&2
    printf '%s\n' "$verification" >&2
    exit 1
fi
