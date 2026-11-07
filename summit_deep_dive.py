"""Deeper questions about one event's participants and their sections,
on top of the outputs of summit_engagement.py / section_impact.py.

  1. Would the newcomers have become this active anyway? Stratified
     permutation test and a logistic model with controls.
  2. Which metadata matters (permutation importance on a logistic model
     fitted to all YP members, attendance as one feature).
  3. Splits: GSM vs Member+ YPs; travel-grant recipients vs others.
  4. Events organized (contact email = participant email), which registration
     counts miss because organizers rarely register.
  5. YP activity in sections without a YP AG.
  6. What the OUs that participants joined did afterwards.
  7. "Paper" AGs: co-hosting only, YP share of registrants.
  8. Region-wide drift between the two windows.

Usage:
    python summit_deep_dive.py --data-dir /data/ieee/oua --vtools-dir /data/ieee/vtools \\
        --run-dir /data/ieee/oua/summit2025 --event 487732 --reference-date 2025-08-22 --window-days 347 \\
        --grants travel_grants_2025.txt
"""

import argparse
import datetime
import os
import re
from collections import Counter

import numpy as np
import pandas as pd

from engagement_report import load_registrations, link_emails_to_members, assign_person, parse_date
from summit_engagement import load_history, load_members, load_yp, norm_member, active
from section_impact import section_of_ou, yp_ag

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 60)


def logistic_fit(X, y, l2=1e-3, iters=50):
    """IRLS logistic regression with a small ridge penalty. X includes intercept."""
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ beta))
        W = p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + l2 * np.eye(X.shape[1])
        g = X.T @ (y - p) - l2 * beta
        step = np.linalg.solve(H, g)
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def log_loss(X, y, beta):
    p = np.clip(1 / (1 + np.exp(-X @ beta)), 1e-9, 1 - 1e-9)
    return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))


def norm_name(s):
    s = re.sub(r"[^a-z ]", " ", s.lower())
    parts = [p for p in s.split() if len(p) > 1]
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True); ap.add_argument("--vtools-dir", required=True); ap.add_argument("--run-dir", required=True)
    ap.add_argument("--event", required=True); ap.add_argument("--reference-date", required=True); ap.add_argument("--window-days", type=int, required=True)
    ap.add_argument("--grants"); ap.add_argument("--yp-overrides"); ap.add_argument("--yp-exclusions"); ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    ref = datetime.date.fromisoformat(args.reference_date); W = datetime.timedelta(days=args.window_days)
    b0, b1, a0, a1 = ref - W, ref - datetime.timedelta(days=1), ref + datetime.timedelta(days=1), ref + W
    rng = np.random.default_rng(args.seed)
    out_dir = os.path.join(args.run_dir, "deep"); os.makedirs(out_dir, exist_ok=True)

    p = pd.read_csv(os.path.join(args.run_dir, "participants.csv"), dtype=str, keep_default_na=False)
    c = pd.read_csv(os.path.join(args.run_dir, "comparison.csv"), dtype=str, keep_default_na=False)
    for df in (p, c):
        for col in ["events_before", "events_after", "local_before", "local_after", "new_positions_after", "new_ou_after", "tier_at_ref", "tier_after"]:
            df[col] = df[col].astype(int)
        df["any_after"] = (df["events_after"] > 0).astype(int)
        df["new_ou"] = (df["new_ou_after"] > 0).astype(int)
        df["first_vol"] = (df["new_volunteer_after"] == "yes").astype(int)
        df["gsm"] = df["grade"].str.contains("Graduate Student", case=False).astype(int)
        df["vol_before"] = (df["volunteer_before"] == "yes").astype(int)
        df["ev_before_bin"] = df["events_before_bin"]
    coh = p[p["member_record"].isin(["active member record", "member at the Summit, since lapsed"])].copy()
    cy = coh[coh["yp_status"] == "YP member"].copy()
    c = c[c["member_record"] == "active member record"].copy()

    # ------------------------------------------------------------ 1. permutation test
    print("=" * 100); print("1. WOULD THEY HAVE BECOME THIS ACTIVE ANYWAY?  (stratified permutation, 20,000 draws)")
    strata_keys = ["vol_before", "ev_before_bin", "gsm"]
    def strat(df): return list(zip(*[df[k] for k in strata_keys]))
    c["stratum"] = strat(c)
    pools = {k: g for k, g in c.groupby("stratum")}
    for label, group in (("YP attendees with no position at the Summit", cy[cy["tier_at_ref"] == 0]),
                         ("all YP attendees", cy),
                         ("YP attendees who were already officers", cy[cy["tier_at_ref"] > 0])):
        group = group.copy(); group["stratum"] = strat(group)
        rows = []
        for outcome in ("any_after", "new_ou", "first_vol"):
            observed = int(group[outcome].sum())
            expected = sum(pools[s][outcome].mean() if s in pools else 0 for s in group["stratum"])
            draws = np.zeros(20000, dtype=int)
            for s in group["stratum"]:
                pool = pools.get(s)
                if pool is None or len(pool) == 0:
                    continue
                draws += rng.choice(pool[outcome].values, size=20000, replace=True)
            pval = float(np.mean(draws >= observed))
            rows.append([outcome, len(group), observed, round(expected, 1), f"{np.percentile(draws, 2.5):.0f}–{np.percentile(draws, 97.5):.0f}", pval])
        print(f"\n  {label} (n={len(group)}); strata = volunteered before × events-before bin × GSM")
        print(pd.DataFrame(rows, columns=["outcome", "n", "observed", "expected from comparable YPs", "95% range of draws", "P(≥ observed by chance)"]).to_string(index=False))

    # ------------------------------------------------------------ 2. logistic model + permutation importance
    print("\n" + "=" * 100); print("2. LOGISTIC MODEL ON ALL R1/R2 YP MEMBERS (attendance as one feature) + PERMUTATION IMPORTANCE")
    allyp = pd.concat([cy.assign(attended=1), c.assign(attended=0)], ignore_index=True)
    allyp["ev_before"] = allyp["events_before"].clip(upper=20)
    allyp["log_ev_before"] = np.log1p(allyp["events_before"])
    allyp["tier_num"] = allyp["tier_at_ref"]
    allyp["r2"] = (allyp["region"] == "R2").astype(int)
    allyp["home_ag"] = (allyp["home_yp_ag"] == "yes").astype(int)
    feats = ["attended", "log_ev_before", "vol_before", "tier_num", "gsm", "r2", "home_ag"]
    X = np.column_stack([np.ones(len(allyp))] + [allyp[f].astype(float).values for f in feats])
    for outcome in ("any_after", "new_ou"):
        y = allyp[outcome].astype(float).values
        beta = logistic_fit(X, y)
        base = log_loss(X, y, beta)
        print(f"\n  outcome = {outcome}   (n={len(allyp):,}, positives={int(y.sum()):,})")
        print("   coefficient (log-odds) and odds ratio:")
        for f, b in zip(feats, beta[1:]):
            print(f"     {f:14s} {b:+.2f}   OR {np.exp(b):6.2f}")
        imp = []
        for j, f in enumerate(feats, start=1):
            losses = []
            for _ in range(5):
                Xp = X.copy(); Xp[:, j] = rng.permutation(Xp[:, j])
                losses.append(log_loss(Xp, y, beta) - base)
            imp.append((f, float(np.mean(losses))))
        print("   permutation importance (increase in log-loss when the feature is shuffled):")
        for f, v in sorted(imp, key=lambda x: -x[1]):
            print(f"     {f:14s} {v:.4f}")
        # attendee adjusted effect: predicted probability with attended=1 vs 0 for the attendees' own covariates
        Xa = X[allyp["attended"].values == 1].copy()
        p1 = 1 / (1 + np.exp(-Xa @ beta)); Xa[:, 1] = 0; p0 = 1 / (1 + np.exp(-Xa @ beta))
        print(f"   attendees' predicted rate with attendance: {100 * p1.mean():.0f}%; same people had they not attended: {100 * p0.mean():.0f}%; observed {100 * allyp[allyp.attended == 1][outcome].mean():.0f}%")

    # ------------------------------------------------------------ 3. splits: GSM vs Member+, grants
    print("\n" + "=" * 100); print("3. SPLITS")
    def summ(g):
        n = len(g)
        return [n, f"{100 * g['any_after'].mean():.0f}%" if n else "–", f"{g['events_after'].mean():.1f}" if n else "–",
                f"{100 * g['new_ou'].mean():.0f}%" if n else "–", f"{100 * g['first_vol'].mean():.0f}%" if n else "–"]
    cols = ["n", "≥1 event after", "events after, mean", "new-OU position", "first-ever volunteer"]
    rows = []
    for gsm, lab in ((1, "Graduate Student Member"), (0, "Member or higher")):
        for vb, vlab in ((0, "never volunteered"), (1, "volunteered before")):
            rows.append([lab, vlab, "YP attendees"] + summ(cy[(cy.gsm == gsm) & (cy.vol_before == vb)]))
            rows.append([lab, vlab, "other YPs, ≥1 event before"] + summ(c[(c.gsm == gsm) & (c.vol_before == vb) & (c.events_before > 0)]))
            rows.append([lab, vlab, "other YPs, all"] + summ(c[(c.gsm == gsm) & (c.vol_before == vb)]))
    print("\n  GSM vs Member+ (YP members only):"); print(pd.DataFrame(rows, columns=["grade", "prior volunteering", "group"] + cols).to_string(index=False))

    grant_names = []
    if args.grants:
        grant_names = [norm_name(l) for l in open(args.grants) if l.strip()]
    def matches_grant(name):
        parts = norm_name(name)
        if not parts: return False
        for g in grant_names:
            if parts[-1] == g[-1] and (parts[0] == g[0] or parts[0][:3] == g[0][:3]):
                return True
        return False
    p["grant"] = p["name"].map(matches_grant)
    coh["grant"] = coh["name"].map(matches_grant)
    matched = set(); 
    for g in grant_names:
        hit = [n for n in p["name"] if matches_grant(n) and norm_name(n)[-1] == g[-1]]
        if not hit: print(f"  grant recipient not found among attendees: {' '.join(g)}")
    rows = []
    for lab, g in (("travel grant", coh[coh.grant]), ("no grant", coh[~coh.grant])):
        rows.append([lab, "all"] + summ(g))
        rows.append([lab, "no position at the Summit"] + summ(g[g.tier_at_ref == 0]))
        rows.append([lab, "already an officer"] + summ(g[g.tier_at_ref > 0]))
    print("\n  Travel-grant recipients vs others (analysed attendees):"); print(pd.DataFrame(rows, columns=["grant", "subset"] + cols).to_string(index=False))
    print("  grant recipients analysed:", int(coh.grant.sum()), "| tickets:", Counter(coh[coh.grant]["ticket"]))

    # ------------------------------------------------------------ 4. events organized (contact email)
    print("\n" + "=" * 100); print("4. EVENTS ORGANIZED (event contact email = participant email), before → after")
    hosts = pd.read_csv(os.path.join(args.vtools_dir, "events_hosts.csv"), dtype=str, keep_default_na=False).drop_duplicates("Id")
    hosts["date"] = pd.to_datetime(hosts["start_time"], errors="coerce").dt.date
    hosts = hosts[(hosts["cancelled"] != "True") & (hosts["Id"] != args.event)]
    hosts["window"] = np.where((hosts["date"] >= b0) & (hosts["date"] <= b1), "before", np.where((hosts["date"] >= a0) & (hosts["date"] <= a1), "after", ""))
    reg = load_registrations(os.path.join(args.vtools_dir, "registrations.csv"))
    reg = assign_person(reg, link_emails_to_members(reg))
    members = load_members(os.path.join(args.data_dir, "members.csv"))
    emails_of = {}
    for person, email in zip(coh["person"], coh["email"]):
        emails_of.setdefault(person, set()).add(email.lower())
    for person, em in zip(reg["person"], reg["email"]):
        if person in emails_of and em: emails_of[person].add(em)
    org_rows = []
    for _, r in coh.iterrows():
        ems = emails_of.get(r["person"], set())
        mine = hosts[hosts["contact_email"].isin(ems) & (hosts["window"] != "")]
        org_rows.append([r["name"], r["tier_at_ref_name"], int((mine["window"] == "before").sum()), int((mine["window"] == "after").sum()),
                         r["events_before"], r["events_after"], "; ".join(mine[mine.window == "after"]["title"].str[:40].head(3))])
    org = pd.DataFrame(org_rows, columns=["name", "tier at Summit", "organized before", "organized after", "registered before", "registered after", "examples after"])
    print(org[(org["organized before"] + org["organized after"]) > 0].sort_values("organized after", ascending=False).to_string(index=False))
    print(f"  attendees who organized ≥1 event: before {(org['organized before'] > 0).sum()}, after {(org['organized after'] > 0).sum()}; total events organized {org['organized before'].sum()} → {org['organized after'].sum()}")
    org.to_csv(os.path.join(out_dir, "events_organized.csv"), index=False)

    # ------------------------------------------------------------ 5. YP activity in sections without an AG
    print("\n" + "=" * 100); print("5. YP ACTIVITY PER SECTION (any event of the section, not just AG events): distinct YP-member registrants, new YP product holders, YP officers")
    from summit_engagement import load_overrides
    ypd = load_yp(os.path.join(args.data_dir, "yp_products.csv"))
    yp = (set(ypd) | load_overrides(args.yp_overrides)) - load_overrides(args.yp_exclusions)
    ypd = pd.read_csv(os.path.join(args.data_dir, "yp_products.csv"), dtype=str, keep_default_na=False)
    ypd["member"] = ypd["member/customer number"].map(norm_member); ypd["start"] = ypd["asset start date"].map(parse_date)
    ypd = ypd.drop_duplicates("member")
    sec = pd.read_csv(os.path.join(args.run_dir, "sections.csv"))
    ev = pd.read_csv(os.path.join(args.run_dir, "section_events.csv"), dtype=str, keep_default_na=False)
    reg_by_event = reg.groupby("Event Id")["person"].agg(set)
    positions = load_history(os.path.join(args.data_dir, "volunteer_history.csv"))
    section_names = list(sec["section"])
    rows = []
    for _, s in sec.iterrows():
        e_sec = ev[(ev["geo"] == s["geo"]) & (ev["primary_host"] == "True")]
        r = {"section": s["section"], "participants": int(s["participants"]), "has_yp_ag": "yes" if (s["yp_ag_officers_at_ref"] > 0 or s["yp_ag_events_before"] + s["yp_ag_events_after"] > 0) else "no"}
        for w in ("before", "after"):
            ids = e_sec[e_sec["window"] == w]["Id"]
            people_ = set().union(*[reg_by_event.get(i, set()) for i in ids]) if len(ids) else set()
            r[f"yp_registrants_{w}"] = len({x for x in people_ if x in yp})
            r[f"registrants_{w}"] = len(people_)
            lo, hi = (b0, b1) if w == "before" else (a0, a1)
            r[f"new_yp_holders_{w}"] = int(((ypd["section"] == s["section"]) & (ypd["start"] >= lo) & (ypd["start"] <= hi)).sum())
        # YP members holding any position in the section's OUs at ref / at end
        for lab, day in (("at_ref", ref), ("at_end", a1)):
            n = 0
            for member, seats in positions.items():
                if member in yp and any(active(seat, day) and section_of_ou(seat["ou"], section_names) == s["section"] for seat in seats):
                    n += 1
            r[f"yp_officers_{lab}"] = n
        rows.append(r)
    ysec = pd.DataFrame(rows)
    ysec["grp"] = np.where(ysec["participants"] > 0, "with attendee", "without")
    ysec.to_csv(os.path.join(out_dir, "section_yp_activity.csv"), index=False)
    for ag in ("no", "yes"):
        print(f"\n  sections {'WITHOUT' if ag == 'no' else 'with'} a YP AG:")
        for g, sub in ysec[ysec.has_yp_ag == ag].groupby("grp"):
            print(f"    {g:14s} n={len(sub):2d}  YP registrants at section events {sub.yp_registrants_before.sum()}->{sub.yp_registrants_after.sum()} (median per section {sub.yp_registrants_before.median():.0f}->{sub.yp_registrants_after.median():.0f}; up/same/down {(sub.yp_registrants_after > sub.yp_registrants_before).sum()}/{(sub.yp_registrants_after == sub.yp_registrants_before).sum()}/{(sub.yp_registrants_after < sub.yp_registrants_before).sum()})  "
                  f"new YP product holders {sub.new_yp_holders_before.sum()}->{sub.new_yp_holders_after.sum()}  YP officers {sub.yp_officers_at_ref.sum()}->{sub.yp_officers_at_end.sum()}")
        print(ysec[ysec.has_yp_ag == ag][["section", "grp", "yp_registrants_before", "yp_registrants_after", "registrants_before", "registrants_after", "new_yp_holders_before", "new_yp_holders_after", "yp_officers_at_ref", "yp_officers_at_end"]].sort_values(["grp", "section"]).to_string(index=False))

    # ------------------------------------------------------------ 6. OUs that participants joined
    print("\n" + "=" * 100); print("6. THE OUs PARTICIPANTS JOINED AFTER THE SUMMIT: events run by that OU (primary host), before → after")
    joined = []
    for _, r in coh[coh.new_ou_after > 0].iterrows():
        for item in r["new_ou_detail"].split(";"):
            m = re.match(r"\s*(.+?) @ (.+?) \((\d{4}-\d{2}-\d{2})\)", item)
            if m: joined.append((r["name"], m.group(2).strip(), m.group(1).strip(), m.group(3)))
    rows = []
    for name, ou, title, start in joined:
        h = hosts[hosts["primary_host_name"].str.strip() == ou]
        rb, ra = h[h.window == "before"], h[h.window == "after"]
        regb = sum(len(reg_by_event.get(i, set())) for i in rb["Id"]); rega = sum(len(reg_by_event.get(i, set())) for i in ra["Id"])
        rows.append([name, title, ou, start, len(rb), len(ra), regb, rega, int(pd.to_numeric(rb["ieee_attending"], errors="coerce").fillna(0).sum() + pd.to_numeric(rb["guests_attending"], errors="coerce").fillna(0).sum()),
                     int(pd.to_numeric(ra["ieee_attending"], errors="coerce").fillna(0).sum() + pd.to_numeric(ra["guests_attending"], errors="coerce").fillna(0).sum())])
    print(pd.DataFrame(rows, columns=["participant", "title", "OU", "start", "events before", "events after", "registrations before", "registrations after", "reported before", "reported after"]).to_string(index=False))

    # ------------------------------------------------------------ 7. paper AGs
    print("\n" + "=" * 100); print("7. YP AFFINITY GROUPS: run vs co-host only, officers, and YP share of registrants at their events vs the section's other events")
    ag_names = sorted({n for n in hosts["primary_host_name"] if yp_ag.search(n)} | {n.strip() for s in hosts["cohost_names"] for n in s.split(";") if yp_ag.search(n)})
    rows = []
    for ag in ag_names:
        section = section_of_ou(ag, section_names)
        if not section: continue
        run = hosts[(hosts["primary_host_name"].str.strip() == ag) & (hosts.window != "")]
        co = hosts[hosts["cohost_names"].str.contains(re.escape(ag)) & (hosts.window != "") & (hosts["primary_host_name"].str.strip() != ag)]
        def yp_share(ids):
            people_ = set().union(*[reg_by_event.get(i, set()) for i in ids]) if len(ids) else set()
            return (len({x for x in people_ if x in yp}), len(people_))
        yr, nr = yp_share(run["Id"]); yc, nc = yp_share(co["Id"])
        geo = sec[sec.section == section]["geo"].iloc[0]
        other = ev[(ev["geo"] == geo) & (ev["primary_host"] == "True") & (ev["own_yp_ag_hosts"] != "True")]
        yo, no = yp_share(other["Id"])
        officers = sum(1 for seats in positions.values() for seat in seats if seat["ou"].strip() == ag and active(seat, a1))
        officers_ref = sum(1 for seats in positions.values() for seat in seats if seat["ou"].strip() == ag and active(seat, ref))
        desc_yp = int(hosts.loc[hosts["Id"].isin(co["Id"]), "title"].str.contains("young professional|\\bYP\\b", case=False, regex=True).sum())
        rows.append([ag.replace(" Section", "").replace("Affinity Group", "AG"), officers_ref, officers, len(run), len(co), f"{yr}/{nr} ({100 * yr / nr:.0f}%)" if nr else "–",
                     f"{yc}/{nc} ({100 * yc / nc:.0f}%)" if nc else "–", f"{yo}/{no} ({100 * yo / no:.0f}%)" if no else "–", desc_yp])
    agdf = pd.DataFrame(rows, columns=["YP AG", "officers at Summit", "officers at end", "AG-run events", "co-hosted events", "YP share of registrants, AG-run", "YP share, co-hosted", "YP share, section's other events", "co-hosted with YP in title"])
    print(agdf.sort_values("AG-run events", ascending=False).to_string(index=False))
    agdf.to_csv(os.path.join(out_dir, "yp_ags.csv"), index=False)

    # ------------------------------------------------------------ 8. drift
    print("\n" + "=" * 100); print("8. REGION-WIDE DRIFT BETWEEN THE WINDOWS (all R1/R2 events)")
    for w in ("before", "after"):
        h = hosts[hosts.window == w]
        people_ = set().union(*[reg_by_event.get(i, set()) for i in h["Id"]])
        print(f"  {w:6s}: events {len(h):5d}  registrations {sum(len(reg_by_event.get(i, set())) for i in h['Id']):6d}  distinct registrants {len(people_):6d}  YP-member registrants {len({x for x in people_ if x in yp}):5d}  reported attendance {int(pd.to_numeric(h['ieee_attending'], errors='coerce').fillna(0).sum() + pd.to_numeric(h['guests_attending'], errors='coerce').fillna(0).sum()):6d}")


if __name__ == "__main__":
    main()
