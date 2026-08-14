# oua-reports

Reporting scripts built on the IEEE OU Analytics Tableau API: member lookups
(YP / WIE status), section & chapter officer rosters (HTML + PDF), per-area
roster batches, and member → section-officer contact sheets. Aimed at IEEE
Region 1 & 2 section / chapter / Young Professionals volunteer leaders.

> Companion to [`oua-explorer`](https://opensource.ieee.org/vtoolslabs/oua-explorer):
> the explorer *browses* OU Analytics views; **oua-reports** turns them into
> finished, shareable reports.

> [!IMPORTANT]
> **This repo is public; its outputs are not.** The reports these scripts
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
python member_lookup.py my_token.json 93874105,08084980
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
Chapter, Chair, Chair Email, Vice Chair, Vice Chair Email; unfilled show
`(vacant)`.

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

This repo is public; the data these scripts read and write is not.

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
