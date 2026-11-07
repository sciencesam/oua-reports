"""Format a chapter officers CSV (from chapter_officers.py) as a single
HTML roster document, grouped by society, suitable for printing or
attaching to email.

Usage examples:

    python chapter_roster.py lists/finger_lakes_chapter_officers.csv -o roster.html
    python chapter_roster.py officers.csv --title "Western (Finger Lakes) Area" -o roster.html
"""

import argparse
import csv
import datetime
import html
import logging
import os
import shutil
import subprocess
import sys

style = """
  body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a;
         max-width: 60em; margin: 2em auto; padding: 0 1.5em; line-height: 1.45; }
  h1 { color: #00629B; font-size: 1.6em; margin-bottom: 0.1em; }
  .subtitle { color: #555; margin-top: 0; }
  h2 { color: #00629B; border-bottom: 2px solid #00629B; padding-bottom: 0.15em;
       font-size: 1.2em; margin-top: 1.8em; }
  table { border-collapse: collapse; width: 100%; margin-top: 0.6em; font-size: 0.95em;
          table-layout: fixed; }
  th { text-align: left; background: #eef4f8; color: #00426a; padding: 0.4em 0.6em;
       border-bottom: 2px solid #00629B; }
  td { padding: 0.4em 0.6em; border-bottom: 1px solid #d8d8d8; vertical-align: top;
       overflow-wrap: anywhere; }
  tr:nth-child(even) td { background: #f7fafc; }
  a { color: #00629B; }
  .vacant { color: #a33; font-style: italic; }
  .previous { color: #555; font-size: 0.85em; margin-top: 0.2em; }
  .summary { background: #eef4f8; padding: 0.8em 1em; border-left: 4px solid #00629B;
             margin-top: 1.2em; }
  @media print {
    body { margin: 0.5em; max-width: none; }
    h2 { break-after: avoid; }
    table { break-inside: auto; }
    tr { break-inside: avoid; }
  }
"""


def officer_cell(name, email, previous_name="", previous_email="", previous_term=""):
    """Current holder(s) with mailto links; a vacant seat shows the most
    recent past holder underneath as the person to ask."""
    if name == "(vacant)" or not name:
        cell = '<span class="vacant">Vacant</span>'
        if previous_name:
            contact = html.escape(previous_name)
            if previous_email:
                contact += f'<br><a href="mailto:{html.escape(previous_email)}">{html.escape(previous_email)}</a>'
            cell += (f'<div class="previous">Previously: {contact}'
                     + (f'<br>{html.escape(previous_term)}' if previous_term else "")
                     + '</div>')
        return cell
    parts = []
    for one_name, one_email in zip(name.split("; "), email.split("; ")):
        person = html.escape(one_name)
        if one_email:
            person += f'<br><a href="mailto:{html.escape(one_email)}">{html.escape(one_email)}</a>'
        parts.append(person)
    return "<br>".join(parts)


def find_chrome():
    """Find a Chrome/Chromium binary for PDF conversion. Set the
    CHROME_PATH environment variable to override."""
    candidates = [
        os.environ.get("CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chrome"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def html_to_pdf(html_path, pdf_path):
    """Convert an HTML file to PDF using headless Chrome, which renders
    the document's print stylesheet. Returns True on success."""
    chrome = find_chrome()
    if chrome is None:
        logging.error("No Chrome/Chromium found for PDF conversion; "
                      "install Google Chrome or set CHROME_PATH")
        return False
    result = subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={os.path.abspath(pdf_path)}", os.path.abspath(html_path)],
        capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(pdf_path):
        logging.error("PDF conversion failed for %s: %s", html_path, result.stderr.strip())
        return False
    return True


def read_rows(csv_file):
    with open(csv_file, "r") as fp:
        return list(csv.DictReader(fp))


def build_document(rows, title):
    societies = dict()
    for row in rows:
        key = (row["Society"], row["Society Code"])
        societies.setdefault(key, []).append(row)

    chapters = {row["Chapter"] for row in rows}
    vacant_chairs = {row["Chapter"] for row in rows if row["Chair"] == "(vacant)"}
    today = datetime.date.today().strftime("%B %d, %Y")

    out = [f"<style>{style}</style>"]
    out.append(f"<h1>{html.escape(title)}</h1>")
    out.append(f'<p class="subtitle">Chapter Chair &amp; Vice Chair Roster &mdash; generated {today} from IEEE OU Analytics</p>')
    out.append('<div class="summary">'
               f"{len(chapters)} chapters across {len(societies)} societies. "
               "Joint chapters are listed under each participating society. "
               f"{len(vacant_chairs)} chapters currently have a vacant Chair position; "
               "for vacant seats the most recent past holder is shown as a contact."
               "</div>")

    for (society, code), society_rows in sorted(societies.items()):
        heading = society if society == code else f"{society} ({code})"
        out.append(f"<h2>{html.escape(heading)}</h2>")
        out.append("<table>")
        out.append("<colgroup><col style='width:32%'><col style='width:18%'>"
                   "<col style='width:25%'><col style='width:25%'></colgroup>")
        out.append("<tr><th>Chapter</th><th>Section</th><th>Chair</th><th>Vice Chair</th></tr>")
        for row in sorted(society_rows, key=lambda r: (r["Section"], r["Chapter"])):
            section = "Multiple sections (see chapter name)" if row["Section"] == "*" else row["Section"]
            out.append("<tr>"
                       f"<td>{html.escape(row['Chapter'])}</td>"
                       f"<td>{html.escape(section)}</td>"
                       f"<td>{officer_cell(row['Chair'], row['Chair Email'], row.get('Previous Chair', ''), row.get('Previous Chair Email', ''), row.get('Previous Chair Term', ''))}</td>"
                       f"<td>{officer_cell(row['Vice Chair'], row['Vice Chair Email'], row.get('Previous Vice Chair', ''), row.get('Previous Vice Chair Email', ''), row.get('Previous Vice Chair Term', ''))}</td>"
                       "</tr>")
        out.append("</table>")

    return "\n".join(out)


def parse_command_line():
    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Format a chapter officers CSV as an HTML roster")
    parser.add_argument("csv_file", help="chapter officers CSV produced by chapter_officers.py")
    parser.add_argument("-t",
                        "--title",
                        default="IEEE Chapter Roster",
                        help="document title")
    parser.add_argument("-o",
                        "--output",
                        help="output HTML file (default: print to stdout)")
    parser.add_argument("-p",
                        "--pdf",
                        action="store_true",
                        help="also write a PDF next to the output HTML (requires Chrome)")
    return parser.parse_args()


def main():
    args = parse_command_line()
    rows = read_rows(args.csv_file)
    document = build_document(rows, args.title)
    if args.output:
        with open(args.output, "w") as fp:
            fp.write(document)
        print(f"Wrote roster for {len(rows)} chapter/society entries to {args.output}")
        if args.pdf:
            pdf_path = os.path.splitext(args.output)[0] + ".pdf"
            if html_to_pdf(args.output, pdf_path):
                print(f"Wrote {pdf_path}")
    elif args.pdf:
        print(document)
        logging.error("--pdf requires -o/--output")
    else:
        print(document)


if __name__ == '__main__':
    main()
