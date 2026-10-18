"""
    Copyright (c) 2026 Martin Seener <martin@sysorchestra.com>
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.
"""
import re

# domains remembered per address: CDN addresses serve many names, keep the most recent ones
MAX_NAMES = 5
# only store plain host names: DNS labels may contain any byte (e.g. "<script>"), and these names
# end up in the GUI
SAFE_NAME = re.compile(r'^[a-z0-9_-]+(\.[a-z0-9_-]+)*$')


def _remember(names, name):
    if name and len(name) <= 253 and SAFE_NAME.match(name):
        if name in names:
            names.remove(name)
        names.append(name)
        del names[:-MAX_NAMES]
    return names


class TableState:
    """ addresses of one pf table with their expiry time (pf tables have no per entry TTL) """

    def __init__(self, name, max_entries):
        self.name = name
        self.max_entries = max_entries
        self.entries = {}
        # address -> the most recent domain names it was learned from
        self.names = {}
        self.learned_total = 0
        self.evicted_total = 0
        self.last_learned = None

    def learn(self, addresses, expires, now, name=None):
        """ register addresses, extending the expiry of known ones.

            :param name: the domain name the addresses were learned from
            :return: tuple (new, evicted), the addresses that must be added to / removed from pf
        """
        new = []
        for address in addresses:
            _remember(self.names.setdefault(address, []), name)
            current = self.entries.get(address)
            if current is None:
                new.append(address)
                self.entries[address] = expires
            elif expires > current:
                self.entries[address] = expires
        evicted = []
        if addresses:
            # also when known addresses are only confirmed again (e.g. refreshed exact names)
            self.last_learned = now
        if new:
            self.learned_total += len(new)
            overflow = len(self.entries) - self.max_entries
            if overflow > 0:
                # drop the entries closest to expiry, prefer keeping what we just learned
                fresh = set(new)
                candidates = sorted(
                    self.entries.items(), key=lambda item: (item[0] in fresh, item[1])
                )
                for address, _ in candidates[:overflow]:
                    del self.entries[address]
                    self.names.pop(address, None)
                    evicted.append(address)
                self.evicted_total += len(evicted)
                gone = set(evicted)
                new = [address for address in new if address not in gone]
                evicted = [address for address in evicted if address not in fresh]
        return new, evicted

    def forget(self, addresses):
        for address in addresses:
            self.entries.pop(address, None)
            self.names.pop(address, None)

    def expire(self, now):
        """ remove and return all addresses which expired before now """
        expired = [address for address, expires in self.entries.items() if expires <= now]
        for address in expired:
            del self.entries[address]
            self.names.pop(address, None)
        return expired

    def listing(self, now):
        """ the learned addresses for display: address, seconds until expiry, domain names """
        return [
            {'address': address, 'expires_in': max(0, round(expires - now)), 'names': self.names.get(address, [])}
            for address, expires in self.entries.items()
        ]

    def status(self, now):
        next_expiry = min(self.entries.values()) if self.entries else None
        return {
            'entries': len(self.entries),
            'max_entries': self.max_entries,
            'learned_total': self.learned_total,
            'evicted_total': self.evicted_total,
            'last_learned': self.last_learned,
            'next_expiry_in': round(next_expiry - now) if next_expiry is not None else None,
        }


class Store:
    """ all tables managed by wildcardaliases """

    def __init__(self):
        self.tables = {}

    def reconfigure(self, limits):
        """ align the set of tables with the configuration

            :param limits: dict table name -> max_entries
            :return: list of table names that are no longer configured (removed from the store)
        """
        removed = [name for name in self.tables if name not in limits]
        for name in removed:
            del self.tables[name]
        for name, max_entries in limits.items():
            if name in self.tables:
                self.tables[name].max_entries = max_entries
            else:
                self.tables[name] = TableState(name, max_entries)
        return removed

    def get(self, name):
        return self.tables[name]

    def to_state(self):
        return {
            name: {
                address: {'expires': expires, 'names': table.names.get(address, [])}
                for address, expires in table.entries.items()
            }
            for name, table in self.tables.items()
        }

    def load_state(self, state, now):
        """ restore entries saved by to_state(), skipping expired ones and unknown tables """
        if not isinstance(state, dict):
            return
        for name, entries in state.items():
            table = self.tables.get(name)
            if table is None or not isinstance(entries, dict):
                continue
            for address, value in entries.items():
                # before 1.1 the state only held the expiry time
                names = []
                if isinstance(value, dict):
                    names = value.get('names') if isinstance(value.get('names'), list) else []
                    value = value.get('expires')
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > now:
                    table.entries[address] = value
                    table.names[address] = []
                    for name in names[-MAX_NAMES:]:
                        if isinstance(name, str):
                            _remember(table.names[address], name)
            # respect a possibly lowered limit
            overflow = len(table.entries) - table.max_entries
            if overflow > 0:
                for address, _ in sorted(table.entries.items(), key=lambda item: item[1])[:overflow]:
                    del table.entries[address]
                    table.names.pop(address, None)
