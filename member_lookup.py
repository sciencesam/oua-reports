"""Look up IEEE members by member number.

Given a list of IEEE member numbers, reports each member's first name,
last name, email address, section, and whether they are a member of
IEEE Young Professionals.

Data sources (queried through the Tableau API):

1. The "Memberships, Subscriptions, and More" > "Detail" view, which has
   one row per member per product. A member is reported as a Young
   Professionals member if they have an Active "IEEE Young Professionals"
   (MEMYP060) product row.

2. The "Volunteer Positions & History" view, used as a fallback for member
   numbers not found in the first view. It has no product data, so YP
   status is reported as unknown for these.

Usage examples:

    python member_lookup.py my_token.json members.txt
    python member_lookup.py my_token.json 12345678,23456789
    python member_lookup.py my_token.json members.txt -o results.csv
"""

import argparse
import csv
import io
import json
import logging
import os
import sys

import tableauserverclient as TSC

server_url = "https://tblanalytics.ieee.org"
site_id = "IEEE"
server_version = "3.15"
headers = {
    'Host': 'tblanalytics.ieee.org',
    'Accept': '*/*',
    'Content-Type': 'application/x-www-form-urlencoded'
}
http_options = {'headers': headers}

# "Detail" view in the "Memberships, Subscriptions, and More" workbook
products_view_id = "d5f4ab69-187d-4c65-a6b4-1a3761318438"
# "Volunteer Positions" view in the "Queries for Tableau API users" project
volunteer_view_id = "53aef8cf-e84c-445d-9368-4ff94acbc777"

member_number_filter = "Member/Customer Number"
query_batch_size = 50

# Output column -> product name in the memberships view. A member gets "Y"
# when they hold an Active product row with that name.
affinity_products = {
    "YP Member": "IEEE Young Professionals",
    "WIE Member": "IEEE Women in Engineering Membership",
}

output_columns = ["Member Number", "First Name", "Last Name", "Email", "Section",
                  "YP Member", "YP Eligible", "WIE Member", "Source"]


def normalize_header(name):
    return name.strip().lower()


def normalize_member_number(number):
    number = number.strip()
    return number.lstrip("0") if number.lstrip("0") else number


def read_member_numbers(members_arg):
    """Accept either a file (one member number per line) or an inline
    comma-separated list of member numbers."""
    if os.path.isfile(members_arg):
        with open(members_arg, "r") as fp:
            raw = fp.read().replace(",", "\n").splitlines()
    else:
        raw = members_arg.split(",")
    numbers = []
    for item in raw:
        item = item.strip().strip('"')
        if item and not item.isdigit():
            logging.warning("Skipping non-numeric member number %r", item)
            continue
        if item:
            numbers.append(item)
    # dedupe, preserving order
    seen = set()
    unique = []
    for number in numbers:
        key = normalize_member_number(number)
        if key not in seen:
            seen.add(key)
            unique.append(number)
    return unique


def get_personal_access_token(token_file):
    try:
        with open(token_file, "r") as fp:
            personal_token = json.load(fp)
            logging.debug("Loaded personal_token %s:", personal_token)
    except Exception as e:
        logging.error("Unable to load personal token from %s, exception %s", token_file, e)
        exit()
    return personal_token


def get_server(token_file):
    personal_access_token = get_personal_access_token(token_file)
    try:
        tableau_auth = TSC.PersonalAccessTokenAuth(
            personal_access_token['name'],
            personal_access_token['secret'],
            site_id=site_id)
        server = TSC.Server(server_url, http_options=http_options)
        server.version = server_version
        server.auth.sign_in(tableau_auth)
    except Exception as e:
        logging.error("Unable to authenticate, exception %s", e)
        exit()
    return server


def query_view_rows(server, view_id, member_numbers):
    """Query a view filtered by member number, in batches.
    Yields rows as dicts with normalized (stripped, lowercased) headers."""
    view = server.views.get_by_id(view_id)
    for start in range(0, len(member_numbers), query_batch_size):
        batch = member_numbers[start:start + query_batch_size]
        # Member numbers are stored zero-padded to 8 digits; query both forms
        values = set()
        for number in batch:
            values.add(number)
            values.add(number.zfill(8))
        csv_request_options = TSC.CSVRequestOptions()
        csv_request_options.vf(member_number_filter, ",".join(sorted(values)))
        server.views.populate_csv(view, csv_request_options)
        csv_data = b''.join(view.csv).decode("utf-8-sig")
        for row in csv.DictReader(io.StringIO(csv_data)):
            yield {normalize_header(k): v.strip() for k, v in row.items() if k is not None}
        logging.info("Queried %d of %d member numbers", start + len(batch), len(member_numbers))


def query_products_view(server, member_numbers):
    """Look up members in the memberships/subscriptions detail view.
    Returns {normalized member number: record dict}. YP membership is
    derived from an Active IEEE Young Professionals product row."""
    records = dict()
    for row in query_view_rows(server, products_view_id, member_numbers):
        number = normalize_member_number(row.get("member/customer number", ""))
        if not number:
            continue
        record = records.setdefault(number, {
            "First Name": row.get("first name", ""),
            "Last Name": row.get("last name", ""),
            "Email": row.get("email address", ""),
            "Section": row.get("section", ""),
            "YP Eligible": row.get("yp eligible y/n", ""),
            "Source": "memberships view",
            **{column: "N" for column in affinity_products},
        })
        if row.get("asset status") == "Active":
            for column, product_name in affinity_products.items():
                if row.get("product name") == product_name:
                    record[column] = "Y"
    return records


def query_volunteer_view(server, member_numbers):
    """Fallback lookup in the volunteer positions view (volunteers only,
    no product data). Returns {normalized member number: record dict}."""
    records = dict()
    for row in query_view_rows(server, volunteer_view_id, member_numbers):
        number = normalize_member_number(row.get("member/customer number", ""))
        if not number:
            continue
        records[number] = {
            "First Name": row.get("first name", ""),
            "Last Name": row.get("last name", ""),
            "Email": row.get("email address", ""),
            "Section": row.get("volunteer section", ""),
            "YP Eligible": "unknown",
            "Source": "volunteer view",
            **{column: "unknown" for column in affinity_products},
        }
    return records


def merge_records(member_numbers, records):
    rows = []
    for number in member_numbers:
        row = {
            "Member Number": number,
            "First Name": "unknown",
            "Last Name": "unknown",
            "Email": "unknown",
            "Section": "unknown",
            "YP Eligible": "unknown",
            "Source": "not found",
            **{column: "unknown" for column in affinity_products},
        }
        record = records.get(normalize_member_number(number))
        if record is not None:
            row.update(record)
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
                                     description="Look up IEEE members by member number")
    parser.add_argument("token_file", help="File containing personal access token")
    parser.add_argument("members", help="File with one member number per line, "
                                        "or a comma-separated list of member numbers")
    parser.add_argument("-o",
                        "--output",
                        help="output CSV file (default: print to stdout)")
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
    if missing:
        logging.info("%d member numbers not in memberships view; trying volunteer view", len(missing))
        records.update(query_volunteer_view(server, missing))

    rows = merge_records(member_numbers, records)
    write_output(rows, args.output)

    not_found = sum(1 for r in rows if r["Source"] == "not found")
    if not_found:
        logging.warning("%d of %d member numbers not found in any source", not_found, len(rows))


if __name__ == '__main__':
    main()
