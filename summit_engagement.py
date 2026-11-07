"""What one event's participants did in IEEE before and after it, compared
with people like them who were not there.

Reads only local files (the vTools registrations file and the OU Analytics
bulk exports from oua_bulk_export.py), so it runs in seconds for any event.

Metrics keep their units: distinct events registered for, events hosted by
the person's own section, new volunteer positions started, highest
volunteer tier held (Student branch, Chapter/AG, Section, Region & above),
and membership facts. There is no combined score.

Cohort: non-cancelled, paid-or-free registrations for the event whose
person has an active Region 1/2 member record. Registrations still waiting
for payment are dropped (they are usually no-shows); people without an
active R1/R2 record (visitors from other regions, lapsed members,
unresolvable numbers) are listed but not analysed, because their IEEE
activity is not visible in the Region exports.

Comparison: every R1/R2 IEEE Young Professionals member who was not at the
event, split the same way as the participants by whether they had ever
held a volunteer position before the event and by how many events they
registered for in the year before. No sampling, no matching.

Windows: the same number of days before and after the reference date, as
long as the registration data allows on both sides.

Usage:

    python summit_engagement.py --data-dir /data/ieee/oua \\
        --registrations /data/ieee/vtools/registrations.csv \\
        --event 487732 --out-dir /data/ieee/oua/summit2025

Outputs (personal data; keep local): report.html, participants.csv,
comparison.csv.
"""

import argparse
import datetime
import html
import logging
import os
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

import svgcharts as sc
from engagement_report import load_registrations, link_emails_to_members, assign_person, parse_date

tier_names = {0: "No position", 1: "Student branch", 2: "Chapter/AG", 3: "Section", 4: "Region & above"}
ou_type_tier = {"Student Branch": 1, "Student Branch Chapter": 1, "Student Branch Affinity": 1,
                "Chapter": 2, "Joint Chapter": 2, "Affinity": 2, "Section": 3,
                "Region": 4, "Council": 4, "Committee": 4}
ignored_positions = {"SAMIEEE RECIPIENT"}          # a data-access grant, not a role
events_bins = [(0, 0, "0"), (1, 3, "1–3"), (4, 10 ** 9, "4+")]
fine_bins = [(0, 0, "0"), (1, 1, "1"), (2, 3, "2–3"), (4, 6, "4–6"), (7, 10 ** 9, "7+")]
default_excluded_payment = "Waiting for payment notification"
segment_dimensions = [
    ("Summit role (ticket)", "ticket"), ("Volunteer tier at the Summit", "tier_at_ref_name"),
    ("Volunteered before (ever)", "volunteer_before"), ("YP member", "yp_status"), ("Grade", "grade_group"),
    ("Region", "region"), ("Events in the year before", "events_before_bin"), ("Home section has a YP AG", "home_yp_ag"),
]


def bin_label(value, bins):
    for lo, hi, label in bins:
        if lo <= value <= hi:
            return label
    return ""


def norm_member(value):
    value = (value or "").strip().lstrip("0")
    return value if value.isdigit() else ""


# ------------------------------------------------------------------ loading

def load_members(path, inactive_path=None):
    """Active members, plus (when the inactive export exists) members whose
    status is Arrears/Inactive, so that attendees who lapsed after the event
    can still be followed. 'status_src' says which."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False); df["status_src"] = "Active"
    if inactive_path and os.path.exists(inactive_path):
        di = pd.read_csv(inactive_path, dtype=str, keep_default_na=False); di["status_src"] = di["ieee status"].replace("", "Inactive")
        df = pd.concat([df, di], ignore_index=True)
    df["member"] = df["member/customer number"].map(norm_member)
    df = df[df["member"] != ""].drop_duplicates("member")          # active rows come first and win
    df["email"] = df["email address"].str.strip().str.lower()
    return df.set_index("member")


def load_yp(path):
    """Active YP product holders -> {member: {"joined": initial join date,
    "graduated": graduation date, "years_of_service": str}} (dates or None)."""
    if not os.path.exists(path):
        logging.warning("No %s; YP status will be unknown", path)
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "asset status" in df.columns:
        df = df[df["asset status"].str.lower() == "active"]
    out = {}
    for m, joined, grad, yos, elig in zip(df["member/customer number"].map(norm_member),
                                          df.get("initial join date", pd.Series("", index=df.index)).map(parse_date),
                                          df.get("graduation date", pd.Series("", index=df.index)).map(parse_date),
                                          df.get("years of service", pd.Series("", index=df.index)),
                                          df.get("yp eligible y/n", pd.Series("", index=df.index)).str.strip().str.upper()):
        if m and m not in out:
            out[m] = {"joined": joined, "graduated": grad, "years_of_service": yos, "eligible": elig}
    return out


def load_overrides(path):
    """Member numbers confirmed as YPs by the program lead although no YP
    product is on record (enrollment gaps)."""
    if not path or not os.path.exists(path):
        return set()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return {norm_member(m) for m in df["member"]} - {""}


def load_history(path):
    """{member: [merged positions]} where the same seat (OU + title) that
    appears as several rows (re-entries, term renewals) is one position
    with its earliest start and latest end."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df[~df["ou position"].isin(ignored_positions)]
    df["member"] = df["member/customer number"].map(norm_member)
    df["tier"] = df["ou type"].map(ou_type_tier).fillna(0).astype(int)
    df["start"] = df["position start"].map(parse_date)
    df["end"] = df["position end"].map(parse_date)
    df = df[(df["member"] != "") & df["start"].notna()]
    df = df.rename(columns={"ou name": "ou_name", "ou position": "ou_position"})
    positions = defaultdict(dict)
    for row in df.itertuples(index=False):
        key = (row.ou_name.strip(), row.ou_position.strip())
        seat = positions[row.member].get(key)
        if seat is None:
            positions[row.member][key] = {"ou": key[0], "title": key[1], "tier": row.tier,
                                          "start": row.start, "end": row.end, "open": row.end is None}
        else:
            seat["start"] = min(seat["start"], row.start)
            seat["open"] = seat["open"] or row.end is None
            seat["end"] = None if seat["open"] else max(seat["end"], row.end)
    return {member: list(seats.values()) for member, seats in positions.items()}


def grade_group(grade):
    g = (grade or "").lower()
    if not g:
        return "unknown"
    if "student" in g:
        return "Student / Graduate Student"
    if "life" in g or "fellow" in g:
        return "Life / Fellow"
    if "senior" in g:
        return "Senior Member"
    return "Member"


def yp_ag_sections(positions, reference):
    pattern = re.compile(r"^(.*?)\s+(Jt\.?\s+)?(Affinity Group|Affinity Grp|Affn|Aff Grp)\s*,\s*(WIE/)?YP$")
    sections = set()
    for seats in positions.values():
        for seat in seats:
            match = pattern.match(seat["ou"])
            if match and seat["tier"] == 2 and seat["start"] <= reference and (seat["end"] is None or seat["end"] >= reference):
                sections.add(match.group(1).strip())
    return sections


# ---------------------------------------------------------------- metrics

def active(seat, day):
    return seat["start"] <= day and (seat["end"] is None or seat["end"] >= day)


def position_metrics(seats, reference, window, before_start):
    after_end, before_end = reference + window, before_start + window
    at_ref = [s for s in seats if active(s, reference)]
    new_after = [s for s in seats if reference < s["start"] <= after_end]
    new_before = [s for s in seats if before_start < s["start"] <= before_end]
    active_after = [s for s in seats if s["start"] <= after_end and (s["end"] is None or s["end"] > reference)]
    ever_before = any(s["start"] < reference for s in seats)
    ous_before = {s["ou"] for s in seats if s["start"] < reference}
    new_ou = [s for s in new_after if s["ou"] not in ous_before]
    top = max((s["tier"] for s in at_ref), default=0)
    top_seats = [s for s in at_ref if s["tier"] == top]
    if top == 2:
        role = ("YP affinity group" if any(re.search(r"(Affinity Group|Affinity Grp|Affn|Aff Grp)\s*,\s*(WIE/)?YP\b", s["ou"]) for s in top_seats)
                else "other affinity group" if any("Affinity" in s["ou"] or "Affn" in s["ou"] for s in top_seats) else "chapter")
    else:
        role = tier_names[top]
    return {
        "role_at_ref_name": role,
        "new_positions_before": len(new_before),
        "new_ou_after": len(new_ou),
        "new_ou_detail": "; ".join(f"{s['title']} @ {s['ou']} ({s['start'].isoformat()})" for s in new_ou),
        "new_title_detail": "; ".join(f"{s['title']} @ {s['ou']} ({s['start'].isoformat()})" for s in new_after if s["ou"] in ous_before),
        "tier_at_ref": max((s["tier"] for s in at_ref), default=0),
        "tier_after": max((s["tier"] for s in active_after), default=0),
        "positions_at_ref": len(at_ref),
        "positions_at_ref_detail": "; ".join(f"{s['title']} @ {s['ou']}" for s in at_ref),
        "new_positions_after": len(new_after),
        "volunteer_before": "yes" if ever_before else "no",
        "new_volunteer_after": "yes" if new_after and not ever_before else "no",
        "new_positions_detail": "; ".join(f"{s['title']} @ {s['ou']} ({s['start'].isoformat()})" for s in new_after),
    }


def yp_status_of(member, info, yp, overrides, exclusions=frozenset()):
    """'YP member' = holds the IEEE Young Professionals product (whatever the
    eligibility flag says; it is unset whenever the graduation date is missing
    or odd) or is on the confirmed list; the exclusion list removes product
    holders confirmed not to be YPs."""
    if member in exclusions:
        return "not YP member"
    if member in overrides or member in yp:
        return "YP member"
    if info is not None and info["status_src"] != "Active":
        return "unknown"                       # the YP product view covers active members only
    return "not YP member" if info is not None else "unknown"


def build_people(reg, members, yp, positions, cohort_event, reference, window, before_start, overrides=frozenset(), exclusions=frozenset()):
    """One row per person in the registrations OR the member export.
    Before window: [before_start, before_start + window); after window:
    (reference, reference + window]."""
    before_end, after_end = before_start + window, reference + window
    other = reg[reg["Event Id"] != cohort_event]
    before = other[(other["event_date"] >= before_start) & (other["event_date"] < before_end)]
    after = other[(other["event_date"] > reference) & (other["event_date"] <= after_end)]
    ev_before = before.groupby("person")["Event Id"].nunique()
    ev_after = after.groupby("person")["Event Id"].nunique()
    first_after = after.groupby("person")["event_date"].min()
    last_before = before.groupby("person")["event_date"].max()
    region_of = reg.groupby("person")["Region"].agg(lambda s: Counter(v.split(",")[0].strip() for v in s).most_common(1)[0][0])
    email_of = reg.groupby("person")["email"].first()
    names = reg.groupby("person").agg(first=("First Name", "first"), last=("Last Name", "first"))
    registered = set(reg["person"])

    rows = []
    for person in registered | set(members.index):
        member = person if not person.startswith("email:") else ""
        info = members.loc[member] if member and member in members.index else None
        row = {
            "person": person, "member": member,
            "name": (f"{info['first name']} {info['last name']}".strip() if info is not None else
                     f"{names.loc[person, 'first']} {names.loc[person, 'last']}".strip() if person in names.index else ""),
            "member_record": ("active member record" if info is not None and info["status_src"] == "Active" else
                              "member at the Summit, since lapsed" if info is not None and str(info.get("renew year", "")).isdigit() and int(info["renew year"]) >= reference.year else
                              "lapsed before the Summit" if info is not None else
                              ("no active R1/R2 record" if member else "email only")),
            "member_now": "yes" if info is not None and info["status_src"] == "Active" else "no",
            "last_renew_year": info["renew year"] if info is not None else "",
            "registered_anything": person in registered,
            "email": (info["email"] if info is not None else email_of.get(person, "")),
            "section": info["section"] if info is not None else "",
            "section_spo": info["section geo spo id"] if info is not None else "",
            "region": (info["region"] if info is not None and info.get("region") else region_of.get(person, "unknown")),
            "grade": info["grade"] if info is not None else "",
            "grade_group": grade_group(info["grade"] if info is not None else ""),
            "renew_year": info["renew year"] if info is not None else "",
            "yp_status": yp_status_of(member, info, yp, overrides, exclusions),
            "yp_override": "Y" if member in overrides else ("excluded" if member in exclusions else ""),
            "yp_flag": yp[member]["eligible"] if member in yp else "",
            "joined_ieee": yp[member]["joined"] if member in yp else None,
            "graduated": yp[member]["graduated"] if member in yp else None,
            "years_of_service": yp[member]["years_of_service"] if member in yp else "",
            "events_before": int(ev_before.get(person, 0)), "events_after": int(ev_after.get(person, 0)),
            "first_event_after": first_after.get(person),
            "last_event_before": last_before.get(person),
        }
        row["events_delta"] = row["events_after"] - row["events_before"]
        row["events_before_bin"] = bin_label(row["events_before"], events_bins)
        row.update(position_metrics(positions.get(member, []), reference, window, before_start))
        rows.append(row)
    people = pd.DataFrame(rows).set_index("person")
    people["years_since_graduation"] = people["graduated"].map(lambda d: round((reference - d).days / 365.25, 1) if d else None)
    people["years_since_joining"] = people["joined_ieee"].map(lambda d: round((reference - d).days / 365.25, 1) if d else None)
    people["grad_bin"] = people["years_since_graduation"].map(
        lambda y: "unknown" if y is None or y != y else ("future graduation date (GSM or master's date)" if y < 0 else "0–4 years since graduation" if y < 5 else "5–9" if y < 10 else "10–15" if y <= 15 else "over 15"))
    people["tier_at_ref_name"] = people["tier_at_ref"].map(tier_names)
    people["tier_after_name"] = people["tier_after"].map(tier_names)
    people["tier_up"] = people["tier_after"] > people["tier_at_ref"]

    local = other.merge(people[["section_spo"]], left_on="person", right_index=True)
    local = local[local["section_spo"] != ""]
    local = local[[spo in {v.strip() for v in host.split(",")} for host, spo in zip(local["Section"], local["section_spo"])]]
    lb = local[(local["event_date"] >= before_start) & (local["event_date"] < before_end)].groupby("person")["Event Id"].nunique()
    la = local[(local["event_date"] > reference) & (local["event_date"] <= after_end)].groupby("person")["Event Id"].nunique()
    people["local_before"] = people.index.map(lambda p: int(lb.get(p, 0)))
    people["local_after"] = people.index.map(lambda p: int(la.get(p, 0)))
    return people


# ------------------------------------------------------------------ report

style = """
  body { font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; color: #0b0b0b; background: #f9f9f7;
         max-width: 70em; margin: 2em auto; padding: 0 1.5em; line-height: 1.45; }
  h1 { font-size: 1.6em; margin-bottom: 0.1em; } h2 { font-size: 1.25em; margin-top: 2em; border-bottom: 1px solid #e1e0d9; padding-bottom: 0.2em; }
  h3 { font-size: 1.05em; margin-top: 1.4em; }
  .subtitle { color: #52514e; margin-top: 0; }
  table { border-collapse: collapse; font-size: 0.86em; margin: 0.6em 0 1.2em; background: #fcfcfb; }
  caption { text-align: left; font-weight: 600; padding: 0.3em 0; }
  th { text-align: left; background: #f0efec; padding: 0.35em 0.6em; border-bottom: 1px solid #c3c2b7; vertical-align: bottom; }
  td { padding: 0.3em 0.6em; border-bottom: 1px solid #e1e0d9; font-variant-numeric: tabular-nums; vertical-align: top; }
  tr.total td { font-weight: 600; border-top: 2px solid #c3c2b7; }
  svg { display: block; margin: 0.8em 0; max-width: 100%; height: auto; }
  .note { color: #52514e; font-size: 0.9em; } .wrap { overflow-x: auto; }
  @media print { body { margin: 0.5em; max-width: none; } svg, table { break-inside: avoid; } h2 { break-after: avoid; } }
"""


def count_pct(mask):
    n, k = len(mask), int(mask.sum())
    return f"{k} ({100 * k / n:.0f}%)" if n else "–"


def outcome_cells(group):
    """The outcome columns used by every table: counts with percentages."""
    return [len(group),
            count_pct(group["events_after"] > 0),
            f"{group['events_after'].mean():.1f}" if len(group) else "–",
            count_pct(group["events_delta"] > 0),
            count_pct(group["local_after"] > 0),
            count_pct(group["new_ou_after"] > 0),
            count_pct(group["new_positions_after"] > group["new_ou_after"]),
            count_pct(group["new_volunteer_after"] == "yes"),
            count_pct(group["tier_up"])]


outcome_columns = ["n", "≥1 event after", "Events after, mean", "More events after than before", "≥1 own-section event after",
                   "Position in a new OU", "New title in an OU already served", "Brand-new volunteer", "Moved up a tier"]


def outcome_table(rows, caption, first_columns):
    return "<div class='wrap'>" + sc.table(first_columns + outcome_columns, rows, caption) + "</div>"


def hist_pct(values, bins):
    counts = Counter(bin_label(v, bins) for v in values)
    total = len(values) or 1
    return [100.0 * counts.get(label, 0) / total for _, _, label in bins]


def curve(group, reference, months):
    pts = []
    for m in months:
        cutoff = reference + datetime.timedelta(days=30 * m)
        pts.append((m, 100.0 * sum(1 for d in group["first_event_after"] if d is not None and d == d and d <= cutoff) / (len(group) or 1)))
    return pts


def monthly_rate(reg, persons, cohort_event, start, end):
    """Registrations per 100 people per calendar month, for the given persons."""
    sub = reg[reg["person"].isin(persons) & (reg["Event Id"] != cohort_event)]
    sub = sub[(sub["event_date"] >= start) & (sub["event_date"] <= end)]
    months = pd.to_datetime(sub["event_date"]).dt.to_period("M")
    counts = sub.assign(month=months).groupby("month")["person"].size()
    span = pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
    return [(i, 100.0 * counts.get(m, 0) / (len(persons) or 1)) for i, m in enumerate(span)], [str(m) for m in span]


def build_report(args, people, registered, dropped_payment, cohort, reference, window, event_title, yp_sections,
                 before_start, reg, data_start, data_end, no_show=0):
    days = window.days
    before_label = (f"{before_start.isoformat()} to {(before_start + window).isoformat()}")
    after_label = f"{reference.isoformat()} to {(reference + window).isoformat()}"
    all_reg = people.loc[registered]
    coh = people.loc[cohort].sort_values(["tier_at_ref", "name"], ascending=[False, True])
    not_analysed = all_reg[all_reg["member_record"] != "active member record"]
    comp = people[(people["yp_status"] == "YP member") & ~people.index.isin(registered)]
    coh_yp = coh[coh["yp_status"] == "YP member"]
    out = [f"<style>{style}</style>", f"<h1>{html.escape(event_title)}</h1>",
           f"<p class='subtitle'>What participants did in IEEE before ({before_label}) and after ({after_label}) the event "
           f"· {days}-day windows · generated {datetime.date.today().isoformat()}</p>"]

    # ---- 0. who is in
    out.append("<h2>1. Who is in the analysis</h2>")
    rows = [["Registrations not cancelled", len(registered) + dropped_payment + no_show, ""],
            ["Dropped: still waiting for payment (usually no-shows)", dropped_payment, ""],
            ["Dropped: not on the attendee list", no_show, ""],
            ["Not analysed: no active Region 1/2 member record (visitor from another region, lapsed, or unresolvable number)",
             int((not_analysed["member_record"] == "no active R1/R2 record").sum()),
             ", ".join(f"{k}: {v}" for k, v in Counter(not_analysed[not_analysed["member_record"] == "no active R1/R2 record"]["ticket"]).items())],
            ["Not analysed: registered with an email only, no member match", int((not_analysed["member_record"] == "email only").sum()), ""],
            ["<b>Analysed participants</b> (active R1/R2 members)", len(coh), f"of whom {len(coh_yp)} YP members"]]
    out.append(sc.table(["", "People", ""], rows, "From registrations to the analysed cohort").replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>"))
    out.append("<p class='note'>Registration is the unit for every other event: vTools records attendance for almost none. "
               + (f"For this event the cohort is restricted to the attendee list ({args.attendees}); registered people not on it are dropped."
                  if args.attendees else "If you have the actual attendee list, pass it with --attendees to restrict the cohort to people who were there.")
               + "</p>")

    # ---- 1. participant table
    out.append("<h2>2. What each participant did</h2>")
    prow = []
    for _, r in coh.iterrows():
        prow.append([r["name"], r["ticket"].replace("Speaker / VIP / Event Volunteer", "Speaker/VIP/Volunteer"),
                     r["tier_at_ref_name"], r["positions_at_ref_detail"], "YP" if r["yp_status"] == "YP member" else "",
                     r["grade"], r["section"].replace(" Section", ""), r["events_before"], r["events_after"],
                     r["local_before"], r["local_after"], r["new_ou_detail"], r["new_title_detail"], "↑ " + r["tier_after_name"] if r["tier_up"] else ""])
    out.append("<div class='wrap'>" + sc.table(["Name", "Ticket", "Tier at Summit", "Positions at the Summit", "YP", "Grade", "Section",
                                                 "Events before", "Events after", "Own-section before", "Own-section after",
                                                 "New position in a new OU", "New title in an OU already served", "Tier change"], prow,
                                                "Participants, highest volunteer tier first") + "</div>")
    drows = [(f"{r['name']} · {r['tier_at_ref_name']}", r["events_before"], r["events_after"],
              f"{r['name']}: before {r['events_before']}, after {r['events_after']}")
             for _, r in coh.sort_values(["events_before", "events_after"]).iterrows()]
    out.append(sc.dumbbell(drows, title="Events registered for, before → after, per participant", xlabel="distinct events (the Summit excluded)"))

    # ---- seasonality: monthly activity across the whole data span
    span_start, span_end = data_start, data_end
    a_pts, labels_m = monthly_rate(reg, set(coh.index), args.event, span_start, span_end)
    b_pts, _ = monthly_rate(reg, set(comp.index), args.event, span_start, span_end)
    summit_month = labels_m.index(str(pd.Period(reference, "M")))
    out.append(sc.lines([(f"participants (n={len(coh)})", a_pts, None),
                         (f"other YP members (n={len(comp):,})", b_pts, None)],
                        title="Registrations per 100 people, by calendar month (the Summit itself excluded)",
                        xlabel=f"month (0 = {labels_m[0]}; the Summit is in month {summit_month}, {labels_m[summit_month]})",
                        ylabel="registrations per 100 people", x_ticks=list(range(0, len(labels_m), 3)), value_format="{:.0f}"))
    out.append("<p class='note'>Activity is seasonal (academic year; volunteer terms start on 1 January), so before and after "
               f"windows must cover the same months. This run uses the '{args.before}' before window: "
               + ("the same length immediately before the event." if args.before == "mirror" else
                  "the same calendar span one year earlier.") + "</p>")

    # ---- 2. counts by segment
    out.append("<h2>3. Outcomes by participant metadata</h2>")
    out.append("<p class='note'>Counts of participants, with the share of the row in parentheses. "
               "A position in a new OU is a seat in an organizational unit the person had never served in before the Summit: new involvement. "
               "A new title in an OU already served (e.g. Vice Chair becoming Treasurer) is listed separately, since vTools often records such "
               "changes late. Term renewals of an existing seat count as neither.</p>")
    srows = []
    for title, key in segment_dimensions:
        for level in sorted(set(coh[key]), key=str):
            srows.append([title, level] + outcome_cells(coh[coh[key] == level]))
    srows.append(["All participants", ""] + outcome_cells(coh))
    out.append(outcome_table(srows, "Participants by segment", ["Dimension", "Level"]))

    # ---- 3. comparison with YP members
    out.append("<h2>4. Compared with YP members who were not at the Summit</h2>")
    out.append(f"<p class='note'>Every active Region 1/2 IEEE Young Professionals member ({len(comp):,} people), split the same way as "
               "the YP participants: whether they had ever held a volunteer position before the Summit, and how many events they "
               "registered for in the year before. Each row pairs the participants in a stratum with the non-participants in the same stratum. "
               "Most YP members never register for anything in vTools; the 'events before' split is what makes the rows comparable.</p>")
    crows = []
    for vol in ("no", "yes"):
        for _, _, label in events_bins:
            a = coh_yp[(coh_yp["volunteer_before"] == vol) & (coh_yp["events_before_bin"] == label)]
            b = comp[(comp["volunteer_before"] == vol) & (comp["events_before_bin"] == label)]
            crows.append([vol, label, "participants"] + outcome_cells(a))
            crows.append([vol, label, "other YP members"] + outcome_cells(b))
    crows.append(["all", "all", "participants"] + outcome_cells(coh_yp))
    crows.append(["all", "all", "other YP members"] + outcome_cells(comp))
    out.append(outcome_table(crows, "YP participants vs other YP members, by prior volunteering and prior events",
                             ["Volunteered before", "Events before", "Group"]))

    labels = [label for _, _, label in fine_bins]
    for vol, heading in (("no", "people who had never held a volunteer position"), ("yes", "people who had already volunteered")):
        a = coh_yp[coh_yp["volunteer_before"] == vol]
        b = comp[(comp["volunteer_before"] == vol) & (comp["events_before"] > 0)]
        c = comp[(comp["volunteer_before"] == vol) & (comp["events_before"] == 0)]
        out.append(sc.grouped_bars(labels, [
            (f"YP participants (n={len(a)})", hist_pct(a["events_after"], fine_bins), None),
            (f"other YPs with ≥1 event before (n={len(b):,})", hist_pct(b["events_after"], fine_bins), None),
            (f"other YPs with 0 events before (n={len(c):,})", hist_pct(c["events_after"], fine_bins), sc.context_gray)],
            title=f"Events registered for in the {days} days after — {heading}", ylabel="% of group", value_format="{:.0f}%"))
    months = list(range(0, days // 30 + 1))
    for vol, heading in (("no", "never volunteered before"), ("yes", "already a volunteer")):
        a = coh_yp[coh_yp["volunteer_before"] == vol]
        b = comp[(comp["volunteer_before"] == vol) & (comp["events_before"] > 0)]
        out.append(sc.lines([(f"YP participants (n={len(a)})", curve(a, reference, months), None),
                             (f"other YPs with ≥1 event before (n={len(b):,})", curve(b, reference, months), None)],
                            title=f"Share who registered for another event, by month — {heading}",
                            xlabel="months after the Summit", ylabel="% of group", x_ticks=months[::2]))
    names = [tier_names[t] for t in range(5)]
    short = ["None", "Student br.", "Chapter/AG", "Section", "Region+"]
    matrix = [[int(((coh["tier_at_ref"] == i) & (coh["tier_after"] == j)).sum()) for j in range(5)] for i in range(5)]
    out.append(sc.heatmap(names, short, matrix, title="Participants: highest tier at the Summit → highest tier held afterwards",
                          row_title="at the Summit", col_title="after"))

    # ---- method
    out.append("<h2>Method</h2>")
    out.append(f"<p class='note'>Cohort: registrations for event {args.event} with status Registered and a payment status other than "
               f"'{args.exclude_payment_status}', whose person has an active Region 1/2 member record (Members & Affiliates export). "
               f"Windows: before {before_label}, after {after_label} ({days} days each; before mode '{args.before}'), "
               f"limited by the registration data ({args.registrations}). "
               "Events: distinct vTools events with a non-cancelled registration, the Summit itself excluded, nothing else excluded. "
               "Positions: OU Analytics volunteer history (active and past) for R1/R2 volunteers, SAMIEEE recipient grants ignored, rows for "
               "the same seat merged; tiers by OU type. YP = active IEEE Young Professionals product. Own-section events: host section geo "
               f"SPO id equals the person's section. Home YP AG: {len(yp_sections)} sections with a YP affinity group position filled on the reference date. "
               "People matched to registrations by member number, else by email (later registrations or Members & Affiliates).</p>")
    return "\n".join(out)


# --------------------------------------------------------------------- main

def parse_command_line():
    parser = argparse.ArgumentParser(description="Before/after engagement of an event's participants vs YP members who were not there")
    parser.add_argument("--data-dir", required=True, help="directory with volunteer_history.csv, members.csv, yp_products.csv")
    parser.add_argument("--registrations", required=True, help="merged registrations CSV")
    parser.add_argument("--event", required=True, help="vTools event id of the cohort event")
    parser.add_argument("--reference-date", help="YYYY-MM-DD (default: the event's date)")
    parser.add_argument("--window-days", type=int, help="override the automatic window length")
    parser.add_argument("--before", choices=["mirror", "year-earlier"], default="mirror",
                        help="'mirror': the before window is the W days before the event; 'year-earlier': the same "
                             "calendar span one year before the after window (controls for seasonality; use it when "
                             "less than a year of data exists after the event)")
    parser.add_argument("--attendees", help="optional list of people actually present, which restricts the cohort to them: "
                                            "a CSV with Member Number / First Name / Last Name (and optionally Email) columns, "
                                            "or a plain file of member numbers or emails, one per line")
    parser.add_argument("--yp-overrides", help="CSV with a 'member' column: people confirmed as YPs who lack the product")
    parser.add_argument("--yp-exclusions", help="CSV with a 'member' column: product holders confirmed not to be YPs")
    parser.add_argument("--grants", help="CSV with 'member' column: travel-grant recipients (adds a 'grant' column)")
    parser.add_argument("--speakers", help="speakers.json from the Summit site (adds a 'speaker' column)")
    parser.add_argument("--exclude-payment-status", default=default_excluded_payment,
                        help="registrations with this payment status are dropped everywhere (default: %(default)s; use '' to keep all)")
    parser.add_argument("--title", help="report title (default: the event title)")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("-l", "--loglevel", default="INFO", choices=["ERROR", "WARNING", "INFO", "DEBUG"])
    return parser.parse_args()


def main():
    args = parse_command_line()
    logging.basicConfig(format="%(levelname)s %(message)s", level=getattr(logging, args.loglevel))
    os.makedirs(args.out_dir, exist_ok=True)

    reg = load_registrations(args.registrations)
    dropped_payment = 0
    if args.attendees and args.exclude_payment_status:
        logging.info("Attendee list given; the payment-status filter is not applied (the list decides who was there)")
        args.exclude_payment_status = ""
    if args.exclude_payment_status:
        unpaid = reg["Payment Status"].str.strip() == args.exclude_payment_status
        dropped_payment = int((unpaid & (reg["Event Id"] == args.event)).sum())
        logging.info("Dropping %d registrations with payment status '%s' (%d for the cohort event)",
                     int(unpaid.sum()), args.exclude_payment_status, dropped_payment)
        reg = reg[~unpaid]
    reg = assign_person(reg, link_emails_to_members(reg))
    cohort_rows = reg[reg["Event Id"] == args.event]
    if cohort_rows.empty:
        logging.error("No registered people for event %s", args.event)
        sys.exit(1)
    reference = datetime.date.fromisoformat(args.reference_date) if args.reference_date else cohort_rows["event_date"].min()
    data_start, data_end = reg["event_date"].min(), reg["event_date"].max()
    if args.before == "mirror":
        window = datetime.timedelta(days=args.window_days or min((reference - data_start).days, (data_end - reference).days))
        before_start = reference - window
    else:
        year = datetime.timedelta(days=365)
        window = datetime.timedelta(days=args.window_days or min((data_end - reference).days, 365))
        before_start = reference - year
        if before_start < data_start:
            logging.warning("Registration data starts %s, after the year-earlier window start %s; the before window is "
                            "shortened at its start and the calendar alignment is only partial", data_start, before_start)
            before_start = data_start
            window = min(window, reference - before_start)
    logging.info("Reference %s; data %s..%s; before window %s..%s, after window %s..%s (%d days)",
                 reference, data_start, data_end, before_start, before_start + window, reference, reference + window, window.days)
    if window.days < 90:
        logging.warning("Only %d days of data after the reference date; 'after' metrics will be thin", window.days)

    members = load_members(os.path.join(args.data_dir, "members.csv"), os.path.join(args.data_dir, "members_inactive.csv"))
    yp = load_yp(os.path.join(args.data_dir, "yp_products.csv"))
    positions = load_history(os.path.join(args.data_dir, "volunteer_history.csv"))
    yp_sections = yp_ag_sections(positions, reference)
    logging.info("%d members, %d YP members, %d volunteers with history, %d sections with a YP AG",
                 len(members), len(yp), len(positions), len(yp_sections))

    email_to_member = {e: m for m, e in members["email"].items() if e}
    reg.loc[reg["person"].str.startswith("email:") & reg["email"].isin(email_to_member), "matched_by"] = "email->member"
    reg["person"] = reg.apply(lambda r: email_to_member.get(r["email"], r["person"]) if r["person"].startswith("email:") else r["person"], axis=1)

    overrides = load_overrides(args.yp_overrides); exclusions = load_overrides(args.yp_exclusions)
    people = build_people(reg, members, yp, positions, args.event, reference, window, before_start, overrides, exclusions)
    grants = load_overrides(args.grants)
    people["grant"] = people["member"].map(lambda m: "Y" if m and m in grants else "")
    people["speaker"] = ""
    if args.speakers and os.path.exists(args.speakers):
        import json
        def key(n):
            return [x for x in re.sub(r"[^a-z ]", " ", n.lower()).split() if x not in ("dr",) and len(x) > 1]
        speakers = [(key(sp["name"]), sp.get("title", "")) for sp in json.load(open(args.speakers))]
        def match(name):
            kp = key(name)
            for sp, title in speakers:
                if kp and sp and sp[-1][:5] == kp[-1][:5] and sp[0][:3] == kp[0][:3]:
                    return title or "speaker"
            return ""
        people["speaker"] = people["name"].map(match)
    people["home_yp_ag"] = people["section"].map(lambda s: "yes" if s in yp_sections else ("no" if s else "unknown"))
    summit = reg[reg["Event Id"] == args.event]
    registered = sorted(set(summit["person"]))
    ticket = (summit.groupby("person")["Fee Description"].first().str.strip()
              .str.replace(r"^Last-minute ", "", regex=True).str.replace(r"\s*\(e\.g\..*\)$", "", regex=True)
              .replace("", "(no ticket type)"))
    people["ticket"] = people.index.map(lambda p: ticket.get(p, ""))
    if args.attendees:
        keys = set()
        with open(args.attendees) as fp:
            head = fp.readline()
        if "," in head:
            att = pd.read_csv(args.attendees, dtype=str, keep_default_na=False)
            att.columns = [c.strip().lower() for c in att.columns]
            keys |= {norm_member(v) for v in att.get("member number", pd.Series(dtype=str))} - {""}
            keys |= {v.strip().lower() for v in att.get("email", pd.Series(dtype=str))} - {""}
            keys |= {(a.strip() + " " + b.strip()).lower() for a, b in zip(att.get("first name", []), att.get("last name", []))
                     if a.strip() or b.strip()}
        else:
            with open(args.attendees) as fp:
                keys = {line.strip().lower().lstrip("0") for line in fp if line.strip()}
        summit_names = summit.groupby("person").apply(lambda g: (g["First Name"].iloc[0].strip() + " " + g["Last Name"].iloc[0].strip()).lower())
        present = [p for p in registered if norm_member(p) in keys or people.loc[p, "email"] in keys or summit_names.get(p, "") in keys]
        # walk-ins: on the attendee list with a member number, no vTools registration for the event, but known to us
        walk_ins = [k for k in keys if k.isdigit() and k in people.index and k not in present]
        for k in walk_ins:
            people.loc[k, "ticket"] = "(walk-in, no vTools registration)"
        logging.info("Attendee list: %d of %d registered people matched (%d keys); %d walk-ins added by member number",
                     len(present), len(registered), len(keys), len(walk_ins))
        registered = present + walk_ins
    no_show = len(set(summit["person"])) - len(registered)
    cohort = [p for p in registered if people.loc[p, "member_record"] in ("active member record", "member at the Summit, since lapsed")]
    logging.info("Registered %d people; analysed cohort %d (R1/R2 members at the Summit, %d since lapsed)", len(registered), len(cohort),
                 sum(1 for p in cohort if people.loc[p, "member_record"] != "active member record"))

    event_title = args.title or cohort_rows["Event Title"].iloc[0]
    report = build_report(args, people, registered, dropped_payment, cohort, reference, window, event_title, yp_sections,
                          before_start, reg, data_start, data_end, no_show)
    with open(os.path.join(args.out_dir, "report.html"), "w") as fp:
        fp.write(report)
    people.loc[registered].to_csv(os.path.join(args.out_dir, "participants.csv"))
    comp = people[(people["yp_status"] == "YP member") & ~people.index.isin(registered)]
    comp.drop(columns=["email", "name"]).to_csv(os.path.join(args.out_dir, "comparison.csv"))
    for name in ("participants.csv", "comparison.csv", "report.html"):
        os.chmod(os.path.join(args.out_dir, name), 0o600)
    print(f"Wrote report and CSVs to {args.out_dir}")


if __name__ == "__main__":
    main()
