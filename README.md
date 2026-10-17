# os-wildcard-aliases: DNS-learned wildcard FQDN aliases for OPNsense

OPNsense host aliases are resolved by the firewall itself, ahead of time. That cannot work for
wildcards like `*.core.windows.net` (DNS has no way to list all names below a domain), and it is
unreliable even for exact names served by CDNs, where the firewall and the client get different
addresses.

Wildcard Aliases takes the approach of commercial firewalls ("learned" or "wildcard" FQDN objects):
it watches the DNS answers your clients actually receive and puts the returned addresses into a pf
table, *before* the answer reaches the client. Each table is exposed as a regular firewall alias
(type "External (advanced)"), so you can write rules such as:

> pass `TCP 443` from `app_server` to alias `azure_storage` (= `*.blob.core.windows.net`), block the rest

## How it works

```
client ──DNS──▶ resolver (AdGuard Home / Unbound) ──[all or watched domains]──▶ wildcardaliasesd :5354 ──▶ upstream resolver
                                                                                    │
                                             1. answer for a watched name arrives   │
                                             2. A/AAAA (+ HTTPS hints) added to pf  ▼
                                             3. only then the answer is returned    pf table <azure_storage>
```

* **Patterns:** `example.com` matches that name only, `*.example.com` matches any name below it
  (not `example.com` itself).
* **What is learned:** the A and AAAA records of the queried name and its CNAME chain, plus the
  `ipv4hint`/`ipv6hint` of HTTPS/SVCB records. Records not belonging to the chain are ignored.
* **Expiry:** pf tables have no per-entry lifetime, so the daemon tracks one per address:
  `max(longest TTL in the chain + grace, minimum hold)`, capped by the maximum hold time.
  Expired addresses are removed every few seconds. Established connections keep working, because
  pf tracks them by state, not by table.
* **Restarts:** learned addresses are saved to `/var/db/wildcardaliases/state.json` and restored on start.
  pf tables are replaced on start, so leftovers from earlier runs and from disabled aliases disappear.
* **Failure mode:** if a table cannot be updated, the answer becomes SERVFAIL by default ("closed").
  The client then can't resolve the name instead of resolving it and being blocked.

## Installation

The plugin is published in [martinseener/opnsense-repo](https://github.com/martinseener/opnsense-repo).
On the firewall, as root:

```sh
fetch -o /usr/local/etc/pkg/repos/martinseener.conf https://martinseener.github.io/opnsense-repo/martinseener.conf
pkg update
```

Then install **os-wildcard-aliases** under System → Firmware → Plugins. Removing the package keeps
its settings and the aliases it created, like other OPNsense plugins.

## Installation (development)

The plugin follows the layout of [opnsense/plugins](https://github.com/opnsense/plugins), so it
can be built like any other plugin later. Until there is a package, copy it onto a **test** box:

```sh
make test                                   # unit tests, no firewall needed
make install-dev HOST=user@firewall         # copies the files, restarts configd
make uninstall-dev HOST=user@firewall       # removes everything again (see below)
```

`make uninstall-dev` stops the daemon and removes the plugin files, its settings in config.xml, the
aliases it created, their pf tables and the learned state. Aliases still used by rules or other
aliases are kept, with their tables emptied, and listed as "kept"; remove those references first for
a complete removal. To remove only the files and keep settings, aliases and state, run
`tools/install-dev.sh --uninstall --keep-config user@firewall`. Older revisions under System →
Configuration → History still contain the settings until they rotate out.

No extra packages are needed: dnspython is already a dependency of OPNsense core.

## Configuration

1. **Firewall → Wildcard Aliases → Settings**: enable it, keep listen address `127.0.0.1:5354`, and set
   the upstream that really resolves the names (see below).
2. **Aliases** tab: add a wildcard alias, e.g. name `azure_storage`, patterns `*.blob.core.windows.net`.
   Apply. The alias `azure_storage` appears under Firewall → Aliases.
3. **Send DNS through the daemon.** There are two modes:

   * **All queries** (recommended): the resolver forwards *every* query to the daemon. Names that
     don't match any pattern pass through unchanged, so new aliases work without touching the
     resolver again. Every cache miss of the resolver goes through the daemon, which is no problem
     at home or small-office scale.
   * **Watched domains only**: the resolver forwards only the watched domains. Fewer queries reach
     the daemon, so a problem with it affects only those domains, but every new alias needs a
     second change in the resolver. Good for a first test.

   How to set this up depends on your resolver:

   **AdGuard Home in front of Unbound** (AdGuard on port 53, Unbound on `127.0.0.1:5353`). Set the
   daemon's upstream to Unbound, `127.0.0.1:5353`. Then in AdGuard → Settings → DNS settings:

   | | All queries | Watched domains only |
   |---|---|---|
   | Upstream DNS servers | `127.0.0.1:5354` (this line only) | `[/blob.core.windows.net/]127.0.0.1:5354`<br>`127.0.0.1:5353` |
   | Fallback DNS servers | `127.0.0.1:5353` | unchanged |
   | Bootstrap DNS servers | `127.0.0.1:5353` | unchanged |

   * In "all queries" mode, don't list Unbound as a second *upstream*. With load balancing or
     parallel requests, AdGuard would send part of the queries past the daemon. As a *fallback* it
     keeps DNS working if the daemon is down. Nothing is learned meanwhile, so rules using the
     aliases miss new addresses, which is the safe direction.
   * Turn off **optimistic caching**: it hands out expired answers without asking upstream, after
     the daemon has already removed the addresses. If you override the minimum cache TTL, keep it
     at or below the daemon's minimum hold (default 300 s).
   * AdGuard keeps the real client addresses for its statistics and per-client settings, because it
     stays in front.

   **Unbound only** (default OPNsense):

   * *All queries*: put the daemon in front of Unbound. In Services → Unbound DNS → General, set the
     port to `5353`, and limit it to Localhost if possible. Otherwise block port 5353 to the firewall
     from your networks. Set the daemon's listen addresses to the firewall's interface addresses on
     port 53 (e.g. `192.168.1.1:53,[fd00::1]:53`) and its upstream to `127.0.0.1:5353`. Point DNS
     redirect rules, if you have any, at port 53 as before. The daemon has no cache of its own, and
     Unbound sees all queries as coming from 127.0.0.1, so per-client Unbound statistics and
     blocklist reports stop being useful.
   * *Watched domains only*: Services → Unbound DNS → Query Forwarding, domain
     `blob.core.windows.net`, server `127.0.0.1` port `5354`. The daemon's upstream then has to be a
     resolver that does *not* forward back to the daemon, e.g. your provider's or a public resolver,
     otherwise the query loops. Note that Unbound caches CNAME targets separately: if a CNAME leads
     out of the watched domain (common with `*.trafficmanager.net`), later lookups of the target
     bypass the daemon. Add those target domains as patterns too, or use "all queries" mode.

   **Per-client (strictest)**: redirect DNS of the restricted hosts (NAT port forward, UDP/TCP 53)
   to the daemon's listen address, with your normal resolver as upstream. The daemon then sees every
   query of those hosts, and forwards the ones that don't match unchanged.

   To check: `configctl wildcardaliases stats` counts every query that reaches the daemon. Answers
   from the resolver's cache, names the resolver blocks, and repeated lookups of non-existent names
   never reach it.

4. **Rules**: use the alias as destination, typically "pass TCP/443 to `azure_storage`" followed by
   a block rule. Block **UDP 443** (QUIC) for those hosts as well, unless the alias also covers it.
5. Clients must not bypass your resolver (DoH/DoT), otherwise nothing is learned for them, and the
   rule fails closed.

## Security considerations

* **Wildcards include other people's names.** Anyone can create `something.blob.core.windows.net`.
  An allow rule for `*.blob.core.windows.net` therefore also allows uploads to storage accounts that
  aren't yours. Be as specific as the service allows.
* **Shared addresses.** CDN and cloud addresses serve many names. An allowed address is allowed for
  everything hosted on it. For strict control, combine Wildcard Aliases with SNI inspection (e.g. Suricata
  `tls.sni` rules).
* **Learning is global.** An address learned from any client's query is in the table for every
  rule using the alias. Restrict the *source* in your rules.
* **Table size** is limited per alias (`max_entries`, default 4096). This also limits the damage
  from someone deliberately resolving many random names below a watched domain.

## Development

```
net/wildcard-aliases/         the plugin (copy into a fork of opnsense/plugins to build a package)
  src/opnsense/scripts/OPNsense/WildcardAliases/
    wildcardaliasesd.py       daemon entry point (-f foreground, -t config test, --dry-run)
    wildcardaliases/          matcher, answer extraction, table store, pf backend, server
  src/opnsense/mvc/...        model, API, GUI
  src/opnsense/service/...    configd actions and templates
tests/                        pytest suite (fake upstream DNS server, in-memory pf backend)
tests/php/                    PHPUnit tests running the model against opnsense/core
tools/install-dev.sh          copy onto a test firewall, or remove from it again
tools/purge.php               config.xml cleanup used by the uninstall
```

| Target | What it does |
|---|---|
| `make test` | Python tests (daemon, templates), no firewall needed |
| `make php-test` | PHP model/controller tests against a fresh `opnsense/core` checkout, then renders the config.xml written by PHP with core's **real** template engine and validates the result with the daemon's parser |
| `make lint` | flake8 + `php -l` |
| `make check` | all of the above |
| `make run CONFIG=…` | daemon in the foreground without touching pf, try it with `dig -p 5354 @127.0.0.1 …` |

The PHP tests need the PHP and Phalcon versions OPNsense ships. On macOS:

```sh
brew tap shivammathur/php && brew tap shivammathur/extensions
brew install shivammathur/php/php@8.4 shivammathur/extensions/phalcon5@8.4 phpunit
```

### Roadmap / ideas

* Per-client tables (learn addresses per source address).
* Optional built-in Unbound integration that avoids the CNAME caching caveat.
* Widget for the OPNsense dashboard.
* Submit to opnsense/plugins.

## License

BSD 2-Clause, see [LICENSE](LICENSE). This is the license used by OPNsense itself.
