"""One-shot bulk exports from OU Analytics for population-level analysis.

Writes three CSVs into --data-dir (all personal data; keep local):

  volunteer_history.csv   every Active and Past volunteer position in the
                          given regions (view "Volunteer Positions" of the
                          OU Analytics volunteer workbook)
  members.csv             one row per member in each of the token owner's
                          Region-level OUs (Members and Affiliates Detail)
  yp_products.csv         every IEEE Young Professionals product row
                          (memberships detail view; row-level scoped to the
                          token owner's regions)
  members_inactive.csv    (--only inactive, opt-in: ~200k rows) former
                          members (IEEE Status Inactive/Arrears) in the
                          same OUs, used to track attendees who lapsed

Usage:

    python oua_bulk_export.py my_token.json --data-dir /data/ieee/oua
    python oua_bulk_export.py my_token.json --data-dir /data/ieee/oua --only members
"""

import argparse
import csv
import io
import logging
import os
import sys
import time

import tableauserverclient as TSC

from member_lookup import get_server, normalize_header
from engagement_report import (history_view_id, members_view_id, status_parameter,
                               all_ou_types, token_owner_scope)

products_view_id = "d5f4ab69-187d-4c65-a6b4-1a3761318438"


def export_rows(token_file, view_id, filters):
    """A fresh sign-in per export: a large CSV export invalidates the
    session for the next request (observed 401 right after ~25 s exports)."""
    server = get_server(token_file)
    view = server.views.get_by_id(view_id)
    options = TSC.CSVRequestOptions()
    for name, value in filters.items():
        options.vf(name, value)
    started = time.time()
    server.views.populate_csv(view, options)
    text = b"".join(view.csv).decode("utf-8-sig")
    rows = [{normalize_header(k): v.strip() for k, v in row.items() if k is not None}
            for row in csv.DictReader(io.StringIO(text))]
    logging.info("%s %s -> %d rows in %.0fs", view_id[:8], filters, len(rows), time.time() - started)
    return rows


def write_csv(path, rows):
    if not rows:
        logging.warning("No rows for %s; not written", path)
        return
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with open(path, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(path, 0o600)
    print(f"Wrote {len(rows)} rows to {path}")


def export_history(token_file, regions, path):
    rows = []
    for region in regions:
        rows += export_rows(token_file, history_view_id, {
            "OU Type": all_ou_types, "Volunteer Region": region, status_parameter: "(All)"})
    write_csv(path, rows)


def export_members(token_file, path, scope, status=None):
    """status=None exports the view's default (Active members only);
    "Inactive,Arrears" exports former members (the 'IEEE Status' filter)."""
    rows = []
    for ou in scope or token_owner_scope(get_server(token_file)):
        filters = {"Volunteer SPO Name": ou}
        if status:
            filters["IEEE Status"] = status
        rows += export_rows(token_file, members_view_id, filters)
    write_csv(path, rows)


def export_yp_products(token_file, path):
    rows = export_rows(token_file, products_view_id, {"Product Name": "IEEE Young Professionals"})
    write_csv(path, rows)


def main():
    parser = argparse.ArgumentParser(description="Bulk OU Analytics exports for population analysis")
    parser.add_argument("token_file")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--regions", default="R1,R2", help="volunteer regions for the history export")
    parser.add_argument("--scope", help="comma-separated 'Volunteer SPO Name' values for the members "
                        "export (default: discovered from the token owner's Region roles)")
    parser.add_argument("--only", choices=["history", "members", "inactive", "yp"], action="append",
                        help="run only these exports (repeatable)")
    parser.add_argument("-l", "--loglevel", default="INFO", choices=["ERROR", "WARNING", "INFO", "DEBUG"])
    args = parser.parse_args()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=getattr(logging, args.loglevel))
    os.makedirs(args.data_dir, exist_ok=True)
    wanted = set(args.only or ["history", "members", "yp"])
    if "history" in wanted:
        export_history(args.token_file, args.regions.split(","), os.path.join(args.data_dir, "volunteer_history.csv"))
    if "members" in wanted:
        export_members(args.token_file, os.path.join(args.data_dir, "members.csv"),
                       [s.strip() for s in args.scope.split(",")] if args.scope else None)
    if "inactive" in wanted:
        export_members(args.token_file, os.path.join(args.data_dir, "members_inactive.csv"),
                       [s.strip() for s in args.scope.split(",")] if args.scope else None, status="Inactive,Arrears")
    if "yp" in wanted:
        export_yp_products(args.token_file, os.path.join(args.data_dir, "yp_products.csv"))


if __name__ == "__main__":
    main()
