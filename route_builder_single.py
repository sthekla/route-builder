#!/usr/bin/env python3
"""
EEI Route Builder - Single Show Mode
Processes one show by exact Opportunity Name.

Usage: python3 route_builder_single.py "LNC- Charlotte NC- 9/3/2026" [--write]
  --write  : Also write route record back to Airtable
"""

import sys
import os

# Import everything from route_builder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from route_builder import (
    find_env, load_env, airtable_get, parse_opportunity, parse_account,
    geocode, build_route, generate_html, generate_index, load_geocode_cache,
    load_web_cache, save_geocode_cache, save_web_cache,
    AIRTABLE_BASE_ID, TABLE_OPPORTUNITIES, TABLE_ACCOUNTS,
    ROUTES_DIR, DAYS_AHEAD
)
from datetime import date, timedelta
import os


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        print("Usage: python3 route_builder_single.py \"LNC- Charlotte NC- 9/3/2026\" [--write]")
        print()
        print("The show name must match exactly the Opportunity Name in Airtable.")
        sys.exit(1)

    show_filter = sys.argv[1]
    write_back = "--write" in sys.argv

    print(f"Single show mode: {show_filter}")

    env_path = find_env()
    if not env_path:
        print("[ERROR] Cannot find .env file")
        sys.exit(1)
    env = load_env(env_path)
    token = env.get("AIRTABLE_TOKEN")
    if not token:
        print("[ERROR] AIRTABLE_TOKEN not found in .env")
        sys.exit(1)

    os.makedirs(ROUTES_DIR, exist_ok=True)
    load_geocode_cache()
    load_web_cache()

    today = date.today()
    cutoff = today + timedelta(days=DAYS_AHEAD)

    # Fetch the specific show
    print(f"\nFetching show: {show_filter}")
    safe = show_filter.replace('"', '\\"')
    opp_records = airtable_get(
        token, AIRTABLE_BASE_ID, TABLE_OPPORTUNITIES,
        filter_formula=f'AND(Status="Confirmed", {{Opportunity Name}}="{safe}")'
    )

    if not opp_records:
        # Try without status filter in case it's useful to route even non-confirmed
        print(f"  No confirmed show found. Trying without status filter...")
        opp_records = airtable_get(
            token, AIRTABLE_BASE_ID, TABLE_OPPORTUNITIES,
            filter_formula=f'{{Opportunity Name}}="{safe}"'
        )

    if not opp_records:
        print(f"[ERROR] Show not found: {show_filter}")
        print("Note: The name must match exactly (case-sensitive).")
        sys.exit(1)

    show = parse_opportunity(opp_records[0])
    print(f"Found: {show['opportunity_name']} | {show['show_date']} | {show['venue_name']}")

    # Check 90-day rule but warn rather than skip
    if show["show_date"]:
        show_date = date.fromisoformat(show["show_date"])
        days_out = (show_date - today).days
        if days_out < DAYS_AHEAD:
            print(f"[WARN] Show is only {days_out} days out (< {DAYS_AHEAD}). Processing anyway.")

    # Fetch all accounts
    print(f"\nFetching accounts...")
    acc_records = airtable_get(token, AIRTABLE_BASE_ID, TABLE_ACCOUNTS)
    accounts = [parse_account(r) for r in acc_records]
    print(f"  {len(accounts)} accounts loaded")

    # Geocode accounts
    print(f"Geocoding accounts...")
    for i, acc in enumerate(accounts):
        if not acc["city"] and not acc["address"]:
            acc["_lat"] = None
            acc["_lng"] = None
            continue
        acc["_lat"], acc["_lng"] = geocode(acc["address"], acc["city"], acc["state"], acc.get("zip_code", ""))
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(accounts)} geocoded...", end="\r")

    print(f"\n  Done geocoding.")

    # Build route
    route_data = build_route(show, accounts, write_back=write_back, token=token)
    if not route_data:
        print("[ERROR] Could not build route (geocoding failed for booked venue?)")
        sys.exit(1)

    route_name, html_content = generate_html(route_data)
    out_path = os.path.join(ROUTES_DIR, f"{route_name}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    generate_index([show], 1)

    print(f"\n✅ Report written: {out_path}")
    if write_back:
        print("Route record written to Airtable.")
    else:
        print("(Run with --write to push route record to Airtable)")


if __name__ == "__main__":
    main()
