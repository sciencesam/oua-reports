"""Collect vTools event registrations for whole Regions over a date range.

Three subcommands, meant to be run in order:

  probe    Sign in and fetch ONE event's registrant export, printing what the
           server returns (status, content type, columns). Run this first so
           we know the export's shape before harvesting thousands of events.
  harvest  Build the event inventory for the given regions and date window
           from the public vTools export, then download each event's
           registrant CSV into cache/registrations/<event id>.csv. Resumable:
           already-downloaded events are skipped, so re-runs only fetch new
           events. A few parallel workers share one rate limit (default 2
           requests/second in total) and back off on 429/5xx, so the load on
           vTools stays bounded.
  merge    Combine every cached registrant CSV, plus the event's title, date,
           host OU, section and region, into one registrations.csv.

Sign-in drives the real IEEE login form with your username and password,
always prompted (never taken from the environment or a file). The password
is never written anywhere; the resulting session cookie is saved to
<data-dir>/.vtools_cookie (mode 600) and reused until it expires, then you
are prompted again. `forget` deletes that cookie; do that when you're done.

All state lives under one data directory: --data-dir, or the VTOOLS_DATA
environment variable, or ./cache by default. On the desktop point it at the
big drive, e.g. --data-dir /data/ieee/vtools.

Usage examples:

    python vtools_registrations.py probe 555473
    python vtools_registrations.py --data-dir /data/ieee/vtools harvest --regions R1,R2 --start 2024-09-01
    python vtools_registrations.py harvest --regions R1,R2 --start 2024-09-01 --limit 20
    python vtools_registrations.py merge -o registrations.csv
    python vtools_registrations.py forget

Everything this writes contains personal data. Keep it local.
"""

import argparse
import concurrent.futures
import csv
import shlex
import datetime
import getpass
import io
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse

import requests

from vtools_auth import get_login_headers

vtools_base = "https://events.vtools.ieee.org"
public_export = vtools_base + "/events/export_to_csv"
# Candidate registrant-export paths, tried in order until one returns CSV.
# `probe` reports which one works; harvest uses the first that does.
# Confirmed 2026-09-09: the "Download CSV" link on the event's attendance
# page. Everything the harvester requests is a GET of this one path per
# event; no other admin URL is ever touched.
registration_paths = [
    "/tego_/event/attendance/export_to_csv/{id}",
]
app_sign_in = vtools_base + "/tego_/authentication/sign_in?_landing=/"
# Politeness: a few workers share ONE rate limit on requests to vTools, so the
# total load is bounded no matter how many workers run. Defaults are gentle
# (3 requests/second across all workers); 429 or 5xx responses back off and
# repeated failures abort the run rather than hammer the server.
default_workers = 4
default_rate = 2.0            # max requests per second, total
read_timeout = (10, 900)  # (connect, read). The export is generated on demand and scales with
                          # registrations: a 700-registrant event needs well over 120s. Let it finish
                          # once rather than abandon and re-request it; workers absorb the wait.
max_retries = 4
network_attempts = 2      # a hanging request gets ONE retry, then is parked in failed_ids.txt for a later sweep
abort_after_consecutive_failures = 10
# vTools answers the export URL with HTTP 500 for events that have no vTools
# registration (external registration, "email the organizer", etc.). That is
# a per-event "no export", not an outage, so it is never retried. To still
# catch a real outage, after this many 500s in a row one request is made for
# an event known to export; if THAT fails too, the run aborts.
canary_after_500s = 25
user_agent = "Mozilla/5.0"
# The only host the login form may be posted to.
identity_host = "services10.ieee.org"

# Set by main() from --data-dir / VTOOLS_DATA; everything is written beneath it.
data_dir = "cache"
cookie_file = None
cache_dir = None
inventory_file = None
no_registration_file = None
failed_file = None


def set_data_dir(path):
    global data_dir, cookie_file, cache_dir, inventory_file, no_registration_file, failed_file
    data_dir = os.path.abspath(path)
    cookie_file = os.path.join(data_dir, ".vtools_cookie")
    cache_dir = os.path.join(data_dir, "registrations")
    inventory_file = os.path.join(data_dir, "events_inventory.csv")
    global hosts_file
    hosts_file = os.path.join(data_dir, "events_hosts.csv")
    no_registration_file = os.path.join(data_dir, "no_registration_ids.txt")
    failed_file = os.path.join(data_dir, "failed_ids.txt")   # timed out / connection errors; retried on the next run
    os.makedirs(data_dir, exist_ok=True)


# ---------------------------------------------------------------- session

def load_cookie():
    try:
        with open(cookie_file) as fp:
            return fp.read().strip() or None
    except FileNotFoundError:
        return None


def save_cookie(cookie):
    with open(cookie_file, "w") as fp:
        fp.write(cookie)
    os.chmod(cookie_file, 0o600)


non_interactive = False   # set by --non-interactive: never prompt; exit 3 when a sign-in is needed


def sign_in():
    """Two layers: IEEE SSO (PingAccess) and then the vTools app's own
    session, which is only established by visiting its sign_in page with
    the PingAccess cookie. Keep every cookie both layers set."""
    if non_interactive:
        print("vTools session expired or missing and --non-interactive is set. "
              "Sign in again (run `probe` interactively, or copy a fresh .vtools_cookie), "
              "then re-run; progress is saved.", flush=True)
        sys.exit(3)
    user = input("IEEE username: ").strip()
    password = getpass.getpass("IEEE password: ")
    # Prompt once; retry the network side a few times, because the IEEE
    # auth/proxy tier intermittently 403s the OIDC callback or times out.
    delay = 8.0
    for attempt in range(1, 3):
        try:
            pa_cookie = get_login_headers(user, password, identity_host)["Cookie"]
            name, _, value = pa_cookie.partition("=")
            session = requests.Session()
            session.headers["User-Agent"] = user_agent
            session.cookies.set(name.strip(), value.strip(), domain="events.vtools.ieee.org")
            response = session.get(app_sign_in, timeout=120, allow_redirects=True)
            cookie = "; ".join(f"{c.name}={c.value}" for c in session.cookies)
            if "_session_id" not in {c.name for c in session.cookies}:
                raise requests.HTTPError("vTools app session not established (no _session_id)")
            logging.info("Signed in; cookies: %s", ", ".join(c.name for c in session.cookies))
            save_cookie(cookie)
            return cookie
        except (requests.RequestException, AssertionError) as exc:
            if attempt == 2:
                logging.error("Scripted sign-in keeps failing (often a 403 on the OIDC callback). "
                              "Use the browser handoff instead: sign in to vTools in your browser, "
                              "DevTools > Network > any request > Copy as cURL into my_vtools_curl.txt, "
                              "then run: vtools_registrations.py set-cookie")
                raise
            logging.warning("Sign-in attempt %d failed (%s); the IEEE auth server is flaky, "
                            "retrying in %.0fs (not re-prompting)", attempt, exc, delay)
            time.sleep(delay)
            delay *= 2


def make_session(cookie):
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Cookie": cookie})
    return session


def looks_like_login_page(response):
    """PingAccess answers an expired/missing session with the OIDC login
    shim (HTTP 200 + HTML mentioning the IdP client) or a redirect."""
    if response.status_code in (301, 302, 303, 307):
        return True
    head = response.content[:4000]
    return b"PF_AS_FOR_PA" in head or b"authorization.oauth2" in head


def get_session(force_login=False):
    cookie = None if force_login else load_cookie()
    if cookie is not None and ";" not in cookie:
        logging.info("Saved session predates app-level sign-in; signing in again")
        cookie = None
    if cookie is None:
        cookie = sign_in()
    return make_session(cookie)


def cookie_from_curl(text):
    """Pull the Cookie header out of a browser DevTools 'Copy as cURL'."""
    try:
        tokens = shlex.split(text.replace("\\\n", " ").replace("^\n", " "))
    except ValueError:
        tokens = text.split()
    def clean(value):
        value = value.strip()
        if value.startswith("$"):            # bash $'...' ANSI-C quoting
            value = value[1:]
        return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), value)
    for i, tok in enumerate(tokens):
        if tok in ("-b", "--cookie") and i + 1 < len(tokens):
            return clean(tokens[i + 1])
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            header = tokens[i + 1]
            if header.lower().startswith("cookie:"):
                return clean(header.split(":", 1)[1])
    return None


def set_cookie(args):
    """Save a vTools session captured from the browser, bypassing the
    scripted login. Validates it with one read-only request."""
    if args.cookie:
        cookie = args.cookie.strip()
    else:
        source = args.from_curl or "my_vtools_curl.txt"
        if not os.path.exists(source):
            print(f"Paste your browser's 'Copy as cURL' of any vTools request into {source}, "
                  f"then re-run: set-cookie --from-curl {source}", flush=True)
            sys.exit(1)
        with open(source) as fp:
            cookie = cookie_from_curl(fp.read())
        if not cookie:
            print(f"Could not find a Cookie header in {source}.", flush=True)
            sys.exit(1)
    names = {c.split("=", 1)[0].strip() for c in cookie.split(";") if "=" in c}
    session = make_session(cookie)
    path, response = fetch_registrations(session, "551815")
    if path and path not in ("LOGIN", "FAILED"):
        save_cookie(cookie)
        print(f"Session saved to {cookie_file}. Cookies: {', '.join(sorted(names))}. "
              f"Verified with a live export. You can harvest now.")
    else:
        print(f"That cookie did not work (probe returned {path}). Make sure you copied a request "
              f"while signed in to events.vtools.ieee.org.", flush=True)
        sys.exit(1)


def forget(args):
    if cookie_file and os.path.exists(cookie_file):
        os.remove(cookie_file)
        print(f"Deleted {cookie_file}")
    else:
        print("No saved session cookie.")


# -------------------------------------------------------------- inventory

def vtools_time(date):
    """Format a date the way the public export URL expects (midnight)."""
    return date.strftime("%d %b %Y 12:00 AM")


def fetch_events(region, start, end):
    """Public event export for one region, one date window. Yields dict rows."""
    params = {
        "meeting[region_spoid]": "R0" if region == "R10" else region,
        "meeting_after": vtools_time(start),
        "meeting_before": vtools_time(end),
        "order": "start_time",
    }
    url = public_export + "?" + urllib.parse.urlencode(params)
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=120)
    response.raise_for_status()
    text = response.content.decode("utf-8", "replace").replace("\0", "")
    return list(csv.DictReader(io.StringIO(text)))


def month_windows(start, end):
    """Split [start, end] into calendar-month windows so no single export
    is huge."""
    current = start
    while current < end:
        nxt = (current.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        yield current, min(nxt, end)
        current = nxt


# Each region's public export lists only the hosts inside that region, so an
# event co-hosted across regions appears once per region with a partial host
# list; these list-valued columns are merged across the copies.
host_list_fields = ("Region", "Section", "SPOID", "Organizational Unit", "Meeting Hosts Spoid",
                    "Meeting Hosts Submitter", "Reported On")


def merge_host_lists(kept, new):
    for field in host_list_fields:
        if field not in kept or field not in new:
            continue
        seen = [v.strip() for v in kept[field].split(";" if field == "Organizational Unit" else ",") if v.strip()]
        for value in new[field].split(";" if field == "Organizational Unit" else ","):
            if value.strip() and value.strip() not in seen:
                seen.append(value.strip())
        kept[field] = ("; " if field == "Organizational Unit" else ", ").join(seen)
    return kept


def build_inventory(regions, start, end):
    events = dict()
    for region in regions:
        for w_start, w_end in month_windows(start, end):
            rows = fetch_events(region, w_start, w_end)
            for row in rows:
                if row.get("Id"):
                    if row["Id"] in events:
                        merge_host_lists(events[row["Id"]], row)
                    else:
                        events[row["Id"]] = row
            logging.info("%s %s..%s: %d events (%d total so far)",
                         region, w_start, w_end, len(rows), len(events))
            time.sleep(0.5)
    if events:
        with open(inventory_file, "w", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(next(iter(events.values())).keys()))
            writer.writeheader()
            writer.writerows(events.values())
    logging.info("Inventory: %d events written to %s", len(events), inventory_file)
    return events


events_api = vtools_base + "/api/public/v5/events/list"
hosts_file = None     # set in main from data_dir


def fetch_event_json(event_id):
    """One event from the public Events List API (JSON), which unlike the CSV
    export distinguishes the primary host from co-hosts."""
    params = {"id": event_id, "published": "all", "span": "2000-01-01~2099-12-31"}
    response = requests.get(events_api, params=params, headers={"User-Agent": user_agent}, timeout=60)
    response.raise_for_status()
    items = response.json().get("data", [])
    return items[0]["attributes"] if items else None


def host_row(event_id, attrs):
    ph = attrs.get("primary-host") or {}
    co = attrs.get("cohosts") or []
    return {
        "Id": event_id, "title": attrs.get("title", ""), "start_time": attrs.get("start-time", ""),
        "cancelled": attrs.get("cancelled", ""), "published": attrs.get("publish", ""), "location_type": attrs.get("location-type", ""),
        "primary_host_spoid": ph.get("spoid", ""), "primary_host_name": ph.get("name", ""), "primary_host_type": ph.get("type_description", ""),
        "primary_host_section": ph.get("section_spoids", "") or "", "primary_host_region": ph.get("region_spoids", "") or "",
        "n_cohosts": len(co),
        "cohost_spoids": "; ".join(c.get("spoid", "") for c in co), "cohost_names": "; ".join(c.get("name", "") for c in co),
        "cohost_types": "; ".join(c.get("type_description", "") or "" for c in co),
        "cohost_sections": "; ".join(c.get("section_spoids", "") or "" for c in co),
        "ieee_attending": attrs.get("ieee-attending", ""), "guests_attending": attrs.get("guests-attending", ""),
        "tags": " ".join(attrs.get("tags") or []), "updated_at": attrs.get("updated-at", ""),
        "contact_email": (attrs.get("contact-email") or "").strip().lower(),
        "address1": attrs.get("address1", ""), "city": attrs.get("city", ""), "postal_code": attrs.get("postal-code", ""),
        "latitude": attrs.get("latitude", ""), "longitude": attrs.get("longitude", ""),
    }


def enrich(args):
    """Fetch every inventory event from the Events List API and write
    events_hosts.csv (primary host, co-hosts, reported attendance).
    Resumable: ids already in the file are skipped."""
    events = load_inventory()
    done = set()
    if os.path.exists(hosts_file):
        with open(hosts_file, newline="") as fp:
            done = {row["Id"] for row in csv.DictReader(fp)}
    todo = [i for i in events if i not in done]
    logging.info("%d events in inventory, %d already enriched, %d to fetch", len(events), len(done), len(todo))
    limiter = RateLimiter(args.rate)
    fields = list(host_row("", {}).keys())
    new_file = not os.path.exists(hosts_file)
    raw_file = os.path.join(data_dir, "events_raw.jsonl")     # the full API record per event, for later questions
    with open(hosts_file, "a", newline="") as fp, open(raw_file, "a") as raw:
        writer = csv.DictWriter(fp, fieldnames=fields)
        if new_file:
            writer.writeheader()
        for n, event_id in enumerate(todo, 1):
            limiter.wait()
            try:
                attrs = fetch_event_json(event_id)
            except requests.RequestException as exc:
                logging.warning("%s: %s", event_id, exc)
                time.sleep(5)
                continue
            if attrs is None:
                logging.info("%s: not returned by the API", event_id)
                continue
            writer.writerow(host_row(event_id, attrs))
            raw.write(json.dumps({"id": event_id, "attributes": attrs}) + "\n")
            if n % 100 == 0:
                fp.flush()
                raw.flush()
                logging.info("%d of %d fetched", n, len(todo))
    logging.info("Wrote %s", hosts_file)


def load_inventory():
    with open(inventory_file, newline="") as fp:
        return {row["Id"]: row for row in csv.DictReader(fp)}


# ---------------------------------------------------------------- harvest

def decode_export(data):
    """vTools serves the export as ISO-8859-1; some rows may be UTF-8.
    Try UTF-8 first, fall back to Latin-1 so accented names survive."""
    data = data.replace(b"\0", b"")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("iso-8859-1")


class RateLimiter:
    """Token bucket shared by all workers: at most `rate` requests/second."""

    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_allowed = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            wait_for = max(0.0, self.next_allowed - now)
            self.next_allowed = max(now, self.next_allowed) + self.interval
        if wait_for:
            time.sleep(wait_for)


def polite_get(session, url, limiter):
    """GET with the shared rate limit and exponential backoff on 429/5xx."""
    delay = 2.0
    net_attempts = 0
    for attempt in range(max_retries):
        waited = time.monotonic()
        limiter.wait()
        waited = time.monotonic() - waited
        started = time.monotonic()
        try:
            response = session.get(url, timeout=read_timeout, allow_redirects=False)
        except requests.RequestException as exc:
            net_attempts += 1
            response = None
            if net_attempts >= network_attempts:
                logging.warning("%s for %s after %d attempts; parking it for a later sweep",
                                type(exc).__name__, url.rsplit("/", 1)[-1], net_attempts)
                return None
            logging.warning("%s for %s; retrying once in %.0fs", type(exc).__name__,
                            url.rsplit("/", 1)[-1], delay)
            time.sleep(delay)
            continue
        logging.debug("GET %s -> %s in %.2fs (rate-limit wait %.2fs, %dB)", url.rsplit("/", 1)[-1],
                      response.status_code, time.monotonic() - started, waited, len(response.content))
        if response.status_code in (429, 502, 503, 504):
            retry_after = response.headers.get("Retry-After")
            pause = float(retry_after) if retry_after and retry_after.isdigit() else delay
            logging.warning("HTTP %s from vTools; backing off %.0fs (attempt %d/%d)",
                            response.status_code, pause, attempt + 1, max_retries)
            time.sleep(pause)
            delay *= 2
            continue
        return response
    return response   # None if every attempt raised


def fetch_registrations(session, event_id, paths=None, limiter=None):
    """Try the candidate export paths. Returns (path, response) for the first
    CSV-looking success, or (None, last_response)."""
    last = None
    for path in paths or registration_paths:
        url = vtools_base + path.format(id=event_id)
        response = polite_get(session, url, limiter) if limiter else session.get(url, timeout=read_timeout, allow_redirects=False)
        last = response
        if response is None:
            return "FAILED", None
        if looks_like_login_page(response):
            return "LOGIN", response
        if response.status_code == 200 and b"<html" not in response.content[:300].lower():
            return path, response
    return None, last


def list_manage_links(session, event_id):
    """Read-only: fetch the event's manage page once and print links that
    look like exports. Never follows any of them."""
    r = session.get(vtools_base + f"/tego_/event/manage/{event_id}", timeout=60)
    html = r.content.decode("utf-8", "replace")
    links = sorted(set(l for l in re.findall(r'href="([^"]+)"', html)
                       if re.search(r"export|csv|tsv|download", l, re.I)))
    print("  export-looking links on the manage page (not followed):", links[:10] or "none")


def probe(args):
    session = get_session()
    path, response = fetch_registrations(session, args.event_id)
    if path == "LOGIN":
        print("Session cookie rejected; signing in again.")
        session = get_session(force_login=True)
        path, response = fetch_registrations(session, args.event_id)
    print(f"event {args.event_id}: status {response.status_code}, "
          f"content-type {response.headers.get('content-type', '?')}, {len(response.content)} bytes")
    if path == "FAILED":
        print("Network failure talking to vTools after retries; try again in a minute.")
        return
    if path in (None, "LOGIN"):
        html = response.content.decode("utf-8", "replace")
        links = sorted(set(re.findall(r'(?:href|action)="([^"]*(?:csv|export|xls|download|registra)[^"]*)"', html, re.I)))
        title = re.search(r"<title>([^<]*)", html, re.I)
        print("No candidate path returned CSV.")
        print("  page title:", title.group(1).strip() if title else "?")
        print("  export-looking links/forms:", links[:12] or "none")
        print("  signed-in marker present:", bool(re.search(r"sign_out|logout|Signed in as", html, re.I)))
        list_manage_links(session, args.event_id)
        print("Paste this output back and we'll adjust the path list.")
        return
    rows = list(csv.DictReader(io.StringIO(decode_export(response.content))))
    print(f"working path: {path}")
    print(f"rows: {len(rows)}")
    print("columns:", list(rows[0].keys()) if rows else "(no rows)")
    if rows:
        sample = {k: ("***" if re.search(r"name|email|phone|address", k, re.I) else v)
                  for k, v in rows[0].items()}
        print("first row (names/emails redacted):", sample)


def harvest(args):
    regions = [r.strip().upper() for r in args.regions.split(",") if r.strip()]
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end) if args.end else datetime.date.today()
    if args.skip_inventory and os.path.exists(inventory_file):
        events = load_inventory()
        logging.info("Using existing inventory: %d events", len(events))
    else:
        events = build_inventory(regions, start, end)

    os.makedirs(cache_dir, exist_ok=True)
    no_registration = set()
    if os.path.exists(no_registration_file):
        with open(no_registration_file) as fp:
            no_registration = {line.strip() for line in fp if line.strip()}

    cached = sum(1 for eid in events if os.path.exists(os.path.join(cache_dir, f"{eid}.csv")))
    known_empty = len(no_registration & set(events))
    todo = [eid for eid in events if not os.path.exists(os.path.join(cache_dir, f"{eid}.csv"))
            and eid not in no_registration]
    if args.limit:
        todo = todo[:args.limit]
    previously_failed = set()
    if os.path.exists(failed_file):
        with open(failed_file) as fp:
            previously_failed = {line.strip() for line in fp if line.strip()}
    logging.info("%d events in scope: %d already cached, %d known to have no registration, "
                 "%d to fetch now (%d of them failed with network errors last time and will be retried)",
                 len(events), cached, known_empty, len(todo), len(previously_failed & set(todo)))

    session = get_session()
    limiter = RateLimiter(args.rate)
    fetched = empty = 0
    consecutive_failures = 0
    consecutive_500s = 0
    remaining = list(todo)
    lock = threading.Lock()
    aborted = False
    # An event known to export CSV, for outage checks: any already-cached one.
    known_good = next((name[:-4] for name in os.listdir(cache_dir) if name.endswith(".csv")), None)

    def work(eid):
        path, response = fetch_registrations(session, eid, limiter=limiter)
        return eid, path, response

    chunk_size = args.workers * 5   # keep few futures in flight so Ctrl-C and re-login stop things fast

    def handle(eid, path, response):
        """Record one result. Returns 'abort', 'login', or None."""
        nonlocal fetched, empty, consecutive_failures, consecutive_500s, known_good
        if path == "LOGIN":
            return "login"
        if path == "FAILED":
            consecutive_failures += 1
            with lock, open(failed_file, "a") as fp:
                fp.write(eid + "\n")
            logging.warning("Event %s: network failure after retries; recorded in %s for the next run",
                            eid, os.path.basename(failed_file))
            if consecutive_failures >= abort_after_consecutive_failures:
                logging.error("%d consecutive failures; aborting. Progress is saved.", consecutive_failures)
                return "abort"
            return None
        if path is None and response is not None and response.status_code == 500:
            consecutive_500s += 1
            with lock, open(no_registration_file, "a") as fp:
                fp.write(eid + "\n")
            empty += 1
            if consecutive_500s >= canary_after_500s and known_good:
                limiter.wait()
                probe = session.get(vtools_base + registration_paths[0].format(id=known_good),
                                    timeout=120, allow_redirects=False)
                if probe.status_code == 200 and b"<html" not in probe.content[:300].lower():
                    logging.info("%d events in a row had no export; canary %s still exports, continuing",
                                 consecutive_500s, known_good)
                    consecutive_500s = 0
                else:
                    logging.error("Canary event %s failed too (HTTP %s): vTools looks unhealthy; aborting. "
                                  "Progress is saved.", known_good, probe.status_code)
                    return "abort"
            return None
        if path is None:
            if response is not None and response.status_code in (429, 502, 503, 504):
                consecutive_failures += 1
                if consecutive_failures >= abort_after_consecutive_failures:
                    logging.error("%d consecutive server errors; aborting so we don't hammer vTools. "
                                  "Re-run later; progress is saved.", consecutive_failures)
                    return "abort"
                return None
            with lock, open(no_registration_file, "a") as fp:
                fp.write(eid + "\n")
            empty += 1
            return None
        consecutive_failures = 0
        consecutive_500s = 0
        known_good = known_good or eid
        with open(os.path.join(cache_dir, f"{eid}.csv"), "wb") as fp:
            fp.write(response.content)
        fetched += 1
        return None

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    try:
        while remaining and not aborted:
            chunk, remaining = remaining[:chunk_size], remaining[chunk_size:]
            futures = {pool.submit(fetch_registrations, session, eid, None, limiter): eid for eid in chunk}
            relogin = False
            for future in concurrent.futures.as_completed(futures):
                eid = futures[future]
                path, response = future.result()
                verdict = handle(eid, path, response)
                if verdict == "login":
                    relogin = True
                    remaining.insert(0, eid)
                elif verdict == "abort":
                    aborted = True
                    break
                total_done = fetched + empty
                if total_done % 50 == 0:
                    logging.info("%d/%d done (%d fetched, %d without registration)",
                                 total_done, len(todo), fetched, empty)
            if aborted:
                break
            if relogin:
                logging.warning("Session expired; signing in again, then resuming %d events", len(remaining))
                session = get_session(force_login=True)
    except KeyboardInterrupt:
        print("\nInterrupted; progress is saved. Re-run the same command to resume.", flush=True)
        pool.shutdown(wait=False, cancel_futures=True)
        os._exit(130)   # don't wait for in-flight downloads; everything finished is already on disk
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    outcome = "Harvest ABORTED after repeated server errors" if aborted else "Harvest complete"
    print(f"{outcome}: {fetched} registrant files saved to {cache_dir}/, "
          f"{empty} events had no registration export.")


def merge(args):
    events = load_inventory()
    event_fields = ["Event Id", "Event Title", "Event Date", "Organizational Unit",
                    "SPOID", "Section", "Region", "Event Category"]
    out_rows = []
    registrant_fields = []
    for name in sorted(os.listdir(cache_dir)):
        if not name.endswith(".csv"):
            continue
        eid = name[:-4]
        event = events.get(eid, {})
        with open(os.path.join(cache_dir, name), "rb") as fp:
            text = decode_export(fp.read())
        for row in csv.DictReader(io.StringIO(text)):
            for key in row:
                if key and key not in registrant_fields:
                    registrant_fields.append(key)
            merged = {
                "Event Id": eid,
                "Event Title": event.get("Event Title", ""),
                "Event Date": event.get("Event Date", ""),
                "Organizational Unit": event.get("Organizational Unit", ""),
                "SPOID": event.get("SPOID", ""),
                "Section": event.get("Section", ""),
                "Region": event.get("Region", ""),
                "Event Category": event.get("Event Category", ""),
            }
            merged.update({k: v for k, v in row.items() if k})
            out_rows.append(merged)
    fp = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(fp, fieldnames=event_fields + registrant_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(out_rows)
    if args.output:
        fp.close()
        print(f"Wrote {len(out_rows)} registrations from {len({r['Event Id'] for r in out_rows})} events to {args.output}")


# -------------------------------------------------------------------- cli

def parse_command_line():
    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Collect vTools event registrations by region and date range")
    parser.add_argument("-l", "--loglevel", default="INFO",
                        choices=["ERROR", "WARNING", "INFO", "DEBUG"])
    parser.add_argument("--non-interactive", action="store_true",
                        help="never prompt for a login; exit with status 3 if the session has expired "
                             "(for unattended runs)")
    parser.add_argument("-d", "--data-dir", default=os.environ.get("VTOOLS_DATA", "cache"),
                        help="directory for the cookie, inventory, and downloaded CSVs "
                             "(default: $VTOOLS_DATA or ./cache)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="fetch one event's registrant export and describe it")
    p.add_argument("event_id")
    p.set_defaults(func=probe)

    h = sub.add_parser("harvest", help="download registrant CSVs for every event in scope")
    h.add_argument("--regions", required=True, help="comma-separated region codes, e.g. R1,R2")
    h.add_argument("--start", required=True, help="first event date, YYYY-MM-DD")
    h.add_argument("--end", help="last event date, YYYY-MM-DD (default: today)")
    h.add_argument("--limit", type=int, help="stop after this many downloads (for testing)")
    h.add_argument("--skip-inventory", action="store_true",
                   help="reuse the saved events inventory instead of rebuilding it")
    h.add_argument("--workers", type=int, default=default_workers,
                   help=f"parallel downloads (default {default_workers}); total rate is still capped by --rate")
    h.add_argument("--rate", type=float, default=default_rate,
                   help=f"max requests per second across all workers (default {default_rate})")
    h.set_defaults(func=harvest)

    i = sub.add_parser("inventory", help="rebuild only the public event inventory (no login needed)")
    i.add_argument("--regions", required=True, help="comma-separated region codes, e.g. R1,R2")
    i.add_argument("--start", required=True, help="first event date, YYYY-MM-DD")
    i.add_argument("--end", help="last event date, YYYY-MM-DD (default: today)")
    i.set_defaults(func=lambda a: build_inventory([r.strip().upper() for r in a.regions.split(",") if r.strip()],
                                                  datetime.date.fromisoformat(a.start),
                                                  datetime.date.fromisoformat(a.end) if a.end else datetime.date.today()))

    en = sub.add_parser("enrich", help="fetch primary host / co-hosts for every inventory event from the Events List API (no login)")
    en.add_argument("--rate", type=float, default=default_rate, help="max requests per second")
    en.set_defaults(func=enrich)

    m = sub.add_parser("merge", help="combine cached registrant CSVs into one file")
    m.add_argument("-o", "--output", help="output CSV (default: stdout)")
    m.set_defaults(func=merge)

    c = sub.add_parser("set-cookie", help="use a session cookie captured from your browser (DevTools 'Copy as cURL')")
    c.add_argument("--from-curl", help="file holding a 'Copy as cURL' paste (default my_vtools_curl.txt)")
    c.add_argument("--cookie", help="the raw Cookie header string, as an alternative to --from-curl")
    c.set_defaults(func=set_cookie)

    f = sub.add_parser("forget", help="delete the saved session cookie")
    f.set_defaults(func=forget)
    return parser.parse_args()


def main():
    args = parse_command_line()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        level=getattr(logging, args.loglevel))
    global non_interactive
    non_interactive = args.non_interactive
    set_data_dir(args.data_dir)
    logging.info("Data directory: %s", data_dir)
    args.func(args)


if __name__ == "__main__":
    main()
