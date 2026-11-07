"""List chapters per section, grouped by society, with Chapter Chair and
Vice Chair names and emails.

For the given sections, builds the list of section chapters and joint
chapters (student branch chapters are excluded), then looks up each
chapter's active Chapter Chair and Chapter Vice Chair. Joint chapters are
listed once per member society, so a society's list includes every chapter
where that society participates.

Data sources:

1. "Geo OU Analysis" > "Chapter Summary" view for the chapter inventory
   (includes chapters that currently have no officers).

2. "Volunteer Positions & History" view for the officers (the "IEEE OU
   Analytics" project copy, which also carries past positions). Chapter
   rows there have no section column, so officers are matched to the
   inventory by chapter OU name. For each position the most recent past
   holder (by Position End) is reported alongside the current one, so a
   vacant seat still names who to ask.

Usage examples:

    python chapter_officers.py my_token.json lists/finger_lakes_sections.txt
    python chapter_officers.py my_token.json "Syracuse Section,Ithaca Section" -o chapters.csv
    python chapter_officers.py my_token.json lists/areas.csv --area "Western (Finger Lakes)" -o chapters.csv
"""

import argparse
import csv
import datetime
import io
import json
import logging
import os
import re
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

# "Chapter Summary" view in the "Geo OU Analysis" workbook
chapter_summary_view_id = "aca6fc53-83e9-43da-9dae-d94fa2d8b568"
# "Volunteer Positions" view in the "IEEE OU Analytics" project's copy of
# "Volunteer Positions & History". Unlike the API-users copy (53aef8cf...),
# this one exports past positions too. It needs "OU Type" set (the
# dashboard is empty until it is) and it selects active vs past rows through
# a Tableau parameter whose name has TWO spaces before "Parameter".
volunteer_view_id = "8294b666-4829-4c5d-9144-9049a36b6dcc"
position_status_parameter = "Officer Position Status  Parameter"

chair_position = "Chapter Chair"
vice_chair_position = "Chapter Vice Chair"

society_names = {
    "AES10": "Aerospace and Electronic Systems Society",
    "AP03": "Antennas and Propagation Society",
    "BIO46": "Biometrics Council",
    "BT02": "Broadcast Technology Society",
    "CAS04": "Circuits and Systems Society",
    "CE08": "Consumer Technology Society",
    "CIS11": "Computational Intelligence Society",
    "COM19": "Communications Society",
    "CS23": "Control Systems Society",
    "C16": "Computer Society",
    "DEI32": "Dielectrics and Electrical Insulation Society",
    "ED15": "Electron Devices Society",
    "EMB18": "Engineering in Medicine and Biology Society",
    "EMC27": "Electromagnetic Compatibility Society",
    "EP21": "Electronics Packaging Society",
    "E25": "Education Society",
    "GRS29": "Geoscience and Remote Sensing Society",
    "IA34": "Industry Applications Society",
    "IE13": "Industrial Electronics Society",
    "IM09": "Instrumentation and Measurement Society",
    "IT12": "Information Theory Society",
    "ITS38": "Intelligent Transportation Systems Society",
    "MAG33": "Magnetics Society",
    "MTT17": "Microwave Theory and Technology Society",
    "NANO42": "Nanotechnology Council",
    "NPS05": "Nuclear and Plasma Sciences Society",
    "OE22": "Oceanic Engineering Society",
    "PC26": "Professional Communication Society",
    "PE31": "Power & Energy Society",
    "PEL35": "Power Electronics Society",
    "PHO36": "Photonics Society",
    "RA24": "Robotics and Automation Society",
    "RL07": "Reliability Society",
    "SEN39": "Sensors Council",
    "SIT30": "Society on Social Implications of Technology",
    "SMC28": "Systems, Man, and Cybernetics Society",
    "SP01": "Signal Processing Society",
    "SSC37": "Solid-State Circuits Society",
    "TEM14": "Technology and Engineering Management Society",
    "UFFC20": "Ultrasonics, Ferroelectrics, and Frequency Control Society",
    "VT06": "Vehicular Technology Society",
}

output_columns = ["Society Code", "Society", "Section", "Chapter",
                  "Chair", "Chair Email", "Vice Chair", "Vice Chair Email",
                  "Previous Chair", "Previous Chair Email", "Previous Chair Term",
                  "Previous Vice Chair", "Previous Vice Chair Email", "Previous Vice Chair Term"]


def normalize_header(name):
    return name.strip().lower()


def normalize_ou_name(name):
    """Chapter OU names have inconsistent spacing around separators."""
    return re.sub(r"\s*([,/])\s*", r"\1", name.strip()).lower()


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


def query_view(server, view_id, view_filters):
    view = server.views.get_by_id(view_id)
    csv_request_options = TSC.CSVRequestOptions()
    for filter_name, filter_value in view_filters.items():
        csv_request_options.vf(filter_name, filter_value)
    server.views.populate_csv(view, csv_request_options)
    csv_data = b''.join(view.csv).decode("utf-8-sig")
    for row in csv.DictReader(io.StringIO(csv_data)):
        yield {normalize_header(k): v.strip() for k, v in row.items() if k is not None}


def is_section_or_joint_chapter(ou_name, section):
    """Keep section chapters and joint chapters; drop student branch
    chapters, which are named after their school. Chapter names vary
    ("Section Chapter", "Jt Chapter", "Jt Chapt"), so match any name
    containing "chapt". Multi-section chapters are reported with
    section '*'."""
    return "chapt" in ou_name.lower() or section == "*"


def query_chapters(server, sections):
    """Build the chapter inventory for the given sections.
    Returns {normalized OU name: chapter dict}."""
    chapters = dict()
    rows = query_view(server, chapter_summary_view_id, {"Section": ",".join(sections)})
    for row in rows:
        ou_name = row.get("ou name", "")
        section = row.get("section name", "")
        if not ou_name or not is_section_or_joint_chapter(ou_name, section):
            continue
        codes = [code.strip() for code in ou_name.split(",")[-1].split("/")]
        chapters.setdefault(normalize_ou_name(ou_name), {
            "Chapter": ou_name,
            "Section": section,
            "Region": row.get("region geo spo id", ""),
            "codes": codes,
        })
    return chapters


def fetch_officer_rows(server):
    """Fetch all active chapter chair and vice chair rows once; the result
    can be reused across query_chapter_officers calls for several areas."""
    return list(query_view(server, volunteer_view_id, {
        "OU Type": "Chapter,Joint Chapter",
        "OU Position": ",".join([chair_position, vice_chair_position]),
        position_status_parameter: "Active",
    }))


def fetch_past_officer_rows(server, regions=None):
    """Fetch past (ended) chapter chair and vice chair rows once, for reuse
    across areas. `regions` is a collection of region codes such as
    {"R1", "R2"} taken from the chapters being reported; None means all
    regions."""
    view_filters = {
        "OU Type": "Chapter,Joint Chapter",
        "OU Position": ",".join([chair_position, vice_chair_position]),
        position_status_parameter: "Past",
    }
    if regions:
        view_filters["Volunteer Region"] = ",".join(sorted(regions))
    return list(query_view(server, volunteer_view_id, view_filters))


def chapter_regions(chapters):
    """Region codes (e.g. R1) covered by a chapter inventory."""
    return {c["Region"] for c in chapters.values() if c.get("Region")}


def parse_date(text):
    for fmt in ("%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    return None


def query_chapter_officers(server, chapters, officer_rows=None, past_officer_rows=None):
    """Look up active chapter chairs and vice chairs, matched to the
    inventory by OU name, plus the most recent past holder of each. Adds
    chairs/vice_chairs lists and previous_chair/previous_vice_chair
    (name, email, term) tuples to each chapter."""
    for chapter in chapters.values():
        chapter["chairs"] = []
        chapter["vice_chairs"] = []
        chapter["previous_chair"] = None
        chapter["previous_vice_chair"] = None
    if officer_rows is None:
        officer_rows = fetch_officer_rows(server)
    if past_officer_rows is None:
        past_officer_rows = fetch_past_officer_rows(server, chapter_regions(chapters))

    for row in officer_rows:
        chapter = chapters.get(normalize_ou_name(row.get("ou name", "")))
        if chapter is None:
            continue
        # The view includes placeholder rows for vacant positions
        if row.get("ou position status") == "Vacant":
            continue
        name = " ".join(part for part in [row.get("first name", ""), row.get("last name", "")] if part)
        officer = (name, row.get("email address", ""))
        if row.get("ou position") == chair_position and officer not in chapter["chairs"]:
            chapter["chairs"].append(officer)
        elif row.get("ou position") == vice_chair_position and officer not in chapter["vice_chairs"]:
            chapter["vice_chairs"].append(officer)

    # Most recent past holder per position: latest Position End wins.
    latest = dict()
    for row in past_officer_rows:
        key = normalize_ou_name(row.get("ou name", ""))
        if key not in chapters:
            continue
        name = " ".join(part for part in [row.get("first name", ""), row.get("last name", "")] if part)
        if not name:
            continue
        ended = parse_date(row.get("position end", ""))
        if ended is None:
            continue
        slot = (key, row.get("ou position"))
        if slot not in latest or ended > latest[slot][0]:
            started = row.get("position start", "")
            term = f"{started} to {row.get('position end', '')}" if started else row.get("position end", "")
            latest[slot] = (ended, (name, row.get("email address", ""), term))
    for (key, position), (ended, previous) in latest.items():
        if position == chair_position:
            chapters[key]["previous_chair"] = previous
        elif position == vice_chair_position:
            chapters[key]["previous_vice_chair"] = previous


def previous_columns(prefix, previous):
    """Three output columns for a most-recent past holder, blank if none."""
    name, email, term = previous if previous else ("", "", "")
    return {prefix: name, prefix + " Email": email, prefix + " Term": term}


def build_society_tables(chapters):
    """One output row per (society, chapter); joint chapters repeat under
    each member society."""
    rows = []
    for chapter in chapters.values():
        for code in chapter["codes"]:
            rows.append({
                "Society Code": code,
                "Society": society_names.get(code, code),
                "Section": chapter["Section"],
                "Chapter": chapter["Chapter"],
                "Chair": "; ".join(name for name, email in chapter["chairs"]) or "(vacant)",
                "Chair Email": "; ".join(email for name, email in chapter["chairs"]),
                "Vice Chair": "; ".join(name for name, email in chapter["vice_chairs"]) or "(vacant)",
                "Vice Chair Email": "; ".join(email for name, email in chapter["vice_chairs"]),
                **previous_columns("Previous Chair", chapter["previous_chair"]),
                **previous_columns("Previous Vice Chair", chapter["previous_vice_chair"]),
            })
    rows.sort(key=lambda r: (r["Society"], r["Section"], r["Chapter"]))
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
                                     description="List chapters by society with chairs and vice chairs")
    parser.add_argument("token_file", help="File containing personal access token")
    parser.add_argument("sections", help="File with one section name per line, "
                                         "a comma-separated list of section names, "
                                         "or an area CSV (Region,Area,Section,Section,...)")
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

    sections = read_sections(args.sections, args.area, args.region)
    if not sections:
        logging.error("No sections given")
        exit()

    server = get_server(args.token_file)
    chapters = query_chapters(server, sections)
    if not chapters:
        logging.error("No chapters found for sections: %s", ", ".join(sections))
        exit()
    query_chapter_officers(server, chapters)

    rows = build_society_tables(chapters)
    write_output(rows, args.output)

    vacant = [c["Chapter"] for c in chapters.values() if not c["chairs"]]
    if vacant:
        logging.warning("Chapters with no active Chapter Chair: %s", "; ".join(sorted(vacant)))


if __name__ == '__main__':
    main()
