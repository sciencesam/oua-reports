"""IEEE activity of the people who attended one event, before vs after.

Given the merged registrations file from vtools_registrations.py and an
event id, takes that event's registered people as the cohort and reports,
per person and in aggregate, what they did before and after the event:

  * event participation: distinct vTools events registered for before and
    after the reference date (the cohort event itself excluded), and when
    their first later event was;
  * volunteering: positions held before, positions started after (OU and
    title), and whether they were a brand-new volunteer afterwards (no
    position of any kind before); from the OU Analytics volunteer history;
  * membership: grade, IEEE status, renew year, section; from Members and
    Affiliates, scoped to the OUs where the token owner holds a Region or
    Section role (discovered automatically).

People are matched by IEEE member number when the registration has one and
by email otherwise. Someone who registered as a non-member and joined IEEE
later is linked to their member number through any later registration
that carries both, or through Members and Affiliates by email.

A baseline group is built from the same file: people who registered for at
least one event in the year before the reference date but did not attend
the cohort event. Their aggregates are printed beside the cohort's.

Only registrations with Status "Registered" count; cancelled ones are
dropped. The export has no attended/no-show flag, so "registered" is the
closest available proxy for attendance.

Usage examples:

    python engagement_report.py my_token.json registrations.csv --event 487732 -o summit2025.csv
    python engagement_report.py my_token.json registrations.csv --event 487732 --no-baseline
    python engagement_report.py my_token.json registrations.csv --cohort members.txt --reference-date 2025-08-22

Outputs contain member names and numbers; keep them local.
"""

import argparse
import csv
import datetime
import random
import io
import logging
import os
import sys
from collections import defaultdict

import pandas as pd
import tableauserverclient as TSC

from member_lookup import get_server, normalize_header, normalize_member_number, read_member_numbers

history_view_id = "8294b666-4829-4c5d-9144-9049a36b6dcc"     # volunteer positions, active + past
members_view_id = "3394b7f3-d710-4869-83c9-5cd43885300b"     # Members and Affiliates detail
status_parameter = "Officer Position Status  Parameter"      # two spaces; values Active/Past/(All)
all_ou_types = ("Section,Chapter,Joint Chapter,Affinity,Region,Committee,Council,"
                "Student Branch,Student Branch Chapter,Student Branch Affinity")
batch_size = 50

output_columns = [
    "Person", "Member Number", "Matched By", "Name", "Section", "Grade", "IEEE Status", "Renew Year",
    "Events Before", "Events After", "First Event After", "Days To First Event After",
    "Positions Before", "Positions Started After", "New Volunteer After",
    "Positions Started After (detail)", "Active Positions Now",
]


# ------------------------------------------------------------ registrations

def load_registrations(path):
    """Registered rows only, as a DataFrame with normalized keys:
    member (digits, no leading zeros, or ''), email (lowercase), event_date."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8", encoding_errors="replace")
    df = df[df["Status"].str.strip().str.lower() == "registered"].copy()
    df["member"] = df["Member Number"].str.strip().str.lstrip("0")
    df.loc[~df["member"].str.fullmatch(r"\d+"), "member"] = ""
    df["email"] = df["Email"].str.strip().str.lower()
    df["event_date"] = pd.to_datetime(df["Event Date"].str.replace(" UTC", "", regex=False),
                                      errors="coerce").dt.date
    df = df[df["event_date"].notna() & (df["Event Id"] != "")]
    return df


def link_emails_to_members(df):
    """email -> member number, from any registration carrying both. If an
    email maps to several member numbers (shared inbox), keep the most
    frequent."""
    both = df[(df["member"] != "") & (df["email"] != "")]
    if both.empty:
        return {}
    counts = both.groupby(["email", "member"]).size().reset_index(name="n")
    best = counts.sort_values(["email", "n"], ascending=[True, False]).drop_duplicates("email")
    return dict(zip(best["email"], best["member"]))


def assign_person(df, email_to_member):
    """Person key: member number when known (directly or via email), else
    'email:<address>'. Also records how the row was matched."""
    resolved = df["email"].map(email_to_member).fillna("")
    member = df["member"].where(df["member"] != "", resolved)
    df["person"] = member.where(member != "", "email:" + df["email"])
    df["matched_by"] = "member"
    df.loc[(df["member"] == "") & (resolved != ""), "matched_by"] = "email->member"
    df.loc[(df["member"] == "") & (resolved == ""), "matched_by"] = "email"
    df = df[df["person"] != "email:"]          # no member number and no email: unmatchable
    return df


# ---------------------------------------------------------------- OU data

def query_view(server, view_id, filters):
    view = server.views.get_by_id(view_id)
    options = TSC.CSVRequestOptions()
    for name, value in filters.items():
        options.vf(name, value)
    server.views.populate_csv(view, options)
    text = b"".join(view.csv).decode("utf-8-sig")
    return [{normalize_header(k): v.strip() for k, v in row.items() if k is not None}
            for row in csv.DictReader(io.StringIO(text))]


def batches(values, size=batch_size):
    values = sorted(set(v for v in values if v))
    for start in range(0, len(values), size):
        yield values[start:start + size]


def member_filter(batch):
    return ",".join(sorted(set(batch) | {n.zfill(8) for n in batch}))


def parse_date(text):
    for fmt in ("%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def query_positions(server, numbers):
    """All positions (active and past) for the members: {member: [dict]}."""
    positions = defaultdict(list)
    for batch in batches(numbers):
        for row in query_view(server, history_view_id, {
                "OU Type": all_ou_types, "Member/Customer Number": member_filter(batch),
                status_parameter: "(All)"}):
            member = normalize_member_number(row.get("member/customer number", ""))
            if member and row.get("ou position status") != "Vacant":
                positions[member].append({
                    "ou": row.get("ou name", ""), "title": row.get("ou position", ""),
                    "start": parse_date(row.get("position start")),
                    "end": parse_date(row.get("position end")),
                    "status": row.get("volunteer position status", "")})
    return positions


def token_owner_scope(server):
    """OUs usable as the Members and Affiliates 'Volunteer SPO Name'
    filter: the token owner's Region roles if any, else Section roles."""
    user = server.users.get_by_id(server.user_id)
    rows = query_view(server, history_view_id, {
        "OU Type": all_ou_types, "Email Address": user.email or user.name, status_parameter: "Active"})
    by_type = defaultdict(set)
    for row in rows:
        by_type[row.get("ou type", "")].add(row.get("ou name", ""))
    for level in ("Region", "Section"):
        if by_type.get(level):
            logging.info("Members and Affiliates scope from %s roles: %s", level, sorted(by_type[level]))
            return sorted(by_type[level])
    logging.warning("Token owner has no Region or Section volunteer role; membership lookups skipped")
    return []


def membership_record(row):
    return {"Name": " ".join(p for p in [row.get("first name", ""), row.get("last name", "")] if p),
            "Section": row.get("section", ""), "Grade": row.get("grade", ""),
            "IEEE Status": row.get("ieee status", ""), "Renew Year": row.get("renew year", ""),
            "Email": row.get("email address", "").strip().lower()}


def query_membership(server, numbers, scope):
    """{member: membership dict} from Members and Affiliates."""
    membership = {}
    for ou in scope:
        for batch in batches(numbers):
            for row in query_view(server, members_view_id, {
                    "Volunteer SPO Name": ou, "Member/Customer Number": member_filter(batch)}):
                member = normalize_member_number(row.get("member/customer number", ""))
                if member and member not in membership:
                    membership[member] = membership_record(row)
    return membership


def resolve_emails_in_oua(server, emails, scope):
    """For people known only by email: look them up in Members and
    Affiliates by email. Returns {email: (member, membership dict)}."""
    found = {}
    for ou in scope:
        for batch in batches(emails):
            for row in query_view(server, members_view_id, {
                    "Volunteer SPO Name": ou, "Email Address": ",".join(batch)}):
                record = membership_record(row)
                member = normalize_member_number(row.get("member/customer number", ""))
                if record["Email"] and member and record["Email"] not in found:
                    found[record["Email"]] = (member, record)
    return found


# ---------------------------------------------------------------- analysis

def event_stats(df, persons, reference, cohort_event):
    """Per person: distinct events before/after the reference date
    (cohort event excluded) and first event date after. Vectorized."""
    sub = df[df["person"].isin(persons) & (df["Event Id"] != cohort_event)]
    before = sub[sub["event_date"] < reference].groupby("person")["Event Id"].nunique()
    after_rows = sub[sub["event_date"] >= reference]
    after = after_rows.groupby("person")["Event Id"].nunique()
    first_after = after_rows.groupby("person")["event_date"].min()
    return before, after, first_after


def person_row(person, reference, stats, positions, membership, names, matched_by):
    before, after, first_after = stats
    member = person if not person.startswith("email:") else ""
    held = positions.get(member, []) if member else []
    before_pos = [p for p in held if p["start"] and p["start"] < reference]
    started = [p for p in held if p["start"] and p["start"] >= reference]
    info = membership.get(member, {}) if member else {}
    first = first_after.get(person)
    return {
        "Person": person, "Member Number": member, "Matched By": matched_by.get(person, ""),
        "Name": info.get("Name") or names.get(person, ""),
        "Section": info.get("Section", ""), "Grade": info.get("Grade", ""),
        "IEEE Status": info.get("IEEE Status", ""), "Renew Year": info.get("Renew Year", ""),
        "Events Before": int(before.get(person, 0)), "Events After": int(after.get(person, 0)),
        "First Event After": first.isoformat() if first else "",
        "Days To First Event After": (first - reference).days if first else "",
        "Positions Before": len(before_pos), "Positions Started After": len(started),
        "New Volunteer After": "Y" if started and not before_pos else "N",
        "Positions Started After (detail)": "; ".join(
            f"{p['title']} @ {p['ou']} ({p['start'].isoformat()})" for p in started),
        "Active Positions Now": sum(1 for p in held if p["status"] == "Active"),
    }


def summarize(label, rows):
    n = len(rows)
    if not n:
        return f"{label}: no people"
    pct = lambda k: f"{100.0 * k / n:.0f}%"
    with_before = sum(1 for r in rows if r["Events Before"] > 0)
    with_after = sum(1 for r in rows if r["Events After"] > 0)
    started = sum(1 for r in rows if r["Positions Started After"] > 0)
    new_vol = sum(1 for r in rows if r["New Volunteer After"] == "Y")
    members = sum(1 for r in rows if r["Member Number"])
    active = sum(1 for r in rows if r["IEEE Status"] == "Active")
    known = sum(1 for r in rows if r["IEEE Status"])
    mean = lambda k: sum(r[k] for r in rows) / n
    return (f"{label}: {n} people ({members} with a member number)\n"
            f"  >=1 other event BEFORE: {with_before} ({pct(with_before)}), mean {mean('Events Before'):.2f}\n"
            f"  >=1 event AFTER:        {with_after} ({pct(with_after)}), mean {mean('Events After'):.2f}\n"
            f"  started a volunteer position AFTER: {started} ({pct(started)})\n"
            f"  brand-new volunteer AFTER:          {new_vol} ({pct(new_vol)})\n"
            f"  IEEE status Active today: {active} of {known} with a membership record")


# --------------------------------------------------------------------- cli

def parse_command_line():
    parser = argparse.ArgumentParser(prog="python " + os.path.basename(sys.argv[0]),
                                     description="Before/after IEEE activity of an event's attendees")
    parser.add_argument("token_file", help="OU Analytics personal access token JSON")
    parser.add_argument("registrations", help="merged registrations CSV from vtools_registrations.py merge")
    parser.add_argument("--event", help="vTools event id whose registered people form the cohort")
    parser.add_argument("--cohort", help="instead of --event: file or list of member numbers")
    parser.add_argument("--reference-date", help="YYYY-MM-DD; default: the cohort event's date")
    parser.add_argument("--no-baseline", action="store_true",
                        help="skip the comparison group (active before the date, did not attend)")
    parser.add_argument("--baseline-sample", type=int, metavar="N",
                        help="use a random sample of N baseline people (fixed seed) instead of all of "
                             "them; the full baseline can be tens of thousands of OU Analytics lookups")
    parser.add_argument("-o", "--output", help="per-person CSV (default: stdout)")
    parser.add_argument("-l", "--loglevel", default="WARNING", choices=["ERROR", "WARNING", "INFO", "DEBUG"])
    args = parser.parse_args()
    if not args.event and not args.cohort:
        parser.error("give --event or --cohort")
    if args.cohort and not args.reference_date:
        parser.error("--cohort needs --reference-date")
    return args


def main():
    args = parse_command_line()
    logging.basicConfig(format="%(levelno)s %(module)s:%(funcName)s %(message)s",
                        level=getattr(logging, args.loglevel))

    df = load_registrations(args.registrations)
    email_to_member = link_emails_to_members(df)
    df = assign_person(df, email_to_member)
    logging.info("%d registered rows, %d people, %d emails linked to member numbers",
                 len(df), df["person"].nunique(), len(email_to_member))

    if args.event:
        cohort_df = df[df["Event Id"] == args.event]
        if cohort_df.empty:
            logging.error("No registered people found for event %s", args.event)
            sys.exit(1)
        cohort = sorted(cohort_df["person"].unique())
        names = dict(zip(cohort_df["person"], (cohort_df["First Name"] + " " + cohort_df["Last Name"]).str.strip()))
        reference = (datetime.date.fromisoformat(args.reference_date) if args.reference_date
                     else cohort_df["event_date"].min())
    else:
        cohort = sorted({normalize_member_number(n) for n in read_member_numbers(args.cohort)})
        names = {}
        reference = datetime.date.fromisoformat(args.reference_date)
    matched_by = dict(zip(df["person"], df["matched_by"]))
    logging.info("Cohort: %d people, reference date %s", len(cohort), reference)

    baseline = []
    if not args.no_baseline:
        year_before = reference - datetime.timedelta(days=365)
        active_before = df[(df["event_date"] >= year_before) & (df["event_date"] < reference)]["person"].unique()
        baseline = sorted(set(active_before) - set(cohort))
        logging.info("Baseline: %d people active in the year before who did not attend", len(baseline))
        if args.baseline_sample and len(baseline) > args.baseline_sample:
            baseline = sorted(random.Random(20250822).sample(baseline, args.baseline_sample))
            logging.info("Baseline sampled down to %d people (fixed seed)", len(baseline))

    server = get_server(args.token_file)
    scope = token_owner_scope(server)

    # People known only by email: try to find their member number in OU Analytics.
    everyone = cohort + baseline
    email_only = [p[len("email:"):] for p in everyone if p.startswith("email:")]
    upgraded = resolve_emails_in_oua(server, email_only, scope) if email_only and scope else {}
    if upgraded:
        logging.info("Resolved %d of %d email-only people to member numbers via Members and Affiliates",
                     len(upgraded), len(email_only))
        remap = {f"email:{e}": m for e, (m, _) in upgraded.items()}
        df["person"] = df["person"].replace(remap)
        df.loc[df["person"].isin(remap.values()) & (df["matched_by"] == "email"), "matched_by"] = "email->OUA"
        cohort = sorted({remap.get(p, p) for p in cohort})
        baseline = sorted({remap.get(p, p) for p in baseline} - set(cohort))
        names = {remap.get(p, p): n for p, n in names.items()}
        matched_by = dict(zip(df["person"], df["matched_by"]))
        everyone = cohort + baseline

    members = [p for p in everyone if not p.startswith("email:")]
    positions = query_positions(server, members)
    membership = query_membership(server, members, scope) if scope else {}
    for _, (m, record) in upgraded.items():
        membership.setdefault(m, record)

    stats = event_stats(df, set(everyone), reference, args.event or "")
    cohort_out = [person_row(p, reference, stats, positions, membership, names, matched_by) for p in cohort]
    baseline_out = [person_row(p, reference, stats, positions, membership, {}, matched_by) for p in baseline]

    fp = open(args.output, "w", newline="") if args.output else sys.stdout
    writer = csv.DictWriter(fp, fieldnames=output_columns)
    writer.writeheader()
    writer.writerows(cohort_out)
    if args.output:
        fp.close()
        print(f"Wrote {len(cohort_out)} cohort rows to {args.output}")

    print(f"\nReference date: {reference}   (events on/after this date count as 'after')")
    print(summarize("COHORT (attended the event)", cohort_out))
    if baseline_out:
        print(summarize("BASELINE (active before, did not attend)", baseline_out))


if __name__ == "__main__":
    main()
