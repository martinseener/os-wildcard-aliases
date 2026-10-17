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
import asyncio
import os
import tempfile

# keep command lines short, pfctl accepts many addresses per call
CHUNK_SIZE = 256


class BackendError(Exception):
    pass


class MemoryBackend:
    """ in-memory table backend, used for tests and --dry-run """

    def __init__(self):
        self.tables = {}
        self.calls = []

    async def add(self, table, addresses):
        self.calls.append(('add', table, sorted(addresses)))
        self.tables.setdefault(table, set()).update(addresses)

    async def delete(self, table, addresses):
        self.calls.append(('delete', table, sorted(addresses)))
        self.tables.setdefault(table, set()).difference_update(addresses)

    async def replace(self, table, addresses):
        self.calls.append(('replace', table, sorted(addresses)))
        self.tables[table] = set(addresses)


class PfctlBackend:
    """ manage pf tables using pfctl(8) """

    def __init__(self, pfctl='/sbin/pfctl', tmpdir=None):
        self.pfctl = pfctl
        self.tmpdir = tmpdir

    async def _run(self, *args):
        proc = await asyncio.create_subprocess_exec(
            self.pfctl, *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise BackendError('pfctl %s failed (%d): %s' % (
                ' '.join(args[:4]), proc.returncode, stderr.decode(errors='replace').strip()
            ))

    async def add(self, table, addresses):
        addresses = list(addresses)
        for idx in range(0, len(addresses), CHUNK_SIZE):
            await self._run('-q', '-t', table, '-T', 'add', *addresses[idx:idx + CHUNK_SIZE])

    async def delete(self, table, addresses):
        addresses = list(addresses)
        for idx in range(0, len(addresses), CHUNK_SIZE):
            await self._run('-q', '-t', table, '-T', 'delete', *addresses[idx:idx + CHUNK_SIZE])

    async def replace(self, table, addresses):
        # replace is atomic in pf, feed the (possibly long) list via a file
        fd, path = tempfile.mkstemp(prefix='wildcardaliases.', dir=self.tmpdir)
        try:
            with os.fdopen(fd, 'w') as fhandle:
                fhandle.write('\n'.join(addresses))
                fhandle.write('\n')
            await self._run('-q', '-t', table, '-T', 'replace', '-f', path)
        finally:
            os.unlink(path)
