#!/usr/bin/env python3
"""Read a server list from a JSON or CSV file and return a list of IP addresses.

The input file is the iperf3 server list as published by
https://iperf3serverlist.net/ (its "Export JSON" / "Export CSV" buttons), so
records look like:

    {"IP/HOST": "160.242.19.254", "PORT": "9205-9240", "COUNTRY": "AO", ...}

Any JSON or CSV file with an IP/HOST column works, as does a JSON array of bare
addresses. Roughly half of the listed entries are DNS names (e.g.
speedtestfl.telecom.mu) rather than IP literals, so those are resolved to IPv4
addresses. A name the system resolver rejects is retried against public
resolvers over the wire, which is what recovers names that mDNSResponder has
negatively cached; anything still unresolved after that is reported on stderr
rather than dropped silently. Duplicates are dropped, so every element is a
distinct address that ping/traceroute/geolocation can be pointed at directly.

Use as a library:

    from fetch_servers import fetch_ips
    ips = fetch_ips("servers.json")

or as a CLI, which prints one address per line:

    ./fetch_servers.py servers.json
    ./fetch_servers.py servers.csv > ips.txt

Standard library only; no pip install required.
"""

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import random
import socket
import struct
import sys
import time
from pathlib import Path

# Column names accepted for the host field, in order of preference.
HOST_KEYS = ("IP/HOST", "IP", "HOST", "HOSTNAME")
SUPPORTED_SUFFIXES = (".json", ".csv")

# Queried directly, over the wire, when the system resolver fails a name.
PUBLIC_DNS_SERVERS = ("8.8.8.8", "1.1.1.1")

# Extra passes over the names that failed. The first retry is immediate, since
# it goes straight to a public resolver and has no cache to wait out; later
# rounds pause a growing amount to let a genuinely flaky server recover.
DNS_RETRY_ROUNDS = 4
DNS_RETRY_DELAY = 2.0


def fetch_ips(path: str | Path) -> list[str]:
    """Read a server list file and return all of its distinct IP addresses.

    Hostnames are resolved to IPv4 and anything that cannot be resolved is
    skipped. Addresses come back in file order.

    Raises FileNotFoundError if the file is missing and ValueError if it is not
    readable JSON/CSV carrying a host column.
    """
    hosts = read_hosts(path)
    resolved = _resolve_hosts(hosts)

    unresolved = [h for h, ip in resolved.items() if not ip]
    if unresolved:
        print(
            f"warning: {len(unresolved)} host(s) still unresolved after "
            f"{DNS_RETRY_ROUNDS} retry round(s) and excluded: "
            f"{', '.join(unresolved)}",
            file=sys.stderr,
        )

    ips: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        ip = resolved.get(host)
        # Distinct hostnames sometimes share one machine, and at least one
        # address is listed both as a literal and behind a name.
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)

    return ips


def read_hosts(path: str | Path) -> list[str]:
    """Validate a JSON or CSV server list file and return its host column.

    The values are returned exactly as written, so a mix of IP literals and DNS
    names is expected. Resolution happens later, in fetch_ips.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such file: {path}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        expected = " or ".join(SUPPORTED_SUFFIXES)
        raise ValueError(
            f"unsupported file type '{suffix or path.name}': expected {expected}"
        )

    try:
        # utf-8-sig strips the byte-order mark that spreadsheet exports add.
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    if not text.strip():
        raise ValueError(f"{path} is empty")

    records = _parse_json(text, path) if suffix == ".json" else _parse_csv(text, path)
    if not records:
        raise ValueError(f"{path} contained no server records")

    hosts = []
    for record in records:
        host = record if isinstance(record, str) else _host_of(record)
        host = host.strip().rstrip(".")
        if host:
            hosts.append(host)
    if not hosts:
        raise ValueError(
            f"{path} has no usable host values; expected a "
            f"{' / '.join(HOST_KEYS)} column"
        )
    return hosts


def _parse_json(text: str, path: Path) -> list:
    """Parse a JSON server list: a list of records, or of bare addresses."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    # Tolerate the list being wrapped, as in {"servers": [...]}.
    records = data.get("servers", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError(f"{path} should hold a list of servers, found {type(records).__name__}")
    usable = [r for r in records if isinstance(r, (dict, str))]
    if not usable:
        raise ValueError(f"{path} holds no objects or address strings")
    return usable


def _parse_csv(text: str, path: Path) -> list[dict]:
    """Parse a CSV server list, which must have a header naming the host column."""
    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    except csv.Error as exc:
        raise ValueError(f"{path} is not valid CSV: {exc}") from exc

    headers = {(name or "").strip().upper() for name in fieldnames}
    if not headers & set(HOST_KEYS):
        found = ", ".join(fieldnames) if fieldnames else "none"
        raise ValueError(
            f"{path} has no host column; expected one of "
            f"{', '.join(HOST_KEYS)} but the header is: {found}"
        )
    return rows


def _host_of(record: dict) -> str:
    """Read the host field, tolerating header drift between exports."""
    for key in HOST_KEYS:
        for actual, value in record.items():
            if actual and actual.strip().upper() == key:
                return str(value or "").strip()
    return ""


def _resolve(host: str) -> str | None:
    """Resolve one host to an address, preferring IPv4. None if it fails.

    IP literals pass through untouched. Otherwise this asks for an A record
    first and only falls back to an unrestricted lookup if that yields nothing,
    so a dual-stack host still comes back as IPv4. This is a single attempt;
    retrying is _resolve_hosts's job.
    """
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass

    for want in (socket.AF_INET, socket.AF_UNSPEC):
        try:
            infos = socket.getaddrinfo(host, None, want, socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError, OSError):
            continue
        # AF_UNSPEC can list IPv6 first; keep preferring IPv4.
        for info in sorted(infos, key=lambda i: 0 if i[0] == socket.AF_INET else 1):
            return info[4][0]
    return None


def _skip_name(reply: bytes, offset: int) -> int:
    """Return the offset just past a (possibly compressed) DNS name."""
    while offset < len(reply):
        length = reply[offset]
        if length & 0xC0 == 0xC0:  # compression pointer: 2 bytes, ends the name
            return offset + 2
        offset += 1
        if length == 0:
            return offset
        offset += length
    return offset


def _first_a_record(reply: bytes, query_id: int) -> str | None:
    """Pull the first A record out of a DNS reply, or None if there is none."""
    try:
        if len(reply) < 12 or struct.unpack("!H", reply[:2])[0] != query_id:
            return None
        flags, qdcount, ancount = struct.unpack("!HHH", reply[2:8])
        if flags & 0x000F:  # RCODE is non-zero, e.g. NXDOMAIN or SERVFAIL
            return None
        offset = 12
        for _ in range(qdcount):  # skip the echoed question section
            offset = _skip_name(reply, offset) + 4
        for _ in range(ancount):
            offset = _skip_name(reply, offset)
            rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", reply[offset:offset + 10])
            offset += 10
            if rtype == 1 and rdlength == 4:  # an A record holds a 4-byte address
                return socket.inet_ntoa(reply[offset:offset + 4])
            offset += rdlength  # a CNAME or anything else: keep looking
    except (struct.error, OSError):
        return None
    return None


def _query_public_dns(host: str, timeout: float = 3.0) -> str | None:
    """Ask public resolvers for an A record directly, bypassing the OS resolver.

    macOS routes getaddrinfo through mDNSResponder, which will happily serve a
    stale negative answer for a name that resolves fine upstream, so retrying
    getaddrinfo only re-reads that bad cache entry. Going over the wire is what
    actually recovers those names. Returns None if no server gives an answer.
    """
    try:
        labels = [label.encode("idna") for label in host.rstrip(".").split(".") if label]
    except UnicodeError:
        return None
    question = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    question += struct.pack("!HH", 1, 1)  # QTYPE=A, QCLASS=IN

    for server in PUBLIC_DNS_SERVERS:
        query_id = random.randrange(1 << 16)
        # Flags 0x0100: standard query, recursion desired. One question, no RRs.
        packet = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + question
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(packet, (server, 53))
                reply, _ = sock.recvfrom(4096)
        except OSError:
            continue
        address = _first_a_record(reply, query_id)
        if address:
            return address
    return None


def _resolve_hosts(hosts: list[str]) -> dict[str, str | None]:
    """Resolve every host, then keep retrying the ones that failed.

    Retries query public resolvers directly first, because the usual reason a
    name fails here is the local resolver rather than the name itself. Returns a
    host -> address mapping, where None means the name survived every round.
    """
    resolved: dict[str, str | None] = {host: _resolve(host) for host in dict.fromkeys(hosts)}

    for round_number in range(1, DNS_RETRY_ROUNDS + 1):
        pending = [host for host, ip in resolved.items() if not ip]
        if not pending:
            break
        if round_number > 1:
            time.sleep(DNS_RETRY_DELAY * (round_number - 1))
        print(
            f"retrying {len(pending)} unresolved host(s) over the wire, "
            f"round {round_number}/{DNS_RETRY_ROUNDS}",
            file=sys.stderr,
        )
        for host in pending:
            resolved[host] = _query_public_dns(host) or _resolve(host)

    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read a JSON/CSV server list and print one IP address per line.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("path", help="JSON or CSV file listing the servers")
    args = parser.parse_args(argv)

    try:
        ips = fetch_ips(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not ips:
        print(f"error: no addresses resolved from {args.path}", file=sys.stderr)
        return 1

    print("\n".join(ips))
    print(f"({len(ips)} addresses)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
