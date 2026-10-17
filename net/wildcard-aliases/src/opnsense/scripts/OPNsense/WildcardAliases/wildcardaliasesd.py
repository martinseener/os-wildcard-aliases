#!/usr/local/bin/python3

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

    wildcardaliasesd: DNS forwarder feeding pf tables with the addresses of wildcard domain patterns
"""
import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/usr/local/opnsense/site-python')

from wildcardaliases import __version__, config as configuration  # noqa: E402
from wildcardaliases.backend import MemoryBackend, PfctlBackend  # noqa: E402
from wildcardaliases.server import WildcardAliasesServer  # noqa: E402

log = logging.getLogger('wildcardaliases')


def setup_logging(foreground):
    if foreground or not os.path.exists('/var/run/log'):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    else:
        handler = logging.handlers.SysLogHandler(
            address='/var/run/log', facility=logging.handlers.SysLogHandler.LOG_DAEMON
        )
        handler.setFormatter(logging.Formatter('wildcardaliases[%(process)d]: %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def read_state(path):
    try:
        with open(path) as fhandle:
            return json.load(fhandle)
    except (OSError, ValueError):
        return None


async def run(args):
    cfg = configuration.load(args.config)
    backend = MemoryBackend() if args.dry_run else PfctlBackend(pfctl=args.pfctl)
    server = WildcardAliasesServer(cfg, backend)
    await server.start(read_state(cfg.state_file))
    log.info('wildcardaliases %s started with %d aliases%s', __version__, len(cfg.aliases),
             ' (dry run)' if args.dry_run else '')

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def reload():
        async def do_reload():
            try:
                await server.reload(configuration.load(args.config))
            except configuration.ConfigError as exc:
                log.error('reload failed, keeping current configuration: %s', exc)
        server.spawn(do_reload())

    loop.add_signal_handler(signal.SIGHUP, reload)
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    loop.add_signal_handler(signal.SIGINT, stopping.set)

    await stopping.wait()
    log.info('shutting down')
    await server.stop()


def main():
    parser = argparse.ArgumentParser(description='DNS-learned wildcard FQDN aliases for pf')
    parser.add_argument('-c', '--config', default='/usr/local/etc/wildcardaliases/wildcardaliases.json',
                        help='configuration file [json]')
    parser.add_argument('-p', '--pid', default='/var/run/wildcardaliases.pid', help='pid file location')
    parser.add_argument('-f', '--foreground', action='store_true', help='run (and log) in foreground')
    parser.add_argument('-t', '--test', action='store_true', help='validate the configuration and exit')
    parser.add_argument('--dry-run', action='store_true', help='do not touch pf, keep tables in memory')
    parser.add_argument('--pfctl', default='/sbin/pfctl', help='path to pfctl')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()

    if args.test:
        try:
            cfg = configuration.load(args.config)
        except configuration.ConfigError as exc:
            print('configuration error: %s' % exc)
            sys.exit(1)
        print('configuration ok (%d aliases)' % len(cfg.aliases))
        sys.exit(0)

    def start():
        # set up logging after daemonizing, Daemonize closes all inherited file descriptors
        setup_logging(args.foreground)
        try:
            asyncio.run(run(args))
        except configuration.ConfigError as exc:
            log.error('configuration error: %s', exc)
            sys.exit(1)

    if args.foreground:
        start()
    else:
        from daemonize import Daemonize
        Daemonize(app='wildcardaliases', pid=args.pid, action=start, foreground=False).start()


if __name__ == '__main__':
    main()
