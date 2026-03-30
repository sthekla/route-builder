#!/usr/bin/env python3
"""
EEI Route Builder - Main Script
Processes all confirmed shows that are 90+ days from today.

Usage: python3 route_builder.py [--write]
  --write  : Also write route records back to Airtable (Route Key table)
"""

import sys
import os
import json
import math
import time
import re
import urllib.request
import urllib.parse
import urllib.error
import html
import csv
import io
from datetime import date, timedelta

# ─── Config ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)), "workspace", ".env")
# Also try the workspace root
if not os.path.exists(ENV_FILE):
    ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
if not os.path.exists(ENV_FILE):
    # Try relative to script
    ENV_FILE = os.path.join(os.path.dirname(SCRIPT_DIR), ".env")

ROUTES_DIR = os.path.join(SCRIPT_DIR, "routes")
GEOCODE_CACHE_FILE = os.path.join(ROUTES_DIR, "_geocode_cache.json")
WEB_CACHE_FILE = os.path.join(ROUTES_DIR, "_web_cache.json")

AIRTABLE_BASE_ID = "appusU3FpBl41FN7X"
TABLE_OPPORTUNITIES = "tbl9jOUgKeKNjAUDl"
TABLE_ACCOUNTS = "tblNqDrF5eq37iHKL"
TABLE_ROUTE_KEY = "tblsivMJtpAnlZURB"

NOMINATIM_UA = "EEI-RouteBuilder/1.0 sonya@entertainmentevents.com"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

MIN_MILES = 60
MAX_MILES = 210
LONG_HAUL_MILES = 150
DRIVE_SPEED_MPH = 50
DAYS_AHEAD = 90
CAPACITY_MISMATCH_PCT = 0.20


# ─── Env loading ──────────────────────────────────────────────────────────────

def load_env(path):
    env = {}
    if not os.path.exists(path):
        print(f"[WARN] .env not found at {path}")
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def find_env():
    """Search common locations for .env file."""
    candidates = [
        os.path.join(SCRIPT_DIR, ".env"),
        os.path.join(os.path.dirname(SCRIPT_DIR), ".env"),
        "/home/sthekla/.openclaw/workspace/.env",
        os.path.expanduser("~/.openclaw/workspace/.env"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


# ─── Airtable helpers ─────────────────────────────────────────────────────────

def airtable_get(token, base_id, table_id, filter_formula=None, fields=None):
    """Fetch all records from an Airtable table (handles pagination)."""
    records = []
    offset = None
    while True:
        params = {"pageSize": "100"}
        if filter_formula:
            params["filterByFormula"] = filter_formula
        if fields:
            for i, f in enumerate(fields):
                params[f"fields[{i}]"] = f
        if offset:
            params["offset"] = offset
        url = f"https://api.airtable.com/v0/{base_id}/{table_id}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            print(f"[ERROR] Airtable HTTP {e.code}: {e.read().decode()[:200]}")
            break
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return records


def airtable_post(token, base_id, table_id, fields_dict):
    """Create a new record in an Airtable table."""
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    payload = json.dumps({"fields": fields_dict}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print(f"[ERROR] Airtable POST {e.code}: {e.read().decode()[:200]}")
        return None


# ─── Geocoding ────────────────────────────────────────────────────────────────

_geocode_cache = {}

def load_geocode_cache():
    global _geocode_cache
    if os.path.exists(GEOCODE_CACHE_FILE):
        try:
            with open(GEOCODE_CACHE_FILE) as f:
                _geocode_cache = json.load(f)
        except Exception:
            _geocode_cache = {}

def save_geocode_cache():
    os.makedirs(ROUTES_DIR, exist_ok=True)
    with open(GEOCODE_CACHE_FILE, "w") as f:
        json.dump(_geocode_cache, f, indent=2)

def geocode(address, city, state, zip_code=""):
    """Geocode an address using Nominatim. Returns (lat, lng) or (None, None)."""
    if not address and not city:
        return None, None

    # Build cache key
    key = f"{address}|{city}|{state}|{zip_code}".lower().strip()
    if key in _geocode_cache:
        return _geocode_cache[key]

    # Build query string - try full address first, fall back to city+state
    queries = []
    if address and city and state:
        queries.append(f"{address}, {city}, {state} {zip_code}".strip())
    if city and state:
        queries.append(f"{city}, {state}")

    lat, lng = None, None
    for q in queries:
        params = {
            "q": q,
            "format": "json",
            "limit": "1",
            "countrycodes": "us",
        }
        url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": NOMINATIM_UA})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    results = json.load(r)
                if results:
                    lat = float(results[0]["lat"])
                    lng = float(results[0]["lon"])
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 5 * (attempt + 1)
                    print(f"  [WARN] Nominatim 429, waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"[WARN] Geocode error for '{q}': {e}")
                    break
            except Exception as e:
                print(f"[WARN] Geocode error for '{q}': {e}")
                break
        if lat:
            break
        time.sleep(1.5)  # Respect 1 req/sec limit (1.5s for safety)

    _geocode_cache[key] = (lat, lng)
    save_geocode_cache()
    return lat, lng


# ─── Distance / Direction ─────────────────────────────────────────────────────

def haversine_miles(lat1, lng1, lat2, lng2):
    """Calculate great-circle distance in miles."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def drive_time_str(miles):
    """Estimate drive time string at DRIVE_SPEED_MPH."""
    hours = miles / DRIVE_SPEED_MPH
    h = int(hours)
    m = int((hours - h) * 60)
    if h == 0:
        return f"{m}m"
    return f"{h}h {m:02d}m"


def cardinal_direction(lat, lng):
    """Return cardinal region (N/S/E/W/NE/NW/SE/SW) for US venue."""
    # US center reference: 39.5°N, -98.35°W
    REF_LAT, REF_LNG = 39.5, -98.35
    dlat = lat - REF_LAT
    dlng = lng - REF_LNG
    angle = math.degrees(math.atan2(dlat, dlng))
    # atan2 returns angle from east axis; convert to compass bearing
    bearing = (90 - angle) % 360
    dirs = ["N","NE","E","SE","S","SW","W","NW","N"]
    idx = int((bearing + 22.5) / 45) % 8
    return dirs[idx]


# ─── Web verification ─────────────────────────────────────────────────────────

_web_cache = {}

def load_web_cache():
    global _web_cache
    if os.path.exists(WEB_CACHE_FILE):
        try:
            with open(WEB_CACHE_FILE) as f:
                _web_cache = json.load(f)
        except Exception:
            _web_cache = {}

def save_web_cache():
    os.makedirs(ROUTES_DIR, exist_ok=True)
    with open(WEB_CACHE_FILE, "w") as f:
        json.dump(_web_cache, f, indent=2)


def ddg_search(venue_name, city, state):
    """Search DuckDuckGo for venue capacity info. Returns dict with findings."""
    cache_key = f"{venue_name}|{city}|{state}".lower()
    if cache_key in _web_cache:
        return _web_cache[cache_key]

    clean_name = re.sub(r"[^\w\s]", " ", venue_name).strip()
    query = f"{clean_name} {city} {state} theater capacity seating"
    encoded = urllib.parse.urlencode({"q": query})
    url = f"https://html.duckduckgo.com/html/?{encoded}"

    result = {
        "web_capacity": None,
        "spaces": [],
        "snippet": "",
        "search_url": url,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; EEI-RouteBuilder/1.0)",
        "Accept": "text/html",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] Web search failed for {venue_name}: {e}")
        _web_cache[cache_key] = result
        save_web_cache()
        return result

    # Extract text snippets from DDG HTML results
    # DDG HTML result snippets are in <a class="result__snippet"> tags
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', raw, re.DOTALL)
    
    all_text = " ".join(
        re.sub(r"<[^>]+>", " ", s) for s in (titles + snippets)
    )
    all_text = html.unescape(all_text)
    result["snippet"] = all_text[:500]

    # Look for capacity numbers
    # Patterns like "1,200 seats", "800-seat", "capacity of 600", "seats 1500"
    cap_patterns = [
        r"(\d[\d,]+)\s*(?:-seat|seat[s]?\b|capacity)",
        r"capacity[:\s]+(?:of\s+)?(\d[\d,]+)",
        r"seats\s+(\d[\d,]+)",
        r"(\d[\d,]+)\s+seat",
    ]
    found_caps = []
    for pat in cap_patterns:
        for m in re.finditer(pat, all_text, re.IGNORECASE):
            n = int(m.group(1).replace(",", ""))
            if 50 <= n <= 20000:
                found_caps.append(n)

    if found_caps:
        # Use median to avoid outliers
        found_caps.sort()
        result["web_capacity"] = found_caps[len(found_caps) // 2]

    # Look for multiple spaces
    space_keywords = [
        "main stage", "main hall", "main theater", "mainstage",
        "studio theater", "studio theatre", "black box", "black-box",
        "cabaret", "salon", "balcony", "mezzanine", "loft",
        "second stage", "intimate", "small theater", "small theatre",
        "performance space", "recital hall",
    ]
    spaces_found = []
    for kw in space_keywords:
        if kw.lower() in all_text.lower():
            # Try to get capacity near keyword
            pat = rf"{re.escape(kw)}.{{0,60}}?(\d[\d,]+)\s*seat"
            m = re.search(pat, all_text, re.IGNORECASE)
            cap = int(m.group(1).replace(",", "")) if m else None
            spaces_found.append({"name": kw.title(), "capacity": cap})

    result["spaces"] = spaces_found[:5]  # Max 5 spaces

    _web_cache[cache_key] = result
    save_web_cache()
    time.sleep(2.0)
    return result


# ─── Show & venue parsing ──────────────────────────────────────────────────────

def extract_short_name(opportunity_name):
    """Extract show short name like 'LNC' from 'LNC- Charlotte NC- 9/3/2026'."""
    m = re.match(r"^([A-Z0-9]+)", opportunity_name.strip())
    return m.group(1) if m else opportunity_name[:6].strip()


def clean_venue_name(name):
    """Clean up venue name - remove newlines, extra spaces."""
    if isinstance(name, list):
        name = name[0] if name else ""
    name = str(name).replace("\n", " ").replace("  ", " ").strip()
    # Remove city/state suffix like "- Charlotte,NC"
    name = re.sub(r"\s*-\s*[A-Za-z\s]+,\s*[A-Z]{2}\s*$", "", name)
    return name.strip()


def parse_opportunity(record):
    """Parse an Opportunity record into a structured dict."""
    f = record["fields"]
    opp_name = f.get("Opportunity Name", "")
    
    # Get venue details
    venue_name_raw = f.get("Name (from Account Name)", [""])
    venue_name = clean_venue_name(venue_name_raw[0] if isinstance(venue_name_raw, list) else venue_name_raw)
    
    # Address - it's a lookup list
    addr_raw = f.get("Venue Address", [""])
    address = str(addr_raw[0] if isinstance(addr_raw, list) else addr_raw).replace("\n", " ").strip()
    
    def _extract_str(val):
        """Extract a string from a value that may be a list, str, or None."""
        if isinstance(val, list):
            return str(val[0]).strip() if val else ""
        return str(val or "").strip()

    city = _extract_str(f.get("Venue City") or f.get("City (from Account Name)", ""))
    state = _extract_str(f.get("Venue State") or f.get("State (from Account Name)", ""))

    return {
        "id": record["id"],
        "opportunity_name": opp_name,
        "short_name": extract_short_name(opp_name),
        "show_date": f.get("Show Start Date", ""),
        "venue_name": venue_name,
        "address": address,
        "city": city,
        "state": state,
        "capacity": (f.get("Size (from Account Name)", [0]) or [0])[0] if isinstance(f.get("Size (from Account Name)"), list) else f.get("Size (from Account Name)", 0),
        "website": (f.get("Company Website (from Account Name)", [""]) or [""])[0] if isinstance(f.get("Company Website (from Account Name)"), list) else "",
        "account_id": (f.get("Account Name", []) or [])[0] if isinstance(f.get("Account Name"), list) else None,
    }


def parse_account(record):
    """Parse an Account record into a structured dict."""
    f = record["fields"]
    
    # Phone is a lookup list - may contain None values
    phone_raw = f.get("Phone", [])
    phone = ""
    if isinstance(phone_raw, list):
        phone = next((str(p) for p in phone_raw if p), "")
    elif phone_raw:
        phone = str(phone_raw)
    
    # Email
    email_raw = f.get("Email (from Contacts)", [])
    email = ""
    if isinstance(email_raw, list):
        email = next((str(e) for e in email_raw if e), "")
    elif email_raw:
        email = str(email_raw)
    
    website = str(f.get("Company Website", "")).strip()
    if website in ("\xa0", "None", "none"):
        website = ""
    
    name_raw = f.get("Name", "")
    name_clean = clean_venue_name(name_raw)
    
    return {
        "id": record["id"],
        "name": name_clean,
        "name_raw": str(name_raw).replace("\n", " ").strip(),
        "address": str(f.get("Address", "")).strip(),
        "city": str(f.get("City", "")).strip(),
        "state": str(f.get("State", "")).strip(),
        "zip_code": str(f.get("Zip Code", "")).strip(),
        "size": f.get("Size") or 0,
        "website": website,
        "phone": phone,
        "email": email,
        "route_cluster": f.get("Route Cluster", ""),
        "vetted": bool(f.get("Vetted", False)),
        "short_venue": str(f.get("Short Venue", "") or "").strip(),
        "venue_notes": str(f.get("Venue Notes", "") or "").strip(),
    }


# ─── Route generation ─────────────────────────────────────────────────────────

def build_route(show, accounts, write_back=False, token=None):
    """Build route data for a single show."""
    print(f"\n{'='*60}")
    print(f"Processing: {show['opportunity_name']}")
    print(f"  Venue: {show['venue_name']} ({show['city']}, {show['state']})")

    # Geocode the booked venue
    print(f"  Geocoding booked venue...")
    lat, lng = geocode(show["address"], show["city"], show["state"])
    if not lat:
        print(f"  [WARN] Could not geocode booked venue. Skipping.")
        return None

    show["lat"] = lat
    show["lng"] = lng
    region = cardinal_direction(lat, lng)

    print(f"  Booked venue: {lat:.4f}, {lng:.4f} | Region: {region}")

    # Filter candidate accounts within radius
    candidates = []
    print(f"  Finding candidates within {MIN_MILES}-{MAX_MILES} miles...")
    for acc in accounts:
        if not acc["address"] and not acc["city"]:
            continue
        if not acc.get("_lat"):
            continue  # Will be geocoded in batch step

        dist = haversine_miles(lat, lng, acc["_lat"], acc["_lng"])
        if MIN_MILES <= dist <= MAX_MILES:
            acc_copy = dict(acc)
            acc_copy["distance_miles"] = round(dist, 1)
            acc_copy["drive_time"] = drive_time_str(dist)
            acc_copy["long_haul"] = dist > LONG_HAUL_MILES
            acc_copy["is_booked_venue"] = (acc["id"] == show.get("account_id"))
            candidates.append(acc_copy)

    print(f"  Found {len(candidates)} candidates in radius")

    # Web verify each candidate
    print(f"  Web verifying candidates...")
    for i, cand in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {cand['name'][:40]}...", end="\r")
        web = ddg_search(cand["name"], cand["city"], cand["state"])
        cand["web_capacity"] = web["web_capacity"]
        cand["web_spaces"] = web["spaces"]
        cand["web_snippet"] = web["snippet"][:200]

        # Compute flags
        flags = []
        crm_cap = cand.get("size", 0) or 0
        web_cap = cand["web_capacity"] or 0

        if crm_cap == 0:
            flags.append(("⚠️", "No capacity in CRM"))
        elif web_cap > 0 and abs(crm_cap - web_cap) / max(crm_cap, web_cap) > CAPACITY_MISMATCH_PCT:
            flags.append(("⚠️", f"Capacity mismatch: CRM={crm_cap}, Web≈{web_cap}"))

        if not cand.get("phone") and not cand.get("email"):
            flags.append(("❓", "No contact info"))

        if cand.get("long_haul"):
            flags.append(("🚗", f"Long haul ({cand['distance_miles']} mi)"))

        cand["flags"] = flags

    print(f"\n  Done. {len(candidates)} candidates processed.")

    # Optionally write to Airtable
    if write_back and token:
        print("  Writing route to Airtable...")
        route_key = f"{show['short_name']}-{show['city']}-{region}"
        record = airtable_post(token, AIRTABLE_BASE_ID, TABLE_ROUTE_KEY, {
            "Route Name": route_key,
            "Show": show["opportunity_name"],
            "Show Date": show["show_date"],
            "Booked Venue": show["venue_name"],
            "Candidate Count": len(candidates),
        })
        if record:
            print(f"  Wrote route record: {record.get('id')}")

    return {
        "show": show,
        "region": region,
        "lat": lat,
        "lng": lng,
        "candidates": candidates,
    }


# ─── HTML Report ──────────────────────────────────────────────────────────────

def esc(s):
    """HTML-escape a string."""
    return html.escape(str(s or ""), quote=True)


def js_str(s):
    """Escape for JavaScript string literal."""
    return str(s or "").replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", "")


def generate_html(route_data):
    """Generate HTML report for a route."""
    show = route_data["show"]
    candidates = route_data["candidates"]
    lat = route_data["lat"]
    lng = route_data["lng"]
    region = route_data["region"]

    short_name = show["short_name"]
    city = show["city"]
    state = show["state"]
    route_name = f"{short_name}-{city.replace(' ', '')}-{region}"

    # Sort candidates by distance
    candidates = sorted(candidates, key=lambda x: x["distance_miles"])

    # Build JS data for map
    candidate_js = []
    for c in candidates:
        cap = c.get("size") or 0
        flags_text = " ".join(f[0] for f in c.get("flags", []))
        flags_detail = "; ".join(f[1] for f in c.get("flags", []))
        spaces = c.get("web_spaces", [])
        spaces_text = ", ".join(
            f"{s['name']}" + (f" ({s['capacity']})" if s.get("capacity") else "")
            for s in spaces
        ) if spaces else ""
        candidate_js.append({
            "lat": c.get("_lat", 0),
            "lng": c.get("_lng", 0),
            "name": c["name"],
            "city": c["city"],
            "state": c["state"],
            "address": c["address"],
            "zip": c.get("zip_code", ""),
            "distance": c["distance_miles"],
            "drive_time": c["drive_time"],
            "capacity": cap,
            "web_capacity": c.get("web_capacity") or "",
            "spaces": spaces_text,
            "website": c.get("website", ""),
            "phone": c.get("phone", ""),
            "email": c.get("email", ""),
            "route_cluster": c.get("route_cluster", ""),
            "vetted": c.get("vetted", False),
            "long_haul": c.get("long_haul", False),
            "in_crm": True,
            "flags": flags_text,
            "flags_detail": flags_detail,
            "notes": c.get("venue_notes", ""),
            "snippet": c.get("web_snippet", ""),
        })

    venues_json = json.dumps(candidate_js, indent=2)
    booked_name_js = js_str(show["venue_name"])
    booked_city_js = js_str(f"{city}, {state}")

    total_flagged = sum(1 for c in candidates if c.get("flags"))
    total_vetted = sum(1 for c in candidates if c.get("vetted"))
    total_long_haul = sum(1 for c in candidates if c.get("long_haul"))

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Route: {esc(route_name)}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; }}
  header {{ background: #1a1a2e; color: white; padding: 16px 24px; }}
  header h1 {{ font-size: 1.4em; }}
  header p {{ font-size: 0.9em; opacity: 0.8; margin-top: 4px; }}
  .stats {{ display: flex; gap: 16px; padding: 12px 24px; background: #16213e; color: white; flex-wrap: wrap; }}
  .stat {{ background: rgba(255,255,255,0.1); padding: 8px 16px; border-radius: 6px; text-align: center; }}
  .stat .num {{ font-size: 1.6em; font-weight: bold; }}
  .stat .lbl {{ font-size: 0.75em; opacity: 0.8; }}
  #map {{ height: 400px; width: 100%; }}
  .controls {{ padding: 12px 24px; background: white; border-bottom: 1px solid #ddd; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  .filter-btn {{ padding: 6px 14px; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; background: white; font-size: 0.85em; }}
  .filter-btn.active {{ background: #1a1a2e; color: white; border-color: #1a1a2e; }}
  .export-btn {{ padding: 6px 14px; border: 1px solid #27ae60; border-radius: 4px; cursor: pointer; background: #27ae60; color: white; font-size: 0.85em; margin-left: auto; }}
  .table-wrap {{ padding: 0 24px 24px; overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; background: white; margin-top: 16px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.1); font-size: 0.85em; }}
  th {{ background: #1a1a2e; color: white; padding: 10px 12px; text-align: left; cursor: pointer; user-select: none; white-space: nowrap; }}
  th:hover {{ background: #16213e; }}
  th .sort-arrow {{ opacity: 0.5; font-size: 0.8em; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  tr:hover td {{ background: #f9f9f9; }}
  tr.flagged td {{ background: #fff8f0; }}
  tr.long-haul td {{ border-left: 3px solid #e67e22; }}
  .flag-cell {{ font-size: 1.1em; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.75em; margin: 1px; }}
  .badge-vetted {{ background: #d5f5e3; color: #1e8449; }}
  .badge-cluster {{ background: #d6eaf8; color: #1a5276; }}
  .badge-lh {{ background: #fde8d8; color: #922b21; }}
  .expand-row td {{ background: #f8f8f8 !important; padding: 10px 24px; }}
  .expand-content {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }}
  .expand-field {{ font-size: 0.8em; }}
  .expand-field label {{ font-weight: 600; display: block; color: #666; }}
  .expand-field a {{ color: #2980b9; word-break: break-all; }}
  .cap-mismatch {{ color: #e74c3c; font-weight: 600; }}
  .hidden {{ display: none !important; }}
  .search-box {{ padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.85em; width: 180px; }}
</style>
</head>
<body>

<header>
  <h1>📍 Route: {esc(route_name)}</h1>
  <p>{esc(show['opportunity_name'])} &nbsp;|&nbsp; Show Date: {esc(show['show_date'])} &nbsp;|&nbsp; Booked: {esc(show['venue_name'])}, {esc(city)}, {esc(state)}</p>
</header>

<div class="stats">
  <div class="stat"><div class="num">{len(candidates)}</div><div class="lbl">Candidates</div></div>
  <div class="stat"><div class="num">{total_vetted}</div><div class="lbl">Vetted</div></div>
  <div class="stat"><div class="num">{total_flagged}</div><div class="lbl">Flagged</div></div>
  <div class="stat"><div class="num">{total_long_haul}</div><div class="lbl">Long Haul (&gt;{LONG_HAUL_MILES}mi)</div></div>
  <div class="stat"><div class="num">{MIN_MILES}–{MAX_MILES}mi</div><div class="lbl">Radius</div></div>
</div>

<div id="map"></div>

<div class="controls">
  <button class="filter-btn active" onclick="setFilter('all')">All</button>
  <button class="filter-btn" onclick="setFilter('vetted')">Vetted Only</button>
  <button class="filter-btn" onclick="setFilter('flagged')">Flagged Only</button>
  <button class="filter-btn" onclick="setFilter('longhaul')">Long Haul</button>
  <input class="search-box" type="text" id="searchBox" placeholder="Search venues..." oninput="filterRows()">
  <button class="export-btn" onclick="exportCSV()">⬇ Export CSV</button>
</div>

<div class="table-wrap">
<table id="venueTable">
<thead>
  <tr>
    <th onclick="sortTable(0)">Venue Name <span class="sort-arrow">↕</span></th>
    <th onclick="sortTable(1)">City/State <span class="sort-arrow">↕</span></th>
    <th onclick="sortTable(2)">Distance <span class="sort-arrow">↕</span></th>
    <th onclick="sortTable(3)">Drive Time <span class="sort-arrow">↕</span></th>
    <th onclick="sortTable(4)">CRM Cap <span class="sort-arrow">↕</span></th>
    <th onclick="sortTable(5)">Web Cap <span class="sort-arrow">↕</span></th>
    <th>Spaces</th>
    <th>Contact</th>
    <th>Vetted</th>
    <th>Flags</th>
  </tr>
</thead>
<tbody id="venueBody">
</tbody>
</table>
</div>

<script>
const BOOKED_LAT = {lat};
const BOOKED_LNG = {lng};
const BOOKED_NAME = '{booked_name_js}';
const BOOKED_CITY = '{booked_city_js}';
const VENUES = {venues_json};

// ── Map setup ────────────────────────────────────────────────────────────────
const map = L.map('map').setView([BOOKED_LAT, BOOKED_LNG], 7);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© OpenStreetMap contributors'
}}).addTo(map);

// Booked venue - red star
const starIcon = L.divIcon({{
  html: '<div style="font-size:28px;margin-top:-14px;margin-left:-14px;">⭐</div>',
  iconSize: [28, 28],
  className: ''
}});
L.marker([BOOKED_LAT, BOOKED_LNG], {{icon: starIcon}})
  .addTo(map)
  .bindPopup(`<b>⭐ BOOKED: ${{BOOKED_NAME}}</b><br>${{BOOKED_CITY}}`);

// Candidate venue markers
const markers = [];
VENUES.forEach((v, idx) => {{
  if (!v.lat || !v.lng) return;
  const cap = v.capacity || 0;
  const r = Math.max(6, Math.min(22, Math.sqrt(cap / 50)));
  const color = v.long_haul ? '#e67e22' : (v.vetted ? '#27ae60' : '#2980b9');
  const circleIcon = L.divIcon({{
    html: `<div style="width:${{r*2}}px;height:${{r*2}}px;border-radius:50%;background:${{color}};opacity:0.8;border:2px solid white;margin-top:-${{r}}px;margin-left:-${{r}}px;"></div>`,
    iconSize: [r*2, r*2],
    className: ''
  }});
  const m = L.marker([v.lat, v.lng], {{icon: circleIcon}})
    .addTo(map)
    .bindPopup(`<b>${{v.name}}</b><br>${{v.city}}, ${{v.state}}<br>${{v.distance}} mi · ${{v.drive_time}}<br>Cap: ${{cap || '?'}} ${{v.flags}}`);
  markers.push({{marker: m, venue: v, idx}});
}});

// ── Table rendering ─────────────────────────────────────────────────────────
let currentFilter = 'all';
let currentSort = {{col: 2, asc: true}};
let expandedRow = null;

function renderTable() {{
  const body = document.getElementById('venueBody');
  const search = document.getElementById('searchBox').value.toLowerCase();
  body.innerHTML = '';

  let sorted = [...VENUES];
  const col = currentSort.col;
  sorted.sort((a, b) => {{
    let va, vb;
    if (col === 0) {{ va = a.name; vb = b.name; }}
    else if (col === 1) {{ va = a.city + a.state; vb = b.city + b.state; }}
    else if (col === 2) {{ va = a.distance; vb = b.distance; }}
    else if (col === 3) {{ va = a.distance; vb = b.distance; }}  // proxy
    else if (col === 4) {{ va = a.capacity || 0; vb = b.capacity || 0; }}
    else if (col === 5) {{ va = a.web_capacity || 0; vb = b.web_capacity || 0; }}
    else {{ va = ''; vb = ''; }}
    if (typeof va === 'string') return currentSort.asc ? va.localeCompare(vb) : vb.localeCompare(va);
    return currentSort.asc ? va - vb : vb - va;
  }});

  sorted.forEach((v, idx) => {{
    // Filter
    if (currentFilter === 'vetted' && !v.vetted) return;
    if (currentFilter === 'flagged' && !v.flags) return;
    if (currentFilter === 'longhaul' && !v.long_haul) return;
    if (search && !v.name.toLowerCase().includes(search) && !(v.city+v.state).toLowerCase().includes(search)) return;

    const hasFlag = v.flags && v.flags.length > 0;
    const capMismatch = v.web_capacity && v.capacity && Math.abs(v.capacity - v.web_capacity) / Math.max(v.capacity, v.web_capacity) > 0.2;

    const tr = document.createElement('tr');
    tr.className = (hasFlag ? 'flagged ' : '') + (v.long_haul ? 'long-haul' : '');
    tr.dataset.idx = idx;
    tr.innerHTML = `
      <td><a href="#" onclick="expandRow(event,${{idx}})">🏛 ${{escHtml(v.name)}}</a>
        ${{v.route_cluster ? '<br><span class="badge badge-cluster">'+escHtml(v.route_cluster)+'</span>' : ''}}
      </td>
      <td>${{escHtml(v.city)}}, ${{escHtml(v.state)}}</td>
      <td>${{v.distance}} mi</td>
      <td>${{v.drive_time}}${{v.long_haul ? ' <span class="badge badge-lh">Long Haul</span>' : ''}}</td>
      <td>${{v.capacity || '<em>—</em>'}}</td>
      <td class="${{capMismatch ? 'cap-mismatch' : ''}}">${{v.web_capacity || '<em>—</em>'}}</td>
      <td style="font-size:0.8em">${{escHtml(v.spaces) || '—'}}</td>
      <td style="font-size:0.8em">${{v.phone || v.email ? (escHtml(v.phone || '') + (v.email ? '<br>'+escHtml(v.email) : '')) : '<em>—</em>'}}</td>
      <td>${{v.vetted ? '✅' : ''}}</td>
      <td class="flag-cell" title="${{escHtml(v.flags_detail)}}">${{v.flags || ''}}</td>
    `;
    tr.addEventListener('click', (e) => {{ if (e.target.tagName !== 'A') expandRow(e, idx); }});
    body.appendChild(tr);
  }});
}}

function expandRow(e, idx) {{
  e.preventDefault();
  const body = document.getElementById('venueBody');
  // Remove existing expand row
  const existing = body.querySelector('.expand-row');
  if (existing) existing.remove();
  if (expandedRow === idx) {{ expandedRow = null; return; }}
  expandedRow = idx;
  
  const v = VENUES[idx];
  const tr = document.createElement('tr');
  tr.className = 'expand-row';
  tr.innerHTML = `<td colspan="10">
    <div class="expand-content">
      <div class="expand-field"><label>Full Address</label>${{escHtml(v.address + ', ' + v.city + ', ' + v.state + ' ' + v.zip)}}</div>
      ${{v.website ? '<div class="expand-field"><label>Website</label><a href="'+escHtml(v.website)+'" target="_blank">'+escHtml(v.website)+'</a></div>' : ''}}
      ${{v.phone ? '<div class="expand-field"><label>Phone</label>'+escHtml(v.phone)+'</div>' : ''}}
      ${{v.email ? '<div class="expand-field"><label>Email</label>'+escHtml(v.email)+'</div>' : ''}}
      ${{v.spaces ? '<div class="expand-field"><label>Performance Spaces</label>'+escHtml(v.spaces)+'</div>' : ''}}
      ${{v.notes ? '<div class="expand-field"><label>CRM Notes</label>'+escHtml(v.notes)+'</div>' : ''}}
      ${{v.snippet ? '<div class="expand-field" style="grid-column:span 2"><label>Web Info</label><em style="font-size:0.9em">'+escHtml(v.snippet.substring(0,300))+'</em></div>' : ''}}
      ${{v.flags_detail ? '<div class="expand-field"><label>Flag Details</label><span style="color:#e74c3c">'+escHtml(v.flags_detail)+'</span></div>' : ''}}
    </div>
  </td>`;
  
  // Insert after clicked row
  const rows = body.querySelectorAll('tr:not(.expand-row)');
  let targetRow = null;
  rows.forEach(r => {{ if (parseInt(r.dataset.idx) === idx) targetRow = r; }});
  if (targetRow) targetRow.after(tr);
  else body.appendChild(tr);
}}

function escHtml(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function setFilter(f) {{
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  renderTable();
}}

function filterRows() {{ renderTable(); }}

function sortTable(col) {{
  if (currentSort.col === col) currentSort.asc = !currentSort.asc;
  else {{ currentSort.col = col; currentSort.asc = true; }}
  renderTable();
}}

function exportCSV() {{
  const rows = [['Venue Name','City','State','Address','Distance (mi)','Drive Time','CRM Capacity','Web Capacity','Spaces','Phone','Email','Website','Vetted','Route Cluster','Flags','Notes']];
  VENUES.forEach(v => {{
    rows.push([v.name, v.city, v.state, v.address+' '+v.zip, v.distance, v.drive_time,
      v.capacity||'', v.web_capacity||'', v.spaces, v.phone, v.email, v.website,
      v.vetted?'Yes':'', v.route_cluster, v.flags_detail, v.notes]);
  }});
  const csv = rows.map(r => r.map(c => '"'+String(c||'').replace(/"/g,'""')+'"').join(',')).join('\\n');
  const blob = new Blob([csv], {{type:'text/csv'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '{esc(route_name)}.csv';
  a.click();
}}

renderTable();
</script>
</body>
</html>"""

    return route_name, html_content


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    write_back = "--write" in sys.argv
    show_filter = None  # Used by route_builder_single.py

    # Load config
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

    # Determine target date (90 days from today)
    today = date.today()
    cutoff = today + timedelta(days=DAYS_AHEAD)
    print(f"Today: {today} | 90-day cutoff: {cutoff}")

    # Fetch confirmed shows
    print("\nFetching confirmed shows from Airtable...")
    opp_records = airtable_get(token, AIRTABLE_BASE_ID, TABLE_OPPORTUNITIES,
                                filter_formula='Status="Confirmed"')
    print(f"Total confirmed shows: {len(opp_records)}")

    shows = []
    for rec in opp_records:
        show = parse_opportunity(rec)
        if not show["show_date"]:
            continue
        show_date = date.fromisoformat(show["show_date"])
        if show_date <= cutoff:
            continue
        if show_filter and show["opportunity_name"] != show_filter:
            continue
        shows.append(show)

    shows.sort(key=lambda x: x["show_date"])
    print(f"Shows 90+ days out: {len(shows)}")
    for s in shows:
        print(f"  {s['show_date']} | {s['opportunity_name']}")

    if not shows:
        print("No shows to process.")
        return

    # Fetch all accounts
    print("\nFetching all accounts from Airtable...")
    acc_records = airtable_get(token, AIRTABLE_BASE_ID, TABLE_ACCOUNTS)
    print(f"Total accounts: {len(acc_records)}")
    accounts = [parse_account(r) for r in acc_records]

    # Batch geocode all accounts
    print(f"\nGeocoding {len(accounts)} accounts (cached where possible)...")
    geocoded = 0
    for i, acc in enumerate(accounts):
        if not acc["city"] and not acc["address"]:
            acc["_lat"] = None
            acc["_lng"] = None
            continue
        acc["_lat"], acc["_lng"] = geocode(acc["address"], acc["city"], acc["state"], acc.get("zip_code", ""))
        if acc["_lat"]:
            geocoded += 1
        if (i + 1) % 10 == 0:
            print(f"  Geocoded {i+1}/{len(accounts)}...", end="\r")

    print(f"\n  {geocoded}/{len(accounts)} accounts geocoded successfully")

    # Process each show
    reports_written = 0
    for show in shows:
        route_data = build_route(show, accounts, write_back=write_back, token=token)
        if not route_data:
            continue

        route_name, html_content = generate_html(route_data)
        out_path = os.path.join(ROUTES_DIR, f"{route_name}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"  ✅ Report: {out_path}")
        reports_written += 1

    # Generate index page
    generate_index(shows, reports_written)

    print(f"\n{'='*60}")
    print(f"Done! {reports_written} reports written to {ROUTES_DIR}/")
    if write_back:
        print("Route records written to Airtable.")
    else:
        print("(Run with --write to push route records to Airtable)")


def generate_index(shows, count):
    """Generate an index HTML listing all reports."""
    rows = ""
    for show in shows:
        short_name = show["short_name"]
        city = show["city"]
        state = show["state"]
        # We can't easily know region here without geocoding, so link by pattern
        rows += f"""<tr>
          <td>{esc(show['show_date'])}</td>
          <td>{esc(show['opportunity_name'])}</td>
          <td>{esc(show['venue_name'])}</td>
          <td>{esc(city)}, {esc(state)}</td>
          <td><a href="./{esc(short_name)}-{esc(city.replace(' ',''))}-*.html">View (search below)</a></td>
        </tr>"""

    # List actual HTML files
    import glob
    html_files = sorted(glob.glob(os.path.join(ROUTES_DIR, "*.html")))
    file_links = "\n".join(
        f'<li><a href="{esc(os.path.basename(f))}">{esc(os.path.basename(f).replace(".html",""))}</a></li>'
        for f in html_files
        if not os.path.basename(f).startswith("_") and os.path.basename(f) != "index.html"
    )

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>EEI Route Builder - Reports</title>
<style>
  body {{ font-family: sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; }}
  h1 {{ color: #1a1a2e; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 8px 0; border-bottom: 1px solid #eee; }}
  a {{ color: #2980b9; text-decoration: none; font-size: 1.1em; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ color: #666; font-size: 0.85em; margin-top: 8px; }}
</style>
</head>
<body>
<h1>🗺 EEI Route Builder Reports</h1>
<p class="meta">Generated: {date.today()} | {count} reports</p>
<ul>
{file_links}
</ul>
</body>
</html>"""

    with open(os.path.join(ROUTES_DIR, "index.html"), "w") as f:
        f.write(index_html)
    print(f"  📋 Index: {os.path.join(ROUTES_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
