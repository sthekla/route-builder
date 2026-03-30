# EEI Route Builder

Generates interactive HTML route reports for Entertainment Events Inc (EEI) confirmed shows.

For each confirmed show that is **90+ days from today**, this tool:
1. Fetches the booked venue address from Airtable
2. Geocodes it via Nominatim (free, no API key)
3. Finds all venues in the CRM within 60–210 miles
4. Web-verifies capacities and multi-space info via DuckDuckGo
5. Generates an HTML report with an interactive map + sortable/filterable table

---

## Requirements

- **Python 3.6+** (standard library only — no pip installs needed)
- A `.env` file at `/home/sthekla/.openclaw/workspace/.env` with `AIRTABLE_TOKEN`

---

## Setup

```bash
cd /home/sthekla/.openclaw/workspace/projects/route-builder
mkdir -p routes
```

The `.env` file is already configured. Fields used:
- `AIRTABLE_TOKEN` — Airtable personal access token
- Base ID: `appusU3FpBl41FN7X`
- Opportunities table: `tbl9jOUgKeKNjAUDl`
- Accounts table: `tblNqDrF5eq37iHKL`

---

## Usage

### Process ALL confirmed shows (90+ days out):
```bash
python3 route_builder.py
```

### Process a single show by exact name:
```bash
python3 route_builder_single.py "LNC- Charlotte NC- 9/3/2026"
```

### Write route records back to Airtable (Route Key table):
```bash
python3 route_builder.py --write
python3 route_builder_single.py "LNC- Charlotte NC- 9/3/2026" --write
```

> ⚠️ **The `--write` flag is required to modify Airtable.** Without it, the script is read-only.

---

## Output

Reports are saved to `routes/` as HTML files, one per show.

**Filename format:** `{ShowShortName}-{VenueCity}-{Region}.html`

Examples:
- `LNC-Charlotte-SW.html`
- `SP-Schenectady-NE.html`
- `GN-Torrington-NE.html`

An `index.html` is also generated listing all reports.

---

## Report Features

Each HTML report includes:

### 🗺 Interactive Map
- **Red star (⭐)** = booked venue
- **Blue circles** = candidate venues (sized by capacity)
- **Green circles** = vetted venues
- **Orange circles** = long-haul venues (>150 miles)
- Click any marker for details

### 📊 Sortable Table
| Column | Description |
|--------|-------------|
| Venue Name | Expandable — click to see full details |
| City/State | Location |
| Distance | Miles from booked venue |
| Drive Time | Estimated at 50 mph |
| CRM Cap | Capacity from Airtable |
| Web Cap | Capacity found via DuckDuckGo |
| Spaces | Multiple performance spaces found |
| Contact | Phone/email from CRM |
| Vetted | ✅ if marked Vetted in CRM |
| Flags | ⚠️ capacity mismatch, ❓ no contact, 🚗 long haul |

### 🔍 Filter Controls
- **All** — show everything
- **Vetted Only** — show only CRM-vetted venues
- **Flagged Only** — show only venues with issues
- **Long Haul** — show only venues >150 miles
- **Search box** — filter by venue name or city

### 📥 Export CSV
Downloads all venue data as a spreadsheet.

---

## Distance & Routing Logic

| Parameter | Value |
|-----------|-------|
| Min radius | 60 miles |
| Max radius | 210 miles |
| Long haul threshold | >150 miles |
| Drive time estimate | 50 mph average |
| Geocoding | Nominatim (1 req/sec) |

---

## Caching

The script caches geocoding results and web search results to avoid repeat API calls:
- `routes/_geocode_cache.json` — Nominatim lat/lng cache
- `routes/_web_cache.json` — DuckDuckGo search cache

Delete these files to force fresh lookups.

---

## Flags

| Flag | Meaning |
|------|---------|
| ⚠️ | Capacity mismatch >20% between CRM and web, or capacity = 0 |
| ❓ | No phone or email in CRM |
| 🚗 | Long-haul venue (>150 miles) |

---

## Data Sources

- **Venue data**: Airtable Accounts table (`tblNqDrF5eq37iHKL`)
- **Show data**: Airtable Opportunities table (`tbl9jOUgKeKNjAUDl`)
- **Geocoding**: [Nominatim / OpenStreetMap](https://nominatim.openstreetmap.org/) — free, no key
- **Capacity verification**: DuckDuckGo HTML search (no API key)
- **Maps**: [Leaflet.js](https://leafletjs.com/) + OpenStreetMap tiles

---

## Notes

- The script respects Nominatim's 1 req/sec rate limit with 1.5-second delays between calls
- Web searches have 2-second delays between requests
- Missing data is handled gracefully (blank address → skip geocoding, etc.)
- Employee/personal PII is never included in reports — only venue business data

## ⏱ Expected Runtime

The Airtable Accounts table has ~9,800 records. Geocoding all of them takes about 4–5 hours on first run (1.5s/request × 9800 accounts).

**Good news:** Results are cached in `routes/_geocode_cache.json`. Subsequent runs are fast (cached lookups are instant).

**Recommended workflow:**
1. Run `python3 route_builder_single.py "ShowName"` for quick one-off reports — it still geocodes all accounts but caches as it goes
2. Let `route_builder.py` run overnight to generate all 54+ reports
3. After the first full run, re-runs take only a few minutes

The geocode cache persists between runs and grows over time. Delete it to force re-geocoding.

## 🏃 Quick Start (Recommended)

For a fast first report, run the single-show mode:
```bash
python3 route_builder_single.py "LNC- Charlotte NC- 9/3/2026"
```

Open `routes/LNC-Charlotte-E.html` in a browser to see the map and table.

For all shows (run overnight):
```bash
nohup python3 route_builder.py > routes/run.log 2>&1 &
tail -f routes/run.log  # monitor progress
```
