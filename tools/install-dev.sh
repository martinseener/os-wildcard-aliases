#!/bin/sh
#
# Copy the plugin sources onto an OPNsense box for testing, without building a package.
#
#   tools/install-dev.sh user@firewall              install / update
#   tools/install-dev.sh --uninstall user@firewall  remove everything again: files, settings, created
#                                                   aliases (unless still used by rules), pf tables, state
#   tools/install-dev.sh --uninstall --keep-config user@firewall
#                                                   remove the files only, keep settings, aliases and state
#
# The remote user needs ssh access and sudo (or is root); sudo may ask for a password.
# Files land where the package would install them (/usr/local/...).

set -e

UNINSTALL=0
KEEP_CONFIG=0
while [ $# -gt 1 ]; do
	case "$1" in
	--uninstall) UNINSTALL=1 ;;
	--keep-config) KEEP_CONFIG=1 ;;
	*) break ;;
	esac
	shift
done

HOST="$1"
if [ -z "${HOST}" ]; then
	echo "usage: $0 [--uninstall [--keep-config]] user@firewall" >&2
	exit 1
fi

TOOLS="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "${TOOLS}/../net/wildcard-aliases/src" && pwd)"
REMOTE_TMP="/tmp/wildcardaliases-dev"
CLEAR_CACHES="rm -f /var/lib/php/tmp/opnsense_menu_cache.xml /var/lib/php/tmp/opnsense_acl_cache.json"

if [ ${UNINSTALL} -eq 1 ]; then
	FILES="$(cd "${SRC}" && find . -type f | sed 's|^\./|/usr/local/|' | tr '\n' ' ')"
	SCRIPT="$(mktemp -t wildcardaliases)"
	trap 'rm -f "${SCRIPT}"' EXIT
	cat > "${SCRIPT}" <<-EOS
		set -e
		PID=/var/run/wildcardaliases.pid
		if [ -x /usr/local/etc/rc.d/wildcardaliases ]; then
			/usr/local/etc/rc.d/wildcardaliases onestop || true
		elif [ -f \${PID} ]; then
			kill \$(cat \${PID}) 2>/dev/null || true
		fi
		if [ ${KEEP_CONFIG} -eq 0 ]; then
			# aliases and settings in config.xml, then the pf tables behind them
			/usr/local/bin/php ${REMOTE_TMP}/purge.php > ${REMOTE_TMP}/purge.out
			cat ${REMOTE_TMP}/purge.out
			if grep -q '^removed ' ${REMOTE_TMP}/purge.out; then
				configctl filter reload > /dev/null
			fi
			for table in \$(awk '\$1 == "removed" { print \$2 }' ${REMOTE_TMP}/purge.out); do
				pfctl -q -t "\${table}" -T kill 2>/dev/null || true
			done
			# still referenced by rules: the table stays, but must not keep allowing learned addresses
			for table in \$(awk '\$1 == "kept" { print \$2 }' ${REMOTE_TMP}/purge.out); do
				pfctl -q -t "\${table}" -T flush 2>/dev/null || true
			done
			rm -rf /var/db/wildcardaliases
		fi
		rm -f ${FILES} /etc/rc.conf.d/wildcardaliases \${PID} /var/run/wildcardaliases.child.pid
		rm -rf /usr/local/opnsense/scripts/OPNsense/WildcardAliases /usr/local/etc/wildcardaliases /var/run/wildcardaliases
		${CLEAR_CACHES}
		service configd restart
	EOS
	ssh "${HOST}" "rm -rf ${REMOTE_TMP} && mkdir -p ${REMOTE_TMP}"
	scp -q "${SCRIPT}" "${HOST}:${REMOTE_TMP}/uninstall.sh"
	scp -q "${TOOLS}/purge.php" "${HOST}:${REMOTE_TMP}/purge.php"
	ssh -t "${HOST}" "sudo sh ${REMOTE_TMP}/uninstall.sh; rc=\$?; sudo rm -rf ${REMOTE_TMP}; exit \$rc"
	if [ ${KEEP_CONFIG} -eq 1 ]; then
		echo "removed the plugin files. Kept: its settings, the aliases it created and the learned state."
	else
		echo "removed. Aliases listed as \"kept\" are still used by rules or other aliases: their tables were"
		echo "emptied; delete those references and then the alias under Firewall > Aliases."
	fi
	exit 0
fi

ARCHIVE="$(mktemp -t wildcardaliases).tar"
trap 'rm -f "${ARCHIVE}"' EXIT
COPYFILE_DISABLE=1 tar -C "${SRC}" --exclude '__pycache__' --exclude '.DS_Store' -cf "${ARCHIVE}" .

ssh "${HOST}" "rm -rf ${REMOTE_TMP} && mkdir -p ${REMOTE_TMP}"
scp -q "${ARCHIVE}" "${HOST}:${REMOTE_TMP}/src.tar"
ssh -t "${HOST}" "sudo sh -c '\
	tar -xf ${REMOTE_TMP}/src.tar -C /usr/local -o && \
	chown -R root:wheel /usr/local/opnsense/scripts/OPNsense/WildcardAliases && \
	chmod 755 /usr/local/etc/rc.d/wildcardaliases /usr/local/opnsense/scripts/OPNsense/WildcardAliases/*.py && \
	${CLEAR_CACHES} && \
	service configd restart && \
	configctl template reload OPNsense/WildcardAliases' && rm -rf ${REMOTE_TMP}"
echo "installed on ${HOST}: open Firewall > Wildcard Aliases in the GUI (reload the page once)"
