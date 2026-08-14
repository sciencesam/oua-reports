"""List section Chairs and Vice Chairs with their names and emails.

Queries the "Volunteer Positions & History" view for active Chair and
Vice Chair positions at Section-type OUs. Optionally restrict the output
to a list of sections.

Usage examples:

    python section_officers.py my_token.json
    python section_officers.py my_token.json sections.txt -o officers.csv
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

# "Volunteer Positions" view in the "Queries for Tableau API users" project
volunteer_view_id = "53aef8cf-e84c-445d-9368-4ff94acbc777"

positions = ["Chair", "Vice Chair"]

output_columns = ["Section", "Position", "First Name", "Last Name", "Email", "Status"]


def normalize_header(name):
    return name.strip().lower()


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


def read_sections(sections_arg, area=None, region=None):
    """Accept an inline comma-separated list of section names, a file with
    one section per line, or an area CSV whose rows are
    "Region,Area,Section,Section,...". For an area CSV, --area (and
    optionally --region) selects which row(s) to use."""
    if not os.path.isfile(sections_arg):
        return [s.strip() for s in sections_arg.split(",") if s.strip()]

    with open(sections_arg, "r") as fp:
        rows = [row for row in csv.reader(fp) if any(cell.strip() for cell in row)]

    is_area_csv = all(len(row) >= 3 for row in rows)
    if not is_area_csv:
        return [cell.strip() for row in rows for cell in row if cell.strip()]

    if area is None:
        logging.error("%s is an area CSV; choose one with --area. Available: %s",
                      sections_arg,
                      "; ".join(f"{row[0].strip()} / {row[1].strip()}" for row in rows))
        exit()

    sections = []
    for row in rows:
        row_region, row_area = row[0].strip(), row[1].strip()
        if area.lower() not in row_area.lower():
            continue
        if region is not None and region.lower() not in row_region.lower():
            continue
        sections.extend(cell.strip() for cell in row[2:] if cell.strip())
    if not sections:
        logging.error("No row in %s matches area %r%s", sections_arg, area,
                      f" and region {region!r}" if region else "")
        exit()
    return sections


def query_section_officers(server):
    """Query active Chair and Vice Chair positions for all sections.
    Returns a list of row dicts."""
    view = server.views.get_by_id(volunteer_view_id)
    csv_request_options = TSC.CSVRequestOptions()
    csv_request_options.vf("OU Type", "Section")
    csv_request_options.vf("Volunteer Position Status ", "Active")
    csv_request_options.vf("OU Position", ",".join(positions))
    server.views.populate_csv(view, csv_request_options)
    csv_data = b''.join(view.csv).decode("utf-8-sig")
    officers = []
    for row in csv.DictReader(io.StringIO(csv_data)):
        row = {normalize_header(k): v.strip() for k, v in row.items() if k is not None}
        officers.append({
            "Section": row.get("ou name", ""),
            "Position": row.get("ou position", ""),
            "First Name": row.get("first name", ""),
            "Last Name": row.get("last name", ""),
            "Email": row.get("email address", ""),
            # The view includes placeholder rows for vacant positions
            "Status": "Vacant" if row.get("ou position status") == "Vacant" else "Filled",
        })
    return officers


def filter_and_sort(officers, sections):
    if sections is not None:
        wanted = {section.lower() for section in sections}
        officers = [o for o in officers if o["Section"].lower() in wanted]
        found = {o["Section"].lower() for o in officers}
        for section in sections:
            if section.lower() not in found:
                logging.warning("No active Chair/Vice Chair records for %r; reporting as vacant", section)
                for position in positions:
                    officers.append({"Section": section, "Position": position,
                                     "First Name": "", "Last Name": "", "Email": "",
                                     "Status": "Vacant"})
    officers.sort(key=lambda o: (o["Section"], positions.index(o["Position"]), o["Last Name"]))
    return officers


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
                                     description="List section Chairs and Vice Chairs")
    parser.add_argument("token_file", help="File containing personal access token")
    parser.add_argument("sections_file", nargs="?",
                        help="optional file with one section name per line, "
                             "a comma-separated list of section names, or an area "
                             "CSV (Region,Area,Section,Section,...); default is all sections")
    parser.add_argument("-a",
                        "--area",
                        help="area name to select from an area CSV (substring match)")
    parser.add_argument("-r",
                        "--region",
                        help="region name to disambiguate an area CSV row (substring match)")
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

    sections = read_sections(args.sections_file, args.area, args.region) if args.sections_file else None

    server = get_server(args.token_file)
    officers = query_section_officers(server)
    officers = filter_and_sort(officers, sections)
    write_output(officers, args.output)


if __name__ == '__main__':
    main()
