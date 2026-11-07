"""Section-level before/after around one event: did the sections that had
someone at the event change more than the sections that did not?

For every Region 1/2 section, in the same windows before and after the
reference date, counts from local files only:

  * events hosted by the section or any of its chapters / affinity groups
    (vTools inventory, cancelled events excluded), and among them YP AG
    events;
  * registrations and distinct registrants for those events (registrations
    file) and reported attendance where an event report was filed
    (inventory: member + non-member attendee counts);
  * new officer positions started in the section's OUs (OU Analytics
    volunteer history), and whether a YP affinity group had anyone in a
    position at the start and at the end of the after window.

Sections are tagged with how many analysed participants (from
summit_engagement.py's participants.csv) call them home.

Usage:

    python section_impact.py --data-dir /data/ieee/oua --inventory /data/ieee/vtools/events_inventory.csv \\
        --registrations /data/ieee/vtools/registrations.csv --participants /data/ieee/oua/summit2025/participants.csv \\
        --event 487732 --reference-date 2025-08-22 --window-days 347 --out-dir /data/ieee/oua/summit2025

Outputs: sections.csv (every section, every metric), section_events.csv and
section_positions.csv (the rows behind the numbers for sections with a
participant), section_report.html.
"""

import argparse
import datetime
import html
import logging
import os
import re
from collections import defaultdict

import pandas as pd

import svgcharts as sc
from engagement_report import load_registrations, link_emails_to_members, assign_person, parse_date
from summit_engagement import load_history, load_members, load_yp, style, ignored_positions

yp_ag = re.compile(r"(Affinity Group|Affinity Grp|Affn|Aff Grp)\s*,\s*(WIE/)?YP\b")


def section_of_ou(ou_name, section_names):
    """Longest section name that prefixes the OU name ('Boston Section
    Chapter,AES10' -> 'Boston Section')."""
    name = ou_name.strip()
    best = ""
    for section in section_names:
        if name == section or name.startswith(section + " ") or name.startswith(section + ","):
            if len(section) > len(best):
                best = section
    return best


def in_window(dates, start, end):
    return (dates >= start) & (dates <= end)


def main():
    parser = argparse.ArgumentParser(description="Section-level before/after around an event")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--registrations", required=True)
    parser.add_argument("--participants", required=True, help="participants.csv from summit_engagement.py")
    parser.add_argument("--event", required=True)
    parser.add_argument("--reference-date", required=True)
    parser.add_argument("--window-days", type=int, required=True)
    parser.add_argument("--yp-overrides", help="CSV with a 'member' column of confirmed YPs lacking the product")
    parser.add_argument("--yp-exclusions", help="CSV with a 'member' column of product holders confirmed not to be YPs")
    parser.add_argument("--title", default="Section-level activity before and after the Summit")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    logging.basicConfig(format="%(levelname)s %(message)s", level=logging.INFO)
    os.makedirs(args.out_dir, exist_ok=True)

    reference = datetime.date.fromisoformat(args.reference_date)
    window = datetime.timedelta(days=args.window_days)
    b0, b1, a0, a1 = reference - window, reference - datetime.timedelta(days=1), reference + datetime.timedelta(days=1), reference + window

    members = load_members(os.path.join(args.data_dir, "members.csv"))
    sections = (members[members["region"].isin(["R1", "R2"])].groupby(["section", "section geo spo id"]).size()
                .reset_index(name="members"))
    sections = sections[(sections["section"] != "") & (sections["section geo spo id"] != "")]
    geo_of = dict(zip(sections["section"], sections["section geo spo id"]))
    section_names = list(geo_of)
    logging.info("%d R1/R2 sections", len(sections))

    # ---- events (inventory)
    inv = pd.read_csv(args.inventory, dtype=str, keep_default_na=False)
    inv = inv[(inv["Cancelled"] != "1") & (inv["Id"] != args.event)].copy()
    inv["date"] = pd.to_datetime(inv["Event Date"].str.replace(" UTC", "", regex=False), errors="coerce").dt.date
    inv = inv[inv["date"].notna()]
    inv["reported"] = pd.to_numeric(inv["Number of IEEE Member Attendees"], errors="coerce").fillna(0) + \
        pd.to_numeric(inv["Number of Non-Member Attendees"], errors="coerce").fillna(0)
    inv["has_report"] = (inv["Number of IEEE Member Attendees"].str.strip() != "") | (inv["Number of Non-Member Attendees"].str.strip() != "")
    hosts_path = os.path.join(os.path.dirname(os.path.abspath(args.inventory)), "events_hosts.csv")
    if not os.path.exists(hosts_path):
        raise SystemExit(f"{hosts_path} not found; run vtools_registrations.py enrich first")
    hosts = pd.read_csv(hosts_path, dtype=str, keep_default_na=False).drop_duplicates("Id").set_index("Id")
    inv = inv[inv["Id"].isin(hosts.index)].copy()
    inv["primary_geo"] = inv["Id"].map(hosts["primary_host_section"]).fillna("")
    inv["primary_name"] = inv["Id"].map(hosts["primary_host_name"]).fillna("")
    inv["primary_type"] = inv["Id"].map(hosts["primary_host_type"]).fillna("")
    inv["cohost_names"] = inv["Id"].map(hosts["cohost_names"]).fillna("")
    inv["cohost_sections"] = inv["Id"].map(hosts["cohost_sections"]).fillna("")
    inv["n_cohosts"] = inv["Id"].map(hosts["n_cohosts"]).fillna("0").astype(int)
    inv["reported"] = pd.to_numeric(inv["Id"].map(hosts["ieee_attending"]), errors="coerce").fillna(0) + \
        pd.to_numeric(inv["Id"].map(hosts["guests_attending"]), errors="coerce").fillna(0)
    inv["has_report"] = inv["Id"].map(hosts["ieee_attending"]).fillna("").str.strip() != ""
    inv["primary_is_yp_ag"] = inv["primary_name"].map(lambda n: bool(yp_ag.search(n)))
    # sections whose own YP AG is the primary host / a co-host (from the JSON host structure)
    inv["yp_ag_primary_section"] = [section_of_ou(n, section_names) if yp else "" for n, yp in zip(inv["primary_name"], inv["primary_is_yp_ag"])]
    inv["yp_ag_cohost_sections"] = inv["cohost_names"].map(
        lambda s: {section_of_ou(ou.strip(), section_names) for ou in s.split(";") if yp_ag.search(ou)} - {""})
    inv["n_hosts"] = inv["n_cohosts"] + 1
    inv["ag_only"] = inv["primary_is_yp_ag"] & (inv["n_cohosts"] == 0)
    inv["window"] = ""
    inv.loc[in_window(inv["date"], b0, b1), "window"] = "before"
    inv.loc[in_window(inv["date"], a0, a1), "window"] = "after"
    inv = inv[inv["window"] != ""]
    section_of_geo = {g: n for n, g in geo_of.items()}
    event_sections = []
    for row in inv.itertuples():
        geos = {g.strip() for g in (row.primary_geo + ";" + row.cohost_sections).replace(",", ";").split(";") if g.strip()}
        for geo in geos:
            name = section_of_geo.get(geo, "")
            event_sections.append((row.Id, geo, geo == row.primary_geo, name != "" and name == row.yp_ag_primary_section,
                                   name != "" and name in row.yp_ag_cohost_sections))
    es = pd.DataFrame(event_sections, columns=["Id", "geo", "primary_host", "own_yp_ag_primary", "own_yp_ag_cohost"])
    es["own_yp_ag_hosts"] = es["own_yp_ag_primary"] | es["own_yp_ag_cohost"]
    es = es.merge(inv[["Id", "date", "window", "reported", "has_report", "n_hosts", "ag_only", "primary_name", "primary_type", "cohost_names",
                       "Event Title", "Organizational Unit", "Event Category"]], on="Id")
    from summit_engagement import load_overrides
    ypd = load_yp(os.path.join(args.data_dir, "yp_products.csv"))
    yp = (set(ypd) | load_overrides(args.yp_overrides)) - load_overrides(args.yp_exclusions)

    # ---- registrations
    reg = load_registrations(args.registrations)
    reg = assign_person(reg, link_emails_to_members(reg))
    reg = reg[reg["Event Id"] != args.event]
    reg["window"] = ""
    reg.loc[in_window(reg["event_date"], b0, b1), "window"] = "before"
    reg.loc[in_window(reg["event_date"], a0, a1), "window"] = "after"
    reg = reg[reg["window"] != ""]
    geos_of_event = es.groupby("Id")["geo"].agg(set)
    primary_of_event = dict(zip(inv["Id"], inv["primary_geo"]))
    rs = [(person, event, w, geo, geo == primary_of_event.get(event, ""))
          for person, event, w in zip(reg["person"], reg["Event Id"], reg["window"])
          for geo in geos_of_event.get(event, set())]
    rs = pd.DataFrame(rs, columns=["person", "event", "window", "geo", "primary_host"])
    reg_counts = rs[rs["primary_host"]].groupby(["geo", "window"]).agg(registrations=("person", "size"), registrants=("person", "nunique"))
    reg_counts_any = rs.groupby(["geo", "window"]).agg(registrants=("person", "nunique"))
    reg_by_event = reg.groupby("Event Id")["person"].agg(lambda s: set(s))

    # ---- positions
    positions = load_history(os.path.join(args.data_dir, "volunteer_history.csv"))
    hist = pd.read_csv(os.path.join(args.data_dir, "volunteer_history.csv"), dtype=str, keep_default_na=False)
    hist = hist[~hist["ou position"].isin(ignored_positions)]
    names_of = dict(zip(hist["member/customer number"].str.lstrip("0"), (hist["first name"] + " " + hist["last name"]).str.strip()))
    prow = []
    for member, seats in positions.items():
        for seat in seats:
            section = section_of_ou(seat["ou"], section_names)
            if not section:
                continue
            prow.append({"member": member, "name": names_of.get(member, ""), "section": section, "geo": geo_of[section],
                         "ou": seat["ou"], "title": seat["title"], "tier": seat["tier"], "start": seat["start"], "end": seat["end"],
                         "yp_ag": bool(yp_ag.search(seat["ou"]))})
    pos = pd.DataFrame(prow)
    pos["window"] = ""
    pos.loc[in_window(pos["start"], b0, b1), "window"] = "before"
    pos.loc[in_window(pos["start"], a0, a1), "window"] = "after"

    def active_on(day, mask=None):
        m = (pos["start"] <= day) & (pos["end"].isna() | (pos["end"] >= day))
        return pos[m & mask] if mask is not None else pos[m]

    # ---- participants
    part = pd.read_csv(args.participants, dtype=str, keep_default_na=False)
    part = part[part["member_record"].isin(["active member record", "member at the Summit, since lapsed"])]
    part["tier_at_ref"] = part["tier_at_ref"].astype(int)
    sent = part.groupby("section_spo").agg(participants=("person", "size"), newcomers=("tier_at_ref", lambda s: int((s == 0).sum())),
                                            names=("name", lambda s: "; ".join(sorted(s))))

    rows = []
    for _, sec in sections.iterrows():
        geo, name = sec["section geo spo id"], sec["section"]
        e = es[es["geo"] == geo]
        r = {"section": name, "geo": geo, "members": int(sec["members"]),
             "participants": int(sent["participants"].get(geo, 0)), "newcomers": int(sent["newcomers"].get(geo, 0)),
             "participant_names": sent["names"].get(geo, "")}
        for w in ("before", "after"):
            ew = e[e["window"] == w]
            primary = ew[ew["primary_host"]]
            r[f"events_{w}"] = primary["Id"].nunique()
            r[f"events_any_host_{w}"] = ew["Id"].nunique()
            ag = ew[ew["own_yp_ag_hosts"]].drop_duplicates("Id")
            r[f"yp_ag_events_{w}"] = len(ag)
            r[f"yp_ag_events_nonadmin_{w}"] = int((ag["Event Category"] != "Administrative").sum())
            people_sets = [reg_by_event.get(i, set()) for i in ag["Id"]]
            r[f"yp_ag_registrations_{w}"] = sum(len(x) for x in people_sets)
            everyone = set().union(*people_sets) if people_sets else set()
            r[f"yp_ag_registrants_{w}"] = len(everyone)
            r[f"yp_ag_yp_registrants_{w}"] = len({x for x in everyone if x in yp})
            r[f"yp_ag_reported_attendance_{w}"] = int(ag["reported"].sum())
            r[f"yp_ag_events_with_report_{w}"] = int(ag["has_report"].sum())
            for kind, mask in (("run", ag["own_yp_ag_primary"]), ("cohost", ~ag["own_yp_ag_primary"]), ("only", ag["ag_only"])):
                sub = ag[mask]
                sets = [reg_by_event.get(i, set()) for i in sub["Id"]]
                people_ = set().union(*sets) if sets else set()
                r[f"yp_ag_{kind}_events_{w}"] = len(sub)
                r[f"yp_ag_{kind}_registrants_{w}"] = len(people_)
                r[f"yp_ag_{kind}_yp_registrants_{w}"] = len({x for x in people_ if x in yp})
                r[f"yp_ag_{kind}_reported_attendance_{w}"] = int(sub["reported"].sum())
                r[f"yp_ag_{kind}_events_with_report_{w}"] = int(sub["has_report"].sum())
            r[f"events_with_report_{w}"] = int(primary["has_report"].sum())
            r[f"reported_attendance_{w}"] = int(primary["reported"].sum())
            r[f"registrations_{w}"] = int(reg_counts["registrations"].get((geo, w), 0))
            r[f"registrants_{w}"] = int(reg_counts["registrants"].get((geo, w), 0))
            r[f"registrants_any_host_{w}"] = int(reg_counts_any["registrants"].get((geo, w), 0))
            pw = pos[(pos["geo"] == geo) & (pos["window"] == w)]
            r[f"new_positions_{w}"] = len(pw)
            r[f"new_yp_ag_positions_{w}"] = int(pw["yp_ag"].sum())
        r["yp_ag_officers_at_ref"] = len(active_on(reference, (pos["geo"] == geo) & pos["yp_ag"]))
        r["yp_ag_officers_at_end"] = len(active_on(a1, (pos["geo"] == geo) & pos["yp_ag"]))
        r["officers_at_ref"] = len(active_on(reference, pos["geo"] == geo))
        r["officers_at_end"] = len(active_on(a1, pos["geo"] == geo))
        rows.append(r)
    df = pd.DataFrame(rows).sort_values(["participants", "section"], ascending=[False, True])
    df.to_csv(os.path.join(args.out_dir, "sections.csv"), index=False)
    sent_geos = set(df[df["participants"] > 0]["geo"])
    es["registrants"] = es["Id"].map(lambda i: len(reg_by_event.get(i, set())))
    es["yp_registrants"] = es["Id"].map(lambda i: len({x for x in reg_by_event.get(i, set()) if x in yp}))
    es.merge(df[["geo", "section"]], on="geo").sort_values(["section", "date"]) \
        .to_csv(os.path.join(args.out_dir, "section_events.csv"), index=False)
    pos[pos["geo"].isin(sent_geos) & (pos["window"] != "")].sort_values(["section", "start"]) \
        .to_csv(os.path.join(args.out_dir, "section_positions.csv"), index=False)

    # ---- report
    def delta(a, b):
        return f"{b - a:+d}"
    out = [f"<style>{style}</style>", f"<h1>{html.escape(args.title)}</h1>",
           f"<p class='subtitle'>Before {b0} to {b1} · after {a0} to {a1} · {len(df)} Region 1/2 sections · generated {datetime.date.today()}</p>"]
    cols = ["Section", "Participants (newcomers)", "Events before → after", "YP AG events", "Registrants", "Reported attendance (events with reports)",
            "New positions", "New YP AG positions", "YP AG officers at Summit → end", "Officers at Summit → end"]
    trows = []
    for _, r in df[df["participants"] > 0].iterrows():
        trows.append([r["section"], f"{r['participants']} ({r['newcomers']})",
                      f"{r['events_before']} → {r['events_after']} ({delta(r['events_before'], r['events_after'])})",
                      f"{r['yp_ag_events_before']} → {r['yp_ag_events_after']}",
                      f"{r['registrants_before']} → {r['registrants_after']} ({delta(r['registrants_before'], r['registrants_after'])})",
                      f"{r['reported_attendance_before']} ({r['events_with_report_before']}) → {r['reported_attendance_after']} ({r['events_with_report_after']})",
                      f"{r['new_positions_before']} → {r['new_positions_after']}", f"{r['new_yp_ag_positions_before']} → {r['new_yp_ag_positions_after']}",
                      f"{r['yp_ag_officers_at_ref']} → {r['yp_ag_officers_at_end']}", f"{r['officers_at_ref']} → {r['officers_at_end']}"])
    out.append("<h2>1. Sections with someone at the Summit</h2><div class='wrap'>" + sc.table(cols, trows, "Per section, before → after") + "</div>")

    ycols = ["Section", "Participants", "YP AG events before → after (of which committee meetings)", "AG-run (AG is primary host)", "Registrations", "Distinct registrants", "of whom YP members",
             "Reported attendance (events with reports)", "YP AG officers at Summit → end"]
    yrows = []
    for _, r in df[(df["yp_ag_events_before"] + df["yp_ag_events_after"]) > 0].sort_values(["participants", "yp_ag_events_after"], ascending=False).iterrows():
        yrows.append([r["section"], r["participants"],
                      f"{r['yp_ag_events_before']} ({r['yp_ag_events_before'] - r['yp_ag_events_nonadmin_before']}) → {r['yp_ag_events_after']} ({r['yp_ag_events_after'] - r['yp_ag_events_nonadmin_after']})",
                      f"{r['yp_ag_run_events_before']} → {r['yp_ag_run_events_after']}",
                      f"{r['yp_ag_registrations_before']} → {r['yp_ag_registrations_after']}",
                      f"{r['yp_ag_registrants_before']} → {r['yp_ag_registrants_after']}",
                      f"{r['yp_ag_yp_registrants_before']} → {r['yp_ag_yp_registrants_after']}",
                      f"{r['yp_ag_reported_attendance_before']} ({r['yp_ag_events_with_report_before']}) → {r['yp_ag_reported_attendance_after']} ({r['yp_ag_events_with_report_after']})",
                      f"{r['yp_ag_officers_at_ref']} → {r['yp_ag_officers_at_end']}"])
    out.append("<h2>2. YP affinity group events and who registered for them</h2><div class='wrap'>"
               + sc.table(ycols, yrows, "Every section whose own YP AG hosted at least one event in either window") + "</div>")
    out.append("<h2>3. Sections with vs without a participant</h2>")
    grp = df.assign(group=df["participants"].map(lambda n: "with a participant" if n else "without"))
    srows = []
    for g, sub in grp.groupby("group"):
        def med_change(a, b):
            base = sub[a].where(sub[a] > 0)
            pct = 100.0 * (sub[b] - sub[a]) / base
            return f"{pct.median():+.0f}%" if pct.notna().any() else "–"
        srows.append([g, len(sub), f"{sub['events_before'].sum()} → {sub['events_after'].sum()}", med_change("events_before", "events_after"),
                      f"{int((sub['events_after'] > sub['events_before']).sum())} / {int((sub['events_after'] == sub['events_before']).sum())} / {int((sub['events_after'] < sub['events_before']).sum())}",
                      f"{sub['registrants_before'].sum()} → {sub['registrants_after'].sum()}", med_change("registrants_before", "registrants_after"),
                      f"{sub['new_positions_before'].sum()} → {sub['new_positions_after'].sum()}",
                      f"{sub['yp_ag_events_before'].sum()} → {sub['yp_ag_events_after'].sum()}",
                      f"{sub['yp_ag_registrations_before'].sum()} → {sub['yp_ag_registrations_after'].sum()}",
                      f"{sub['yp_ag_yp_registrants_before'].sum()} → {sub['yp_ag_yp_registrants_after'].sum()}",
                      f"{int((sub['yp_ag_officers_at_ref'] > 0).sum())} → {int((sub['yp_ag_officers_at_end'] > 0).sum())}"])
    out.append("<div class='wrap'>" + sc.table(["Sections", "n", "Events (sum)", "Median % change in events", "Sections up / same / down",
                                                 "Registrants (sum)", "Median % change in registrants", "New positions (sum)", "YP AG events (sum)",
                                                 "YP AG registrations (sum)", "YP members registering for YP AG events (sum)", "Sections with a staffed YP AG"], srows, "Totals and medians by group") + "</div>")
    out.append("<p class='note'>Events are vTools events hosted by the section or any of its chapters and affinity groups (co-hosted events count once per "
               "section). Registrants are distinct people with a non-cancelled vTools registration; events with external registration contribute "
               "no registrants. Reported attendance is the member + non-member count from filed event reports, summed over the events that have one. "
               "New positions are seats in the section's OUs that started in the window (term renewals of the same seat excluded). "
               "Every number here is also in sections.csv; the rows behind sections with a participant are in section_events.csv and section_positions.csv.</p>")
    rows_all = [[r["section"], r["participants"], r["events_before"], r["events_after"], r["registrants_before"], r["registrants_after"],
                 r["new_positions_before"], r["new_positions_after"], r["yp_ag_events_before"], r["yp_ag_events_after"],
                 r["yp_ag_officers_at_ref"], r["yp_ag_officers_at_end"]] for _, r in df.iterrows()]
    out.append("<h2>4. Every section</h2><div class='wrap'>" + sc.table(
        ["Section", "Participants", "Events before", "Events after", "Registrants before", "Registrants after", "New positions before",
         "New positions after", "YP AG events before", "YP AG events after", "YP AG officers at Summit", "YP AG officers at end"], rows_all) + "</div>")
    with open(os.path.join(args.out_dir, "section_report.html"), "w") as fp:
        fp.write("\n".join(out))
    print(f"Wrote sections.csv, section_events.csv, section_positions.csv, section_report.html to {args.out_dir}")


if __name__ == "__main__":
    main()
