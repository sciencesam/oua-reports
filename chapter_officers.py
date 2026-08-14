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

2. "Volunteer Positions & History" view for the officers. Chapter rows
   there have no section column, so officers are matched to the inventory
   by chapter OU name.

Usage examples:

    python chapter_officers.py my_token.json lists/finger_lakes_sections.txt
    python chapter_officers.py my_token.json "Syracuse Section,Ithaca Section" -o chapters.csv
    python chapter_officers.py my_token.json lists/areas.csv --area "Western (Finger Lakes)" -o chapters.csv
"""

import argparse
import csv
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
# "Volunteer Positions" view in the "Queries for Tableau API users" project
volunteer_view_id = "53aef8cf-e84c-445d-9368-4ff94acbc777"

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
                  "Chair", "Chair Email", "Vice Chair", "Vice Chair Email"]


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
            "codes": codes,
        })
    return chapters


def fetch_officer_rows(server):
    """Fetch all active chapter chair and vice chair rows once; the result
    can be reused across query_chapter_officers calls for several areas."""
    return list(query_view(server, volunteer_view_id, {
        "OU Type": "Chapter,Joint Chapter",
        "Volunteer Position Status ": "Active",
        "OU Position": ",".join([chair_position, vice_chair_position]),
    }))


def query_chapter_officers(server, chapters, officer_rows=None):
    """Look up active chapter chairs and vice chairs, matched to the
    inventory by OU name. Adds chairs/vice_chairs lists to each chapter."""
    for chapter in chapters.values():
        chapter["chairs"] = []
        chapter["vice_chairs"] = []
    if officer_rows is None:
        officer_rows = fetch_officer_rows(server)
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
