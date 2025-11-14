# Foodborne Outbreak Alert System

A real-time foodborne outbreak monitoring system that aggregates and analyzes outbreak data from both FDA and CDC sources.

## Features

- **Multi-Source Scraping**: Combines outbreak data from:
  - FDA Public Health Advisories
  - CDC Investigation Updates (Salmonella, Listeria, E. coli, Campylobacter)
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

## Usage

### Basic Usage

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

### Running the Demo

```bash
# Run the demo script
uv run python main.py
```

This will scrape FDA outbreaks and known CDC investigations, then generate three JSON files in `data/raw/`:
- `data/raw/combined_outbreaks.json` - Unified data with summary statistics
- `data/raw/fda_outbreaks.json` - FDA data only
- `data/raw/cdc_outbreaks.json` - CDC data only

### CDC Known URLs

Due to CDC pages loading content dynamically, you may need to provide known investigation URLs:

```python
known_cdc_urls = [
    'https://www.cdc.gov/salmonella/outbreaks/cotham-11-25/investigation.html',
    'https://www.cdc.gov/salmonella/outbreaks/eggs-08-25/investigation.html',
    'https://www.cdc.gov/listeria/outbreaks/ready-to-eat-foods-may-2025/investigation.html',
]

all_data = aggregator.scrape_all_sources(
    fda_limit=None,
    delay=2.0,
    cdc_known_urls=known_cdc_urls
)
```

You can find current CDC investigations at: https://www.cdc.gov/foodborne-outbreaks/outbreaks/

### Individual Scrapers

You can also use the scrapers independently:

**FDA Scraper:**
```python
from main import FDAOutbreakScraper

scraper = FDAOutbreakScraper()
outbreaks = scraper.scrape_all(limit=10, delay=2.0)
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
    "outbreaks_by_source": {"CDC": 3, "FDA": 3},
    "outbreaks_by_pathogen": {"salmonella": 2, "listeria": 1},
    "outbreaks_by_status": {"ongoing": 4, "closed": 2}
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
3. **Check CDC Website**: Manually check for new CDC investigations and update known URLs
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
