"""Final engagement report for one Summit, written for Region / MGA readers.

Reads from --out-dir: participants.csv, comparison.csv, sections.csv,
section_events.csv, deep/events_organized.csv, deep/yp_ag_officers.csv.
Writes summit_report.html (+ PDF with --pdf).

    python summit_report.py --out-dir output/summit2025 --reference-date 2025-08-22 --window-days 347 \\
        --event-name "2025 YP Summit" --attended 62 --pdf
"""

import argparse
import datetime
import html
import os
import re
from collections import Counter

import numpy as np
import pandas as pd

import svgcharts as sc
from chapter_roster import html_to_pdf
from summit_engagement import tier_names

style = """
  @page { size: letter; margin: 0.6in 0.55in; }
  body { font-family: Helvetica, Arial, sans-serif; color: #0b0b0b; background: #fff; max-width: 60em; margin: 1.5em auto; padding: 0 1.2em; line-height: 1.45; font-size: 10.5pt; }
  h1 { font-size: 20pt; margin: 0 0 2px 0; } h2 { font-size: 14pt; margin: 1.6em 0 0.3em; border-bottom: 1px solid #c3c2b7; padding-bottom: 3px; break-after: avoid; }
  h3 { font-size: 11pt; margin: 1.1em 0 0.2em; break-after: avoid; } h3.chart { margin-bottom: 0; }
  .subtitle { color: #52514e; margin: 0 0 1em 0; }
  .summary { background: #f4f6f8; border-left: 4px solid #2a78d6; padding: 0.7em 1em; margin: 1em 0; } .summary li { margin: 0.35em 0; }
  .tiles { display: flex; flex-wrap: wrap; gap: 10px; margin: 0.8em 0; }
  .tile { background: #fcfcfb; border: 1px solid #e1e0d9; border-radius: 6px; padding: 8px 12px; flex: 1; min-width: 150px; }
  .tile-label { font-size: 8.5pt; color: #52514e; } .tile-value { font-size: 18pt; font-weight: 600; } .tile-delta { font-size: 8.5pt; color: #52514e; }
  table { border-collapse: collapse; font-size: 9pt; margin: 0.4em 0 1em; width: 100%; }
  caption { text-align: left; font-weight: 600; padding: 0.3em 0; font-size: 9.5pt; }
  th { text-align: left; background: #f0efec; padding: 3px 6px; border-bottom: 1px solid #c3c2b7; vertical-align: bottom; }
  td { padding: 2.5px 6px; border-bottom: 1px solid #e1e0d9; vertical-align: top; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  tr { break-inside: avoid; } thead { display: table-header-group; }
  svg { display: block; margin: 0.2em 0 0.6em; max-width: 100%; height: auto; break-inside: avoid; }
  .note { color: #52514e; font-size: 9pt; } .small td, .small th { font-size: 8pt; padding: 2px 4px; }
  .pagebreak { break-before: page; } sup { font-size: 7pt; }
"""

# Neutral confirmations by the Region 1 YP Chair (from the working notes; edited for a reader).
CHAIR_NOTES = {
    "Alexis Gorgacz": "Confirmed YP.",
    "Kalyani Palash Matey": "Confirmed YP.",
    "Kartik Srinivasan": "Confirmed YP.",
    "Abdulsalam Almadani": "Confirmed YP.",
    "Andrew Lowery": "Not a YP: the 2014 date on file is a later PhD; the first degree was well over 15 years ago.",
    "Michael Wright": "Confirmed YP; first degree 2019.",
    "Toussaint Lohomboli": "Confirmed YP.",
    "Brian Tamieus": "Confirmed YP.",
    "Hemangi Patel": "Confirmed YP.",
    "Qingwan Cheng": "Confirmed YP.",
    "Phaneendra Mane": "Not a YP.",
}
GRADE_ABBR = {"Graduate Student Member": "GSM", "Senior Member": "SM", "Member": "M", "Life Senior": "LS", "Life Member": "LM", "Student Member": "StM", "Fellow": "F", "Life Fellow": "LF", "Associate Member": "AM"}


def e(v):
    return html.escape(str(v))


def table(columns, rows, caption="", cls="", widths=None):
    out = [f"<table class='{cls}'" + (" style='table-layout:fixed'" if widths else "") + ">"]
    if widths:
        out.append("<colgroup>" + "".join(f"<col style='width:{w}%'>" for w in widths) + "</colgroup>")
    if caption:
        out.append(f"<caption>{e(caption)}</caption>")
    out.append("<thead><tr>" + "".join(f"<th>{e(c)}</th>" for c in columns) + "</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def chart(title, svg, note=""):
    return f"<h3 class='chart'>{e(title)}</h3>" + svg + (f"<p class='note'>{note}</p>" if note else "")


def of(k, n):
    return f"{k} of {n}"


def ev_range(v):
    return 0 if v == 0 else 1 if v <= 3 else 2 if v <= 9 else 3


def comparable(pool, r, min_size=10):
    """YP members matching the attendee on prior volunteering, graduate-student
    status and prior-event range; if fewer than min_size match, the
    graduate-student criterion is dropped."""
    base = pool[(pool["vol_before"] == r["vol_before"]) & (pool["ev_range"] == ev_range(r["events_before"]))]
    full = base[base["gsm"] == r["gsm"]]
    return full if len(full) >= min_size else base


def matched_rate(group, pool, outcome, min_pool=1):
    vals = [comparable(pool, r)[outcome].mean() for _, r in group.iterrows() if len(comparable(pool, r)) >= min_pool]
    return float(np.mean(vals)) if vals else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True); ap.add_argument("--reference-date", required=True); ap.add_argument("--window-days", type=int, required=True)
    ap.add_argument("--event-name", default="2025 YP Summit"); ap.add_argument("--title", default="IEEE Young Professionals Summit — engagement report")
    ap.add_argument("--attended", type=int, default=62); ap.add_argument("--registered", type=int, default=72)
    ap.add_argument("--next-summit", default="The 2026 YP Summit, run by Boston's YP AG with 113 registrations, falls three days after the window and is not in these figures.")
    ap.add_argument("--backdrop", default="Across Regions 1 and 2 as a whole, distinct people registering for any event fell from about 17,300 in the first year to 14,900 in the second (−14%). Among YP product holders who were already IEEE members on the Summit date, the count went from 776 to 746 (−4%); counting all current holders it appears to rise 30%, but that is people who joined IEEE after the Summit and could only appear in the second year.")
    ap.add_argument("--grad-mismatch", default="for 1,090 of the 11,031 product holders in Regions 1 and 2 who hold a grade above Student Member, the date on file is six or more years after their first IEEE grade entry, so it is a later degree, not the first")
    ap.add_argument("--pdf", action="store_true")
    args = ap.parse_args()
    S = args.event_name
    ref = datetime.date.fromisoformat(args.reference_date); W = args.window_days
    b0, b1, a1 = ref - datetime.timedelta(days=W), ref - datetime.timedelta(days=1), ref + datetime.timedelta(days=W)
    before_txt, after_txt = f"{b0:%b %Y} to {b1:%b %Y}", f"{ref:%b %Y} to {a1:%b %Y}"

    # ---------------------------------------------------------------- data
    p = pd.read_csv(os.path.join(args.out_dir, "participants.csv"), dtype=str, keep_default_na=False)
    c = pd.read_csv(os.path.join(args.out_dir, "comparison.csv"), dtype=str, keep_default_na=False)
    for df in (p, c):
        for col in ["events_before", "events_after", "local_before", "local_after", "new_positions_after", "new_ou_after", "tier_at_ref", "tier_after"]:
            df[col] = df[col].astype(int)
        df["first_event_after"] = pd.to_datetime(df["first_event_after"].replace("", None)).dt.date
        df["any_after"] = (df["events_after"] > 0).astype(int); df["new_ou"] = (df["new_ou_after"] > 0).astype(int)
        df["first_vol"] = (df["new_volunteer_after"] == "yes").astype(int)
        df["gsm"] = df["grade"].str.contains("Graduate Student").astype(int); df["vol_before"] = (df["volunteer_before"] == "yes").astype(int)
        df["ev_range"] = df["events_before"].map(ev_range)
        df["last_event_before"] = pd.to_datetime(df["last_event_before"].replace("", None)).dt.date if "last_event_before" in df.columns else None
    for col in ("grant", "speaker", "yp_override", "role_at_ref_name"):
        if col not in p.columns: p[col] = p["tier_at_ref_name"] if col == "role_at_ref_name" else ""
    tracked = ["active member record", "member at the Summit, since lapsed"]
    coh = p[p["member_record"].isin(tracked)].copy()
    not_an = p[~p["member_record"].isin(tracked)]
    lapsed = coh[coh["member_now"] == "no"] if "member_now" in coh.columns else coh.iloc[0:0]
    org_path = os.path.join(args.out_dir, "deep", "events_organized.csv")
    org = pd.read_csv(org_path, dtype=str, keep_default_na=False).set_index("name") if os.path.exists(org_path) else None
    coh["organized_before"] = coh["name"].map(lambda n: int(org["organized before"].get(n, 0)) if org is not None else 0)
    coh["organized_after"] = coh["name"].map(lambda n: int(org["organized after"].get(n, 0)) if org is not None else 0)
    cy = coh[coh["yp_status"] == "YP member"]
    c = c[(c["member_record"] == "active member record") & (c["yp_status"] == "YP member")].copy()
    if "joined_ieee" in c.columns:
        joined = pd.to_datetime(c["joined_ieee"].replace("", None), errors="coerce").dt.date
        c = c[joined.notna() & (joined <= ref)].copy()          # people who were already IEEE members on the Summit date
    newcomers = coh[coh["tier_at_ref"] == 0]; officers = coh[coh["tier_at_ref"] > 0]
    yp_new = cy[cy["tier_at_ref"] == 0]; yp_off = cy[cy["tier_at_ref"] > 0]
    N, NY = len(coh), len(cy)
    role = "role_at_ref_name"
    s = pd.read_csv(os.path.join(args.out_dir, "sections.csv"))
    ev = pd.read_csv(os.path.join(args.out_dir, "section_events.csv"), dtype=str, keep_default_na=False)
    for col in ("registrants", "yp_registrants", "n_hosts"): ev[col] = ev[col].astype(int)
    base = ev[(ev["primary_host"] == "True") & (ev["own_yp_ag_hosts"] != "True")].groupby("section").agg(r=("registrants", "sum"), y=("yp_registrants", "sum"))
    base["share"] = base.y / base.r.replace(0, 1)
    ag = ev[ev["own_yp_ag_hosts"] == "True"].drop_duplicates(["Id", "section"]).copy()
    ag["run"] = ag["own_yp_ag_primary"] == "True"
    ag["yp_title"] = ag["Event Title"].str.contains(r"young professional|\bYP\b", case=False, regex=True)
    ag["share"] = ag["yp_registrants"] / ag["registrants"].replace(0, 1); ag["base"] = ag["section"].map(base["share"]).fillna(0)
    def kind(r):
        if r.run: return "run"
        if r.yp_title or (r.registrants >= 5 and r.share >= max(0.25, 2 * r.base)): return "yp_cohost"
        if r.registrants < 5: return "unknown"
        return "paper"
    ag["kind"] = ag.apply(kind, axis=1)
    has_ag = s[(s.yp_ag_officers_at_ref > 0) | (s.yp_ag_officers_at_end > 0) | (s.yp_ag_events_before + s.yp_ag_events_after > 0)].copy()
    ago_path = os.path.join(args.out_dir, "deep", "yp_ag_officers.csv")
    ago = pd.read_csv(ago_path) if os.path.exists(ago_path) else None
    def sec_of_ag(name): return re.split(r"\s+(Jt\.?\s+)?(Affinity|Affn|Aff)", name)[0].strip()
    officer_att = {sec_of_ag(x) for x in ago[ago.attended].ag} if ago is not None else set()
    gained = set()
    for d in coh["new_ou_detail"]:
        for item in d.split(";"):
            m = re.match(r"\s*(.+?) @ (.+?) \(", item)
            if m and re.search(r"(Affinity Group|Affinity Grp|Affn|Aff Grp)\s*,\s*(WIE/)?YP\b", m.group(2)): gained.add(sec_of_ag(m.group(2)))
    G_OFF, G_GAIN, G_SEC, G_NONE = "where an officer attended the Summit", "that gained an officer from among the attendees", "whose section had another attendee (not an AG officer)", "with no connection to the Summit"
    sections_with_attendee = set(coh["section"])
    has_ag["ag_group"] = [G_OFF if x in officer_att else G_GAIN if x in gained else G_SEC if x in sections_with_attendee else G_NONE for x in has_ag.section]
    GROUPS = (G_OFF, G_GAIN, G_SEC, G_NONE)
    agg = {}
    for g in GROUPS:
        sub = has_ag[has_ag.ag_group == g]
        agg[g] = dict(n=len(sub), names=", ".join(x.replace(" Section", "") for x in sub.sort_values("yp_ag_run_events_after", ascending=False).section),
                      run_b=int(sub.yp_ag_run_events_before.sum()), run_a=int(sub.yp_ag_run_events_after.sum()), reg_b=int(sub.yp_ag_run_registrants_before.sum()), reg_a=int(sub.yp_ag_run_registrants_after.sum()),
                      yp_b=int(sub.yp_ag_run_yp_registrants_before.sum()), yp_a=int(sub.yp_ag_run_yp_registrants_after.sum()), rep_b=int(sub.yp_ag_run_reported_attendance_before.sum()), rep_a=int(sub.yp_ag_run_reported_attendance_after.sum()),
                      up=int((sub.yp_ag_run_events_after > sub.yp_ag_run_events_before).sum()), down=int((sub.yp_ag_run_events_after < sub.yp_ag_run_events_before).sum()), sub=sub)
    mr = {(g, o): matched_rate(grp, c, o) for g, grp in (("new", yp_new), ("off", yp_off)) for o in ("any_after", "new_ou", "first_vol")}
    min_pool = min(len(comparable(c, r)) for _, r in cy.iterrows())
    # newcomers' first roles, one per person (the latest-starting seat)
    first_roles = Counter()
    for d in newcomers[newcomers["first_vol"] == 1]["new_ou_detail"]:
        seats = []
        for item in d.split(";"):
            m = re.match(r"\s*(.+?) @ (.+?) \((\d{4}-\d{2}-\d{2})\)", item.strip())
            if m: seats.append(m.groups())
        if not seats: continue
        t, ou, _ = max(seats, key=lambda x: x[2])          # the latest seat, e.g. Vice Chair then Chair -> Chair
        if re.search(r"(Affinity Group|Affinity Grp|Affn|Aff Grp)\s*,\s*(WIE/)?YP\b", ou): first_roles["YP affinity group " + t.replace("Affinity Group ", "").lower()] += 1
        elif ou.endswith("Section"): first_roles["section " + t.lower()] += 1
        else: first_roles[t.lower() + " at " + ou] += 1
    role_summary = ", ".join(f"{n} {t}s" if n > 1 else ("an " if t[0] in "aeiou" else "a ") + t for t, n in first_roles.most_common()).replace(" yp ", " YP ")
    grant = coh[coh["grant"] == "Y"]; nogrant = coh[coh["grant"] != "Y"]

    out = [f"<style>{style}</style>", f"<h1>{e(args.title)}</h1>",
           f"<p class='subtitle'>What the people who attended the {S} on {ref:%-d %B %Y} did in IEEE during the year before ({before_txt}) and the year after ({after_txt}), and what happened in the Young Professionals affinity groups connected to them. "
           f"Sources: vTools event registrations and hosts, OU Analytics volunteer history and membership. Generated {datetime.date.today():%-d %B %Y}.</p>"]

    # ---------------------------------------------------------------- summary
    out.append("<div class='summary'><b>Summary</b><ul>"
               f"<li><b>{args.attended} people attended the {S}.</b> {N} were Region 1 or 2 members whose IEEE activity can be followed ({len(lapsed)} have since let their membership lapse); at least {NY} of those are Young Professionals (IEEE's YP records are incomplete, section 6). "
               "The rest were speakers and guests from other regions, IEEE staff, or people we could not match to a record.</li>"
               f"<li><b>Newcomers took on roles.</b> {len(newcomers)} of the {N} held no volunteer position when they came. Within a year, {int(newcomers['first_vol'].sum())} had taken their first IEEE position, nearly all in their home section ({role_summary}), "
               f"and {int(newcomers['any_after'].sum())} registered for further events. For comparison, of YP members with similar prior activity who were not at the Summit, {100 * mr[('new', 'new_ou')]:.0f}% took a new position and {100 * mr[('new', 'any_after')]:.0f}% registered for an event in the same year.</li>"
               f"<li><b>Attendees who were already officers carried on much as before.</b> {len(officers)} came as chapter, affinity group, section or region officers; {int((officers['events_after'] > 0).sum())} of them registered for events afterwards and {int((officers['new_ou_after'] > 0).sum())} took an additional role. "
               + (f"YP affinity group officers who did not attend stayed in their seats at a similar rate ({int(ago[~ago.attended].still_ag_officer_at_end.sum())} of {int((~ago.attended).sum())}, against {int(ago[ago.attended].still_ag_officer_at_end.sum())} of {int(ago.attended.sum())} who attended). " if ago is not None else "")
               + f"Where attendees did do more is organizing: the events they organized themselves went from {int(coh['organized_before'].sum())} in the year before to {int(coh['organized_after'].sum())} in the year after.<sup>1</sup></li>"
               f"<li><b>Affinity groups connected to the Summit ran far more events; the others ran almost nothing.</b> Counting only events where the YP affinity group is the primary host: the {agg[G_OFF]['n']} AGs whose officer attended ran {agg[G_OFF]['run_b']} events in the year before and {agg[G_OFF]['run_a']} in the year after "
               f"({agg[G_OFF]['reg_b']} then {agg[G_OFF]['reg_a']} distinct registrants; {agg[G_OFF]['rep_b']} then {agg[G_OFF]['rep_a']} attendees reported by organizers), and the {agg[G_GAIN]['n']} AGs that gained an officer from among the attendees ran {agg[G_GAIN]['run_b']} then {agg[G_GAIN]['run_a']} ({agg[G_GAIN]['reg_b']} then {agg[G_GAIN]['reg_a']} registrants). "
               f"The {agg[G_SEC]['n']} AGs whose section sent someone else ran {agg[G_SEC]['run_b']} then {agg[G_SEC]['run_a']}, and the {agg[G_NONE]['n']} with no connection to the Summit ran {agg[G_NONE]['run_b']} then {agg[G_NONE]['run_a']}; most of those exist only on paper, serving only as co-hosts on other OUs' events (section 5). "
               "The connection runs both ways: active groups send their officers to the Summit, and new groups recruit officers from the room.</li>"
               "<li><b>Limits.</b> vTools records who registered for an event and, where a report was filed, how many people attended, but not who; so every per-person event figure is a registration count. Sample sizes are small (5 to 25 people). "
               "People who travel to a Summit may already be more inclined to get involved, so the comparison with non-attendees describes a difference, not its cause. IEEE's Young Professionals membership records and its 'YP eligible' flag are both unreliable: "
               "some people carry the YP label who are not YPs, and some YPs carry no label (section 6).</li></ul>"
               "<p class='note'><sup>1</sup> An event counts as organized by an attendee when its vTools contact email is an address that person has used in any vTools registration. Addresses used only for organizing are not found, so these are minimum counts.</p></div>")

    # ---------------------------------------------------------------- 1 who attended
    out.append("<h2>1. Who attended</h2>")
    n_reg = len(p) - int(p["ticket"].str.contains("walk-in").sum()); walk_no = args.attended - n_reg - int(p["ticket"].str.contains("walk-in").sum())
    out.append(f"<p>{args.attended} people were on the organizers' attendance sheet: {n_reg} who had registered through vTools and {args.attended - n_reg} walk-ins ({args.registered} had registered, so {args.registered - n_reg} did not come). "
               f"{N} attendees were Region 1 or 2 members at the Summit with a membership record we could access (active or lapsed since), which is what the Region-level data covers; they are the 'tracked' group followed in sections 2 and 3, and {len(lapsed)} of them have since let their membership lapse. "
               f"The other {args.attended - N} are speakers and guests from other regions ({int((not_an['ticket'] != 'General Attendee').sum())}), general attendees with no Region 1/2 record active or inactive ({int((not_an['ticket'] == 'General Attendee').sum())}), and walk-ins without a member number ({walk_no}).</p>")
    unknown = ("unknown", args.attended - N)
    role_order = ["Region & above", "Section", "YP affinity group", "other affinity group", "chapter", "Student branch", "No position"]
    out.append(chart("Volunteer role at the Summit", sc.hbars([(t[0].upper() + t[1:], int((coh[role] == t).sum())) for t in role_order if (coh[role] == t).sum()] + [unknown], vmax=args.attended)))
    n_excl = int((coh["yp_override"] == "excluded").sum()); n_conf = int((coh["yp_override"] == "Y").sum())
    yf_path = os.path.join(args.out_dir, "deep", "attendee_yp_facts.csv")
    yf = pd.read_csv(yf_path, dtype=str, keep_default_na=False).set_index("member") if os.path.exists(yf_path) else None
    n_conf_noprod = int(((coh["yp_override"] == "Y") & (coh["member"].map(lambda m: yf["yp_product"].get(m, "") if yf is not None else "") != "Y")).sum())
    out.append(chart("Young Professional status", sc.hbars([("YP", NY), ("not a YP (no product, no override)", int((coh['yp_status'] == 'not YP member').sum())), ("lapsed member: YP data unavailable", int((coh['yp_status'] == 'unknown').sum())), unknown], vmax=args.attended),
                     f"YP = holds the IEEE Young Professionals membership product, or was confirmed as a YP by the Region 1 YP Chair (overrides: {n_conf} people, listed in the appendix, of whom {n_conf_noprod} have no product on record). "
                     f"'Not a YP' = no product on record and no override ({int((coh['yp_status'] == 'not YP member').sum()) - n_excl}), plus {n_excl} product holder overridden as not a YP. The YP product is exported for active members only, so attendees who have since lapsed are unknown unless overridden. "
                     "Enrollment in the product is incomplete (section 6), so the YP count is a minimum."))
    grade_order = [("Graduate Student Member", "Student / Graduate Student"), ("Member", "Member"), ("Senior Member", "Senior Member"), ("Life / Fellow", "Life / Fellow")]
    out.append(chart("Grade", sc.hbars([(lab, int((coh['grade_group'] == key).sum())) for lab, key in grade_order if (coh['grade_group'] == key).sum()] + [unknown], vmax=args.attended)))
    out.append(chart("Events registered for in the year before", sc.hbars([(b, int((coh['events_before_bin'] == b).sum())) for b in ["0", "1–3", "4+"]] + [unknown], vmax=args.attended)))
    out.append(chart("Region", sc.hbars([("Region 1", int((coh['region'] == 'R1').sum())), ("Region 2", int((coh['region'] == 'R2').sum())), ("unknown / other", args.attended - N)], vmax=args.attended)))
    out.append(f"<p class='note'>Of the {N} tracked attendees, {int((coh['speaker'] != '').sum())} were speakers per the Summit programme and {len(grant)} received a travel grant. Grants were open to anyone who applied, and grant recipients' outcomes below are much like everyone else's "
               f"({of(int((grant['events_after'] > 0).sum()), len(grant))} registered for a later event against {of(int((nogrant['events_after'] > 0).sum()), len(nogrant))}; {int((grant['new_ou_after'] > 0).sum())} against {int((nogrant['new_ou_after'] > 0).sum())} took a new role).</p>")

    # ---------------------------------------------------------------- 2 what they did
    out.append("<h2>2. What the attendees did in the year after</h2>")
    out.append(f"<p>This section follows the {N} attendees who were Region 1 or 2 members at the {S} and whose records we could access (the 'tracked' attendees), leading with the {NY} of them who are Young Professionals by the rule in section 6 (the 'tracked YPs'). "
               f"'Events' are distinct vTools events in Regions 1 and 2 the person registered for, the {S} itself excluded; the year before is {before_txt}, the year after {after_txt}. "
               "A 'new role' is a volunteer position in an organizational unit (section, chapter, affinity group, committee) the person had never served in; a change of title inside a unit they already served (for example Vice Chair to Treasurer) is not counted as new.</p>")
    def tile(label, mask_all, mask_yp, sub_all_before=None, sub_yp_before=None):
        return (label, f"{of(int(mask_yp.sum()), NY)} tracked YPs", (f"year before: {of(int(sub_yp_before.sum()), NY)}; " if sub_yp_before is not None else "") + f"all {N} tracked attendees: {int(mask_all.sum())}" + (f" (year before {int(sub_all_before.sum())})" if sub_all_before is not None else ""))
    out.append(sc.stat_tiles([
        tile("Registered for at least one event", coh["events_after"] > 0, cy["events_after"] > 0, coh["events_before"] > 0, cy["events_before"] > 0),
        tile("Registered for an event in their own section", coh["local_after"] > 0, cy["local_after"] > 0, coh["local_before"] > 0, cy["local_before"] > 0),
        tile("Took a new role", coh["new_ou_after"] > 0, cy["new_ou_after"] > 0),
        tile("Took their first IEEE position", coh["first_vol"] == 1, cy["first_vol"] == 1),
        tile("Organized at least one event", coh["organized_after"] > 0, cy["organized_after"] > 0, coh["organized_before"] > 0, cy["organized_before"] > 0),
        ("Renewed their IEEE membership for 2026", f"{of(int((coh['member_now'] == 'yes').sum()), N)} tracked attendees" if "member_now" in coh.columns else "–", f"the {len(lapsed)} who did not renew are listed in Appendix A; whether they were YPs cannot be read from the data, because the YP product is exported for active members only"),
    ]))
    out.append("<p class='note'>Events organized: an event counts as organized by an attendee when its vTools contact email is an address that person has used in any vTools registration; addresses used only for organizing are not found, so these are minimum counts.</p>")
    out.append("<h3>New roles taken in the year after</h3>")
    nr = [[r["name"], r["section"].replace(" Section", ""), r[role] if r["tier_at_ref"] else "none", "YP" if r["yp_status"] == "YP member" else "", r["new_ou_detail"].replace("Affinity Group,WIE/YP", "WIE/YP AG").replace("Affinity Group,YP", "YP AG").replace("Affinity Group ", "AG ")]
          for _, r in coh[coh["new_ou_after"] > 0].sort_values(["tier_at_ref", "name"]).iterrows()]
    out.append(table(["Name", "Section", "Role at the Summit", "", "New role (unit not served before)"], nr, widths=[17, 12, 14, 5, 52]))
    out.append(chart(f"Events registered for, year before ({before_txt}) → year after ({after_txt}), each attendee",
                     sc.dumbbell([(f"{r['name']} · {r[role]}", r["events_before"], r["events_after"], f"{r['name']}: before {r['events_before']}, after {r['events_after']}")
                                  for _, r in coh.sort_values(["events_before", "events_after"]).iterrows()], xlabel="distinct events registered for", row_h=13)))
    names = [tier_names[t].replace("Chapter/AG", "Chapter / affinity group") for t in range(5)]
    matrix = [[int(((coh["tier_at_ref"] == i) & (coh["tier_after"] == j)).sum()) for j in range(5)] for i in range(5)]
    out.append(chart("Highest volunteer role at the Summit → highest role held in the year after", sc.heatmap(names, ["None", "Student br.", "Chapter / AG", "Section", "Region+"], matrix, row_title="at the Summit", col_title="year after")))
    out.append("<h3>Events organized by attendees</h3>")
    out.append(f"<p class='note'>Number of vTools events whose contact person was the attendee, in each year. All {N} tracked attendees were checked; those with at least one are listed. These are minimum counts: an event is found only when its contact email is an address the person has used in a vTools registration.</p>")
    out.append(table(["Name", "Section", "Role at the Summit", "", "Events organized, year before", "Events organized, year after", "Change"],
                     [[r["name"], r["section"].replace(" Section", ""), r[role], "YP" if r["yp_status"] == "YP member" else "", r["organized_before"], r["organized_after"], f"{r['organized_after'] - r['organized_before']:+d}"]
                      for _, r in coh[(coh["organized_before"] + coh["organized_after"]) > 0].sort_values(["organized_after", "organized_before"], ascending=[False, True]).iterrows()], widths=[22, 15, 15, 5, 16, 16, 11]))

    # ---------------------------------------------------------------- 3 comparison
    out.append(f"<h2 class='pagebreak'>3. Compared with similar YPs who did not attend</h2>")
    out.append(f"<p>Did the attendees do more than they would have anyway? The only comparison the data allows is with YP members who looked similar before the {S}. "
               f"For each of the {NY} YP attendees we take the Region 1/2 YP members who were not at the Summit and who match them on three things: whether they had ever held a volunteer position, whether they are a graduate student, "
               f"and how many events they registered for in the year before, in the same range (none, 1 to 3, 4 to 9, or 10 or more). Only YP members who had already joined IEEE by the Summit date are eligible, so that nobody is compared with people who could only have been active in the second year; there are {len(c):,} of them (all currently active members, since the YP product is only exported for those). "
               f"Where fewer than ten people match all three criteria, the graduate-student criterion is dropped; the smallest comparison set is then {min_pool} people. "
               "The tables show what the attendees did in the year after beside what their comparison sets did over the same year. Tighter or looser matching on prior events (exact count, within one or two, within a quarter) moves the comparison figures by at most a few points. "
               "This describes a difference; attending was the attendees' own choice, and no matching removes that.</p>")
    def comp_table(grp, key):
        rows = [[oname, f"{of(int(grp[okey].sum()), len(grp))} ({100 * grp[okey].mean():.0f}%)", f"{100 * mr[(key, okey)]:.0f}%"]
                for okey, oname in (("any_after", "Registered for at least one event"), ("new_ou", "Took a new role"), ("first_vol", "Took their first IEEE position"))]
        return table(["In the year after the Summit…", f"Attendees ({len(grp)})", "Their comparison sets"], rows, widths=[46, 27, 27])
    out.append("<h3>YP attendees who came with no volunteer position</h3>"); out.append(comp_table(yp_new, "new"))
    out.append("<h3>YP attendees who came as officers</h3>"); out.append(comp_table(yp_off, "off"))
    out.append(f"<p class='note'>{int((coh['yp_status'] == 'not YP member').sum())} tracked attendees are not YPs and {int((coh['yp_status'] == 'unknown').sum())} lapsed with YP status unknown; they have no comparison set. {int((newcomers['yp_status'] != 'YP member').sum())} of the {len(newcomers)} newcomers are among them.</p>")
    if ago is not None:
        out.append("<h3>YP affinity group officers who attended and who did not</h3>")
        out.append(f"<p>A more direct comparison for officers: everyone in Regions 1 and 2 who held a YP affinity group officer position on the day of the {S}, split by whether they attended. The two groups happen to be the same size.</p>")
        rows = []
        for att, lab in ((True, "Attended"), (False, "Did not attend")):
            g = ago[ago.attended == att]
            rows.append([lab, len(g), of(int(g.still_ag_officer_at_end.sum()), len(g)), of(int(g.new_ou_after.sum()), len(g)), of(int((g.events_after > 0).sum()), len(g)), f"{g.events_before.mean():.1f} → {g.events_after.mean():.1f}"])
        out.append(table(["YP AG officers on the Summit date", "People", "Still an AG officer a year later", "Took a new role", "Registered for ≥1 event after", "Events registered for, mean, year before → after"], rows))
    months = list(range(0, W // 30 + 1))
    def curve(dates, n, sign=1):
        """share with an event within m months after (sign=1) or before (sign=-1) the Summit"""
        return [(sign * m, 100.0 * sum(1 for d in dates if d is not None and d == d and (d <= ref + datetime.timedelta(days=30 * m) if sign == 1 else d >= ref - datetime.timedelta(days=30 * m))) / (n or 1)) for m in months]
    def matched_curve(grp, col, sign=1):
        acc = np.zeros(len(months)); n = 0
        for _, r in grp.iterrows():
            pl = comparable(c, r)
            if not len(pl): continue
            acc += np.array([y for _, y in curve(pl[col], len(pl), sign)]); n += 1
        return [(sign * m, v) for m, v in zip(months, acc / (n or 1))]
    def both(grp):
        a = sorted(curve(grp["last_event_before"], len(grp), -1)) + curve(grp["first_event_after"], len(grp), 1)
        b = sorted(matched_curve(grp, "last_event_before", -1)) + matched_curve(grp, "first_event_after", 1)
        return a, b
    mon_path = os.path.join(args.out_dir, "deep", "monthly_activity.csv")
    if os.path.exists(mon_path):
        mon = pd.read_csv(mon_path, dtype={"person": str}); M = W // 30
        active = mon[mon["events"] > 0].groupby("person")["m"].agg(set)
        def rate(persons, m): return 100.0 * sum(1 for x in persons if m in active.get(x, set())) / (len(persons) or 1)
        def group_line(grp): return [(m, rate(list(grp["person"]), m)) for m in range(-M, M)]
        def matched_line(grp):
            acc = np.zeros(2 * M); n = 0
            for _, r in grp.iterrows():
                pl = list(comparable(c, r)["person"])
                acc += np.array([rate(pl, m) for m in range(-M, M)]); n += 1
            return [(m, v) for m, v in zip(range(-M, M), acc / (n or 1))]
        for label, grp, colors in ((f"YP attendees who came with no position (n={len(yp_new)})", yp_new, (None, "#86b6ef")), (f"YP attendees who came as officers (n={len(yp_off)})", yp_off, ("#eb6834", "#f4b08a"))):
            out.append(chart(f"{label}: share registering for at least one event in each month, year before and year after the {S}",
                             sc.lines([("attendees", group_line(grp), colors[0]), ("their comparison sets", matched_line(grp), colors[1])],
                                      xlabel="months relative to the Summit (0 = the month starting on the Summit date; the Summit itself is excluded)", ylabel="% registering that month",
                                      x_ticks=[t for t in (-11, -9, -6, -3, 0, 3, 6, 9, 11) if -M <= t < M], height=300)))

    # ---------------------------------------------------------------- 4 affinity groups
    out.append("<h2>4. The YP affinity groups</h2>")
    out.append(f"<p>Every YP affinity group in Regions 1 and 2 with any officers or events is grouped by its connection to the {S}: one of its officers on the Summit date attended; it gained an officer from among the attendees during the year after; someone else from its section attended; or nothing connects it. "
               "In vTools an event has one primary host and any number of co-hosts, and most events that name a YP AG are chapter or section events the AG was added to as a co-host, so only events with the AG as <b>primary host</b> are counted as run by it (co-hosting is examined in section 5). "
               "Registrants are distinct people with a vTools registration for those events; reported attendance is the organizer's count where a report was filed. Both years cover the same calendar months.</p>")
    rows = [[g[0].upper() + g[1:], agg[g]["n"], f"{agg[g]['run_b']} → {agg[g]['run_a']}", f"{agg[g]['reg_b']} → {agg[g]['reg_a']}", f"{agg[g]['yp_b']} → {agg[g]['yp_a']}", f"{agg[g]['rep_b']} → {agg[g]['rep_a']}", f"{agg[g]['up']} up, {agg[g]['down']} down"]
            for g in GROUPS]
    out.append(table(["Affinity groups…", "AGs", "Events with the AG as primary host, total, year before → after", "Distinct registrants at those events", "of whom YPs", "Reported attendance", "AGs whose events went"], rows, widths=[28, 6, 16, 14, 10, 12, 14]))
    vmax = int(max(has_ag.yp_ag_run_events_before.max(), has_ag.yp_ag_run_events_after.max()))
    for g in GROUPS:
        sub = agg[g]["sub"].sort_values(["yp_ag_run_events_after", "yp_ag_run_events_before"])
        if not len(sub): continue
        rows = [(r.section.replace(" Section", ""), int(r.yp_ag_run_events_before), int(r.yp_ag_run_events_after), f"{r.section}: {int(r.yp_ag_run_events_before)} → {int(r.yp_ag_run_events_after)} events with the YP AG as primary host") for _, r in sub.iterrows()]
        out.append(chart(f"Events with the YP AG as primary host, year before → year after: affinity groups {g} ({len(rows)})", sc.dumbbell(rows, xlabel="events", row_h=13, vmax=vmax)))
    big = ag[(ag["registrants"] >= 50)].drop_duplicates("Id")
    out.append(f"<p class='note'>{args.next_summit} Large events dominate any registrant total they fall into: {len(big)} events naming a YP AG had 50 or more registrants in these two years, and all but {int((big['kind'] == 'run').sum())} were chapter or section events with the AG as co-host, so they do not enter the counts above.</p>")

    # ---------------------------------------------------------------- 5 paper AGs
    out.append("<h2>5. Paper affinity groups</h2>")
    out.append("<p>Many YP affinity groups appear in vTools only as co-hosts on events that chapters or the section organize. Whether that is real collaboration or a name added to the host list can be judged from who registered: "
               "a co-hosted event whose title names Young Professionals, or where YPs make up at least a quarter of registrants and at least twice the section's usual share, is treated as a real YP co-host; other co-hosted events with five or more vTools registrants are paper; "
               "events with fewer than five vTools registrants took their registrations elsewhere or at the door, so there is no way to tell. The tables cover every YP AG with an event in either year; groups whose section had someone at the Summit come first.</p>")
    agrows = {True: [], False: []}
    for sec, g in ag.groupby("section"):
        r = s[s.section == sec].iloc[0]
        run_share = f"{100 * g[g.kind == 'run'].yp_registrants.sum() / max(g[g.kind == 'run'].registrants.sum(), 1):.0f}%" if (g.kind == "run").sum() else "–"
        flag = (("no officers on record at the Summit or a year later" + ("; an attendee held a seat in between" if sec in gained else "")) if r.yp_ag_officers_at_ref == 0 and r.yp_ag_officers_at_end == 0
                else "no officers on record at the Summit; reported during the year after" if r.yp_ag_officers_at_ref == 0 else "")
        agrows[bool(r.participants)].append([sec.replace(" Section", ""), f"{int(r.yp_ag_officers_at_ref)} → {int(r.yp_ag_officers_at_end)}", int((g.kind == "run").sum()), int((g.kind == "yp_cohost").sum()), int((g.kind == "paper").sum()), int((g.kind == "unknown").sum()),
                                             f"{run_share} vs {100 * base['share'].get(sec, 0):.0f}%", flag])
    cols5 = ["YP affinity group", "Officers, Summit → a year later", "Events with the AG as primary host (both years)", "Co-hosted, real YP audience", "Co-hosted, paper", "Co-hosted, can't tell", "YP share of registrants at AG-hosted events vs the section's other events", "Note"]
    for had, title in ((True, "Affinity groups in sections that had someone at the Summit"), (False, "Affinity groups in sections with nobody at the Summit")):
        rows5 = sorted(agrows[had], key=lambda x: (-x[2], -x[3]))
        out.append(table(cols5, rows5, title, widths=[16, 11, 11, 10, 9, 9, 16, 18]))
    allrows = agrows[True] + agrows[False]
    paper_only = [r[0] for r in allrows if r[2] == 0 and r[3] == 0]
    no_off_none = [r[0] for r in allrows if r[7] == "no officers on record at the Summit or a year later"]
    no_off_gap = [r[0] for r in allrows if r[7].startswith("no officers on record at the Summit or") and r[7] != "no officers on record at the Summit or a year later"]
    no_off_ref = [r[0] for r in allrows if r[7].startswith("no officers on record at the Summit;")]
    out.append(f"<p class='note'>{len(paper_only)} of these {len(allrows)} affinity groups hosted nothing themselves and co-hosted nothing with a visible YP audience in two years: {', '.join(paper_only)}. "
               + (f"{', '.join(no_off_none)} appears on events with no officers on record at all: a name on a host list with nobody reported behind it. " if no_off_none else "")
               + (f"{', '.join(no_off_gap)} had no officers on record at the Summit or a year later; the only seat on file in between was held by a Summit attendee for part of the year after. " if no_off_gap else "")
               + (f"{', '.join(no_off_ref)} had no officers on record at the Summit and reported them during the year after, so their 'before' activity ran without any officer on file. " if no_off_ref else "")
               + "An AG with officers on record but no events of its own may still be recruiting or meeting off vTools, but nothing in the data shows it.</p>")

    # ---------------------------------------------------------------- 6 caveats
    out.append("<h2>6. What to trust, and what 'YP' means in IEEE's data</h2>")
    out.append("<ul class='note'>"
               f"<li><b>Registrations, not attendance.</b> vTools records who registered and, where the organizer filed a report, how many people attended, but not who. Every per-person event figure is therefore a registration count; people who attend without registering, and events with external sign-up, are missing. The {S} cohort itself comes from the organizers' attendance sheet.</li>"
               f"<li><b>Windows and backdrop.</b> Each 'year' is {W} days, the most the registration data allows on both sides, and the two cover the same calendar months. {args.backdrop} An attendee whose count stayed flat therefore did not fall behind the region.</li>"
               "<li><b>Sample size.</b> The groups are 5 to 25 people; one person moves a percentage by several points. Every name is in Appendix A so any figure can be traced.</li>"
               "<li><b>Self-selection.</b> People who applied for a travel grant and travelled to a Summit may already have been more inclined to get involved. The comparison in section 3 matches on what the data shows (prior events, prior roles, student status), not on that inclination.</li>"
               "<li><b>Officer records.</b> Volunteer positions come from vTools officer reporting, which sections file late or not at all. Title changes inside a unit are therefore not counted as new roles.</li>"
               "<li><b>Events organized</b> are minimum counts: an event counts as organized by an attendee when its vTools contact email is an address that person has used in any vTools registration, so events entered under an address used only for organizing are missed.</li>"
               f"<li><b>Who counts as a YP.</b> A Young Professional is a member within 15 years of a first degree. Two IEEE records exist for it and both are unreliable. The Young Professionals membership product is added automatically but never removed: 1,092 of the 11,031 holders in Regions 1 and 2 first held a grade above Student Member more than 15 years ago and cannot be YPs. It is also exported for active members only, so YP status is unknown for anyone who has lapsed. "
               f"IEEE's own 'YP eligible' flag is unset for 1,560 holders, but not because of the 15-year rule: 766 of them have a graduation date within 15 years, and the flag is also unset when the date is missing or in the future, which is why four attendees who are certainly YPs carry it. The graduation date itself is whatever the member entered: {args.grad_mismatch}. "
               f"In the other direction, enrollment is incomplete: five attendees known to be YPs have no product at all. This report therefore counts as YP anyone holding the product, plus the overrides in the appendix (people confirmed by the Region 1 YP Chair), minus one product holder overridden as not a YP; every YP count is a minimum. "
               "IEEE's membership views hold current holders only, so nothing about YPs in past years can be derived from them. A separate report covers this in full.</li>"
               "</ul>")

    # ---------------------------------------------------------------- appendix
    short_ticket = {"General Attendee": "attendee", "Speaker": "speaker", "Speaker / VIP / Event Volunteer": "speaker/VIP/volunteer"}
    ordered = coh.sort_values(["tier_at_ref", "name"], ascending=[False, True])
    out.append("<h2 class='pagebreak'>Appendix A: tracked attendees</h2>")
    out.append(table(["Name", "Section", "Ticket", "Speaker", "Grant", "Role at the Summit", "YP", "Grade"],
                     [[r["name"], r["section"].replace(" Section", ""), short_ticket.get(r["ticket"], r["ticket"]), "Y" if r["speaker"] else "", r["grant"], r[role],
                       ("Y (override)" if r["yp_override"] == "Y" else "Y" if r["yp_status"] == "YP member" else "no (override)" if r["yp_override"] == "excluded" else "unknown (lapsed)" if r["yp_status"] == "unknown" else "no"), r["grade"]] for _, r in ordered.iterrows()],
                     "Who they are", widths=[20, 14, 16, 7, 6, 13, 12, 12]))
    out.append(table(["Name", "Events registered for, year before → after", "Own-section events, before → after", "Events organized, before → after", "New role", "New title in a unit already served"],
                     [[r["name"], f"{r['events_before']} → {r['events_after']}", f"{r['local_before']} → {r['local_after']}", f"{r['organized_before']} → {r['organized_after']}",
                       r["new_ou_detail"].replace("Affinity Group,WIE/YP", "WIE/YP AG").replace("Affinity Group,YP", "YP AG").replace("Affinity Group ", "AG "), r["new_title_detail"]] for _, r in ordered.iterrows()],
                     "What they did", cls="small", widths=[16, 11, 11, 11, 26, 25]))
    lk_path = os.path.join(args.out_dir, "deep", "not_analysed_lookup.csv")
    lk = pd.read_csv(lk_path, dtype=str, keep_default_na=False).set_index("name") if os.path.exists(lk_path) else None
    out.append(table(["Name", "Ticket", "What the records show"], [[r["name"], short_ticket.get(r["ticket"], r["ticket"]),
                      (lk["record"].get(r["name"], "") if lk is not None else "") or r["member_record"].replace("no active R1/R2 record", "no Region 1/2 membership record, active or inactive").replace("email only", "registered by email only, no member number")] for _, r in not_an.iterrows()],
                     "Attended, not tracked (checked against active and inactive Region 1/2 records)", widths=[26, 24, 50]))
    if len(lapsed):
        out.append(table(["Name", "Section", "Grade", "Last paid year", "Role at the Summit", "Events, year before → after"],
                         [[r["name"], r["section"].replace(" Section", ""), r["grade"], r["last_renew_year"], r[role], f"{r['events_before']} → {r['events_after']}"] for _, r in lapsed.iterrows()],
                         "Tracked attendees whose IEEE membership lapsed after the Summit", widths=[22, 16, 16, 12, 16, 18]))
    if yf is not None:
        out.append("<h2 class='pagebreak'>Appendix B: how each attendee's YP status was determined</h2>")
        out.append("<p class='note'>Everything IEEE's data offers for deciding who is a Young Professional (a member within 15 years of a first degree), per tracked attendee. "
                   "<b>YP product</b>: holds the IEEE Young Professionals membership product, and the year it started (the product is added automatically and never removed); it is exported for active members only, so for lapsed members it is unknown. "
                   "<b>IEEE 'YP eligible' flag</b>: IEEE's own computed eligibility, exported only alongside the product, so it is unknown ('–') for anyone without the product; N means the system does not consider the person eligible, but it is also N when the graduation date is missing or in the future. "
                   "<b>Graduation date on file</b>: the single date in the member's profile; it is often a later degree, not the first, so a date within 15 years does not by itself make someone a YP. "
                   "<b>Last year as Student Member</b> and <b>first year at a higher grade</b> (Graduate Student Member or above): from the grade history; someone whose first year above Student Member is more than 15 years ago cannot be a YP. "
                   "Notes appear only where the records need explaining; overrides are decisions by the Region 1 YP Chair where the records are wrong.</p>")
        notes = CHAIR_NOTES
        yrows = []
        for _, r in ordered.iterrows():
            f = yf.loc[r["member"]] if r["member"] in yf.index else None
            if f is None: continue
            auto = []
            gy = int(f["graduation_date_on_file"][-4:]) if f["graduation_date_on_file"] and f["graduation_date_on_file"][-4:].isdigit() else None
            fy = int(float(f["first_grade_above_student_year"])) if f["first_grade_above_student_year"] else None
            if f["yp_product"] == "Y" and f["yp_eligible_flag"] == "N":
                auto.append("flag N: " + ("no graduation date on file" if gy is None else "the date on file is in the future" if gy > ref.year else "the date on file is within 15 years (the flag is not following it)" if gy >= ref.year - 15 else "the date on file is more than 15 years old"))
            if f["yp_product"] == "Y" and fy and fy <= ref.year - 15: auto.append(f"holds the product but first held a grade above Student Member in {fy}, more than 15 years ago")
            if r["member_now"] == "no": auto.append(f"membership lapsed after the Summit (last paid year {r['last_renew_year']}); the YP product is not exported for lapsed members")
            note = notes.get(r["name"], "")
            status = {"YP member": "YP", "not YP member": "not a YP", "unknown": "unknown"}.get(r["yp_status"], r["yp_status"]) + (" (override)" if r["yp_override"] in ("Y", "excluded") else "")
            yrows.append([r["name"], r["member"], GRADE_ABBR.get(f["grade"], f["grade"]),
                          ("Y, since " + f["yp_product_since"]) if f["yp_product"] == "Y" else ("unknown" if r["member_now"] == "no" else "no"), f["yp_eligible_flag"] or "–", f["graduation_date_on_file"] or "–",
                          f["last_student_member_year"] or "–", f["first_grade_above_student_year"] or "–", status, "; ".join(auto), note])
        out.append(table(["Name", "Member #", "Grade", "YP product (since)", "IEEE 'YP eligible' flag", "Graduation date on file", "Last year as Student Member", "First year at a higher grade", "Status in this report", "Data note", "Region 1 YP Chair"],
                         yrows, cls="small", widths=[12, 10, 5.5, 8, 6, 9, 6.5, 6.5, 9, 14, 13.5]))
        out.append("<p class='note'>Grades: M Member, SM Senior Member, GSM Graduate Student Member, LS Life Senior.</p>")
    path = os.path.join(args.out_dir, "summit_report.html")
    with open(path, "w") as fp:
        fp.write("\n".join(out))
    print("Wrote", path)
    if args.pdf:
        print("PDF", html_to_pdf(path, path.replace(".html", ".pdf")))


if __name__ == "__main__":
    main()
