"""Generate a chapter officer roster document for every area in an area CSV.

For each row of the area CSV ("Region,Area,Section,Section,..."), queries
the chapters and their officers and writes two files per area to the
output directory: the officers CSV and the formatted HTML roster.

The chapter officer data is fetched once and reused across areas, so this
makes one officers query plus one chapter inventory query per area.

Usage examples:

    python area_chapter_rosters.py my_token.json lists/areas.csv
    python area_chapter_rosters.py my_token.json lists/areas.csv -d lists/rosters
"""

import argparse
import csv
import logging
import os
import re
import sys

import chapter_officers
import chapter_roster


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def read_areas(areas_csv):
    """Returns a list of (region, area, sections) tuples."""
    areas = []
    with open(areas_csv, "r") as fp:
        for row in csv.reader(fp):
            cells = [cell.strip() for cell in row if cell.strip()]
            if not cells:
                continue
            if len(cells) < 3:
                logging.warning("Skipping area row with no sections: %s", cells)
                continue
            areas.append((cells[0], cells[1], cells[2:]))
    return areas


def report_area(server, officer_rows, past_officer_rows, region, area, sections, chapters, output_dir, pdf=False):
    if not chapters:
        logging.error("No chapters found for %s / %s", region, area)
        return
    found_sections = {c["Section"] for c in chapters.values()}
    for section in sections:
        if section not in found_sections:
            logging.warning("%s / %s: no chapters found for %r (missing, or name "
                            "doesn't match OU Analytics)", region, area, section)
    chapter_officers.query_chapter_officers(server, chapters, officer_rows, past_officer_rows)
    rows = chapter_officers.build_society_tables(chapters)

    slug = slugify(f"{region} {area}")
    csv_path = os.path.join(output_dir, f"{slug}_chapters.csv")
    chapter_officers.write_output(rows, csv_path)

    title = f"{region} {area}"
    document = chapter_roster.build_document(rows, title)
    html_path = os.path.join(output_dir, f"{slug}_chapter_roster.html")
    with open(html_path, "w") as fp:
        fp.write(document)
    print(f"Wrote roster to {html_path}")
    if pdf:
        pdf_path = os.path.join(output_dir, f"{slug}_chapter_roster.pdf")
        if chapter_roster.html_to_pdf(html_path, pdf_path):
            print(f"Wrote {pdf_path}")


def parse_command_line():
    default_log_level = "WARNING"

    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Chapter officer rosters for every area in an area CSV")
    parser.add_argument("token_file", help="File containing personal access token")
    parser.add_argument("areas_csv", help="area CSV (Region,Area,Section,Section,...)")
    parser.add_argument("-d",
                        "--output-dir",
                        default="rosters",
                        help="directory for the output files (default: rosters)")
    parser.add_argument("-p",
                        "--pdf",
                        action="store_true",
                        help="also write a PDF per area (requires Chrome)")
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

    areas = read_areas(args.areas_csv)
    if not areas:
        logging.error("No areas found in %s", args.areas_csv)
        exit()
    os.makedirs(args.output_dir, exist_ok=True)

    server = chapter_officers.get_server(args.token_file)
    officer_rows = chapter_officers.fetch_officer_rows(server)

    # Build every area's chapter inventory first so the past-officer query
    # can be scoped to exactly the regions involved and run once.
    inventories = []
    for region, area, sections in areas:
        chapters = chapter_officers.query_chapters(server, sections)
        inventories.append((region, area, sections, chapters))
    regions = set()
    for _, _, _, chapters in inventories:
        regions |= chapter_officers.chapter_regions(chapters)
    past_officer_rows = chapter_officers.fetch_past_officer_rows(server, regions)

    for region, area, sections, chapters in inventories:
        report_area(server, officer_rows, past_officer_rows, region, area, sections, chapters, args.output_dir, args.pdf)


if __name__ == '__main__':
    main()
