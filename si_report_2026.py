"""YP Strategic Initiative report, November 2026: method, the 2025 Summit one
year on, the 2026 Summit three months on. Written for Region and MGA readers.

Reads:
  --run-2025   summit_engagement output for event 487732 with --window-days 90 --before year-earlier
  --run-2026   summit_engagement output for event 555473 with --window-days 90 --before year-earlier
               (also attendees_2026_provisional.csv, checkin_unresolved.csv, grants_2026.csv, ag_windows.csv)
  --vtools-dir registrations.csv (for the monthly chart)
  --checkin    the organizers' YPS26_Reg.xlsx (Check-in tab, for roles)

    python si_report_2026.py --run-2025 output/summit2025_90d --run-2026 output/summit2026 \\
        --vtools-dir output/data2026/vtools --checkin ../oua-explorer/YPS26_Reg.xlsx -o output/summit2026/si_report_2026.html --pdf
"""
import argparse
import html
import os

import pandas as pd

import svgcharts as sc
from chapter_roster import html_to_pdf
from summit_report import style

e = html.escape
TRACKED = ["active member record", "member at the Summit, since lapsed"]

# Figures from the finished 2025 engagement report (full-year windows, 347 days each).
Y25 = dict(attended=62, tracked=45, yps=35, lapsed=4, no_position=20, first_position=7, newcomers_registered=15,
           cmp_new_position_pct=6, cmp_registered_pct=40, officers=25, officers_registered=23, officers_new_role=6,
           organized_before=20, organized_after=42, organizers_before=6, organizers_after=13,
           registered_after_yp=32, registered_before_yp=29, new_role_yp=10, renewed=41,
           ag_groups=[("Officer attended the Summit", 11, 8, 23, 53, 279), ("Gained an officer from the attendees", 5, 2, 30, 26, 111),
                      ("Section sent someone else", 2, 1, 1, None, None), ("No connection to the Summit", 4, 3, 2, None, None)])


def load_run(d):
    p = pd.read_csv(os.path.join(d, "participants.csv"), dtype=str, keep_default_na=False)
    c = pd.read_csv(os.path.join(d, "comparison.csv"), dtype=str, keep_default_na=False)
    for df in (p, c):
        for col in ["events_before", "events_after", "new_ou_after", "new_positions_after", "local_after", "local_before"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    tr = p[p["member_record"].isin(TRACKED)].copy()
    return p, tr, c


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


def group_stats(g):
    return dict(n=len(g), reg_before=int((g.events_before > 0).sum()), reg_after=int((g.events_after > 0).sum()),
                ev_before=int(g.events_before.sum()), ev_after=int(g.events_after.sum()),
                new_role=int((g.new_positions_after > 0).sum()), first_role=int((g.new_volunteer_after == "yes").sum()))


def expected_from_comparison(tr_yp, cmp_yp):
    """Stratify by prior volunteering x prior-event bin; expected counts if the
    participants behaved like other YP members in the same stratum."""
    exp_reg = exp_role = 0.0
    matched = 0
    for (vb, eb), g in tr_yp.groupby(["volunteer_before", "events_before_bin"]):
        pool = cmp_yp[(cmp_yp.volunteer_before == vb) & (cmp_yp.events_before_bin == eb)]
        if len(pool) < 10:
            pool = cmp_yp[cmp_yp.volunteer_before == vb]
        if len(pool) == 0:
            continue
        matched += len(g)
        exp_reg += len(g) * (pool.events_after > 0).mean()
        exp_role += len(g) * (pool.new_positions_after > 0).mean()
    return matched, exp_reg, exp_role


def monthly_share(reg, members, months, exclude_event):
    """Share of `members` (member numbers) registering for >=1 event in each (year, month)."""
    r = reg[(reg["Event Id"] != exclude_event) & (reg["Status"] != "Cancelled")] if "Status" in reg else reg[reg["Event Id"] != exclude_event]
    r = r[r["mn"].isin(members)]
    out = []
    for y, m in months:
        sub = r[(r["date"].dt.year == y) & (r["date"].dt.month == m)]
        out.append(100 * sub["mn"].nunique() / max(len(members), 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-2025", required=True)
    ap.add_argument("--run-2026", required=True)
    ap.add_argument("--vtools-dir", required=True)
    ap.add_argument("--checkin", help="YPS26_Reg.xlsx")
    ap.add_argument("--registered-2026", type=int, default=112)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--pdf", action="store_true")
    args = ap.parse_args()

    p25, t25, c25 = load_run(args.run_2025)
    p26, t26, c26 = load_run(args.run_2026)
    a25 = set(pd.read_csv(os.path.join(args.run_2025, "..", "summit2025", "attendees_2025.csv"), dtype=str)["Member Number"].str.lstrip("0"))
    att26 = pd.read_csv(os.path.join(args.run_2026, "attendees_2026_provisional.csv"), dtype=str, keep_default_na=False)
    unresolved = pd.read_csv(os.path.join(args.run_2026, "checkin_unresolved.csv"), dtype=str, keep_default_na=False)
    agw = pd.read_csv(os.path.join(args.run_2026, "ag_windows.csv"))
    t26["returner"] = t26["member"].str.lstrip("0").isin(a25)
    yp25 = t25[t25.yp_status == "YP member"]
    yp26 = t26[t26.yp_status == "YP member"]
    cmp25 = c25[c25.yp_status == "YP member"]
    cmp26 = c26[c26.yp_status == "YP member"]

    roles = {}
    if args.checkin:
        ci = pd.read_excel(args.checkin, sheet_name="Check-in", dtype=str).fillna("")
        for _, r in ci.iterrows():
            roles[str(r.get("Member Number", "")).replace(".0", "").lstrip("0")] = r.get("No Fee?", "")
    t26["role_ticket"] = t26["member"].str.lstrip("0").map(roles).fillna("")
    speakers26 = int(t26["role_ticket"].str.contains("Speaker", case=False).sum())
    organizers26 = int(t26["role_ticket"].str.contains("Organizer|volunteer", case=False).sum())
    grants26 = int((t26["grant"] == "Y").sum()) if "grant" in t26 else 0

    S = {k: group_stats(g) for k, g in {
        "all25": t25, "yp25": yp25, "nopos25": t25[t25.role_at_ref_name == "No position"],
        "all26": t26, "yp26": yp26, "nopos26": t26[t26.role_at_ref_name == "No position"],
        "first26": t26[~t26.returner], "ret26": t26[t26.returner],
        "first_nopos26": t26[(~t26.returner) & (t26.role_at_ref_name == "No position")],
    }.items()}
    m25, er25, eo25 = expected_from_comparison(yp25, cmp25)
    m26, er26, eo26 = expected_from_comparison(yp26[~yp26.returner], cmp26)
    yp_first26 = yp26[~yp26.returner]

    # monthly chart
    reg = pd.read_csv(os.path.join(args.vtools_dir, "registrations.csv"), dtype=str, keep_default_na=False)
    reg["date"] = pd.to_datetime(reg["Event Date"].str[:10], errors="coerce")
    reg["mn"] = reg["Member Number"].str.lstrip("0")
    months25 = [(2025, m) for m in (5, 6, 7, 8, 9, 10)]
    months26 = [(2026, m) for m in (5, 6, 7, 8, 9, 10)]
    labels = ["May", "Jun", "Jul", "Aug (Summit)", "Sep", "Oct"]
    s25 = monthly_share(reg, set(t25.member.str.lstrip("0")), months25, "487732")
    s26f = monthly_share(reg, set(t26[~t26.returner].member.str.lstrip("0")), months26, "555473")
    s26r = monthly_share(reg, set(t26[t26.returner].member.str.lstrip("0")), months26, "555473")
    monthly = sc.lines([("2025 attendees (45)", list(enumerate(s25)), None),
                        ("2026 first-time attendees (%d)" % S["first26"]["n"], list(enumerate(s26f)), None),
                        ("2026 returning attendees (%d)" % S["ret26"]["n"], list(enumerate(s26r)), None)],
                       title="Share of attendees registering for at least one event, by month", ylabel="% of group",
                       x_labels=dict(enumerate(labels)), x_ticks=list(range(6)))

    sections26 = t26["section"].replace("", "unknown").value_counts()
    agw_all = agw[agw.group == "all R1/R2 YP AGs"].set_index("year")
    agw_off = agw[agw.group == "officer at the 2026 Summit"].set_index("year")
    agw_no = agw[agw.group == "no officer at the 2026 Summit"].set_index("year")

    out = [f"<style>{style}</style>",
           "<h1>What a Young Professionals Summit does</h1>",
           "<p class='subtitle'>Measuring engagement after the 2025 and 2026 IEEE Regions 1 and 2 YP Summits · "
           "YP Strategic Initiative report, November 2026 · Sam Stone, Region 1 Young Professionals Coordinator</p>"]

    # ---- Summary
    out.append("<div class='summary'><ul>"
               f"<li><b>We can now measure what a Summit does.</b> Every Region 1 and 2 event in vTools (about 4,800 since 2024) and every volunteer position and membership record in OU Analytics are joined into a before-and-after view of each attendee, set against YP members with similar prior activity who did not attend. No new system was needed; the scripts are documented and reusable for any Region event.</li>"
               f"<li><b>The 2025 Summit, one year on:</b> of the {Y25['no_position']} attendees who held no volunteer position when they came, {Y25['first_position']} took their first IEEE position within the year and {Y25['newcomers_registered']} registered for further events; among comparable YP members who were not there, {Y25['cmp_new_position_pct']}% and {Y25['cmp_registered_pct']}%. Attendees who were already officers carried on as before but organized twice as many events ({Y25['organized_before']} → {Y25['organized_after']}). {Y25['renewed']} of {Y25['tracked']} tracked attendees renewed for 2026.</li>"
               f"<li><b>Affinity groups connected to the 2025 Summit ran more.</b> The 11 YP affinity groups whose officer attended went from 8 to 23 events as primary host; the 5 that gained an officer from among the attendees went from 2 to 30. Groups with no connection ran almost nothing in either year.</li>"
               f"<li><b>The 2026 Summit (Boston, August 7 to 9) drew {args.registered_2026} registrations</b>, {len(att26)} checked in so far, with {S['all26']['n']} Region 1 and 2 members we can follow, {S['ret26']['n']} of whom were also at the 2025 Summit. Three months on, {S['first26']['reg_after']} of {S['first26']['n']} first-time attendees have registered for a further event ({S['first26']['reg_before']} did in the same three months a year earlier); among first-timers with no position, {S['first_nopos26']['reg_before']} → {S['first_nopos26']['reg_after']} of {S['first_nopos26']['n']}. No new positions yet: in 2025 the first ones appeared in September and most affinity-group elections fall in December and January.</li>"
               "<li><b>Retention and recruitment for the 2026 cohort cannot be read yet.</b> Renewal season closes in early 2027; the 2026 figures in this report are activity, not membership. The three-month comparison is also shaped by its window: autumn programs that start in late November fall outside it.</li>"
               "</ul></div>")

    # ---- 1 Method
    out.append("<h2>1. How the measurement works</h2>"
               "<p>The 2026 Strategic Initiative proposed tracking engagement and retention of participants in Region YP activities against a baseline, using vTools and OU Analytics rather than surveys. This is that measurement. It uses only data the Region already has access to.</p>"
               "<h3>Sources</h3><ul>"
               "<li><b>vTools events.</b> The public event export for Regions 1 and 2 (every event since May 2024, about 4,800), the Events API record of each event's primary host and co-hosts, and the registrant list of every event that used vTools registration (about 2,400 events, 75,000 registrations). vTools records who registered; where an event report was filed it records how many attended, but not who.</li>"
               "<li><b>OU Analytics.</b> Every active and past volunteer position in Regions 1 and 2 (34,000 rows), every active member record (46,000) and inactive or arrears record (196,000), and every IEEE Young Professionals product row (11,700).</li></ul>"
               "<h3>Cohort</h3><p>The organizers' attendance sheet, matched to member records by member number. Attendees who are Region 1 or 2 members with a record we can access are the <i>tracked</i> attendees; speakers and guests from other regions, staff and walk-ins without a member number are counted but not followed. A YP is someone holding the IEEE Young Professionals product, corrected by hand where the records are wrong (section 5).</p>"
               "<h3>Windows</h3><p>For each tracked attendee: events registered for, volunteer positions held and started, and renewals, in a window after the Summit and in the same calendar span one year earlier. The 2025 Summit has a full year on each side (347 days); the 2026 Summit has 90 days, August 7 to November 5, compared with August 7 to November 5, 2025. The Summit itself is excluded from every count. A <i>new role</i> is a position in an organizational unit the person had never served in; a new title in a unit already served is not counted.</p>"
               "<h3>Comparison</h3><p>Every Region 1 and 2 YP member who was not at the Summit, split by whether they had volunteered before and how many events they had registered for in the year before. Each group of attendees is compared with non-attendees in the same stratum. This shows a difference between attendees and their peers, not its cause: people who travel to a Summit may already be more inclined to get involved.</p>"
               "<h3>Affinity groups</h3><p>Events where a YP affinity group is itself the primary host (most YP affinity groups appear mainly as co-hosts on other units' events), grouped by the group's connection to the Summit: an officer attended, the group gained an officer from among the attendees, or neither.</p>")

    # ---- 2 2025
    out.append("<h2>2. The 2025 Summit, one year on</h2>"
               f"<p>{Y25['attended']} people attended the 2025 Summit (August 22 to 24). {Y25['tracked']} were Region 1 or 2 members whose activity can be followed, at least {Y25['yps']} of them Young Professionals; {Y25['lapsed']} have since let their membership lapse. The full report, with per-section and per-group detail, is available on request; the headline results are these.</p>")
    out.append(sc.stat_tiles([
        ("Registered for an event in the year after", f"{Y25['registered_after_yp']} of {Y25['yps']}", f"tracked YPs; {Y25['registered_before_yp']} of {Y25['yps']} the year before"),
        ("Took a role in a unit never served before", f"{Y25['new_role_yp']} of {Y25['yps']}", "tracked YPs"),
        ("Organized at least one event", f"{Y25['organizers_after']} of {Y25['yps']}", f"{Y25['organizers_before']} the year before; events organized {Y25['organized_before']} → {Y25['organized_after']}"),
        ("Renewed IEEE membership for 2026", f"{Y25['renewed']} of {Y25['tracked']}", "all tracked attendees"),
    ]))
    out.append("<h3>Newcomers took on roles; officers carried on</h3>"
               f"<p>{Y25['no_position']} of the {Y25['tracked']} tracked attendees held no volunteer position when they came. Within a year, {Y25['first_position']} had taken their first IEEE position, nearly all in their home section (three YP affinity group chairs, a section vice chair, a section treasurer, a newsletter editor and a section YP representative), and {Y25['newcomers_registered']} had registered for further events. Among YP members with similar prior activity who were not at the Summit, {Y25['cmp_new_position_pct']}% took a new position and {Y25['cmp_registered_pct']}% registered for an event in the same year. The {Y25['officers']} attendees who were already officers looked much like their peers on events and roles ({Y25['officers_registered']} registered, {Y25['officers_new_role']} took an additional role); where they did more is organizing.</p>")
    out.append(sc.grouped_bars(["Took a first or new position", "Registered for a later event"],
                               [("Attendees with no position (20)", [35, 75], None), ("Comparable YP members not at the Summit", [Y25['cmp_new_position_pct'], Y25['cmp_registered_pct']], None)],
                               title="Year after the 2025 Summit: attendees who held no position, against comparable YP members", ylabel="% of group", value_format="{:.0f}%", ylim=100))
    out.append("<h3>Affinity groups connected to the Summit ran more</h3>"
               "<p>Counting only events where the YP affinity group is the primary host, the groups divide sharply by their connection to the Summit. The connection runs both ways: active groups send their officers to the Summit, and new groups recruit officers from the room. Groups with no connection mostly exist on paper, serving as co-hosts on other units' events.</p>")
    out.append(sc.table(["Connection to the 2025 Summit", "Groups", "Events as primary host, year before → after", "Distinct registrants, year before → after"],
                        [[g, n, f"{b} → {a}", f"{rb} → {ra}" if rb is not None else "–"] for g, n, b, a, rb, ra in Y25["ag_groups"]],
                        "YP affinity groups in Regions 1 and 2 with events in either year"))

    # ---- 3 2026
    out.append("<h2>3. The 2026 Summit, three months on</h2>"
               f"<p>The 2026 Summit was hosted by the Boston Section YP Affinity Group at Boston University on August 7 to 9. {args.registered_2026} people registered (cancellations and unpaid registrations excluded): 60 paying attendees and about 50 speakers, organizers and sponsor representatives. The check-in sheet shows {len(att26)} present so far; {len(unresolved)} registrations are still being reconciled against the leftover badges, so the final count will be somewhat higher. New this year were a Listening Lab between YP members and Region leadership, a Humanitarian Technologies pitch competition, and a post-event survey still being collected.</p>")
    out.append(sc.stat_tiles([
        ("Tracked attendees", str(S["all26"]["n"]), f"Region 1/2 members on the check-in sheet with a record; {S['yp26']['n']} YPs"),
        ("First-time attendees", str(S["first26"]["n"]), f"{S['ret26']['n']} were also at the 2025 Summit"),
        ("Held no volunteer position", str(S["nopos26"]["n"]), f"{S['first_nopos26']['n']} of them first-timers"),
        ("Travel grants · speakers", f"{grants26} · {speakers26}", "among tracked attendees (organizers: %d)" % organizers26),
    ]))
    out.append(f"<p>Tracked attendees came from {len(sections26)} sections; the largest contingents were "
               + ", ".join(f"{e(s)} ({n})" for s, n in sections26.head(6).items()) + ".</p>")
    out.append("<h3>Activity in the first 90 days, against the 2025 cohort at the same point</h3>"
               "<p>Three months is too short to read positions or renewals, so this table compares like with like: each cohort's first 90 days after its Summit, against the same calendar span one year earlier. Returning attendees are shown separately: their 'year earlier' window contains the 2025 Summit and the activity that followed it, so for them the comparison runs the wrong way and says little.</p>")
    rows = []
    for label, key in [("2025 attendees, all tracked", "all25"), ("2025 attendees with no position", "nopos25"),
                       ("2026 first-time attendees", "first26"), ("2026 first-timers with no position", "first_nopos26"),
                       ("2026 returning attendees (also at the 2025 Summit)", "ret26")]:
        s = S[key]
        rows.append([label, s["n"], f"{s['reg_before']} → {s['reg_after']}", f"{s['ev_before']} → {s['ev_after']}", s["new_role"], s["first_role"]])
    out.append(sc.table(["Group", "People", "Registered for ≥1 event, same 90 days a year earlier → after", "Events registered for, year earlier → after", "New roles within 90 days", "First-ever positions"], rows,
                        "First 90 days after each Summit (2025: Aug 22 – Nov 20; 2026: Aug 7 – Nov 5), the Summit itself excluded"))
    out.append(f"<p>Set against YP members with the same prior volunteering and prior-event history: if the {m25} tracked YPs at the 2025 Summit had behaved like their peers, about {er25:.0f} would have registered for an event in the first 90 days and {eo25:.1f} would have started a new role; the actual figures were {S['yp25']['reg_after']} and {int((yp25.new_positions_after > 0).sum())}. For the {m26} first-time YP attendees of 2026, the peer expectation is {er26:.0f} registering and {eo26:.1f} new roles; the actual figures so far are {int((yp_first26.events_after > 0).sum())} and {int((yp_first26.new_positions_after > 0).sum())}. The event-registration lift is present in both years; the 2025 cohort's early roles (a section treasurer and an affinity group vice chair in September, an affinity group chair in October) have no 2026 counterpart yet.</p>")
    out.append(monthly)
    out.append("<p class='note'>Months are the event's date, not the day of signing up; November is left out because 2026 data runs only to November 6.</p>")
    out.append("<h3>Affinity-group activity, August to early November, year over year</h3>"
               "<p>The same autumn window across three years, counting events where a Region 1 or 2 YP affinity group is the primary host. The window is short and misses programs that start in late November, so this is an early indicator only: New Jersey Coast's group, which ran 25 events in the year after the 2025 Summit, has none inside these dates in any year.</p>")
    out.append(sc.table(["", "Aug 7 – Nov 5, 2024", "Aug 7 – Nov 5, 2025", "Aug 7 – Nov 5, 2026"],
                        [["Events with a YP affinity group as primary host"] + [int(agw_all.loc[y, "events"]) for y in (2024, 2025, 2026)],
                         ["Groups running at least one"] + [int(agw_all.loc[y, "ags_active"]) for y in (2024, 2025, 2026)],
                         ["Distinct registrants"] + [int(agw_all.loc[y, "registrants"]) for y in (2024, 2025, 2026)],
                         ["Attendance reported by organizers"] + [int(agw_all.loc[y, "reported_attendance"]) for y in (2024, 2025, 2026)],
                         ["… of which groups with an officer at the 2026 Summit (%d groups): events" % int(agw_off.loc[2026, "ags_in_group"])] + [int(agw_off.loc[y, "events"]) for y in (2024, 2025, 2026)],
                         ["… their distinct registrants"] + [int(agw_off.loc[y, "registrants"]) for y in (2024, 2025, 2026)],
                         ["… other groups: events"] + [int(agw_no.loc[y, "events"]) for y in (2024, 2025, 2026)]],
                        "Region 1 and 2 YP affinity groups, primary-host events only"))
    out.append("<h3>What cannot be read yet</h3><ul>"
               "<li><b>Members retained.</b> Whether 2026 attendees renew is visible after renewal season closes, in early 2027. For the 2025 cohort the answer is in: 41 of 45.</li>"
               "<li><b>New members.</b> Whether the Summit brought people into IEEE is read the same way, and from the join dates of 2026 registrants who were not members before.</li>"
               "<li><b>New officers.</b> Most affinity-group and section elections take effect in December and January; the 2025 cohort's first-ever positions appeared from September through January.</li></ul>")

    # ---- 4 MGA form
    out.append("<h2>4. For the MGA annual report form</h2>"
               "<p>The form asks for four counts and says to leave blank what is not measured. The Summit's direct figures are small by design; its purpose is what follows in sections and affinity groups, so each count is given as direct and indirect.</p>")
    out.append(sc.table(["Count", "Direct", "Indirect (measured spill-over from the 2025 Summit)"], [
        ["New members", "Not measured yet: 2026 joins and renewals are readable in early 2027", "—"],
        ["Members retained", f"{Y25['renewed']} of {Y25['tracked']} tracked 2025 attendees renewed for 2026 (91%)", "—"],
        ["People engaged", f"{args.registered_2026} registered for the 2026 Summit", "Distinct registrants at events run by Summit-connected YP affinity groups: 79 → 390 in the year after the 2025 Summit"],
        ["Activities held", "1 (the 2026 Summit)", "Summit-connected YP affinity groups ran 53 events as primary host in the year after the 2025 Summit, against 10 the year before"],
    ]))

    # ---- 5 Caveats
    out.append("<h2>5. Caveats and data notes</h2><ul>"
               "<li>vTools records registrations, not attendance; every per-person event figure here is a registration count, and events registered through other tools are not seen. Events an attendee organized are found through the event's contact email and are minimum counts.</li>"
               "<li>Groups are small (5 to 25 people in most cells). Differences between attendees and non-attendees describe the people who came as much as the event.</li>"
               "<li>IEEE's Young Professionals records are unreliable in both directions. The YP product is exported for active members only, so lapsed attendees' status is unknown. The 'YP eligible' flag is blank when the graduation date is missing and wrong when the date on file is a later degree: of 11,700 product holders in Regions 1 and 2, about 1,100 have a graduation date six or more years after their first IEEE grade, and about 1,100 left Student Member grade more than 15 years ago. YP counts in this report are therefore minimums, corrected by hand for the Summit cohorts.</li>"
               f"<li>The 2026 cohort is provisional: {len(unresolved)} registrations await reconciliation with the badge count, and the check-in sheet does not record walk-ins.</li>"
               "<li>Across Regions 1 and 2 as a whole, distinct people registering for any event fell about 14% between the year before and the year after the 2025 Summit; the attendees' increases are against that backdrop.</li></ul>"
               "<p class='note'>Scripts and documentation: github.com/sciencesam/oua-reports (private while under review; to move to the vTools Labs GitLab). Data as of November 7, 2026.</p>")

    with open(args.output, "w") as fp:
        fp.write("<!doctype html><meta charset='utf-8'><title>YP Strategic Initiative report, November 2026</title>" + "".join(out))
    print("Wrote", args.output)
    if args.pdf:
        print("PDF", html_to_pdf(args.output, args.output.replace(".html", ".pdf")))


if __name__ == "__main__":
    main()
