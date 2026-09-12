[![Foodborne Outbreak nightly update](https://github.com/fartbagxp/foodborne-outbreak-alert/actions/workflows/scrape-outbreaks.yml/badge.svg)](https://github.com/fartbagxp/foodborne-outbreak-alert/actions/workflows/scrape-outbreaks.yml)

# Foodborne Outbreak Alert System

A real-time foodborne outbreak monitoring system that aggregates and analyzes outbreak data from FDA and CDC, alongside food recalls from USDA FSIS.

**Current Coverage:**

- 🏛️ FDA: 81 outbreak investigations (2006-2026)
- 🔬 CDC: 234 total investigations (2006-2026)
- 🚨 Active: 31 ongoing investigations
- 📊 Total: 315 foodborne outbreak investigations

## Current Statistics

| Metric                    | Count  |
| ------------------------- | ------ |
| **Total Outbreaks**       | 315    |
| **Active Investigations** | 31     |
| **Total Cases**           | 41,401 |
| **Deaths**                | 5,026  |
| **Hospitalizations**      | 15,171 |
| **States Affected**       | 51     |

## By Source

| Source | Outbreaks | Year Range |
| ------ | --------- | ---------- |
| CDC    | 234       | 2006-2026  |
| FDA    | 81        | 2006-2026  |

## By Status

| Status  | Count |
| ------- | ----- |
| Unknown | 218   |
| Closed  | 66    |
| Ongoing | 31    |

## By Pathogen (Top 10)

| Pathogen   | Outbreaks |
| ---------- | --------- |
| Salmonella | 139       |
| Unknown    | 64        |
| E. coli    | 61        |
| Listeria   | 44        |
| Botulism   | 4         |
| Vibrio     | 3         |

## By Year (Recent)

| Year | Outbreaks |
| ---- | --------- |
| 2026 | 12        |
| 2025 | 13        |
| 2024 | 11        |
| 2023 | 12        |
| 2022 | 24        |
| 2021 | 22        |
| 2020 | 20        |
| 2019 | 27        |
| 2018 | 20        |
| 2017 | 7         |

## USDA Recalls

| Metric                         | Count     |
| ------------------------------ | --------- |
| **Total Recalls**              | 1,234     |
| **Outbreak-Related**           | 12        |
| **Linked to an Investigation** | 11        |
| **Active Notices**             | 1         |
| **Year Range**                 | 2014-2026 |

## Recalls by Risk Level

| Risk Level           | Recalls |
| -------------------- | ------- |
| High - Class I       | 834     |
| Low - Class II       | 188     |
| Public Health Alert  | 169     |
| Marginal - Class III | 43      |

## Recalls by Pathogen

| Pathogen       | Recalls |
| -------------- | ------- |
| Listeria       | 125     |
| E. coli        | 82      |
| Salmonella     | 48      |
| Staphylococcus | 5       |
| Botulism       | 2       |

## Data Sources

All sources are public `.gov` endpoints. FDA and CDC are scraped from HTML; USDA is a JSON API.

| Source    | What it provides                           | Endpoint                                                                                    | Access                |
| --------- | ------------------------------------------ | ------------------------------------------------------------------------------------------- | --------------------- |
| FDA       | Public health advisories & investigations  | [Outbreak listing][fda-listing]                                                             | HTML, browser + proxy |
| CDC       | Investigation updates, per pathogen        | [Salmonella][cdc-sal] · [Listeria][cdc-lis] · [E. coli][cdc-eco] · [Campylobacter][cdc-cam] | HTML, browser         |
| CDC       | Investigation discovery                    | [Outbreak index][cdc-index] · [full-outbreak-list.csv][cdc-csv]                             | HTML + CSV            |
| USDA FSIS | Recalls & public health alerts (reference) | [fsis.usda.gov/recalls][fsis-page]                                                          | HTML, not scraped     |
| USDA FSIS | Full recall history, every field           | [`/fsis/api/recall/v/1`][fsis-api]                                                          | JSON, one request     |

[fda-listing]: https://www.fda.gov/food/outbreaks-foodborne-illness/public-health-advisories-investigations-foodborne-illness-outbreaks
[cdc-sal]: https://www.cdc.gov/salmonella/outbreaks/index.html
[cdc-lis]: https://www.cdc.gov/listeria/outbreaks/index.html
[cdc-eco]: https://www.cdc.gov/ecoli/outbreaks/index.html
[cdc-cam]: https://www.cdc.gov/campylobacter/outbreaks/index.html
[cdc-index]: https://www.cdc.gov/foodborne-outbreaks/outbreaks/
[cdc-csv]: https://www.cdc.gov/foodborne-outbreaks/media/files/2024/04/full-outbreak-list.csv
[fsis-page]: https://www.fsis.usda.gov/recalls
[fsis-api]: https://www.fsis.usda.gov/fsis/api/recall/v/1

The FSIS recalls page and the API return the same notices; only the API is collected, since one
request yields the full history with every field already structured.

**Access notes:** all three agencies sit behind Akamai, each rejecting for a different reason. FDA
blocks by IP (GitHub Actions runners), so it routes through a fly.dev proxy — see
[`fly-proxy/README.md`](fly-proxy/README.md). CDC blocks plain HTTP clients but accepts a real
browser. USDA rejects non-browser TLS fingerprints _and_ headless Chromium's own `sec-ch-ua`
header, so it uses Playwright with that header overridden — no proxy needed.

## Features

- **Multi-Source Scraping**: Combines outbreak data from:
  - FDA Public Health Advisories (2006-2026)
  - CDC Investigation Updates (2006-2026)
  - Salmonella, Listeria, E. coli, and other pathogens
- **USDA FSIS Recalls**: Meat, poultry and egg product recalls and public health alerts (2014-2026),
  collected from the FSIS recall API in a single request and kept in their own dataset
- **Recall Cross-Linking**: Recalls FSIS flags as outbreak-related are matched to the investigation
  they belong to, and the link is written to both datasets
- **Command-Line Interface**: Run each source separately or together with `--fda`, `--cdc` and `--usda` flags
- **Unified Data Format**: Normalizes data from different sources into a consistent structure
- **Rich Metadata Extraction**:
  - Case counts, deaths, and hospitalizations
  - Affected states and geographic spread
  - Food sources and pathogens
  - Consumer advice and recommendations
  - Investigation status
- **Summary Statistics**: Generates aggregate statistics across all sources
- **JSON Export**: Saves data in structured JSON format for further analysis

## Installation

```bash
# Install dependencies using uv
uv sync
```

## Quick Start

```bash
# Scrape every source
uv run python main.py

# Or scrape selectively
uv run python main.py --fda   # FDA only
uv run python main.py --cdc   # CDC only
uv run python main.py --usda  # USDA FSIS recalls only (one API request)
```

This will create JSON files in `data/raw/` with outbreak data, statistics, and normalized formats ready for analysis.

## Usage

### Command Line Interface

The scraper supports command-line arguments to run each source separately or together:

```bash
# Scrape every source (default)
uv run python main.py

# Scrape FDA only
uv run python main.py --fda

# Scrape CDC only
uv run python main.py --cdc

# Fetch USDA FSIS recalls only
uv run python main.py --usda

# Scrape FDA and CDC with a custom delay
uv run python main.py --fda --cdc --delay 3.0

# Rebuild combined data and recall cross-links without scraping
uv run python main.py --combine

# Show help and available options
uv run python main.py --help
```

**Output files generated:**

- `data/raw/combined_outbreaks.json` - Unified FDA + CDC outbreak data with summary statistics
- `data/raw/fda_outbreaks.json` - FDA data only (when `--fda` is used)
- `data/raw/cdc_outbreaks.json` - CDC data only (when `--cdc` is used)
- `data/raw/usda_recalls.json` - USDA FSIS recalls only (when `--usda` is used)

**Command-line options:**

- `--fda` - Scrape FDA outbreak data only
- `--cdc` - Scrape CDC outbreak data only
- `--usda` - Fetch USDA FSIS recall data only
- `--combine` - Rebuild combined data and cross-links from existing files, without scraping
- `--delay DELAY` - Delay between requests in seconds (default: 2.0)
- `--help` - Show help message

USDA is a single API request that returns the full recall history, so it has no
incremental mode and ignores `--delay`, `--max-age-days` and `--full`.

### Python API

You can also use the scrapers programmatically:

```python
from main import OutbreakAggregator

# Initialize the aggregator
aggregator = OutbreakAggregator()

# Scrape all sources
all_data = aggregator.scrape_all_sources(
    fda_limit=None,  # Set to None to scrape all FDA outbreaks
    delay=2.0,       # Delay between requests (be respectful!)
)

# Combine and normalize the data
combined = aggregator.combine_and_normalize(all_data)

# Generate statistics
stats = aggregator.get_summary_stats(combined)

# Save to JSON (defaults to data/raw/ directory)
aggregator.save_combined_data(combined, stats)
```

### Updating CDC Investigation URLs

The scraper includes **18 pre-configured CDC investigation URLs** (lines 826-850 in main.py) covering:

- 10 Salmonella outbreaks (2025)
- 5 Listeria outbreaks (2024-2025)
- 3 E. coli outbreaks (2024)

**To add new CDC investigations:**

1. Find new outbreak investigation URLs at: [CDC Foodborne Outbreaks](https://www.cdc.gov/foodborne-outbreaks/outbreaks/)
2. Or search Google: `site:cdc.gov/[pathogen]/outbreaks investigation.html 2025`
3. Edit `main.py` (lines 826-850) and add URLs to the `known_cdc_urls` list:

```python
known_cdc_urls = [
    # Add your new URLs here
    'https://www.cdc.gov/salmonella/outbreaks/new-outbreak/investigation.html',
    ...
]
```

**Why manual URLs?** CDC pages load content dynamically via JavaScript, so automatic discovery often finds 0 results. Providing known URLs ensures reliable data collection.

### Individual Scrapers

You can also use the scrapers independently:

**FDA Scraper:**

```python
from main import FDAOutbreakScraper

scraper = FDAOutbreakScraper()
outbreaks = scraper.scrape_all(limit=None, delay=2.0)  # limit=None scrapes all
scraper.save_to_json(outbreaks)  # Saves to data/raw/fda_outbreaks.json
```

**CDC Scraper:**

```python
from main import CDCOutbreakScraper

scraper = CDCOutbreakScraper()

# Option 1: Scrape known URLs
urls = ['https://www.cdc.gov/salmonella/outbreaks/eggs-08-25/investigation.html']
outbreaks = scraper.scrape_known_urls(urls, delay=2.0)

# Option 2: Attempt automatic discovery (may find 0 due to dynamic content)
outbreaks = scraper.scrape_all_pathogens(delay=2.0)

scraper.save_to_json(outbreaks)  # Saves to data/raw/cdc_outbreaks.json
```

**USDA FSIS Recall Scraper:**

```python
from main import USDARecallScraper, link_recalls_to_outbreaks

scraper = USDARecallScraper()
recalls = scraper.scrape_all()   # One API request for the full recall history
scraper.save_to_json(recalls)    # Saves to data/raw/usda_recalls.json

# Cross-link against already-combined outbreak records
link_recalls_to_outbreaks(outbreaks, recalls)
```

## Output Format

### Combined Output Structure

```json
{
  "summary": {
    "total_outbreaks": 6,
    "total_cases": 136,
    "total_deaths": 15,
    "total_hospitalizations": 46,
    "unique_states_affected": 12,
    "outbreaks_by_source": { "CDC": 3, "FDA": 3 },
    "outbreaks_by_pathogen": { "salmonella": 2, "listeria": 1 },
    "outbreaks_by_status": { "ongoing": 4, "closed": 2 }
  },
  "outbreaks": [
    {
      "id": "cdc_salmonella_eggs-08-25",
      "source": "CDC",
      "title": "Investigation Update: Salmonella Outbreak, August 2025",
      "url": "https://www.cdc.gov/salmonella/outbreaks/eggs-08-25/investigation.html",
      "pathogen": "salmonella",
      "food_source": "eggs",
      "case_count": 95,
      "deaths": 0,
      "hospitalizations": 14,
      "state_count": 14,
      "consumer_advice": "Do not eat recalled eggs...",
      "scraped_at": "2025-11-14T15:53:30.368416+00:00"
    }
  ]
}
```

## Data Fields

### Outbreaks (`combined_outbreaks.json`)

- `id`: Unique outbreak identifier
- `source`: Data source (FDA or CDC)
- `title`: Outbreak title/description
- `url`: Link to investigation page
- `pathogen`: Causative pathogen (Salmonella, Listeria, E. coli, etc.)
- `food_source`: Food vehicle or source
- `status`: Investigation status (ongoing, closed, unknown)
- `case_count`: Number of confirmed cases
- `deaths`: Number of deaths
- `hospitalizations`: Number of hospitalizations
- `state_count`: Number of affected states
- `states_affected`: List of state abbreviations
- `consumer_advice`: Public health recommendations
- `related_recalls`: USDA recalls matched to this outbreak (see Recall Cross-Linking below)
- `scraped_at`: Timestamp of data collection

### USDA Recalls (`usda_recalls.json`)

- `recall_id`: FSIS recall number, e.g. `018-2026` or `PHA-01182024-02`
- `source`: Always `USDA`
- `title`, `url`, `summary`: Recall notice headline, link and press-release text
- `pathogen`: Organism named in the notice, or `null` for allergen/misbranding recalls
- `recall_date`, `last_modified_date`, `year`: Notice dates
- `recall_class`: `Class I`, `Class II` or `Class III`
- `risk_level`: FSIS risk wording, e.g. `High - Class I`, or `Public Health Alert`
- `recall_type`: `Active Recall`, `Closed Recall` or `Public Health Alert`
- `reasons`: Why the product was recalled, e.g. `Product Contamination`
- `products`, `establishment`, `processing`: Product descriptions and plant details
- `states_affected`, `state_count`: Distribution states, as abbreviations
- `distribution`: Non-state reach FSIS lists, e.g. `Nationwide`
- `active`, `archived`: Notice lifecycle flags
- `related_to_outbreak`: FSIS's own flag that this recall stems from an outbreak
- `related_outbreaks`: Investigations matched to this recall (see below)
- `scraped_at`: Timestamp of data collection

### Recall Cross-Linking

USDA recalls are kept out of `combined_outbreaks.json` on purpose: a recall is a
different kind of event and carries no case, death or hospitalization counts, so
folding ~1,200 of them into 313 outbreaks would swamp every headline figure.

Instead the two datasets are joined by cross-links, written by `--combine`. Only
recalls FSIS itself flags as outbreak-related are considered, so the matching
never invents a link the agency doesn't already assert — it only works out
_which_ investigation the flagged recall belongs to, which FSIS does not say. A
pair is linked when the pathogen matches and both name the same food vehicle,
and each link records how well the dates agree:

- `corroborated` - both years are known and within a year of each other
- `weak` - the outbreak record carries no determinable date, so only the
  pathogen and food vehicle back the match

Filter to `corroborated` if you need firm links only.

## Best Practices

1. **Respect Rate Limits**: Use delays between requests (2+ seconds recommended)
2. **Update Regularly**: Run the scraper periodically to get fresh data
3. **Check CDC Website**: Manually check for new CDC investigations and update known URLs in main.py (lines 826-850)
4. **Error Handling**: The scrapers handle errors gracefully but log them to stdout
5. **Data Validation**: Always verify scraped data before using in production

## Limitations

- CDC pages load content dynamically, so automatic discovery may not find all investigations
- Scrapers use heuristic pattern matching, which may miss some data points
- Relies on consistent HTML structure from FDA and CDC websites
- USDA recall cross-links are heuristic; `weak` matches rest on pathogen and food vehicle alone
- FSIS names the pathogen only in prose, so `pathogen` is read out of the notice text
- No real-time notifications (runs on-demand only)

## Future Enhancements

- WebSocket or API integration for real-time updates
- Email/SMS alerting system
- Geographic visualization of outbreaks
- Time-series analysis and trending
- Integration with additional data sources (state health departments)
- Database storage for historical data

## Contributing

Feel free to submit issues or pull requests to improve the scraper or add new features!

## License

MIT
