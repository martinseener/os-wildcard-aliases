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
import dns.name


def normalize(name):
    """ canonical lowercase form without trailing dot, IDN names are converted to punycode
        :param name: str or dns.name.Name
        :return: str
    """
    if not isinstance(name, dns.name.Name):
        name = dns.name.from_text(name)
    return name.to_text(omit_final_dot=True).lower()


class Matcher:
    """ match query names against domain patterns.

        Supported pattern forms:
            example.com      exact name only
            *.example.com    any name below example.com (not example.com itself)
    """

    def __init__(self, entries=()):
        """
            :param entries: iterable of (target, patterns) tuples, target is usually a table name
        """
        self._exact = {}
        self._wildcard = {}
        for target, patterns in entries:
            for pattern in patterns:
                self.add(pattern, target)

    @staticmethod
    def parse(pattern):
        """ validate a pattern
            :return: tuple (is_wildcard, normalized domain)
            :raises ValueError: on invalid patterns
        """
        pattern = pattern.strip()
        wildcard = pattern.startswith('*.')
        domain = pattern[2:] if wildcard else pattern
        if not domain or '*' in domain:
            raise ValueError("invalid pattern %r, only a leading '*.' is supported" % pattern)
        try:
            domain = normalize(domain)
        except Exception as exc:
            raise ValueError("invalid pattern %r: %s" % (pattern, exc))
        if domain in ('', '.'):
            raise ValueError("invalid pattern %r, refusing to match the root" % pattern)
        return wildcard, domain

    def add(self, pattern, target):
        wildcard, domain = self.parse(pattern)
        index = self._wildcard if wildcard else self._exact
        index.setdefault(domain, set()).add(target)

    def match(self, qname):
        """ return the set of targets whose patterns match qname
            :param qname: str or dns.name.Name
            :return: frozenset
        """
        try:
            name = normalize(qname)
        except Exception:
            return frozenset()
        result = set(self._exact.get(name, ()))
        labels = name.split('.')
        # walk the proper parent domains: a.b.example.com -> b.example.com, example.com, com
        for idx in range(1, len(labels)):
            targets = self._wildcard.get('.'.join(labels[idx:]))
            if targets:
                result.update(targets)
        return frozenset(result)

    def __bool__(self):
        return bool(self._exact or self._wildcard)
