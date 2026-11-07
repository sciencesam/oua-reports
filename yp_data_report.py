"""Draft report on how IEEE records Young Professionals membership and why
the counts derived from it are unreliable. Written for Region / MGA readers;
built from the OU Analytics exports (memberships detail = YP product rows,
Members & Affiliates active + inactive) and the 2025 YP Summit attendee
checks.

    python yp_data_report.py --out-dir output/summit2025 --pdf
"""

import argparse
import datetime
import html
import os

import pandas as pd

import svgcharts as sc
from chapter_roster import html_to_pdf

style = """
  @page { size: letter; margin: 0.6in 0.55in; }
  body { font-family: Helvetica, Arial, sans-serif; color: #0b0b0b; background: #fff; max-width: 60em; margin: 1.5em auto; padding: 0 1.2em; line-height: 1.45; font-size: 10.5pt; }
  h1 { font-size: 20pt; margin: 0 0 2px 0; } h2 { font-size: 14pt; margin: 1.6em 0 0.3em; border-bottom: 1px solid #c3c2b7; padding-bottom: 3px; break-after: avoid; }
  h3 { font-size: 11pt; margin: 1.1em 0 0.2em; break-after: avoid; } h3.chart { margin-bottom: 0; }
  .subtitle { color: #52514e; margin: 0 0 1em 0; }
  .summary { background: #f4f6f8; border-left: 4px solid #2a78d6; padding: 0.7em 1em; margin: 1em 0; } .summary li { margin: 0.35em 0; }
  table { border-collapse: collapse; font-size: 9pt; margin: 0.4em 0 1em; width: 100%; }
  caption { text-align: left; font-weight: 600; padding: 0.3em 0; font-size: 9.5pt; }
  th { text-align: left; background: #f0efec; padding: 3px 6px; border-bottom: 1px solid #c3c2b7; vertical-align: bottom; }
  td { padding: 2.5px 6px; border-bottom: 1px solid #e1e0d9; vertical-align: top; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  tr { break-inside: avoid; } thead { display: table-header-group; }
  svg { display: block; margin: 0.2em 0 0.6em; max-width: 100%; height: auto; break-inside: avoid; }
  .note { color: #52514e; font-size: 9pt; } .small td, .small th { font-size: 8pt; padding: 2px 4px; }
  .draft { color: #a33; font-weight: 600; }
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True); ap.add_argument("--pdf", action="store_true")
    args = ap.parse_args()
    # Figures verified in the OU Analytics exports of 2026-09-14 (Regions 1 and 2). Kept as named constants so the text and tables agree.
    F = dict(holders=11031, active_members=44448, flag_n=1560, flag_y=9406, flag_blank=65,
             over15_by_grade=1092, over15_flag_n=894, over15_flag_y=194, never_student=7725,
             flag_n_missing=67, flag_n_future=49, flag_n_within15=766, flag_n_over15=678,
             grad_within15=4069, grad_future=6203, grad_over15=683, grad_missing=76, later_degree_nonstudent=1090,
             start_2024=833, start_2025=2196, start_2025_joined=1179, start_2025_existing=1017, start_2026=4875, start_2026_joined_2026=3046, start_2026_joined_2025=797, start_2026_existing=1032,
             inactive_rows=196341, active_rows=44698, student_exit_range="3,300 to 5,600",
             reg_all_before=17318, reg_all_after=14919, reg_yp_fixed_before=776, reg_yp_fixed_after=746, reg_yp_any_before=891, reg_yp_any_after=1162,
             yp_share_active=25, yp_reg_rate=10.5, nonyp_reg_rate=8.5,
             attendees_analysed=45, attendees_active=41, attendees_lapsed=4, attendees_yp=35, attendees_override_noprod=5, attendees_override_flagn=4, attendees_excluded=1)
    today = datetime.date.today()
    out = [f"<style>{style}</style>", "<h1>What IEEE's data says about who is a Young Professional</h1>",
           f"<p class='subtitle'><span class='draft'>Draft</span> · Regions 1 and 2 · from the OU Analytics exports of 14 September 2026 and the 2025 YP Summit attendee checks · {today:%-d %B %Y}</p>"]

    out.append("<div class='summary'><b>Summary</b><ul>"
               "<li><b>The definition is clear; the records are not.</b> A Young Professional is an IEEE member within 15 years of a first degree. IEEE records this two ways, as a membership product (\"IEEE Young Professionals\", added automatically) and as a computed \"YP eligible\" flag. Both are wrong often enough that any count built on them needs a correction.</li>"
               f"<li><b>The product is never removed.</b> {F['holders']:,} members in Regions 1 and 2 hold it. {F['over15_by_grade']:,} of them first held a grade above Student Member more than 15 years ago and cannot be YPs; only {F['over15_flag_n']} of those are flagged by IEEE's own rule as not eligible.</li>"
               f"<li><b>The flag is not the 15-year rule.</b> Of the {F['flag_n']:,} holders flagged not eligible, {F['flag_n_within15']} have a graduation date within 15 years, {F['flag_n_missing']} have none on file and {F['flag_n_future']} have a future date. Four people at the 2025 YP Summit who are certainly YPs carry the N flag.</li>"
               f"<li><b>The graduation date is whatever the member typed.</b> {F['grad_future']:,} of the {F['holders']:,} holders have a future date (their current graduate degree) and {F['later_degree_nonstudent']:,} holders above Student Member grade have a date years after they first joined, so it is a later degree, not the first.</li>"
               f"<li><b>Enrollment is incomplete.</b> Five of the {F['attendees_active']} active-member Summit attendees we could check are YPs with no product at all: members who joined directly at Member grade with no student record.</li>"
               f"<li><b>The product is only visible for active members.</b> The memberships view exports current, active members; for a member who has lapsed, even last year, YP status cannot be read at all.</li>"
               "<li><b>There is no history.</b> The membership views hold current holders only. Nothing about how many YPs there were in any past year can be derived from them, and headline growth figures built on current holders are inflated by people who joined recently. "
               f"Region-wide, YP holders registering for events appear to rise 30% between the year before and the year after the 2025 Summit; holding the group fixed to people who were already members, it fell 4%.</li>"
               "<li><b>What to do.</b> Report YP counts as minimums with the method stated; combine the product with manual confirmation for small groups; fix cohorts by join date before comparing years; and ask staff for the enrollment rule, product removal or an as-of-date status, and a first-degree date with validation (section 7).</li></ul></div>")

    out.append("<h2>1. How IEEE records Young Professionals</h2>")
    out.append("<p>Three fields in OU Analytics bear on whether a member is a YP:</p><ul>"
               "<li><b>The YP membership product</b> (code MEMYP060) in the memberships view: one row per current holder, with an <i>asset start date</i> that is always 1 January of the membership year it was first attached, floored at 2014 when the product was created. It is added automatically and, as far as the data shows, never removed.</li>"
               "<li><b>The 'YP eligible Y/N' flag</b>, computed by IEEE and exported alongside the product. It exists only for product holders in the export; in the Members & Affiliates view it is blank for everyone.</li>"
               "<li><b>The graduation date</b> in the member's profile, a single free field. Graduate students enter the degree in progress; others enter whatever they choose.</li></ul>"
               "<p>Members & Affiliates also carries a <b>grade history</b> (\"M-26, StM-25\": each grade change with its year), which is independent of all three and turns out to be the most reliable signal, and it is available for inactive members as well. "
               "Only Student Member is a student grade for this purpose: Graduate Student Members already hold a first degree, so the year a member first held any grade above Student Member is an upper bound on the year of the first degree.</p>")

    out.append("<h2>2. The product is never removed</h2>")
    out.append(f"<p>{F['holders']:,} active members in Regions 1 and 2 hold the YP product, {F['yp_share_active']}% of all active members. Using the 15-year rule on the grade history, {F['over15_by_grade']:,} of them first held a grade above Student Member in 2010 or earlier and cannot be Young Professionals; "
               f"IEEE's own flag agrees for {F['over15_flag_n']} of them and still says eligible for {F['over15_flag_y']}. {F['never_student']:,} of the holders never held the Student Member grade at all, so for most holders the grade history only bounds the first degree from above. "
               "One holder has a Student Member entry from 1988, a product since 2016 and a graduation date of 2022. Another, retired, graduated in 2012 by the profile and is flagged eligible.</p>")
    out.append(chart("The 11,031 YP product holders in Regions 1 and 2, by IEEE's eligibility flag",
                     sc.hbars([("flag Y", F["flag_y"]), ("flag N", F["flag_n"]), ("blank", F["flag_blank"])], vmax=F["holders"]),
                     f"Among the {F['flag_n']:,} flagged N: graduation date within 15 years {F['flag_n_within15']}, more than 15 years ago {F['flag_n_over15']}, missing {F['flag_n_missing']}, in the future {F['flag_n_future']}. The {F['flag_blank']} with a blank flag hold the product but have no eligibility value at all; the flag exists only for product holders, so for everyone else it is simply unknown."))

    out.append("<h2>3. The flag does not follow the 15-year rule</h2>")
    out.append(f"<p>If the flag were 'graduation date within 15 years', the {F['flag_n']:,} holders flagged N would all have old dates. {F['flag_n_within15']} of them have a date within 15 years; {F['flag_n_missing']} have no date and {F['flag_n_future']} have a future one. "
               "Whatever the rule is, it is not visible in the exported fields, and it fails in both directions: of the 2025 YP Summit attendees, four who are certainly Young Professionals carry the N flag "
               f"(two because no graduation date is on file, one because the date on file is in the future, one with a 2015 date that the flag should accept), while {F['over15_flag_y']} holders region-wide who first held a grade above Student Member more than 15 years ago are flagged Y. "
               "The product is no better: one Summit attendee who is not a YP holds it, with a 2014 graduation date on file that is a later PhD, not a first degree. The per-person evidence is in the appendix.</p>")

    out.append("<h2>4. The graduation date is a free field</h2>")
    out.append(chart("Graduation date on file, YP product holders", sc.hbars([("in the future", F["grad_future"]), ("within the last 15 years", F["grad_within15"]), ("more than 15 years ago", F["grad_over15"]), ("missing", F["grad_missing"])], vmax=F["holders"]),
                     f"Future dates are graduate students' expected completion. Among holders above Student Member grade, {F['later_degree_nonstudent']:,} have a date six or more years after their first IEEE grade entry; a date that far after joining at a non-student grade is a later degree, not the first (the 15-year rule itself is separate, section 2)."))
    out.append("<p>A rule that reads this field as \"first degree\" will keep some people YPs for decades (each new degree resets the clock) and will fail to enroll or will mis-flag people who never filled it in.</p>")

    out.append("<h2>5. Enrollment is incomplete</h2>")
    out.append(f"<p>Automatic enrollment does not reach everyone eligible. Of the {F['attendees_active']} 2025 Summit attendees with an active Region 1/2 membership record, five are YPs, confirmed by the Region 1 YP Chair, with no product on record at all. All five joined IEEE directly at Member grade in 2020 to 2025, have no student-grade history, and presumably no graduation date on file, so nothing in the system marks them as eligible. "
               "They chair YP affinity groups, sit as section officers, and are invisible to any YP count.</p>")

    out.append("<h2>6. Growth figures built on current holders are misleading</h2>")
    out.append("<p>Because the product is a snapshot, any series by year built from it counts survivors: people who held the product in 2022 and have since lapsed or aged out are simply absent. Two examples of how this misleads:</p>")
    out.append(table(["Product start year (current holders only)", "Holders today", "Joined IEEE that year", "Existing members newly given the product"],
                     [["2024", F["start_2024"], "515", "318"], ["2025", F["start_2025"], F["start_2025_joined"], F["start_2025_existing"]], ["2026", F["start_2026"], f"{F['start_2026_joined_2026']} (plus {F['start_2026_joined_2025']} who joined in late 2025)", F["start_2026_existing"]]],
                     "Current holders by the year their product started. Earlier years are smaller partly because their holders have since left; the table says nothing about how many YPs there were in those years.", widths=[34, 16, 25, 25]))
    out.append(f"<p>Existing members newly given the product went from a few hundred a year to about a thousand in 2025 and 1,800 in 2026, and new members enrolled at joining tripled. Whether that is growth or a change in how enrollment runs is not answerable from this data; it needs the total of new members by year as a denominator, which the accessible views do not provide.</p>"
               f"<p>The second example is event participation. Counting distinct YP product holders who registered for any Region 1/2 event, the year after the 2025 Summit shows {F['reg_yp_any_after']:,} against {F['reg_yp_any_before']} the year before, a rise of 30%. Restricting to holders who were already IEEE members on the Summit date, the figures are {F['reg_yp_fixed_after']} and {F['reg_yp_fixed_before']}: a fall of 4%, in line with the region as a whole ({F['reg_all_before']:,} to {F['reg_all_after']:,} distinct registrants, −14%). The apparent rise is new members who could only appear in the second year.</p>"
               f"<p>A related check: with lapsed members included (the Members & Affiliates view exports {F['inactive_rows']:,} inactive and in-arrears records for the two regions alongside {F['active_rows']:,} active ones), the number of members leaving student grade has been {F['student_exit_range']} every year since 2012 with no jump in 2025 or 2026. An earlier version of this analysis, using active members only, showed a large 2026 spike; it was entirely survivorship.</p>")

    out.append("<h2>7. What to do</h2>")
    out.append("<h3>For reports that count YPs</h3><ul>"
               "<li>State the definition used and treat every YP count as a minimum. The 2025 Summit report counts as YP anyone holding the product, plus people confirmed by the Region YP Chair, minus product holders confirmed not to be YPs; the overrides are listed by name.</li>"
               "<li>Do not use the eligibility flag to exclude anyone; it removes real YPs. Do use the grade history: a first grade above Student Member more than 15 years old is a hard exclusion.</li>"
               "<li>Fix cohorts before comparing years. Compare people who were members at the start of the period with themselves later; never compare 'current holders' across years.</li>"
               "<li>Do not derive past-year YP counts from the memberships view. If a series is needed, it has to come from IEEE staff.</li></ul>")
    out.append("<h3>Questions for IEEE staff (OU Analytics and membership)</h3><ul>"
               "<li>What exactly computes 'YP eligible'? Which date field, what happens when it is missing or in the future, and why are holders with dates within 15 years flagged N?</li>"
               "<li>Can the product be removed, or a 'YP as of date' status be exported, so that counts reflect eligibility rather than enrollment history?</li>"
               "<li>Can the profile carry a first-degree graduation date, validated against the grade history (a member cannot have a first degree after they first held a grade above Student Member)?</li>"
               "<li>Can automatic enrollment cover members who join at Member grade without a graduation date, or can sections be told who is eligible but unenrolled?</li>"
               "<li>What changed in enrollment for the 2025 and 2026 membership years, and can year-end YP counts by section be provided for 2020 onward? The year-based OU Analytics views ('5 Year Detail', '10 Year Detail', 'Full Previous Year') exist but return errors without their mandatory filters.</li></ul>")

    # appendix: attendee determination table (names; internal)
    yf_path = os.path.join(args.out_dir, "deep", "attendee_yp_facts.csv")
    if os.path.exists(yf_path):
        yf = pd.read_csv(yf_path, dtype=str, keep_default_na=False)
        notes = CHAIR_NOTES
        out.append("<h2>Appendix: the 2025 YP Summit attendees, field by field</h2>")
        out.append(f"<p class='note'>Internal, contains names and member numbers. For each of the {F['attendees_analysed']} attendees who were Region 1/2 members at the Summit ({F['attendees_lapsed']} have since lapsed): the product and its start year, IEEE's flag, the graduation date on file, "
                   "the last year as Student Member and the first year at a higher grade from the grade history, the status used in the Summit report, and notes. The flag is '–' wherever there is no product, because it is exported only with the product. "
                   "Notes appear only where the records need explaining. This is the evidence behind sections 3 to 5.</p>")
        rows = []
        for _, f in yf.sort_values("name").iterrows():
            auto = []
            g = f["graduation_date_on_file"]; gy = int(g[-4:]) if g and g[-4:].isdigit() else None
            fy = int(float(f["first_grade_above_student_year"])) if f["first_grade_above_student_year"] else None
            lapsed = f["yp_status_in_report"] == "unknown"
            if f["yp_product"] == "Y" and f["yp_eligible_flag"] == "N":
                auto.append("flag N: " + ("no graduation date on file" if not g else "the date on file is in the future" if gy > 2025 else "the date on file is within 15 years (the flag is not following it)" if gy >= 2010 else "the date on file is more than 15 years old"))
            if f["yp_product"] == "Y" and fy and fy <= 2010: auto.append(f"holds the product but first held a grade above Student Member in {fy}, more than 15 years ago")
            if lapsed: auto.append("membership lapsed after the Summit; the product is not exported for lapsed members")
            status = {"YP member": "YP", "not YP member": "not a YP", "unknown": "unknown"}.get(f["yp_status_in_report"], f["yp_status_in_report"]) + (" (override)" if f["override"] in ("Y", "excluded") else "")
            rows.append([f["name"], f["member"], GRADE_ABBR.get(f["grade"], f["grade"]), ("Y, since " + f["yp_product_since"]) if f["yp_product"] == "Y" else ("unknown" if lapsed else "no"),
                         f["yp_eligible_flag"] or "–", g or "–", f["last_student_member_year"] or "–", f["first_grade_above_student_year"] or "–", status, "; ".join(auto), notes.get(f["name"], "")])
        out.append(table(["Name", "Member #", "Grade", "YP product (since)", "IEEE 'YP eligible' flag", "Graduation date on file", "Last year as Student Member", "First year at a higher grade", "Status used", "Data note", "Region 1 YP Chair"],
                         rows, cls="small", widths=[12, 10, 5.5, 8, 6, 9, 6.5, 6.5, 9, 14, 13.5]))
        out.append("<p class='note'>Grades: StM Student Member, GSM Graduate Student Member, M Member, SM Senior Member, LS Life Senior.</p>")
    path = os.path.join(args.out_dir, "yp_data_report.html")
    with open(path, "w") as fp:
        fp.write("\n".join(out))
    print("Wrote", path)
    if args.pdf:
        print("PDF", html_to_pdf(path, path.replace(".html", ".pdf")))


if __name__ == "__main__":
    main()
