[![Foodborne Outbreak nightly update](https://github.com/fartbagxp/foodborne-outbreak-alert/actions/workflows/scrape-outbreaks.yml/badge.svg)](https://github.com/fartbagxp/foodborne-outbreak-alert/actions/workflows/scrape-outbreaks.yml)

# Foodborne Outbreak Alert System

A real-time foodborne outbreak monitoring system that aggregates and analyzes outbreak data from both FDA and CDC sources.

**Current Coverage:**

- 🏛️ FDA: 78 outbreak investigations (2006-2026)
- 🔬 CDC: 230 total investigations (2006-2026)
- 🚨 Active: 33 ongoing investigations
- 📊 Total: 308 foodborne outbreak investigations

## Current Statistics

| Metric                    | Count  |
| ------------------------- | ------ |
| **Total Outbreaks**       | 308    |
| **Active Investigations** | 33     |
| **Total Cases**           | 41,253 |
| **Deaths**                | 5,281  |
| **Hospitalizations**      | 15,901 |
| **States Affected**       | 51     |

## By Source

| Source | Outbreaks | Year Range |
| ------ | --------- | ---------- |
| CDC    | 230       | 2006-2026  |
| FDA    | 78        | 2006-2026  |

## By Status

| Status  | Count |
| ------- | ----- |
| Unknown | 210   |
| Closed  | 65    |
| Ongoing | 33    |

## By Pathogen (Top 10)

| Pathogen   | Outbreaks |
| ---------- | --------- |
| Salmonella | 133       |
| Unknown    | 64        |
| E. coli    | 60        |
| Listeria   | 44        |
| Botulism   | 4         |
| Vibrio     | 3         |

## By Year (Recent)

| Year | Outbreaks |
| ---- | --------- |
| 2026 | 9         |
| 2025 | 13        |
| 2024 | 13        |
| 2023 | 15        |
| 2022 | 24        |
| 2021 | 22        |
| 2020 | 19        |
| 2019 | 26        |
| 2018 | 18        |
| 2017 | 6         |

## Features

- **Multi-Source Scraping**: Combines outbreak data from:
  - FDA Public Health Advisories (70 investigations, 2006-2025)
  - CDC Investigation Updates (219 investigations, 2006-2025)
  - Salmonella, Listeria, E. coli, and other pathogens
- **Command-Line Interface**: Run FDA and CDC scrapers separately or together with `--fda` and `--cdc` flags
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
# Scrape both FDA and CDC data
uv run python main.py

# Or scrape selectively
uv run python main.py --fda  # FDA only (faster, ~70 outbreaks)
uv run python main.py --cdc  # CDC only (18 investigations)
```

This will create JSON files in `data/raw/` with outbreak data, statistics, and normalized formats ready for analysis.

## Usage

### Command Line Interface

The scraper supports command-line arguments to run FDA and CDC scrapers separately or together:

```bash
# Scrape both FDA and CDC (default - 88 total outbreaks)
uv run python main.py

# Scrape FDA only (70 outbreaks from 2011-2025)
uv run python main.py --fda

# Scrape CDC only (18 investigations from 2024-2025)
uv run python main.py --cdc

# Scrape both with custom delay
uv run python main.py --fda --cdc --delay 3.0

# Show help and available options
uv run python main.py --help
```

**Output files generated:**

- `data/raw/combined_outbreaks.json` - Unified data with summary statistics
- `data/raw/fda_outbreaks.json` - FDA data only (when `--fda` is used)
- `data/raw/cdc_outbreaks.json` - CDC data only (when `--cdc` is used)

**Command-line options:**

- `--fda` - Scrape FDA outbreak data only
- `--cdc` - Scrape CDC outbreak data only
- `--delay DELAY` - Delay between requests in seconds (default: 2.0)
- `--help` - Show help message

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
- `scraped_at`: Timestamp of data collection

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
- No real-time notifications (runs on-demand only)

## Future Enhancements

- WebSocket or API integration for real-time updates
- Email/SMS alerting system
- Geographic visualization of outbreaks
- Time-series analysis and trending
- Integration with additional data sources (USDA, state health departments)
- Database storage for historical data

## Contributing

Feel free to submit issues or pull requests to improve the scraper or add new features!

## License

MIT
