"""Format a section officers CSV (from section_officers.py) as a single
HTML roster document — one row per section with Chair and Vice Chair,
vacant positions highlighted — suitable for printing or attaching to email.

Usage examples:

    python section_roster.py lists/r1_r2_section_officers.csv -o roster.html
    python section_roster.py officers.csv --title "IEEE Regions 1 & 2" -o roster.html
"""

import argparse
import csv
import datetime
import html
import logging
import os
import sys

from chapter_roster import html_to_pdf

style = """
  body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a;
         max-width: 60em; margin: 2em auto; padding: 0 1.5em; line-height: 1.45; }
  h1 { color: #00629B; font-size: 1.6em; margin-bottom: 0.1em; }
  .subtitle { color: #555; margin-top: 0; }
  table { border-collapse: collapse; width: 100%; margin-top: 1em; font-size: 0.95em;
          table-layout: fixed; }
  th { text-align: left; background: #eef4f8; color: #00426a; padding: 0.4em 0.6em;
       border-bottom: 2px solid #00629B; }
  td { padding: 0.4em 0.6em; border-bottom: 1px solid #d8d8d8; vertical-align: top;
       overflow-wrap: anywhere; }
  tr:nth-child(even) td { background: #f7fafc; }
  a { color: #00629B; }
  .vacant { color: #a33; font-style: italic; }
  .summary { background: #eef4f8; padding: 0.8em 1em; border-left: 4px solid #00629B;
             margin-top: 1.2em; }
  h2 { color: #00629B; font-size: 1.15em; margin-top: 1.8em; margin-bottom: 0.3em; }
  .emailgroup { margin-bottom: 1em; }
  .emaillabel { font-size: 0.9em; color: #555; }
  .emails { font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 0.9em; background: #f4f6f8; border: 1px solid #d8d8d8;
            border-radius: 4px; padding: 0.5em 0.7em; margin-top: 0.2em;
            overflow-wrap: anywhere; line-height: 1.5; color: #1a1a1a; }
  @media print {
    body { margin: 0.5em; max-width: none; }
    table { break-inside: auto; }
    tr { break-inside: avoid; }
    .emailgroup { break-inside: avoid; }
  }
"""

position_order = ["Chair", "Vice Chair"]


def officer_cell(people):
    """people is a list of (name, email, status) tuples for one position."""
    filled = [(name, email) for name, email, status in people if status != "Vacant" and name]
    if not filled:
        return '<span class="vacant">Vacant</span>'
    parts = []
    for name, email in filled:
        person = html.escape(name)
        if email:
            person += f'<br><a href="mailto:{html.escape(email)}">{html.escape(email)}</a>'
        parts.append(person)
    return "<br>".join(parts)


def dedupe(values):
    seen = set()
    unique = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def email_lists_block(sections, all_positions):
    """A copy-paste-friendly appendix: one selectable, semicolon-separated
    list of emails per position plus a combined list. Semicolons are
    accepted by both Gmail and Outlook address fields."""
    by_position = {position: [] for position in all_positions}
    for section in sorted(sections):
        for position in all_positions:
            for name, email, status in sections[section].get(position, []):
                if status != "Vacant" and email:
                    by_position[position].append(email)

    out = ['<h2>Email lists (copy &amp; paste)</h2>']
    combined = dedupe(email for position in all_positions for email in by_position[position])
    groups = [("All officers", combined)]
    groups += [(position + "s", dedupe(by_position[position])) for position in all_positions]
    for label, emails in groups:
        if not emails:
            continue
        # One email per line, each ending in a semicolon: intact addresses that
        # survive PDF copy-paste and paste cleanly into a To/Bcc field.
        lines = "<br>".join(html.escape(email) + ";" for email in emails)
        out.append('<div class="emailgroup">'
                   f'<span class="emaillabel">{html.escape(label)} ({len(emails)})</span>'
                   f'<div class="emails">{lines}</div>'
                   '</div>')
    return "\n".join(out)


def read_rows(csv_file):
    with open(csv_file, "r") as fp:
        return list(csv.DictReader(fp))


def build_document(rows, title, email_lists=False):
    sections = dict()
    all_positions = list(position_order)
    for row in rows:
        position = row["Position"]
        if position not in all_positions:
            all_positions.append(position)
        name = " ".join(part for part in [row["First Name"], row["Last Name"]] if part)
        # Older CSVs have no Status column; blank names there mean vacant
        status = row.get("Status") or ("Filled" if name else "Vacant")
        sections.setdefault(row["Section"], dict()).setdefault(position, []).append(
            (name, row["Email"], status))

    vacancies = sum(1 for people in sections.values() for position in all_positions
                    if not any(s != "Vacant" and n for n, e, s in people.get(position, [])))
    today = datetime.date.today().strftime("%B %d, %Y")

    out = [f"<style>{style}</style>"]
    out.append(f"<h1>{html.escape(title)}</h1>")
    out.append(f'<p class="subtitle">Section Officer Roster &mdash; generated {today} from IEEE OU Analytics</p>')
    out.append('<div class="summary">'
               f"{len(sections)} sections. {vacancies} positions currently vacant."
               "</div>")

    out.append("<table>")
    header_cells = "".join(f"<th>{html.escape(p)}</th>" for p in all_positions)
    other = f"{(100 - 28) / len(all_positions):.1f}%"
    out.append("<colgroup><col style='width:28%'>"
               + "".join(f"<col style='width:{other}'>" for _ in all_positions) + "</colgroup>")
    out.append(f"<tr><th>Section</th>{header_cells}</tr>")
    for section in sorted(sections):
        cells = "".join(f"<td>{officer_cell(sections[section].get(p, []))}</td>"
                        for p in all_positions)
        out.append(f"<tr><td>{html.escape(section)}</td>{cells}</tr>")
    out.append("</table>")

    if email_lists:
        out.append(email_lists_block(sections, all_positions))

    return "\n".join(out)


def parse_command_line():
    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Format a section officers CSV as an HTML roster")
    parser.add_argument("csv_file", help="section officers CSV produced by section_officers.py")
    parser.add_argument("-t",
                        "--title",
                        default="IEEE Section Roster",
                        help="document title")
    parser.add_argument("-o",
                        "--output",
                        help="output HTML file (default: print to stdout)")
    parser.add_argument("-p",
                        "--pdf",
                        action="store_true",
                        help="also write a PDF next to the output HTML (requires Chrome)")
    parser.add_argument("-e",
                        "--email-lists",
                        action="store_true",
                        help="append copy-paste-friendly email lists (all officers, "
                             "and one per position)")
    return parser.parse_args()


def main():
    args = parse_command_line()
    rows = read_rows(args.csv_file)
    document = build_document(rows, args.title, args.email_lists)
    if args.output:
        with open(args.output, "w") as fp:
            fp.write(document)
        print(f"Wrote roster for {len(rows)} officer entries to {args.output}")
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
