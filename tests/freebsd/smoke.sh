#!/bin/sh
#
# Smoke test on a plain FreeBSD box (run as root, used by the GitHub workflow): install the daemon
# with its rc.d script, then check start, DNS + pf, auto-restart after a crash, reload and stop.
# Needs python3 with dnspython, and network access for the upstream lookup.

set -e

SRC="$(cd "$(dirname "$0")/../../net/wildcard-aliases/src" && pwd)"
SCRIPTS=/usr/local/opnsense/scripts/OPNsense/WildcardAliases
CHILD=/var/run/wildcardaliases.child.pid
SUPERVISOR=/var/run/wildcardaliases.pid

LOG=/var/log/wildcardaliases-smoke.log

fail() { echo "FAIL: $*"; tail -30 "${LOG}"; exit 1; }
query() { drill -Q -p 5354 @127.0.0.1 "$1" A; }

kldstat -q -m pf || kldload pf
# stock FreeBSD drops daemon.info messages (OPNsense keeps them), collect them for the checks below
echo "daemon.*	${LOG}" >> /etc/syslog.conf
touch "${LOG}"
service syslogd restart

mkdir -p "${SCRIPTS}" /usr/local/etc/wildcardaliases
cp -R "${SRC}/opnsense/scripts/OPNsense/WildcardAliases/" "${SCRIPTS}/"
install -m 755 "${SRC}/etc/rc.d/wildcardaliases" /usr/local/etc/rc.d/wildcardaliases

UPSTREAM="$(awk '/^nameserver/ { print $2; exit }' /etc/resolv.conf)"
cat > /usr/local/etc/wildcardaliases/wildcardaliases.json <<EOF
{"listen": ["127.0.0.1:5354"], "upstreams": ["${UPSTREAM}"], "verbose": true,
 "aliases": [{"name": "smoke_test", "patterns": ["*.github.com"], "inet": true, "inet6": true}]}
EOF

sysrc -q wildcardaliases_enable=YES
service wildcardaliases configtest
service wildcardaliases start
sleep 2
service wildcardaliases status || fail "not running after start"

echo ">>> query and pf table"
[ -n "$(query api.github.com)" ] || fail "no answer for api.github.com"
pfctl -t smoke_test -T show | grep -q . || fail "pf table smoke_test is empty"
[ -n "$(query www.freebsd.org)" ] || fail "no answer for a name outside the patterns"

echo ">>> crash: kill -9 the daemon, daemon(8) must start a new one"
OLD="$(cat ${CHILD})"
kill -9 "${OLD}"
sleep 8
NEW="$(cat ${CHILD})"
[ "${NEW}" != "${OLD}" ] && kill -0 "${NEW}" || fail "daemon was not restarted (old ${OLD}, new ${NEW})"
[ -n "$(query api.github.com)" ] || fail "no answer after the restart"
pfctl -t smoke_test -T show | grep -q . || fail "pf table empty after the restart (state not restored)"

echo ">>> reload: same process, new configuration"
sed -i '' 's/"smoke_test"/"smoke_test2"/' /usr/local/etc/wildcardaliases/wildcardaliases.json
service wildcardaliases reload
sleep 2
[ "$(cat ${CHILD})" = "${NEW}" ] || fail "reload restarted the daemon"
query api.github.com > /dev/null
pfctl -t smoke_test2 -T show | grep -q . || fail "reload did not apply the new alias"

echo ">>> learned addresses with their domain (GUI listing)"
sleep 2
${SCRIPTS}/entries.py | grep -q '"api.github.com"' || fail "entries.py does not list api.github.com"

echo ">>> package upgrade: a running daemon gets the new code"
OLD="$(cat ${CHILD})"
sh "${SRC}/../+POST_INSTALL.post"
sleep 2
[ "$(cat ${CHILD})" != "${OLD}" ] || fail "post-install did not restart the daemon"
[ -n "$(query api.github.com)" ] || fail "no answer after the post-install restart"

echo ">>> upgrade from before 0.3: replace a daemon running without daemon(8)"
service wildcardaliases stop
/usr/local/bin/python3 ${SCRIPTS}/wildcardaliasesd.py -f --syslog &
echo $! > ${SUPERVISOR}
sleep 2
[ -n "$(query api.github.com)" ] || fail "legacy-style daemon does not answer"
LEGACY="$(cat ${SUPERVISOR})"
sh "${SRC}/../+POST_INSTALL.post"
sleep 3
kill -0 "${LEGACY}" 2> /dev/null && fail "legacy daemon still running"
service wildcardaliases status || fail "not running under daemon(8) after the upgrade"
[ -n "$(query api.github.com)" ] || fail "no answer after replacing the legacy daemon"

echo ">>> stop: nothing may be left running"
service wildcardaliases stop
sleep 1
pgrep -f wildcardaliasesd.py && fail "daemon still running after stop"
[ ! -f "${CHILD}" ] || fail "child pidfile left behind"
[ "$(stat -f %Lp /var/db/wildcardaliases/state.json)" = "600" ] || fail "state.json is not mode 600"

# one start, plus one after the crash
[ "$(grep -c 'wildcardaliases.*started with' "${LOG}")" -ge 2 ] || fail "start messages missing in syslog"
echo "OK"
