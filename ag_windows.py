"""YP affinity-group activity in a fixed calendar window, year over year.

Counts events whose PRIMARY host is a Region 1/2 YP affinity group (from
vtools_registrations.py enrich -> events_hosts.csv) in the same calendar span
in several years, and splits the AGs by whether one of their officers was at
the Summit of a given cohort (participants.csv from summit_engagement.py).

    python ag_windows.py --vtools-dir output/data2026/vtools \
        --window 08-07 11-05 --years 2024 2025 2026 \
        --cohort 2026=output/summit2026/participants.csv \
        --cohort 2025=output/summit2025_90d/participants.csv -o output/summit2026/ag_windows.csv
"""
import argparse
import os
import re

import pandas as pd


def norm(name):
    return re.sub(r"[\s,.]+", "", name).lower().replace("affn", "affinitygroup").replace("jt", "")


def is_yp_ag(name):
    return bool(re.search(r"(^|[\s,/])YP\b|Young Prof", name))


def ag_officers(participants_csv):
    """AG names (normalized) whose officer is in the cohort, from positions_at_ref_detail."""
    p = pd.read_csv(participants_csv, dtype=str, keep_default_na=False)
    tracked = p[p["member_record"].isin(["active member record", "member at the Summit, since lapsed"])]
    ags = set()
    for det in tracked["positions_at_ref_detail"]:
        for seat in det.split(";"):
            if "@" in seat:
                ou = seat.split("@", 1)[1].strip()
                if "Affinity" in ou or "Affn" in ou:
                    if is_yp_ag(ou):
                        ags.add(norm(ou))
    return ags


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vtools-dir", required=True)
    ap.add_argument("--window", nargs=2, metavar=("MM-DD", "MM-DD"), required=True, help="start and end (inclusive) within each year")
    ap.add_argument("--years", nargs="+", type=int, required=True)
    ap.add_argument("--cohort", action="append", default=[], help="YEAR=participants.csv; AGs are split by an officer attending that year's Summit")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()

    hosts = pd.read_csv(os.path.join(args.vtools_dir, "events_hosts.csv"), dtype=str, keep_default_na=False)
    hosts["date"] = pd.to_datetime(hosts["start_time"].str[:10], errors="coerce")
    hosts = hosts[(hosts["cancelled"] != "True") & hosts["primary_host_region"].isin(["R1", "R2"])]
    ag = hosts[hosts["primary_host_type"].str.contains("Affinity") & hosts["primary_host_name"].map(is_yp_ag)].copy()
    ag["ag"] = ag["primary_host_name"].map(norm)

    reg = pd.read_csv(os.path.join(args.vtools_dir, "registrations.csv"), dtype=str, keep_default_na=False)
    reg = reg[reg.get("Status", "Registered") != "Cancelled"] if "Status" in reg else reg
    reg["person"] = reg["Member Number"].str.lstrip("0").where(reg["Member Number"].str.strip() != "", reg["Email"].str.lower())

    cohorts = {int(k): ag_officers(v) for k, v in (c.split("=", 1) for c in args.cohort)}

    rows = []
    for year in args.years:
        start = pd.Timestamp(f"{year}-{args.window[0]}")
        end = pd.Timestamp(f"{year}-{args.window[1]}")
        w = ag[(ag["date"] >= start) & (ag["date"] <= end)]
        r = reg[reg["Event Id"].isin(w["Id"])]
        rows.append({"year": year, "group": "all R1/R2 YP AGs", "ags_active": w["ag"].nunique(), "events": len(w),
                     "registrations": len(r), "registrants": r["person"].nunique(),
                     "reported_attendance": pd.to_numeric(w["ieee_attending"], errors="coerce").fillna(0).sum()
                     + pd.to_numeric(w["guests_attending"], errors="coerce").fillna(0).sum()})
        for cy, ags in cohorts.items():
            for label, mask in (("officer at the %d Summit" % cy, w["ag"].isin(ags)), ("no officer at the %d Summit" % cy, ~w["ag"].isin(ags))):
                ww = w[mask]
                rr = reg[reg["Event Id"].isin(ww["Id"])]
                rows.append({"year": year, "group": label, "ags_in_group": len(ags) if mask.any() or True else 0,
                             "ags_active": ww["ag"].nunique(), "events": len(ww),
                             "registrations": len(rr), "registrants": rr["person"].nunique(),
                             "reported_attendance": pd.to_numeric(ww["ieee_attending"], errors="coerce").fillna(0).sum()
                             + pd.to_numeric(ww["guests_attending"], errors="coerce").fillna(0).sum()})
    out = pd.DataFrame(rows)
    per_ag = (ag[(ag["date"].dt.strftime("%m-%d") >= args.window[0]) & (ag["date"].dt.strftime("%m-%d") <= args.window[1])]
              .assign(year=lambda d: d["date"].dt.year)
              .groupby(["primary_host_name", "year"]).size().unstack(fill_value=0).reset_index())
    for cy, ags in cohorts.items():
        per_ag["officer at %d Summit" % cy] = per_ag["primary_host_name"].map(norm).isin(ags)
    pd.set_option("display.width", 250)
    print(out.to_string(index=False))
    print()
    print(per_ag.sort_values(per_ag.columns[-len(cohorts) - 1], ascending=False).to_string(index=False))
    if args.output:
        out.to_csv(args.output, index=False)
        per_ag.to_csv(args.output.replace(".csv", "_per_ag.csv"), index=False)
        print("wrote", args.output)


if __name__ == "__main__":
    main()
