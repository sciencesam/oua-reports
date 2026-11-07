"""Full leadership roster for one section: Section, Chapter, and Affinity
Group officers with names and emails, as a CSV, a formatted HTML report,
and copy-paste email lists.

For the given section, builds the inventory of OUs (the section itself,
its chapters and joint chapters from the "Chapter Summary" view, and its
affinity groups from the "Affinity/SIG Summary" view), then reports every
officer position the volunteer view defines for those OUs.

Vacant positions show the last known holder of the role and the month and
year their term ended, so a volunteer who simply wasn't renewed in vTools
isn't silently dropped from the roster. Backfilled people are excluded
from the email lists. The API's volunteer views export only Active
positions (the workbook's history sheet isn't published), so last holders
come from two local sources:

1. An officer history cache the script updates on every run. From the
   second run onward, anyone who disappears from vTools is remembered.
2. --history-csv: a "Volunteer Positions & History" crosstab downloaded
   manually from the OU Analytics web UI (which does show history) can
   seed the cache with past officers going back years.

Officers whose listed term has already ended are flagged in red — that's
the early warning before vTools archives them.

Duplicate rows (the volunteer view often lists the same person twice for
one role with slightly different term dates) are merged by member number,
keeping the widest term. Genuinely different co-officers are all listed.

Usage examples:

    python section_leadership.py my_token.json "Syracuse Section"
    python section_leadership.py my_token.json "Syracuse Section" -d output --pdf
    python section_leadership.py my_token.json "Syracuse Section" \\
        --history-csv downloads/volunteer_history.csv
"""

import argparse
import csv
import datetime
import html
import json
import logging
import os
import re
import sys

import chapter_officers
from chapter_officers import (
    get_server,
    normalize_ou_name,
    query_view,
    society_names,
)
from chapter_roster import html_to_pdf

# "Volunteer Positions" view in the "Queries for Tableau API users" project.
# Exposes only Active positions (plus vacant placeholders); the workbook's
# history sheet is not published as a view, so past officers are kept in a
# local cache instead (see module docstring).
volunteer_view_id = "53aef8cf-e84c-445d-9368-4ff94acbc777"
# "Affinity/SIG Summary" view in the "Geo OU Analysis" workbook
affinity_summary_view_id = "dace3b87-eac3-47aa-88a1-cecec34169f0"

default_history_cache = "lists/officer_history_cache.json"

# Display order for positions; anything else follows alphabetically.
position_order = ["Chair", "Chapter Chair", "Affinity Group Chair",
                  "Vice Chair", "Chapter Vice Chair",
                  "Secretary", "Treasurer", "Secretary/Treasurer"]

output_columns = ["Group", "OU", "Position", "Name", "Email", "Status",
                  "Term Start", "Term End", "Last Holder", "Last Holder Email",
                  "Last Term Ended"]

style = """
  body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a;
         max-width: 60em; margin: 2em auto; padding: 0 1.5em; line-height: 1.45; }
  h1 { color: #00629B; font-size: 1.6em; margin-bottom: 0.1em; }
  .subtitle { color: #555; margin-top: 0; }
  h2 { color: #00629B; border-bottom: 2px solid #00629B; padding-bottom: 0.15em;
       font-size: 1.2em; margin-top: 1.8em; }
  h3 { color: #00426a; font-size: 1.05em; margin-top: 1.4em; margin-bottom: 0.2em; }
  .ouid { color: #777; font-weight: normal; font-size: 0.85em; }
  table { border-collapse: collapse; width: 100%; margin-top: 0.4em; font-size: 0.95em; }
  th { text-align: left; background: #eef4f8; color: #00426a; padding: 0.35em 0.6em;
       border-bottom: 2px solid #00629B; }
  td { padding: 0.35em 0.6em; border-bottom: 1px solid #d8d8d8; vertical-align: top; }
  tr:nth-child(even) td { background: #f7fafc; }
  a { color: #00629B; }
  .vacant { color: #a33; font-style: italic; }
  .lastheld { color: #666; font-size: 0.9em; }
  .term { color: #666; font-size: 0.9em; white-space: nowrap; }
  .expired { color: #a33; }
  .summary { background: #eef4f8; padding: 0.8em 1em; border-left: 4px solid #00629B;
             margin-top: 1.2em; }
  .emailgroup { margin-bottom: 1em; }
  .emaillabel { font-size: 0.9em; color: #555; }
  .emails { font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 0.9em; background: #f4f6f8; border: 1px solid #d8d8d8;
            border-radius: 4px; padding: 0.5em 0.7em; margin-top: 0.2em;
            overflow-wrap: anywhere; line-height: 1.5; color: #1a1a1a; }
  @media print {
    body { margin: 0.5em; max-width: none; }
    h2, h3 { break-after: avoid; }
    table { break-inside: auto; }
    tr { break-inside: avoid; }
    .emailgroup { break-inside: avoid; }
  }
"""


def parse_position_date(text):
    """Position dates look like '1-Jan-2026'; other views use '1/1/2005'."""
    for fmt in ("%d-%b-%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def month_year(date):
    return date.strftime("%b %Y") if date else ""


class Officer:
    """One person in one role, possibly merged from several duplicate rows."""

    def __init__(self, row):
        self.member_number = row.get("member/customer number", "")
        self.first_name = row.get("first name", "")
        self.last_name = row.get("last name", "")
        self.email = row.get("email address", "")
        self.start = parse_position_date(row.get("position start", ""))
        self.end = parse_position_date(row.get("position end", ""))

    @property
    def name(self):
        return " ".join(p for p in [self.first_name, self.last_name] if p)

    def key(self):
        """Duplicate rows repeat the same person with slightly different
        term dates; identify the person by member number when present."""
        return self.member_number or f"{self.name}|{self.email}".lower()

    def merge(self, other):
        """Keep the widest term across duplicate rows. A missing end date
        means the term is open-ended, which outlasts any dated end."""
        if other.start and (self.start is None or other.start < self.start):
            self.start = other.start
        if self.end is not None and (other.end is None or other.end > self.end):
            self.end = other.end

    def term_text(self):
        if self.start and self.end:
            return f"{month_year(self.start)} – {month_year(self.end)}"
        if self.start:
            return f"since {month_year(self.start)}"
        if self.end:
            return f"until {month_year(self.end)}"
        return ""


def dedupe_officers(rows):
    """Merge rows for the same person into one Officer per person."""
    officers = dict()
    for row in rows:
        officer = Officer(row)
        if officer.key() in officers:
            officers[officer.key()].merge(officer)
        else:
            officers[officer.key()] = officer
    return list(officers.values())


class OrgUnit:
    def __init__(self, group, name, ou_id=""):
        self.group = group      # "Section", "Chapters", "Affinity Groups"
        self.name = name        # OU name as OU Analytics spells it
        self.ou_id = ou_id      # SPO ID, e.g. R10039 (filled from rows if empty)
        self.positions = dict() # position -> {"current": [...], "past": [...]}

    def rows_for(self, position):
        return self.positions.setdefault(position, {"current": [], "past": []})


def read_section_arg(section):
    section = section.strip()
    if not section.lower().endswith("section"):
        logging.warning("%r doesn't end in 'Section'; OU Analytics section OU "
                        "names normally do (e.g. 'Syracuse Section')", section)
    return section


def query_affinity_groups(server, section):
    """Affinity groups (and HKN chapters) tied to the section.
    Returns {normalized OU name: (name, spo id)}."""
    ags = dict()
    rows = query_view(server, affinity_summary_view_id, {"Section": section})
    for row in rows:
        name = row.get("affinity/sig name", "")
        spo = row.get("affinity spo id", "")
        if name:
            ags.setdefault(normalize_ou_name(name), (name, spo))
    return ags


def collect_units(server, section):
    """The section itself, its chapters, and its affinity groups, keyed by
    normalized OU name."""
    units = dict()
    units[normalize_ou_name(section)] = OrgUnit("Section", section)

    chapters = chapter_officers.query_chapters(server, [section])
    if not chapters:
        logging.warning("No chapters found for %r (name doesn't match "
                        "OU Analytics?)", section)
    for norm, chapter in chapters.items():
        units[norm] = OrgUnit("Chapters", chapter["Chapter"])

    for norm, (name, spo) in query_affinity_groups(server, section).items():
        units[norm] = OrgUnit("Affinity Groups", name, spo)
    return units


def query_active_positions(server, units):
    """One pass over the active volunteer positions for all section,
    chapter, and affinity OU types, matched to our units by OU name.
    Fills each unit's current officers and vacancy placeholders, and
    collects OU SPO IDs for the history query."""
    logging.info("Querying active volunteer positions (slow view, ~2 min)...")
    rows = query_view(server, volunteer_view_id, {
        "OU Type": "Section,Chapter,Joint Chapter,Affinity",
        "Volunteer Position Status ": "Active",
    })
    for row in rows:
        unit = units.get(normalize_ou_name(row.get("ou name", "")))
        if unit is None:
            continue
        if not unit.ou_id:
            unit.ou_id = row.get("ou spo id", "")
        position = row.get("ou position", "")
        if not position:
            continue
        bucket = unit.rows_for(position)
        # The view emits a placeholder row for every defined-but-unfilled
        # position; recording the position (above) is all it's for.
        if row.get("ou position status") == "Vacant" or not row.get("last name"):
            continue
        bucket["current"].append(row)


def iso_date(date):
    return date.isoformat() if date else None


def from_iso(text):
    return datetime.date.fromisoformat(text) if text else None


def load_history_cache(path):
    if path and os.path.isfile(path):
        try:
            with open(path) as fp:
                return json.load(fp)
        except Exception as e:
            logging.warning("Could not read history cache %s (%s); starting fresh",
                            path, e)
    return {"records": {}}


def save_history_cache(cache, path):
    if not path:
        return
    cache["updated"] = datetime.date.today().isoformat()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as fp:
        json.dump(cache, fp, indent=1)
    logging.info("Updated officer history cache %s", path)


def remember_officer(cache, ou_name, position, officer, last_seen=None):
    """Upsert one person's tenure in one role into the cache."""
    records = (cache["records"].setdefault(normalize_ou_name(ou_name), {})
               .setdefault(position, {}))
    record = records.setdefault(officer.key(), {})
    record["name"] = officer.name or record.get("name", "")
    record["email"] = officer.email or record.get("email", "")
    old_start = from_iso(record.get("start"))
    if officer.start and (old_start is None or officer.start < old_start):
        record["start"] = iso_date(officer.start)
    # A missing end date on a fresher sighting means open-ended; only keep
    # the recorded end when the new sighting has one too.
    old_end = from_iso(record.get("end"))
    if officer.end and (old_end is None or officer.end > old_end):
        record["end"] = iso_date(officer.end)
    if last_seen and last_seen.isoformat() > (record.get("last_seen") or ""):
        record["last_seen"] = last_seen.isoformat()


def update_history_cache(cache, units, today):
    """Record every currently listed officer so a later run can name them
    after vTools drops them."""
    for unit in units.values():
        for position, bucket in unit.positions.items():
            for officer in dedupe_officers(bucket["current"]):
                remember_officer(cache, unit.name, position, officer, today)


def merge_history_csv(cache, csv_path):
    """Seed the cache from a manually downloaded OU Analytics volunteer
    positions/history export (full-data CSV with the view's columns)."""
    with open(csv_path, "r", encoding="utf-8-sig") as fp:
        rows = [{(k or "").strip().lower(): (v or "").strip()
                 for k, v in row.items()}
                for row in csv.DictReader(fp)]
    merged = 0
    for row in rows:
        ou_name = row.get("ou name", "")
        position = row.get("ou position", "") or row.get("position", "")
        officer = Officer(row)
        if not ou_name or not position or not officer.name:
            continue
        remember_officer(cache, ou_name, position, officer)
        merged += 1
    print(f"Merged {merged} rows from {csv_path} into the history cache")


def last_holders(cache, unit, position, current_keys):
    """The most recent past holder(s) of a vacant role from the cache:
    people no longer listed, whose term ended (or who were last seen) in
    the latest month on record. Returns (holders, ended, approximate);
    approximate means the date is a last-seen date, not a term end."""
    records = (cache["records"].get(normalize_ou_name(unit.name), {})
               .get(position, {}))
    candidates = []
    for key, record in records.items():
        if key in current_keys:
            continue
        ended = from_iso(record.get("end"))
        approx = ended is None
        if approx:
            ended = from_iso(record.get("last_seen"))
        if ended:
            candidates.append((record, ended, approx))
    if not candidates:
        return [], None, False
    latest = max(ended for _, ended, _ in candidates)
    recent = [(r, e, a) for r, e, a in candidates
              if e.strftime("%Y-%m") == latest.strftime("%Y-%m")]
    holders = sorted((r for r, _, _ in recent), key=lambda r: r["name"])
    approximate = all(a for _, _, a in recent)
    return holders, latest, approximate


def position_sort_key(position):
    if position in position_order:
        return (0, position_order.index(position), "")
    return (1, 0, position)


def build_rows(units, cache, today):
    """Flatten the units into output rows, one per (OU, position, person),
    with vacancy backfill from the history cache. Returns (rows, stats)."""
    rows = []
    stats = {"current": 0, "vacant": 0, "backfilled": 0, "expired": 0}
    group_order = {"Section": 0, "Chapters": 1, "Affinity Groups": 2}
    for unit in sorted(units.values(),
                       key=lambda u: (group_order[u.group], u.name)):
        for position in sorted(unit.positions, key=position_sort_key):
            bucket = unit.positions[position]
            current = dedupe_officers(bucket["current"])
            current.sort(key=lambda o: (o.last_name, o.first_name))
            if current:
                for officer in current:
                    expired = officer.end is not None and officer.end < today
                    if expired:
                        stats["expired"] += 1
                    stats["current"] += 1
                    rows.append({
                        "Group": unit.group, "OU": unit.name,
                        "Position": position,
                        "Name": officer.name, "Email": officer.email,
                        "Status": "Term expired" if expired else "Current",
                        "Term Start": month_year(officer.start),
                        "Term End": month_year(officer.end),
                        "Last Holder": "", "Last Holder Email": "",
                        "Last Term Ended": "",
                    })
                continue
            stats["vacant"] += 1
            holders, ended, approximate = last_holders(
                cache, unit, position, {o.key() for o in current})
            if holders:
                stats["backfilled"] += 1
            ended_text = month_year(ended)
            if ended_text and approximate:
                ended_text += " (last seen)"
            rows.append({
                "Group": unit.group, "OU": unit.name, "Position": position,
                "Name": "", "Email": "", "Status": "Vacant",
                "Term Start": "", "Term End": "",
                "Last Holder": "; ".join(h["name"] for h in holders),
                "Last Holder Email": "; ".join(h["email"] for h in holders),
                "Last Term Ended": ended_text,
            })
    return rows, stats


def officer_cell(row):
    if row["Status"] != "Vacant":
        person = html.escape(row["Name"])
        if row["Email"]:
            person += (f'<br><a href="mailto:{html.escape(row["Email"])}">'
                       f'{html.escape(row["Email"])}</a>')
        return person
    cell = '<span class="vacant">Vacant</span>'
    if row["Last Holder"]:
        last = html.escape(row["Last Holder"])
        ended = row["Last Term Ended"]
        if ended.endswith(" (last seen)"):
            last += f' (listed until {html.escape(ended[:-len(" (last seen)")])})'
        elif ended:
            last += f' (term ended {html.escape(ended)})'
        cell += f'<br><span class="lastheld">last held by {last}</span>'
    return cell


def term_cell(row):
    if row["Status"] == "Vacant":
        return ""
    if row["Term Start"] and row["Term End"]:
        text = f'{row["Term Start"]} – {row["Term End"]}'
    elif row["Term Start"]:
        text = f'since {row["Term Start"]}'
    elif row["Term End"]:
        text = f'until {row["Term End"]}'
    else:
        return ""
    css = "term expired" if row["Status"] == "Term expired" else "term"
    text = html.escape(text)
    if row["Status"] == "Term expired":
        text += " (expired)"
    return f'<span class="{css}">{text}</span>'


def unit_table(unit_rows):
    out = ["<table>",
           "<tr><th style='width:28%'>Position</th><th style='width:42%'>Officer</th>"
           "<th style='width:30%'>Term</th></tr>"]
    for row in unit_rows:
        out.append("<tr>"
                   f"<td>{html.escape(row['Position'])}</td>"
                   f"<td>{officer_cell(row)}</td>"
                   f"<td>{term_cell(row)}</td>"
                   "</tr>")
    out.append("</table>")
    return "\n".join(out)


def society_note(ou_name):
    """For a chapter OU like 'Syracuse Section Chapter,C16', the member
    societies spelled out."""
    codes = [c.strip() for c in ou_name.split(",")[-1].split("/")]
    names = [society_names.get(code) for code in codes]
    return "; ".join(n for n in names if n)


def dedupe(values):
    seen = set()
    unique = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def email_lists_block(rows):
    """Copy-paste-friendly email lists of the CURRENT officers (including
    expired-but-still-listed terms; excluding vacancy backfills): one list
    per group plus a combined list, one address per line."""
    by_group = dict()
    for row in rows:
        if row["Status"] != "Vacant" and row["Email"]:
            by_group.setdefault(row["Group"], []).append(row["Email"])
    combined = dedupe(email for emails in by_group.values() for email in emails)
    labels = {"Section": "Section officers", "Chapters": "Chapter officers",
              "Affinity Groups": "Affinity group officers"}
    groups = [("All officers", combined)]
    groups += [(labels[group], dedupe(by_group[group]))
               for group in labels if group in by_group]
    out = ['<h2>Email lists (copy &amp; paste)</h2>']
    for label, emails in groups:
        if not emails:
            continue
        lines = "<br>".join(html.escape(email) + ";" for email in emails)
        out.append('<div class="emailgroup">'
                   f'<span class="emaillabel">{html.escape(label)} ({len(emails)})</span>'
                   f'<div class="emails">{lines}</div>'
                   '</div>')
    return "\n".join(out)


def build_document(rows, units, section, stats):
    section_unit = next(u for u in units.values() if u.group == "Section")
    title = f"IEEE {section} Leadership Roster"
    today = datetime.date.today().strftime("%B %d, %Y")

    out = [f"<style>{style}</style>"]
    out.append(f"<h1>{html.escape(title)}</h1>")
    subtitle = "Section, Chapter &amp; Affinity Group Officers"
    if section_unit.ou_id:
        subtitle += f" &mdash; {html.escape(section_unit.ou_id)}"
    subtitle += f" &mdash; generated {today} from IEEE OU Analytics"
    out.append(f'<p class="subtitle">{subtitle}</p>')

    n_chapters = len({r["OU"] for r in rows if r["Group"] == "Chapters"})
    n_ags = len({r["OU"] for r in rows if r["Group"] == "Affinity Groups"})
    summary = (f"{stats['current']} officers across the section, "
               f"{n_chapters} chapters, and {n_ags} affinity groups. "
               f"{stats['vacant']} positions are vacant")
    if stats["backfilled"]:
        summary += (f"; for {stats['backfilled']} of them the most recent "
                    "past holder is shown with the month their term ended")
    summary += "."
    if stats["expired"]:
        summary += (f" {stats['expired']} listed officers have terms that "
                    "have already expired (marked in red).")
    out.append(f'<div class="summary">{summary}</div>')

    by_ou = dict()
    for row in rows:
        by_ou.setdefault((row["Group"], row["OU"]), []).append(row)

    for group in ("Section", "Chapters", "Affinity Groups"):
        group_ous = [(ou, ou_rows) for (g, ou), ou_rows in by_ou.items() if g == group]
        if not group_ous:
            continue
        heading = "Section Officers" if group == "Section" else group
        out.append(f"<h2>{html.escape(heading)}</h2>")
        for ou, ou_rows in sorted(group_ous):
            if group != "Section":
                title_bits = html.escape(ou)
                if group == "Chapters":
                    note = society_note(ou)
                    if note:
                        title_bits += f' <span class="ouid">&mdash; {html.escape(note)}</span>'
                else:
                    unit = next(u for u in units.values()
                                if u.group == group and u.name == ou)
                    if unit.ou_id:
                        title_bits += f' <span class="ouid">&mdash; {html.escape(unit.ou_id)}</span>'
                out.append(f"<h3>{title_bits}</h3>")
            out.append(unit_table(ou_rows))

    out.append(email_lists_block(rows))
    return "\n".join(out)


def write_csv(rows, path):
    with open(path, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=output_columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def parse_command_line():
    default_log_level = "WARNING"

    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Section, chapter, and affinity group "
                                                 "officer roster for one section")
    parser.add_argument("token_file", help="File containing personal access token")
    parser.add_argument("section", help='section OU name as OU Analytics spells it, '
                                        'e.g. "Syracuse Section"')
    parser.add_argument("-d",
                        "--output-dir",
                        default="output",
                        help="directory for the output files (default: output)")
    parser.add_argument("-p",
                        "--pdf",
                        action="store_true",
                        help="also write a PDF (requires Chrome)")
    parser.add_argument("--history-cache",
                        default=default_history_cache,
                        help="officer history cache file, updated every run and "
                             "used to name the last holder of vacant roles "
                             f"(default: {default_history_cache})")
    parser.add_argument("--history-csv",
                        help="volunteer positions/history CSV downloaded manually "
                             "from the OU Analytics web UI, merged into the "
                             "history cache before reporting (the API exports "
                             "only active positions, so this is how backfill "
                             "can reach back before this script's first run)")
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

    section = read_section_arg(args.section)
    server = get_server(args.token_file)

    units = collect_units(server, section)
    query_active_positions(server, units)

    section_unit = units[normalize_ou_name(section)]
    if not section_unit.positions:
        logging.error("No volunteer positions found for %r; is the section "
                      "name spelled as OU Analytics spells it?", section)
        exit()

    today = datetime.date.today()
    cache = load_history_cache(args.history_cache)
    if args.history_csv:
        merge_history_csv(cache, args.history_csv)
    update_history_cache(cache, units, today)
    save_history_cache(cache, args.history_cache)

    rows, stats = build_rows(units, cache, today)

    os.makedirs(args.output_dir, exist_ok=True)
    slug = slugify(section)
    csv_path = os.path.join(args.output_dir, f"{slug}_leadership.csv")
    write_csv(rows, csv_path)

    document = build_document(rows, units, section, stats)
    html_path = os.path.join(args.output_dir, f"{slug}_leadership.html")
    with open(html_path, "w") as fp:
        fp.write(document)
    print(f"Wrote roster to {html_path}")
    if args.pdf:
        pdf_path = os.path.join(args.output_dir, f"{slug}_leadership.pdf")
        if html_to_pdf(html_path, pdf_path):
            print(f"Wrote {pdf_path}")

    print(f"{stats['current']} current officers, {stats['vacant']} vacant "
          f"positions ({stats['backfilled']} with a last-known holder), "
          f"{stats['expired']} expired terms still listed.")


if __name__ == '__main__':
    main()
