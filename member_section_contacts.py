"""For a list of IEEE member numbers, report each member's name, email, and
Section, plus the emails of their Section Chair, Vice Chair, and Treasurer
in a ready-to-copy field.

Member identity/section come from the memberships view, reusing
member_lookup.py. Section officer emails come from the volunteer positions
view, filtered to active Section Chair, Vice Chair, and Treasurer positions.
The 28 sections that use a combined "Secretary/Treasurer" role fill the
Treasurer column from it.

The volunteer positions view is slow to export (~2 minutes), so the
all-sections officer query is cached to disk and reused for a week; only the
first run each week pays that cost.

Members missing from the memberships view are looked up in the volunteer
view as a fallback. Two kinds of member are missing from the memberships
view: those outside Regions 1-2, and members who hold no society/affinity
membership PRODUCT — the memberships view has one row per society/YP/WIE
membership, so a current member whose only membership is base IEEE (no
society product) isn't in it at all, even with a current renew year. The
fallback catches such a member if they hold a volunteer position (e.g. a YP
Affinity Group role). It's a second slow query; skip it with --no-fallback,
which reports missing members as unknown.

Note: a current base-only member with no volunteer position appears in
NEITHER view and can't be resolved through the API (that would need the
"Members and Affiliates" detail view, which is blocked for this token).

Usage examples:

    python member_section_contacts.py my_token.json members.csv
    python member_section_contacts.py my_token.json 12345678,23456789
    python member_section_contacts.py my_token.json members.csv -o contacts.csv
    python member_section_contacts.py my_token.json members.csv --refresh-officers
    python member_section_contacts.py my_token.json members.csv --no-fallback
"""

import argparse
import csv
import datetime
import io
import json
import logging
import os
import sys

import tableauserverclient as TSC

from member_lookup import (
    volunteer_view_id,
    normalize_header,
    normalize_member_number,
    read_member_numbers,
    get_server,
    query_products_view,
    query_volunteer_view,
)

# Output column -> OU Position(s) in the volunteer view that fill it.
# Later positions in a list are fallbacks used only if the earlier ones
# are vacant (Treasurer falls back to the combined Secretary/Treasurer role).
officer_roles = {
    "Chair Email": ["Chair"],
    "Vice Chair Email": ["Vice Chair"],
    "Treasurer Email": ["Treasurer", "Secretary/Treasurer"],
}

# "To" is the member; "Cc" is their section officers (Chair / Vice Chair /
# Treasurer, deduped) — the two fields map straight onto an email addressed
# to the member and cc'ing the section folks.
output_columns = ["Member Number", "First Name", "Last Name", "Section", "To", "Cc",
                  "Chair Email", "Vice Chair Email", "Treasurer Email"]

# The volunteer positions view is slow to export (~2 minutes), and section
# officers change slowly, so the all-sections officer query is cached to disk
# and reused unless it's older than this.
default_officers_cache = "lists/section_officers_cache.json"
officers_cache_max_age_days = 7


def normalize_section(name):
    return name.strip().lower()


def query_section_officers(server):
    """Query active Section Chair, Vice Chair, Treasurer, and combined
    Secretary/Treasurer positions. Returns
    {normalized section: {position: [emails]}}."""
    wanted_positions = sorted({p for roles in officer_roles.values() for p in roles})
    view = server.views.get_by_id(volunteer_view_id)
    csv_request_options = TSC.CSVRequestOptions()
    csv_request_options.vf("OU Type", "Section")
    csv_request_options.vf("Volunteer Position Status ", "Active")
    csv_request_options.vf("OU Position", ",".join(wanted_positions))
    server.views.populate_csv(view, csv_request_options)
    csv_data = b''.join(view.csv).decode("utf-8-sig")

    officers = dict()
    for raw in csv.DictReader(io.StringIO(csv_data)):
        row = {normalize_header(k): v.strip() for k, v in raw.items() if k is not None}
        # The view includes placeholder rows for vacant positions
        if row.get("ou position status") == "Vacant":
            continue
        email = row.get("email address", "")
        if not email:
            continue
        section = normalize_section(row.get("ou name", ""))
        position = row.get("ou position", "")
        officers.setdefault(section, dict()).setdefault(position, [])
        if email not in officers[section][position]:
            officers[section][position].append(email)
    return officers


def load_officers_cache(cache_path, max_age_days):
    """Return cached {section: {position: [emails]}} if the cache exists and
    is fresh, else None."""
    if not cache_path or not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path) as fp:
            data = json.load(fp)
        generated = datetime.datetime.fromisoformat(data["generated"])
        age = datetime.datetime.now() - generated
        if age > datetime.timedelta(days=max_age_days):
            logging.info("Officers cache %s is %d days old; refreshing", cache_path, age.days)
            return None
        logging.info("Using cached section officers from %s (%d days old); "
                     "pass --refresh-officers to re-query", cache_path, age.days)
        return data["officers"]
    except Exception as e:
        logging.warning("Could not read officers cache %s (%s); re-querying", cache_path, e)
        return None


def save_officers_cache(cache_path, officers):
    if not cache_path:
        return
    try:
        directory = os.path.dirname(cache_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(cache_path, "w") as fp:
            json.dump({"generated": datetime.datetime.now().isoformat(),
                       "officers": officers}, fp)
        logging.info("Cached section officers to %s", cache_path)
    except Exception as e:
        logging.warning("Could not write officers cache %s: %s", cache_path, e)


def get_section_officers(server, cache_path, max_age_days, refresh):
    """Section officers from cache when fresh, otherwise from the (slow)
    volunteer view, saving the result to the cache."""
    if not refresh:
        cached = load_officers_cache(cache_path, max_age_days)
        if cached is not None:
            return cached
    logging.info("Querying section officers from the volunteer view "
                 "(this view is slow, ~2 minutes)...")
    officers = query_section_officers(server)
    save_officers_cache(cache_path, officers)
    return officers


def officer_emails_for(section, officers):
    """Return {output column: '; '-joined emails} for one section."""
    section_officers = officers.get(normalize_section(section), dict())
    result = dict()
    for column, positions in officer_roles.items():
        emails = []
        for position in positions:
            emails = section_officers.get(position, [])
            if emails:
                break  # earlier positions win; later ones are fallbacks
        result[column] = "; ".join(emails)
    return result


def build_rows(member_numbers, records, officers):
    rows = []
    for number in member_numbers:
        record = records.get(normalize_member_number(number))
        row = {column: "" for column in output_columns}
        row["Member Number"] = number
        if record is None:
            for column in ("First Name", "Last Name", "To", "Section"):
                row[column] = "unknown"
            rows.append(row)
            continue
        row["First Name"] = record.get("First Name", "")
        row["Last Name"] = record.get("Last Name", "")
        row["To"] = record.get("Email", "")
        row["Section"] = record.get("Section", "")
        row.update(officer_emails_for(row["Section"], officers))
        # Cc field: the three officer emails, deduped, in Chair / Vice Chair /
        # Treasurer order, ready to paste into a Cc line.
        cc = []
        for column in ("Chair Email", "Vice Chair Email", "Treasurer Email"):
            for email in row[column].split("; "):
                if email and email not in cc:
                    cc.append(email)
        row["Cc"] = "; ".join(cc)
        rows.append(row)
    return rows


def write_output(rows, output_file):
    fp = open(output_file, "w", newline="") if output_file else sys.stdout
    writer = csv.DictWriter(fp, fieldnames=output_columns)
    writer.writeheader()
    writer.writerows(rows)
    if output_file:
        fp.close()
        print(f"Wrote {len(rows)} rows to {output_file}")


def parse_command_line():
    default_log_level = "WARNING"

    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Member contacts plus their section officers' emails")
    parser.add_argument("token_file", help="File containing personal access token")
    parser.add_argument("members", help="CSV/file of member numbers (one per line), "
                                        "or a comma-separated list of member numbers")
    parser.add_argument("-o",
                        "--output",
                        help="output CSV file (default: print to stdout)")
    parser.add_argument("--no-fallback",
                        action="store_true",
                        help="skip the volunteer-view fallback for members missing "
                             "from the memberships view (out of Regions 1-2, or "
                             "in-region members with no active membership product). "
                             "The fallback is on by default but adds a slow (~2 min) "
                             "query; with --no-fallback those members show as unknown.")
    parser.add_argument("--officers-cache",
                        default=default_officers_cache,
                        help=f"cache file for the section officers query "
                             f"(default: {default_officers_cache}); the query is "
                             f"reused for {officers_cache_max_age_days} days")
    parser.add_argument("--refresh-officers",
                        action="store_true",
                        help="ignore the officers cache and re-query (use after "
                             "section officer changes)")
    parser.add_argument("-l",
                        "--loglevel",
                        type=str,
                        default=default_log_level,
                        choices=["ERROR", "WARNING", "INFO", "DEBUG"],
                        help=f"logging level (default={default_log_level})")
    args = parser.parse_args()

    args.loglevel = getattr(logging, args.loglevel)
    return args


def main():
    args = parse_command_line()

    log_format = '%(levelno)s %(module)s:%(funcName)s %(message)s'
    logging.basicConfig(format=log_format, level=args.loglevel)

    member_numbers = read_member_numbers(args.members)
    if not member_numbers:
        logging.error("No member numbers to look up")
        exit()

    server = get_server(args.token_file)

    records = query_products_view(server, member_numbers)
    missing = [n for n in member_numbers if normalize_member_number(n) not in records]
    if missing and not args.no_fallback:
        logging.info("%d member numbers not in memberships view; trying volunteer view "
                     "(slow, ~2 min; pass --no-fallback to skip)", len(missing))
        records.update(query_volunteer_view(server, missing))

    officers = get_section_officers(server, args.officers_cache,
                                    officers_cache_max_age_days, args.refresh_officers)

    rows = build_rows(member_numbers, records, officers)
    write_output(rows, args.output)

    not_found = sum(1 for r in rows if r["First Name"] == "unknown")
    if not_found:
        hint = " (they hold no volunteer position either)" if not args.no_fallback \
            else " (--no-fallback set; drop it to check the volunteer view)"
        logging.warning("%d of %d member numbers not found%s", not_found, len(rows), hint)
    no_officers = sum(1 for r in rows if r["Section"] not in ("", "unknown") and not r["Cc"])
    if no_officers:
        logging.warning("%d members' sections had no active Chair/Vice Chair/Treasurer on record", no_officers)


if __name__ == '__main__':
    main()
