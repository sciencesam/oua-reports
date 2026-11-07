# oua-reports

Reporting scripts built on the IEEE OU Analytics Tableau API: member lookups
(YP / WIE status), section & chapter officer rosters (HTML + PDF), per-area
roster batches, and member → section-officer contact sheets. Aimed at IEEE
Region 1 & 2 section / chapter / Young Professionals volunteer leaders.

This repository is private for now. It is intended to move to
`opensource.ieee.org/vtoolslabs` alongside `oua-explorer` once it is ready.

> Companion to [`oua-explorer`](https://opensource.ieee.org/vtoolslabs/oua-explorer):
> the explorer *browses* OU Analytics views; **oua-reports** turns them into
> finished, shareable reports.

> [!IMPORTANT]
> **The code is shareable; its outputs are not.** The reports these scripts
> produce contain real member names, emails, and member numbers. Keep every
> generated file local — the `.gitignore` keeps member lists, outputs, and
> your token out of git. See [Privacy & data handling](#privacy--data-handling).

## Setup

1. **Personal access token.** In OU Analytics: account menu → My Account
   Settings → Personal Access Tokens → create one. Save it as JSON following
   `token-template.json` (these docs assume `my_token.json`). Tokens expire if
   unused; regenerate if a script fails to authenticate.

2. **Python environment.**

   ```
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **(For PDFs)** Google Chrome, used headless to render roster PDFs. Set
   `CHROME_PATH` if it's installed somewhere non-standard.

Every script takes the token file first, supports `-o` to write a CSV
(default: stdout), and `-l DEBUG` for troubleshooting.

## Scripts

### `member_lookup.py` — member info + YP / WIE status

```
python member_lookup.py my_token.json members.txt -o results.csv
python member_lookup.py my_token.json 12345678,23456789
```

Columns: Member Number, First Name, Last Name, Email, Section, YP Member,
YP Eligible, WIE Member, Source. YP/WIE = holds the Active MEMYP060 /
MEMWIE050 product; add others to the `affinity_products` dict.

### `member_section_contacts.py` — members + their section officers' emails

```
python member_section_contacts.py my_token.json members.csv -o contacts.csv
```

Per member: First/Last Name, Section, a **To** column (the member) and a
**Cc** column (their section's Chair, Vice Chair, Treasurer — deduped) that
map straight onto an email to the member cc'ing the section folks, then the
individual officer columns. The 28 "Secretary/Treasurer" sections fill
Treasurer from that combined role.

- The section-officer query is **cached for a week** because the underlying
  view is slow (~2 min); only the first run each week pays it. Force a
  refresh with `--refresh-officers`.
- Members missing from the memberships view (out of R1/R2, or with no active
  society product) are resolved via the volunteer view; that fallback is on
  by default but adds a slow query — skip it with `--no-fallback`.

### `section_officers.py` — section Chairs & Vice Chairs

```
python section_officers.py my_token.json examples/r1_r2_sections.txt -o officers.csv
python section_officers.py my_token.json examples/areas.csv --area "Finger Lakes"
python section_officers.py my_token.json                 # all sections worldwide
```

Columns: Section, Position, First/Last Name, Email, Status (Filled/Vacant).
Extend the `positions` list for Secretary/Treasurer/etc.

### `chapter_officers.py` — chapters by society with Chapter Chair / Vice Chair

```
python chapter_officers.py my_token.json examples/finger_lakes_sections.txt -o chapters.csv
python chapter_officers.py my_token.json examples/areas.csv --area "Finger Lakes"
```

Every section & joint chapter in the given sections (student branches
excluded), one row per chapter per member society. Columns include Society,
Chapter, Chair, Chair Email, Vice Chair, Vice Chair Email, plus Previous
Chair / Previous Vice Chair with Email and Term; unfilled show `(vacant)`
and the Previous columns name the most recent past holder, so the roster
still gives you someone to contact. Past positions come from the OU
Analytics copy of the volunteer workbook, selected through the Tableau
parameter `Officer Position Status  Parameter` (two spaces) = `Past`,
scoped to the regions of the chapters being reported.

### `section_leadership.py` — full leadership roster for one section

```
python section_leadership.py my_token.json "Syracuse Section" --pdf
python section_leadership.py my_token.json "Syracuse Section" --history-csv history.csv
```

Every officer position of the section itself, its chapters & joint
chapters, and its affinity groups, written as a CSV + styled HTML report
(+ optional PDF) with copy-paste email lists (all officers, and per
group) for a listserv. Duplicate volunteer rows for the same person are
merged; genuine co-officers are all listed.

Vacant positions name the **last known holder** and the month their term
ended, so a volunteer who wasn't renewed in vTools isn't silently
dropped; officers whose listed term has already expired are flagged in
red as an early warning. The API's volunteer views export only *active*
positions (the workbook's history sheet isn't published), so last
holders come from `lists/officer_history_cache.json`, which every run
updates — backfill works from the second run onward, and a history
crosstab downloaded manually from the OU Analytics web UI can seed it
further back via `--history-csv`. Backfilled people stay out of the
email lists.

### `area_chapter_rosters.py` — one chapter roster document per area

```
python area_chapter_rosters.py my_token.json examples/areas.csv -d output/rosters --pdf
```

Runs the chapter report for every row of the area CSV, writing a CSV + HTML
(+ optional PDF) per area. Officer data is fetched once and reused.

### `chapter_roster.py` / `section_roster.py` — printable HTML/PDF rosters

Format the officer CSVs into styled, print-ready documents (no API calls):

```
python chapter_roster.py chapters.csv -t "Western (Finger Lakes) Area" -o roster.html --pdf
python section_roster.py officers.csv -t "IEEE Regions 1 & 2" -o roster.html --pdf --email-lists
```

Vacancies in red, `mailto:` links, clean page breaks. `--pdf` renders via
headless Chrome. `section_roster.py --email-lists` appends copy-paste-ready
address blocks, one intact email per line.

## Input formats

- **Member list:** one member number per line (commas also accepted).
- **Section list:** one section name per line, spelled exactly as OU
  Analytics does (e.g. `Mohawk Valley Section`, not `Mohawk Section`).
- **Area CSV:** `Region,Area,Section,Section,…` per row; select with
  `--area` (substring), disambiguate with `--region`; no `--area` lists the
  available areas. Non-personal examples live in `examples/`.

## Privacy & data handling

The code is meant to be shared; the data these scripts read and write is not.

- **Never committed** (enforced by `.gitignore`): `my_token.json` / any
  `*token*.json` except the template; member-number input lists; every
  generated CSV / HTML / PDF; the officers cache; `docs-internal/`.
- **Safe to commit:** the code, this README, `requirements.txt`, the
  placeholder `token-template.json`, and the section/area *name* lists in
  `examples/` (OU names only).
- Put real inputs and outputs under `output/` (git-ignored). Before your
  first commit, run `git ls-files` and confirm it shows only code, docs,
  examples, and the placeholder template.

## Data notes / gotchas

- **R1/R2 scope:** the memberships view returns only Region 1 & 2 members.
  Lookups outside R1/R2 rely on the volunteer-view fallback.
- **Slow view:** the volunteer positions view exports in ~2 minutes
  regardless of filter; that's why officer results are cached.
- **Trailing spaces & spacing:** view headers/filters have stray trailing
  spaces and chapter OU names vary in comma spacing; the scripts normalize
  these.
- **Vacant placeholders:** the volunteer view emits a row for every defined
  position even when unfilled; scripts treat these as vacant.

## Roadmap

- Switch member lookups to the **Members and Affiliates** detail view
  (full member coverage incl. base-only members, plus the YP Eligible flag),
  which requires the mandatory `Volunteer SPO Name` filter set to one of the
  token owner's volunteer-role OUs.
- YP engagement metrics (post-Summit involvement) and an IEEE content
  recommendation engine are tracked separately (internal docs, not public).

## Event participation (vTools)

### Engagement pipeline

The post-event engagement analysis is a chain of scripts. Each one reads the
previous one's files; only the first two talk to OU Analytics or vTools, the
rest work from the local CSVs. One example per step (details and the other
options follow in the per-script sections below):

1. `oua_bulk_export.py` — three population CSVs from OU Analytics
   (`volunteer_history.csv`, `members.csv`, `yp_products.csv`):

   ```
   python oua_bulk_export.py my_token.json --data-dir /data/ieee/oua
   ```

2. `vtools_registrations.py` — event inventory and registrations from vTools:

   ```
   python vtools_registrations.py --data-dir /data/ieee/vtools inventory --regions R1,R2 --start 2024-09-01
   python vtools_registrations.py --data-dir /data/ieee/vtools set-cookie --from-curl my_vtools_curl.txt
   python vtools_registrations.py --data-dir /data/ieee/vtools harvest --regions R1,R2 --start 2024-09-01
   python vtools_registrations.py --data-dir /data/ieee/vtools enrich
   python vtools_registrations.py --data-dir /data/ieee/vtools merge -o /data/ieee/vtools/registrations.csv
   ```

   (`set-cookie` is the alternative to the interactive sign-in: paste a
   DevTools "Copy as cURL" of a signed-in vTools request into
   `my_vtools_curl.txt`, which is git-ignored.)

3. `summit_engagement.py` — the attendee cohort before vs after, against
   comparable YPs (`participants.csv`, `comparison.csv`, `report.html`):

   ```
   python summit_engagement.py --data-dir /data/ieee/oua \
       --registrations /data/ieee/vtools/registrations.csv \
       --event 487732 --out-dir /data/ieee/oua/summit2025
   ```

4. `section_impact.py` — the same windows per section (`sections.csv`,
   `section_events.csv`, `section_positions.csv`):

   ```
   python section_impact.py --data-dir /data/ieee/oua --inventory /data/ieee/vtools/events_inventory.csv \
       --registrations /data/ieee/vtools/registrations.csv --participants /data/ieee/oua/summit2025/participants.csv \
       --event 487732 --reference-date 2025-08-22 --window-days 347 --out-dir /data/ieee/oua/summit2025
   ```

5. `summit_deep_dive.py` — the `deep/` files the final report draws on:

   ```
   python summit_deep_dive.py --data-dir /data/ieee/oua --vtools-dir /data/ieee/vtools \
       --run-dir /data/ieee/oua/summit2025 --event 487732 --reference-date 2025-08-22 --window-days 347 \
       --grants travel_grants_2025.txt
   ```

6. `summit_report.py` and `yp_data_report.py` — the final HTML/PDF reports:

   ```
   python summit_report.py --out-dir output/summit2025 --reference-date 2025-08-22 --window-days 347 \
       --event-name "2025 YP Summit" --attended 62 --pdf
   python yp_data_report.py --out-dir output/summit2025 --pdf
   ```

`engagement_report.py` is the earlier, API-driven per-person version of step
3; it is still usable on its own.

### `vtools_registrations.py` — collect vTools event registrations by region and date range

```
python vtools_registrations.py probe 551815
python vtools_registrations.py --data-dir /data/ieee/vtools harvest --regions R1,R2 --start 2024-09-09
python vtools_registrations.py --data-dir /data/ieee/vtools merge -o /data/ieee/vtools/registrations.csv
python vtools_registrations.py --data-dir /data/ieee/vtools forget
```

Three steps. `harvest` builds an event inventory from the public vTools
export (month by month per region, no login) and then downloads each
event's registrant CSV, which is the "Download CSV" link on the event's
attendance page (`/tego_/event/attendance/export_to_csv/<id>`), the only
authenticated URL it ever requests. `merge` joins every downloaded CSV with
the event's title, date, host OU, section and region into one file.
`probe` fetches a single event first so you can see the columns.

- **Sign-in** drives the real IEEE login form with your username and
  password (prompted, never stored) and keeps the resulting vTools session
  cookie in `<data-dir>/.vtools_cookie` (mode 600). `forget` deletes it.
  You need a vTools role that can view registrants for the events in scope
  (a Region role covers the whole region).
- **Politeness**: a few workers share one rate cap (`--rate`, default 2
  requests/second in total), back off on 429/502/503/504, and abort after
  repeated server errors. HTTP 500 from the export URL is normal: it means
  the event uses external registration, and it is recorded as
  "no registration" without retrying.
- **Host structure**: `enrich` fetches every inventory event from the public Events List API
  (JSON, no login) and writes `events_hosts.csv` (primary host, co-hosts with their sections,
  reported attendance, contact email) plus `events_raw.jsonl` with the full record; the CSV
  export only gives an alphabetical host list. `inventory` rebuilds the event inventory alone,
  merging host lists across the per-region exports.
- **Resumable**: rerunning skips events already downloaded. Keep the data
  directory on the machine with room; the CSVs contain names, emails,
  phones and member numbers.

### `engagement_report.py` — before/after activity of one event's attendees

```
python engagement_report.py my_token.json registrations.csv --event 487732 -o summit2025.csv
python engagement_report.py my_token.json registrations.csv --cohort members.txt --reference-date 2025-08-22
```

Takes the registered people of one event (cancelled registrations are
dropped) and reports, per person and in aggregate: distinct other events
registered for before and after the event date, volunteer positions held
before and started after (from the OU Analytics volunteer history, active
and past), whether they became a brand-new volunteer, and current grade,
IEEE status and renew year (from Members and Affiliates). People are
matched by member number, then by email, and email-only registrants are
resolved to member numbers through later registrations or Members and
Affiliates. A baseline group (people active in the year before who did not
attend) is summarized alongside for comparison; over a year of data that
group is tens of thousands of people, so use `--baseline-sample 500` (fixed
seed) to keep the OU Analytics lookups to minutes. Needs `pandas`.

### `oua_bulk_export.py` — population exports for the engagement analysis

```
python oua_bulk_export.py my_token.json --data-dir /data/ieee/oua --scope "Northeastern USA - Region 1,Eastern USA - Region 2"
```

Three one-shot CSVs (personal data; keep them local): every Active and Past
volunteer position of the given regions' volunteers, every member in the
listed Region OUs (Members and Affiliates; note it lists *active* members
only), and every active IEEE Young Professionals product row. Each export
signs in afresh, because a large export invalidates the session for the
next request. Rerun before each analysis to refresh.

### `summit_engagement.py` — attendees before vs after, against comparable YPs

```
python summit_engagement.py --data-dir /data/ieee/oua --registrations registrations.csv --event 487732 \
    --attendees attendees_2025.csv --yp-overrides yp_overrides.csv --yp-exclusions yp_exclusions.csv \
    --grants grants_2025.csv --speakers speakers.json --out-dir output/summit2025
```

No API calls; reads the registrations file and the bulk exports (`members.csv`, and
`members_inactive.csv` when present, so that attendees who let their membership lapse after the
event are still followed; `volunteer_history.csv`; `yp_products.csv`) and writes `participants.csv`
(one row per registrant or listed attendee), `comparison.csv` (every other Region 1/2 YP member) and
a working `report.html`. Metrics keep their units (events registered for, own-section events, new
roles as "new OU" vs "new title", highest volunteer tier, events organized); there is no combined
score. `--attendees` takes the organizers' attendance sheet (CSV with Member Number / First Name /
Last Name) so the cohort is the people actually present, walk-ins included by member number.
YP status = holds the YP product, or is listed in `--yp-overrides`, minus `--yp-exclusions`; the
product is exported for active members only, so lapsed attendees are "unknown" unless overridden.
Windows are the same calendar span on both sides (`--before mirror`, default, or `year-earlier`),
so a fresh event (e.g. the 2026 Summit) only gets a meaningful "after" side once the registrations
file extends a year past it.

### `section_impact.py`, `summit_deep_dive.py` and `summit_report.py` — section-level impact and the final report

```
python section_impact.py --data-dir /data/ieee/oua --inventory events_inventory.csv --registrations registrations.csv \
    --participants output/summit2025/participants.csv --event 487732 --reference-date 2025-08-22 --window-days 347 \
    --yp-overrides yp_overrides.csv --yp-exclusions yp_exclusions.csv --out-dir output/summit2025
python summit_report.py --out-dir output/summit2025 --reference-date 2025-08-22 --window-days 347 --title "2025 YP Summit" --pdf
```

`section_impact.py` counts, for every Region 1/2 section in the same windows, events by primary host
(from `events_hosts.csv`, i.e. the vTools API host structure), registrants, officer positions, and the
section's own YP affinity group events split into AG-as-primary-host vs co-hosted, with registrants,
YP-member registrants and reported attendance (`sections.csv`, `section_events.csv`,
`section_positions.csv`). `summit_deep_dive.py` writes the `deep/` files the report draws on
(events organized via contact email, YP AG officers on the event date, monthly activity, the
per-attendee YP facts). `summit_report.py` assembles everything into `summit_report.{html,pdf}`:
summary; 1 who attended; 2 what the tracked attendees did (tiles lead with the tracked YPs); 3 the
comparison with similar YPs and with YP AG officers who did not attend; 4 YP affinity groups grouped
by their connection to the event (officer attended / gained an officer / section had another attendee
/ none); 5 paper affinity groups (two tables, with vs without an attendee); 6 caveats; Appendix A
(tracked attendees, those not tracked with what the inactive records show, those who lapsed after
the event); Appendix B (how each attendee's YP status was determined, with "(override)" labels).

### `yp_data_report.py` — draft report on IEEE's Young Professionals records

```
python yp_data_report.py --out-dir output/summit2025 --pdf
```

Explains, for a Region/MGA reader, why counts of Young Professionals built on IEEE's data are
unreliable: the YP product is never removed (1,092 of 11,031 holders in R1/R2 first held a grade above
Student Member more than 15 years ago), the "YP eligible" flag does not follow the 15-year rule, the
graduation date is a free field, enrollment is incomplete, the product is visible for active members
only, and the membership views hold current holders only (no history). Only Student Member counts as
a student grade; Graduate Student Members already hold a first degree. Figures are constants verified
against the OU Analytics exports and stated in the script; the appendix is the per-attendee
determination table from `deep/attendee_yp_facts.csv` with neutral confirmations only where the
records need explaining. `methods_notes.html` (built inline in the session) holds the overlaid
histograms for the matching rules and member activity thresholds.

