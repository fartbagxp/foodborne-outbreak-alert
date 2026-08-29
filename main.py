"""
Foodborne Outbreak Alert System
Scrapes outbreak data from FDA and CDC public health sources
"""

import argparse
import csv
import io
import html
import json
import logging
import os
import re
import requests
import sys
import time

from bs4 import BeautifulSoup
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from typing import List, Dict, Optional

# Module logger. Configured by setup_logging() in main(); if a caller uses the
# scrapers without configuring logging, messages still surface via this handler.
log = logging.getLogger("outbreak")


def setup_logging(log_dir: str = "logs") -> str:
    """
    Configure logging to both the console and a timestamped file.

    The console shows INFO-level progress (a clean running log of what is being
    gathered and whether each item succeeded). The file additionally captures
    DEBUG-level detail (individual fetch attempts, archive fallbacks) so there
    is a complete record to review after a run.

    Returns the path to the log file.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = str(Path(log_dir) / f"scrape-{timestamp}.log")

    log.setLevel(logging.DEBUG)
    log.handlers.clear()  # avoid duplicate handlers if called more than once
    log.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))

    log.addHandler(console)
    log.addHandler(file_handler)

    log.info("Logging to %s", log_path)
    return log_path


_MONTH_NAMES = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10,
    'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}
_SEASON_MONTHS = {'spring': 3, 'summer': 6, 'fall': 9, 'autumn': 9, 'winter': 12}


def parse_fda_date_str(date_str: Optional[str]) -> Optional[datetime]:
    """
    Parse FDA's listing-page date, e.g. 'August 2026' or 'Sept 2024', to a
    UTC datetime. Looked up via _MONTH_NAMES (rather than strptime) so
    abbreviations like 'Sept' parse too, not just full month names.
    """
    if not date_str:
        return None
    parts = date_str.strip().split()
    if len(parts) != 2:
        return None
    month_token, year_token = parts
    month = _MONTH_NAMES.get(month_token.lower())
    if not month or not year_token.isdigit():
        return None
    try:
        return datetime(int(year_token), month, 1, tzinfo=timezone.utc)
    except ValueError:
        return None


def infer_fda_outbreak_date(outbreak_id: str) -> Optional[datetime]:
    """
    Best-effort inference of an outbreak's date from its FDA slug, used when
    the listing-page title didn't yield a parseable date_str. FDA titles
    aren't consistently 'Pathogen: Food (Month Year)' — some use a hyphen
    instead of a colon (e.g. 'E. coli- Packaged Salad (January 2022)'), which
    skips date extraction in _parse_listing_link entirely. Handles slug
    endings like '...-january-2022' and season slugs like '...-fall-2020'.
    """
    m = re.search(r'-([a-zA-Z]+)-(\d{4})$', outbreak_id)
    if not m:
        return None
    token, year = m.group(1).lower(), int(m.group(2))
    month = _MONTH_NAMES.get(token) or _SEASON_MONTHS.get(token)
    if not month:
        return None
    try:
        return datetime(year, month, 1, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_cdc_posted_date(posted_date: Optional[str]) -> Optional[datetime]:
    """Parse CDC's extracted 'posted/updated' date, e.g. 'March 15, 2025'."""
    if not posted_date:
        return None
    try:
        return datetime.strptime(posted_date.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def infer_cdc_outbreak_date(outbreak_id: str) -> Optional[datetime]:
    """
    Best-effort inference of an outbreak's date from its CDC slug, since CDC
    investigation pages rarely expose a clean posted date. Handles the site's
    slug conventions: '...-07-26' (MM-YY), '...-nov-2025' (Mon-YYYY),
    '...-022025' (MMYYYY run together). Returns None if nothing matches.
    """
    for pattern, to_month_year in (
        (re.compile(r'-(\d{1,2})-(\d{2})$'), lambda mo, yr: (int(mo), 2000 + int(yr))),
        (re.compile(r'-([a-zA-Z]+)-(\d{4})$'), lambda mo, yr: (_MONTH_NAMES.get(mo.lower()), int(yr))),
        (re.compile(r'-(\d{2})(\d{4})$'), lambda mo, yr: (int(mo), int(yr))),
    ):
        m = pattern.search(outbreak_id)
        if not m:
            continue
        month, year = to_month_year(*m.groups())
        if not month:
            continue
        try:
            return datetime(year, month, 1, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_stale(outbreak_date: Optional[datetime], max_age_days: int, now: datetime) -> bool:
    """
    True if outbreak_date is known and older than max_age_days. An outbreak
    whose date can't be determined is never considered stale — when in doubt,
    re-scrape rather than silently miss an update.
    """
    if outbreak_date is None:
        return False
    return (now - outbreak_date).days > max_age_days


def load_existing_by_id(path: str) -> Dict[str, Dict]:
    """Load a previously-saved outbreaks JSON file, keyed by outbreak_id."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except Exception as e:
        log.warning("Could not load existing data from %s: %s", path, e)
        return {}
    return {r['outbreak_id']: r for r in records if r.get('outbreak_id')}


class FDAOutbreakScraper:
    def __init__(self):
        self.base_url = "https://www.fda.gov"
        self.listing_url = "https://www.fda.gov/food/outbreaks-foodborne-illness/public-health-advisories-investigations-foodborne-illness-outbreaks"
        # Shared Playwright browser context, reused across the run for speed.
        # FDA sits behind Akamai, which redirects to an "abuse detection" page
        # for some requests. Confirmed via real GitHub Actions runs that this
        # is IP-based, not a bot/fingerprint check: a real headless Chromium
        # got redirected exactly like plain `requests` did, from a GitHub
        # Actions runner IP, while the same code passes from other IPs.
        # GitHub Actions runner IPs are drawn from a large shared pool and
        # only part of it is on Akamai's blocklist at any given time, so
        # results vary run to run. FDA_PROXY_SERVER (optionally with
        # FDA_PROXY_USERNAME / FDA_PROXY_PASSWORD) routes traffic through a
        # fixed non-GitHub egress IP instead — see fly-proxy/README.md. Unset
        # locally, where FDA has not been observed to block requests.
        self._playwright = None
        self._browser = None
        self._context = None
        self._proxy_server = os.environ.get('FDA_PROXY_SERVER')
        self._proxy_username = os.environ.get('FDA_PROXY_USERNAME')
        self._proxy_password = os.environ.get('FDA_PROXY_PASSWORD')

    def _ensure_context(self):
        """Lazily start a shared Playwright browser context and return it."""
        if self._context is None:
            self._playwright = sync_playwright().start()
            launch_kwargs = {"headless": True}
            if self._proxy_server:
                proxy = {"server": self._proxy_server}
                if self._proxy_username:
                    proxy["username"] = self._proxy_username
                if self._proxy_password:
                    proxy["password"] = self._proxy_password
                launch_kwargs["proxy"] = proxy
                log.info("Routing FDA traffic through proxy %s", self._proxy_server)
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
            self._context = self._browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self._context.set_default_timeout(60000)
        return self._context

    def _fetch_page(self, url: str):
        """
        Fetch a page through a real browser (Playwright) so FDA's Akamai bot
        protection does not redirect us to its abuse-detection page the way
        it does plain HTTP clients.

        Returns (status_code, final_url, html). Raises on navigation failure.
        """
        context = self._ensure_context()
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = response.status if response else None
            final_url = page.url
            html = page.content()
            return status, final_url, html
        finally:
            page.close()

    def close(self):
        """Tear down the shared Playwright browser, if one was started."""
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = self._browser = self._playwright = None

    def scrape_listing_page(self) -> List[Dict]:
        """
        Scrape the main listing page for all outbreak links
        Returns a list of outbreak metadata
        """
        log.info("Fetching FDA listing page: %s", self.listing_url)
        status, final_url, html = self._fetch_page(self.listing_url)
        if not status or not (200 <= status < 300):
            raise RuntimeError(f"HTTP {status} fetching FDA listing page {self.listing_url} (final url {final_url})")

        soup = BeautifulSoup(html, 'html.parser')
        outbreaks = []

        # Find all outbreak links (they're in anchor tags)
        links = soup.find_all('a', href=re.compile(r'/food/outbreaks-foodborne-illness/outbreak-investigation-'))

        for link in links:
            outbreak_data = self._parse_listing_link(link)
            if outbreak_data:
                outbreaks.append(outbreak_data)

        log.info("Found %d outbreaks on FDA listing page", len(outbreaks))
        return outbreaks

    def _parse_listing_link(self, link) -> Optional[Dict]:
        """Parse a single outbreak link from the listing page"""
        href = link.get('href')
        title = link.get_text(strip=True)

        if not href or not title:
            return None

        # Skip navigation pages (not actual outbreak investigations)
        if href.endswith('outbreak-investigation-reports'):
            return None

        # Build full URL
        full_url = self.base_url + href if href.startswith('/') else href

        # Extract pathogen and food item from title
        # Format is usually: "Pathogen: Food Item (Month Year)"
        pathogen = None
        food_item = None
        date_str = None

        # Try to parse title
        if ':' in title:
            parts = title.split(':', 1)
            pathogen = parts[0].strip()
            rest = parts[1].strip()

            # Extract date in parentheses
            date_match = re.search(r'\(([^)]+)\)', rest)
            if date_match:
                date_str = date_match.group(1)
                food_item = rest[:date_match.start()].strip()
            else:
                food_item = rest

        # Generate a unique ID from the URL
        outbreak_id = href.split('/')[-1] if '/' in href else href

        return {
            'outbreak_id': outbreak_id,
            'title': title,
            'url': full_url,
            'pathogen': pathogen,
            'food_item': food_item,
            'date_str': date_str,
            'scraped_at': datetime.now(timezone.utc).isoformat()
        }

    def scrape_outbreak_details(self, outbreak_url: str) -> Dict:
        """
        Scrape detailed information from an individual outbreak page
        """
        log.debug("Fetching FDA outbreak details: %s", outbreak_url)

        try:
            status, final_url, html = self._fetch_page(outbreak_url)
        except Exception as e:
            log.warning("Error fetching %s: %s", outbreak_url, e)
            return {
                'url': outbreak_url,
                'scraped_at': datetime.now(timezone.utc).isoformat(),
                'scrape_status': 'error',
                'scrape_error': str(e)
            }

        if not status or not (200 <= status < 300):
            log.warning("HTTP %s fetching %s", status, outbreak_url)
            return {
                'url': outbreak_url,
                'scraped_at': datetime.now(timezone.utc).isoformat(),
                'scrape_status': f'http_error_{status}',
                'scrape_error': f'HTTP {status} for {outbreak_url}'
            }

        log.debug("Fetched %s", outbreak_url)
        soup = BeautifulSoup(html, 'html.parser')

        details = {
            'url': final_url,
            'original_url': outbreak_url if final_url != outbreak_url else None,
            'scraped_at': datetime.now(timezone.utc).isoformat(),
            'scrape_status': 'success'
        }

        # Remove None values
        details = {k: v for k, v in details.items() if v is not None}

        # Extract main content
        main_content = soup.find('div', class_='panel-pane')
        if not main_content:
            main_content = soup.find('article')

        if main_content:
            # Get full text content
            details['full_text'] = main_content.get_text(separator='\n', strip=True)

            # Look for specific information patterns
            text = details['full_text']

            # Extract case counts
            case_match = re.search(r'(\d+)\s+(?:people|individuals|cases|illnesses)', text, re.IGNORECASE)
            if case_match:
                details['case_count'] = int(case_match.group(1))

            # Extract deaths
            death_match = re.search(r'(\d+)\s+death', text, re.IGNORECASE)
            if death_match:
                details['deaths'] = int(death_match.group(1))

            # Extract hospitalizations
            hosp_match = re.search(r'(\d+)\s+(?:hospitalized|hospitalizations)', text, re.IGNORECASE)
            if hosp_match:
                details['hospitalizations'] = int(hosp_match.group(1))

            # Extract states mentioned
            states = self._extract_states(text)
            if states:
                details['states_affected'] = states

            # Look for "What to do" or "Advice" sections
            advice_section = main_content.find(['h2', 'h3'], string=re.compile(r'what.*do|advice|recommendation', re.IGNORECASE))
            if advice_section:
                # Get the next few paragraphs
                advice_text = []
                for sibling in advice_section.find_next_siblings(['p', 'ul', 'ol']):
                    if sibling.name in ['h2', 'h3']:
                        break
                    advice_text.append(sibling.get_text(strip=True))
                details['consumer_advice'] = '\n'.join(advice_text)

            # Look for product/brand information
            details['brands'] = self._extract_brands(text)
            details['products'] = self._extract_products(soup)

        return details

    def _extract_states(self, text: str) -> List[str]:
        """Extract US state abbreviations from text"""
        # Common US state abbreviations
        state_pattern = r'\b([A-Z]{2})\b'
        potential_states = re.findall(state_pattern, text)

        # Filter to valid state codes (simplified - you'd want a complete list)
        valid_states = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                       'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                       'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                       'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                       'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'}

        found_states = [s for s in potential_states if s in valid_states]
        return list(set(found_states))  # Remove duplicates

    def _extract_brands(self, text: str) -> List[str]:
        """Extract brand names (simplified - looks for capitalized words)"""
        # This is a simple heuristic - you'd want to improve this
        brand_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:brand|foods|company|products)'
        brands = re.findall(brand_pattern, text, re.IGNORECASE)
        return list(set(brands))

    def _extract_products(self, soup: BeautifulSoup) -> List[str]:
        """Extract specific product descriptions"""
        products = []

        # Look for lists or tables that might contain product info
        for ul in soup.find_all(['ul', 'ol']):
            for li in ul.find_all('li'):
                text = li.get_text(strip=True)
                # If it looks like a product description (contains numbers, brands, etc.)
                if re.search(r'\d+\s*oz|\d+\s*lb|lot|UPC|best by', text, re.IGNORECASE):
                    products.append(text)

        return products

    def scrape_all(self, limit: Optional[int] = None, delay: float = 1.0,
                    existing: Optional[Dict[str, Dict]] = None,
                    max_age_days: Optional[int] = None) -> List[Dict]:
        """
        Scrape all outbreaks with full details

        Args:
            limit: Maximum number of outbreaks to scrape (None for all)
            delay: Delay between requests in seconds (be respectful!)
            existing: Previously-scraped outbreaks, keyed by outbreak_id. When
                given together with max_age_days, an outbreak already scraped
                successfully and older than max_age_days is reused instead of
                re-fetched, since FDA does not update closed outbreak pages.
            max_age_days: Skip re-fetching previously-successful outbreaks
                older than this many days. None disables skipping (full scrape).
        """
        try:
            # Get listing
            outbreaks = self.scrape_listing_page()

            if limit:
                outbreaks = outbreaks[:limit]

            existing = existing or {}
            now = datetime.now(timezone.utc)

            to_fetch = []
            skip_map = {}
            for outbreak in outbreaks:
                prior = existing.get(outbreak['outbreak_id'])
                if max_age_days is not None and prior and prior.get('scrape_status') == 'success':
                    outbreak_date = (parse_fda_date_str(prior.get('date_str'))
                                      or infer_fda_outbreak_date(outbreak['outbreak_id']))
                    if is_stale(outbreak_date, max_age_days, now):
                        skip_map[outbreak['outbreak_id']] = prior
                        continue
                to_fetch.append(outbreak)

            if skip_map:
                log.info("Skipping %d FDA outbreaks already scraped and older than %d days",
                          len(skip_map), max_age_days)

            # Scrape details for each remaining outbreak
            fetched_map = {}
            total = len(to_fetch)
            for i, outbreak in enumerate(to_fetch, 1):
                details = self.scrape_outbreak_details(outbreak['url'])
                status = details.get('scrape_status', 'unknown')
                if status == 'success':
                    log.info("[%d/%d] OK   %s (cases=%s, deaths=%s)",
                             i, total, outbreak['title'],
                             details.get('case_count', '?'), details.get('deaths', '?'))
                else:
                    log.warning("[%d/%d] FAIL %s -> %s", i, total, outbreak['title'], status)

                # Merge listing data with detailed data
                full_data = {**outbreak, **details}
                fetched_map[outbreak['outbreak_id']] = full_data

                # Be respectful - add delay
                if i < total:
                    time.sleep(delay)

            # Reassemble in the listing page's original (newest-first) order,
            # rather than the skip/fetch split order used above.
            detailed_outbreaks = [
                skip_map.get(o['outbreak_id']) or fetched_map[o['outbreak_id']] for o in outbreaks
            ]

            # Carry forward older outbreaks no longer on the current listing page
            # (FDA can drop old entries from the listing) so history isn't lost.
            current_ids = {o['outbreak_id'] for o in outbreaks}
            for outbreak_id, record in existing.items():
                if outbreak_id not in current_ids:
                    detailed_outbreaks.append(record)

            return detailed_outbreaks
        finally:
            # Always release the shared Playwright browser used for fetches.
            self.close()

    def save_to_json(self, outbreaks: List[Dict], filename: str = 'data/raw/fda_outbreaks.json'):
        """Save scraped data to JSON file"""
        # Ensure directory exists
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(outbreaks, f, indent=2, ensure_ascii=False)
        log.info("Saved %d FDA outbreaks to %s", len(outbreaks), filename)


class CDCOutbreakScraper:
    """
    Scraper for CDC outbreak investigation pages
    CDC organizes outbreaks by pathogen type with individual investigation pages
    """

    def __init__(self):
        self.base_url = "https://www.cdc.gov"
        self.pathogen_pages = {
            'salmonella': 'https://www.cdc.gov/salmonella/outbreaks/index.html',
            'listeria': 'https://www.cdc.gov/listeria/outbreaks/index.html',
            'ecoli': 'https://www.cdc.gov/ecoli/outbreaks/index.html',
            'campylobacter': 'https://www.cdc.gov/campylobacter/outbreaks/index.html',
        }
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        # Shared Playwright browser used for detail fetches. CDC sits behind
        # Akamai bot protection that returns 403 to plain HTTP clients (like
        # `requests`) from datacenter IPs such as GitHub Actions runners, while
        # a real browser passes. The browser is created lazily and reused across
        # the run for speed; call close() when finished.
        self._playwright = None
        self._browser = None
        self._context = None

    def _ensure_context(self):
        """Lazily start a shared Playwright browser context and return it."""
        if self._context is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self._context.set_default_timeout(60000)
        return self._context

    def _fetch_page(self, url: str):
        """
        Fetch a page through a real browser (Playwright) so CDC's Akamai bot
        protection does not 403 us the way it does plain HTTP clients.

        Returns (status_code, final_url, html). Raises on navigation failure.
        """
        context = self._ensure_context()
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = response.status if response else None
            final_url = page.url
            html = page.content()
            return status, final_url, html
        finally:
            page.close()

    def close(self):
        """Tear down the shared Playwright browser, if one was started."""
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = self._browser = self._playwright = None

    def discover_investigation_links(self, pathogen: str) -> List[Dict]:
        """
        Discover active outbreak investigation links for a given pathogen
        Uses multiple strategies since CDC pages load dynamically
        """
        investigations = []

        # Try to scrape the pathogen page
        if pathogen not in self.pathogen_pages:
            log.warning("Unknown pathogen: %s", pathogen)
            return investigations

        url = self.pathogen_pages[pathogen]
        log.info("Discovering %s investigations from %s", pathogen, url)

        try:
            response = self.session.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # Strategy 1: Look for investigation links with the standard pattern
            pattern = re.compile(rf'/{pathogen}/outbreaks/[^/]+/investigation\.html')
            for link in soup.find_all('a', href=pattern):
                href = link.get('href')
                if href:
                    full_url = urljoin(self.base_url, href)
                    title = link.get_text(strip=True)

                    # Extract identifier from URL
                    match = re.search(rf'/{pathogen}/outbreaks/([^/]+)/investigation\.html', href)
                    outbreak_id = match.group(1) if match else href.split('/')[-2]

                    investigations.append({
                        'outbreak_id': f'cdc_{pathogen}_{outbreak_id}',
                        'pathogen': pathogen,
                        'url': full_url,
                        'title': title,
                        'source': 'CDC'
                    })

            # Strategy 2: Look for any outbreak-related links (broader pattern)
            if not investigations:
                broader_pattern = re.compile(rf'/{pathogen}/outbreaks/[^/]+')
                for link in soup.find_all('a', href=broader_pattern):
                    href = link.get('href')
                    if href and 'investigation' in href.lower():
                        full_url = urljoin(self.base_url, href)
                        title = link.get_text(strip=True)
                        outbreak_id = href.split('/')[-1].replace('.html', '')

                        if outbreak_id and outbreak_id != 'investigation':
                            investigations.append({
                                'outbreak_id': f'cdc_{pathogen}_{outbreak_id}',
                                'pathogen': pathogen,
                                'url': full_url,
                                'title': title or f'{pathogen.title()} outbreak',
                                'source': 'CDC'
                            })

            # Strategy 3: Check for "card" or "list item" elements that might contain outbreak info
            if not investigations:
                # CDC often uses cards or list items for outbreak listings
                for card in soup.find_all(['div', 'li'], class_=re.compile(r'card|outbreak|list-item')):
                    link = card.find('a', href=re.compile(rf'/{pathogen}/outbreaks/'))
                    if link:
                        href = link.get('href')
                        if href and 'investigation' in href.lower():
                            full_url = urljoin(self.base_url, href)
                            title = link.get_text(strip=True) or card.get_text(strip=True)[:100]
                            outbreak_id = href.split('/')[-2] if '/' in href else 'unknown'

                            investigations.append({
                                'outbreak_id': f'cdc_{pathogen}_{outbreak_id}',
                                'pathogen': pathogen,
                                'url': full_url,
                                'title': title,
                                'source': 'CDC'
                            })

            log.info("Found %d %s investigations", len(investigations), pathogen)

        except Exception as e:
            log.error("Error discovering %s investigations: %s", pathogen, e)

        return investigations

    def scrape_investigation_details(self, investigation_url: str, pathogen: str) -> Dict:
        """
        Scrape detailed information from a CDC outbreak investigation page
        """
        log.debug("Fetching CDC investigation: %s", investigation_url)

        def _error(status, error):
            return {
                'url': investigation_url,
                'pathogen': pathogen,
                'source': 'CDC',
                'scraped_at': datetime.now(timezone.utc).isoformat(),
                'scrape_status': status,
                'scrape_error': error,
            }

        # Try multiple URL variations if the original fails.
        # If the original URL fails, try the CDC archive.
        # Archive URL format: https://archive.cdc.gov/www_cdc_gov/[path]
        urls_to_try = [investigation_url]
        if investigation_url.startswith('https://www.cdc.gov/'):
            urls_to_try.append(investigation_url.replace('https://www.cdc.gov/', 'https://archive.cdc.gov/www_cdc_gov/'))
        elif investigation_url.startswith('http://www.cdc.gov/'):
            urls_to_try.append(investigation_url.replace('http://www.cdc.gov/', 'https://archive.cdc.gov/www_cdc_gov/'))

        html = None
        final_url = investigation_url
        last_status = None

        for try_url in urls_to_try:
            is_last = try_url == urls_to_try[-1]
            try:
                status, final_url, html = self._fetch_page(try_url)
            except Exception as e:
                log.debug("Error fetching %s: %s", try_url, e)
                html = None
                if is_last:
                    return _error('error', str(e))
                continue

            last_status = status

            if status and 200 <= status < 300:
                log.debug("Fetched %s (status %s)", try_url, status)
                break

            # Non-success status: discard the (likely error) page body.
            html = None
            if status == 404:
                if not is_last:
                    log.debug("404 at %s, trying CDC archive", try_url)
                    continue
                log.debug("404 in archive too for %s", investigation_url)
                return _error('404_not_found', 'Page not found (tried original and archive)')

            log.debug("HTTP %s at %s", status, try_url)
            if is_last:
                return _error(f'http_error_{status}', f'HTTP {status} for {try_url}')
            continue

        if not html:
            return _error('error', f'No content received (last status {last_status})')

        soup = BeautifulSoup(html, 'html.parser')

        details = {
            'url': final_url,  # Use the final URL (may differ if redirected)
            'original_url': investigation_url if final_url != investigation_url else None,
            'pathogen': pathogen,
            'source': 'CDC',
            'scraped_at': datetime.now(timezone.utc).isoformat(),
            'scrape_status': 'success'
        }

        # Remove None values
        details = {k: v for k, v in details.items() if v is not None}

        # Extract title
        title = soup.find('h1')
        if title:
            details['title'] = title.get_text(strip=True)

        # Get main content
        content = soup.find('div', class_='content') or soup.find('main') or soup.find('article')

        if content:
            full_text = content.get_text(separator='\n', strip=True)
            details['full_text'] = full_text

            # Extract investigation status
            if re.search(r'investigation.*(?:closed|ended|over)', full_text, re.IGNORECASE):
                details['status'] = 'closed'
            elif re.search(r'investigation.*ongoing|active', full_text, re.IGNORECASE):
                details['status'] = 'ongoing'
            else:
                details['status'] = 'unknown'

            # Extract case count - CDC often says "X people" or "X cases"
            case_patterns = [
                r'(\d+)\s+people?\s+(?:infected|reported|ill)',
                r'(\d+)\s+cases?',
                r'total.*?(\d+)\s+people'
            ]
            for pattern in case_patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    details['case_count'] = int(match.group(1))
                    break

            # Extract deaths
            death_match = re.search(r'(\d+)\s+death', full_text, re.IGNORECASE)
            if death_match:
                details['deaths'] = int(death_match.group(1))

            # Extract hospitalizations - CDC often gives "X of Y" format
            hosp_patterns = [
                r'(\d+)\s+of\s+\d+.*?hospitalized',
                r'(\d+).*?hospitalized',
                r'hospitalizations?:\s*(\d+)'
            ]
            for pattern in hosp_patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    details['hospitalizations'] = int(match.group(1))
                    break

            # Extract states - CDC often says "X states"
            state_count_match = re.search(r'(\d+)\s+states?', full_text, re.IGNORECASE)
            if state_count_match:
                details['state_count'] = int(state_count_match.group(1))

            # Extract specific states
            states = self._extract_states(full_text)
            if states:
                details['states_affected'] = states

            # Extract posted/updated date
            date_match = re.search(r'(?:posted|updated).*?(\w+\s+\d+,\s+\d{4})', full_text, re.IGNORECASE)
            if date_match:
                details['posted_date'] = date_match.group(1)

            # Extract illness date range
            illness_range = re.search(r'illness.*?(\w+\s+\d+,\s+\d{4})\s+to\s+(\w+\s+\d+,\s+\d{4})', full_text, re.IGNORECASE)
            if illness_range:
                details['illness_start_date'] = illness_range.group(1)
                details['illness_end_date'] = illness_range.group(2)

            # Extract food source/vehicle
            food_patterns = [
                r'linked to\s+([^.]+?)(?:\.|$)',
                r'source.*?:\s*([^.]+?)(?:\.|$)',
                r'vehicle.*?:\s*([^.]+?)(?:\.|$)'
            ]
            for pattern in food_patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    potential_food = match.group(1).strip()
                    # Clean up the extracted text
                    if len(potential_food) < 100:  # Reasonable length
                        details['food_source'] = potential_food
                        break

            # Extract recommendations/advice
            advice_headers = ['what you should do', 'recommendations', 'advice', 'actions']
            for header in advice_headers:
                advice_section = content.find(['h2', 'h3', 'h4'], string=re.compile(header, re.IGNORECASE))
                if advice_section:
                    advice_text = []
                    for sibling in advice_section.find_next_siblings(['p', 'ul', 'ol', 'li']):
                        if sibling.name in ['h2', 'h3', 'h4']:
                            break
                        advice_text.append(sibling.get_text(strip=True))
                    if advice_text:
                        details['consumer_advice'] = '\n'.join(advice_text)
                        break

        return details

    def _extract_states(self, text: str) -> List[str]:
        """Extract US state abbreviations from text"""
        state_pattern = r'\b([A-Z]{2})\b'
        potential_states = re.findall(state_pattern, text)

        valid_states = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                       'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                       'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                       'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                       'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'}

        found_states = [s for s in potential_states if s in valid_states]
        return list(set(found_states))

    def discover_from_main_page(self) -> List[Dict]:
        """
        Discover outbreaks from the main CDC foodborne outbreaks page
        This page lists all currently active investigations
        """
        main_url = "https://www.cdc.gov/foodborne-outbreaks/outbreaks/"
        log.info("Discovering from main CDC outbreak page: %s", main_url)
        investigations = []

        try:
            response = self.session.get(main_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # Look for any links to outbreak investigation pages
            investigation_pattern = re.compile(r'/(salmonella|listeria|ecoli|campylobacter|cyclospora|vibrio|shigella)/outbreaks/.+/investigation')

            for link in soup.find_all('a', href=investigation_pattern):
                href = link.get('href')
                if href:
                    full_url = urljoin(self.base_url, href)
                    title = link.get_text(strip=True)

                    # Extract pathogen from URL
                    pathogen_match = re.search(r'/(salmonella|listeria|ecoli|campylobacter|cyclospora|vibrio|shigella)/', href)
                    pathogen = pathogen_match.group(1) if pathogen_match else 'unknown'

                    # Extract identifier
                    outbreak_id = href.split('/')[-2] if '/' in href else 'unknown'

                    investigations.append({
                        'outbreak_id': f'cdc_{pathogen}_{outbreak_id}',
                        'pathogen': pathogen,
                        'url': full_url,
                        'title': title or f'{pathogen.title()} outbreak',
                        'source': 'CDC'
                    })

            log.info("Found %d investigations from main page", len(investigations))

        except Exception as e:
            log.error("Error scraping main CDC page: %s", e)

        return investigations

    def discover_from_csv_file(self) -> List[Dict]:
        """
        Discover outbreaks by downloading the CSV file that the CDC uses for their DataTable
        This is faster and more reliable than scraping the paginated JavaScript table
        """
        csv_url = "https://www.cdc.gov/foodborne-outbreaks/media/files/2024/04/full-outbreak-list.csv"
        log.info("Fetching outbreak data from CSV: %s", csv_url)
        investigations = []

        try:
            response = self.session.get(csv_url)
            response.raise_for_status()

            # Parse CSV
            csv_content = response.content.decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(csv_content))

            for row in csv_reader:
                # CSV columns: Contaminated Food, Germ, Year
                # The "Contaminated Food" value might be a link text, we need to construct the URL
                food = row.get('Contaminated Food', '').strip()
                germ = row.get('Germ', '').strip()
                year = row.get('Year', '').strip()

                if not food:
                    continue

                # The food name is the link text in the table, but we need to find the actual URL
                # We'll need to scrape the main page to get the URLs, or use the Playwright method
                # For now, let's use the Playwright method to get the actual URLs
                investigations.append({
                    'food': food,
                    'germ': germ,
                    'year': year
                })

            log.info("Found %d outbreak entries in CSV", len(investigations))

        except Exception as e:
            log.error("Error fetching CSV file: %s", e)

        return investigations

    def discover_from_main_page_playwright(self) -> List[Dict]:
        """
        Discover outbreaks from the main CDC foodborne outbreaks page using Playwright
        This handles JavaScript-rendered content and pagination
        """
        main_url = "https://www.cdc.gov/foodborne-outbreaks/outbreaks/index.html"
        log.info("Discovering from main CDC outbreak page via Playwright: %s", main_url)
        investigations = []

        try:
            with sync_playwright() as p:
                # Launch browser in headless mode
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = context.new_page()

                # Set longer timeout for slow-loading pages
                page.set_default_timeout(60000)  # 60 seconds

                # Navigate to the page - use 'load' instead of 'networkidle' for faster loading
                log.info("Loading page (this can take up to 60s)...")
                try:
                    page.goto(main_url, wait_until="load", timeout=60000)
                except PlaywrightTimeoutError:
                    log.warning("Page load timeout, retrying with domcontentloaded...")
                    page.goto(main_url, wait_until="domcontentloaded", timeout=60000)

                # Wait for the table to load - the table has id or class we need to identify
                # Try multiple selectors since CDC might use different structures
                try:
                    # Wait for table to appear (adjust selector based on actual page structure)
                    page.wait_for_selector('table', timeout=20000)
                    log.debug("Outbreak table loaded")
                except PlaywrightTimeoutError:
                    log.warning("Outbreak table did not load within timeout, proceeding anyway")

                # Give JavaScript more time to fully render the content
                log.debug("Waiting for JavaScript to render...")
                time.sleep(5)

                # Get all links from the table using Playwright directly (more reliable)
                log.debug("Extracting outbreak links from table...")
                links_found = set()  # Use set to avoid duplicates

                # Find all table rows
                table_rows = page.locator('table tbody tr').all()
                log.info("Initial table view has %d rows", len(table_rows))

                # Since the table is paginated, we need to get all pages
                # First, try to get the total number of entries
                try:
                    pagination_info = page.locator('.dataTables_info').inner_text()
                    log.info("Pagination: %s", pagination_info)

                    # Try to show all entries by changing the page size to maximum
                    try:
                        # Click on the page size dropdown and select a large number
                        page.select_option('select[name*="DataTables"]', '100')
                        log.debug("Changed page size to 100")
                        time.sleep(2)  # Wait for table to reload
                    except Exception:
                        log.debug("Could not change page size, will paginate manually")
                except Exception:
                    pass

                # Now extract all links from all visible pages
                page_num = 1
                while True:
                    # Get content of current page
                    html_content = page.content()
                    soup = BeautifulSoup(html_content, 'html.parser')

                    # Find the table and extract all links from rows
                    table = soup.find('table')
                    if table:
                        for row in table.find_all('tr'):
                            # Find all links in the first column (Contaminated Food)
                            link = row.find('td')
                            if link:
                                a_tag = link.find('a')
                                if a_tag and a_tag.get('href'):
                                    href = a_tag.get('href')
                                    if href and href not in links_found:
                                        links_found.add(href)
                                        full_url = urljoin(self.base_url, href)
                                        title = a_tag.get_text(strip=True)

                                        # Extract pathogen from URL
                                        pathogen_match = re.search(r'/(salmonella|listeria|ecoli|campylobacter|cyclospora|vibrio|shigella|botulism)/', href)
                                        pathogen = pathogen_match.group(1) if pathogen_match else 'unknown'

                                        # Extract identifier from URL
                                        parts = href.rstrip('/').split('/')
                                        outbreak_id = parts[-2] if len(parts) > 1 else 'unknown'

                                        investigations.append({
                                            'outbreak_id': f'cdc_{pathogen}_{outbreak_id}',
                                            'pathogen': pathogen,
                                            'url': full_url,
                                            'title': title or f'{pathogen.title()} outbreak',
                                            'source': 'CDC'
                                        })

                    # Try to go to next page
                    try:
                        next_button = page.locator('#DataTables_Table_0_next')
                        if next_button.get_attribute('class') and 'disabled' not in next_button.get_attribute('class'):
                            log.debug("Paginating to page %d (%d links so far)", page_num + 1, len(investigations))
                            next_button.click()
                            page_num += 1
                            time.sleep(2)  # Wait for page to load
                        else:
                            log.debug("Reached last page of the table")
                            break
                    except Exception:
                        log.debug("No more pages to paginate")
                        break

                browser.close()

                log.info("Discovered %d investigations from main page (Playwright)", len(investigations))

        except Exception:
            log.exception("Error scraping main CDC page with Playwright")

        return investigations

    def _log_result(self, index: int, total: int, investigation: Dict, details: Dict):
        """Log a one-line outcome for a single investigation fetch."""
        label = investigation.get('title') or investigation.get('url', 'Unknown')
        status = details.get('scrape_status', 'unknown')
        if status == 'success':
            log.info("[%d/%d] OK   %s (cases=%s, deaths=%s, status=%s)",
                     index, total, label,
                     details.get('case_count', '?'),
                     details.get('deaths', '?'),
                     details.get('status', '?'))
        else:
            log.warning("[%d/%d] FAIL %s -> %s", index, total, label, status)

    def _log_scrape_summary(self, results: List[Dict]):
        """Log a summary of how many investigations succeeded vs failed and why."""
        statuses = Counter(r.get('scrape_status', 'unknown') for r in results)
        total = len(results)
        succeeded = statuses.get('success', 0)
        failed = total - succeeded

        log.info("-" * 60)
        log.info("CDC scrape summary: %d attempted, %d succeeded, %d failed", total, succeeded, failed)
        for status, count in statuses.most_common():
            log.info("  %-16s %d", status, count)
        if failed:
            log.info("Failed items:")
            for r in results:
                if r.get('scrape_status', 'unknown') != 'success':
                    log.info("  [%s] %s", r.get('scrape_status', 'unknown'), r.get('url', '?'))

    def scrape_known_urls(self, urls: List[str], delay: float = 1.0) -> List[Dict]:
        """
        Scrape a list of known CDC investigation URLs
        Useful when automatic discovery fails due to dynamic content

        Args:
            urls: List of CDC investigation URLs to scrape
            delay: Delay between requests in seconds
        """
        all_outbreaks = []

        for i, url in enumerate(urls, 1):
            # Extract pathogen from URL
            pathogen_match = re.search(r'/(salmonella|listeria|ecoli|campylobacter|cyclospora|vibrio|shigella)/', url)
            pathogen = pathogen_match.group(1) if pathogen_match else 'unknown'

            # Extract identifier
            outbreak_id = url.split('/')[-2] if '/' in url else 'unknown'

            # Create investigation metadata
            investigation = {
                'outbreak_id': f'cdc_{pathogen}_{outbreak_id}',
                'pathogen': pathogen,
                'url': url,
                'source': 'CDC'
            }

            # Scrape details
            details = self.scrape_investigation_details(url, pathogen)
            self._log_result(i, len(urls), investigation, details)

            # Merge data
            full_data = {**investigation, **details}
            all_outbreaks.append(full_data)

            # Be respectful - add delay
            if i < len(urls):
                time.sleep(delay)

        return all_outbreaks

    def scrape_all_pathogens(self, delay: float = 1.0, use_main_page: bool = True, use_playwright: bool = True,
                              known_urls: Optional[List[str]] = None,
                              existing: Optional[Dict[str, Dict]] = None,
                              max_age_days: Optional[int] = None) -> List[Dict]:
        """
        Discover and scrape all outbreak investigations across all pathogens

        Args:
            delay: Delay between requests in seconds
            use_main_page: Try to discover from main CDC outbreak page first
            use_playwright: Use Playwright for JavaScript-rendered content (recommended)
            known_urls: Optional list of known investigation URLs to scrape directly
            existing: Previously-scraped investigations, keyed by outbreak_id. When
                given together with max_age_days, an investigation already scraped
                successfully that is either marked closed or older than
                max_age_days is reused instead of re-fetched.
            max_age_days: Skip re-fetching previously-successful investigations
                older than this many days. None disables skipping (full scrape).
        """
        all_outbreaks = []

        try:
            # If known URLs provided, use those directly
            if known_urls:
                log.info("Scraping %d known CDC outbreak URLs", len(known_urls))
                all_outbreaks = self.scrape_known_urls(known_urls, delay)
                self._log_scrape_summary(all_outbreaks)
                return all_outbreaks

            # First, try to discover from main CDC page (more reliable)
            if use_main_page:
                log.info("=== Phase 1: discover CDC investigations ===")

                # Use Playwright if requested (better for JavaScript-rendered content)
                if use_playwright:
                    main_page_investigations = self.discover_from_main_page_playwright()
                else:
                    main_page_investigations = self.discover_from_main_page()

                if main_page_investigations:
                    existing = existing or {}
                    now = datetime.now(timezone.utc)

                    to_fetch = []
                    skip_map = {}
                    for investigation in main_page_investigations:
                        prior = existing.get(investigation['outbreak_id'])
                        if max_age_days is not None and prior and prior.get('scrape_status') == 'success':
                            if prior.get('status') == 'closed':
                                skip_map[investigation['outbreak_id']] = prior
                                continue
                            outbreak_date = (infer_cdc_outbreak_date(investigation['outbreak_id'])
                                              or parse_cdc_posted_date(prior.get('posted_date')))
                            if is_stale(outbreak_date, max_age_days, now):
                                skip_map[investigation['outbreak_id']] = prior
                                continue
                        to_fetch.append(investigation)

                    if skip_map:
                        log.info("Skipping %d CDC investigations already scraped and stable "
                                  "(closed, or older than %d days)", len(skip_map), max_age_days)

                    fetched_map = {}
                    total = len(to_fetch)
                    log.info("=== Phase 2: fetch %d investigation pages ===", total)
                    for i, investigation in enumerate(to_fetch, 1):
                        details = self.scrape_investigation_details(
                            investigation['url'],
                            investigation.get('pathogen', 'unknown')
                        )
                        self._log_result(i, total, investigation, details)

                        # Merge metadata with details
                        full_data = {**investigation, **details}
                        fetched_map[investigation['outbreak_id']] = full_data

                        # Be respectful - add delay
                        if i < total:
                            time.sleep(delay)

                    # Reassemble in the discovery page's original order,
                    # rather than the skip/fetch split order used above.
                    all_outbreaks = [
                        skip_map.get(i['outbreak_id']) or fetched_map[i['outbreak_id']]
                        for i in main_page_investigations
                    ]

                    # Carry forward investigations no longer on the current
                    # discovery page so history isn't lost.
                    current_ids = {i['outbreak_id'] for i in main_page_investigations}
                    for outbreak_id, record in existing.items():
                        if outbreak_id not in current_ids:
                            all_outbreaks.append(record)

                    self._log_scrape_summary(all_outbreaks)
                    return all_outbreaks

                log.warning("Main-page discovery returned no investigations")

            # Fallback: Try individual pathogen pages
            log.info("=== Falling back to individual pathogen pages ===")
            for pathogen in self.pathogen_pages.keys():
                log.info("Processing %s outbreaks", pathogen.upper())

                # Discover investigations
                investigations = self.discover_investigation_links(pathogen)

                # Scrape each investigation
                total = len(investigations)
                for i, investigation in enumerate(investigations, 1):
                    details = self.scrape_investigation_details(investigation['url'], pathogen)
                    self._log_result(i, total, investigation, details)

                    # Merge metadata with details
                    full_data = {**investigation, **details}
                    all_outbreaks.append(full_data)

                    # Be respectful - add delay
                    if i < total:
                        time.sleep(delay)

                # Delay between pathogens
                time.sleep(delay)

            self._log_scrape_summary(all_outbreaks)
            return all_outbreaks
        finally:
            # Always release the shared Playwright browser used for detail fetches.
            self.close()

    def save_to_json(self, outbreaks: List[Dict], filename: str = 'data/raw/cdc_outbreaks.json'):
        """Save scraped data to JSON file"""
        # Ensure directory exists
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(outbreaks, f, indent=2, ensure_ascii=False)
        log.info("Saved %d CDC outbreaks to %s", len(outbreaks), filename)


_FSIS_RECALL_API = 'https://www.fsis.usda.gov/fsis/api/recall/v/1'

_FSIS_USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# usda.gov sits behind Akamai, like FDA — but it rejects for a different reason,
# so it needs a different workaround (a real browser, but no proxy). What was
# actually observed, in order:
#   - plain `requests`, even sending a byte-identical full desktop-Chrome header
#     set, is always rejected; the same headers from curl are accepted. So part
#     of the check is on the TLS/client fingerprint, which headers can't fix.
#   - a real headless Chromium is ALSO rejected by default, because headless
#     Chrome names itself in the sec-ch-ua client hint ("HeadlessChrome").
#   - overriding just sec-ch-ua* on the browser context is accepted. Chromium
#     supplies every other header correctly on its own.
# Hence: Playwright, with these three headers overridden. Unlike FDA this is not
# IP-based, so no fly.dev proxy is involved.
_FSIS_HEADERS = {
    'sec-ch-ua': '"Chromium";v="120", "Not(A:Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
}

_STATE_ABBREV = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA',
    'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE', 'District of Columbia': 'DC',
    'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL',
    'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA',
    'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN',
    'Mississippi': 'MS', 'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
    'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK', 'Oregon': 'OR',
    'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
    'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT', 'Virginia': 'VA',
    'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY',
    'American Samoa': 'AS', 'Guam': 'GU', 'Puerto Rico': 'PR',
}

# FSIS mixes distribution reach ('Nationwide', 'Midwest') into the same
# field_states list as real states. Kept separately so states_affected stays a
# list of state codes and state counts aren't inflated by a non-state value.
_DISTRIBUTION_AREAS = {'Nationwide', 'Midwest'}

# Ordered so the more specific organism wins when a notice names more than one.
_PATHOGEN_PATTERNS = (
    ('Listeria', r'listeria'),
    ('Salmonella', r'salmonella'),
    ('E. coli', r'\be\.?\s*coli\b|\bstec\b|\bo157\b'),
    ('Botulism', r'clostridium botulinum|botulism'),
    ('Staphylococcus', r'staphylococc'),
    ('Campylobacter', r'campylobacter'),
)


def strip_html(value: Optional[str]) -> Optional[str]:
    """Turn an HTML fragment from the FSIS API into readable plain text."""
    if not value:
        return None
    text = BeautifulSoup(value, 'html.parser').get_text(' ')
    text = html.unescape(text).replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text or None


def detect_pathogen(text: str) -> Optional[str]:
    """
    Identify the pathogen a recall notice concerns, from its free text.

    FSIS has no pathogen field — the organism is only ever named in the title,
    summary or reason ('...Due to Possible Listeria monocytogenes
    Contamination'), so it has to be read out of the prose. Returns None for
    the many recalls that are about allergens or misbranding, not a pathogen.
    """
    lowered = (text or '').lower()
    for name, pattern in _PATHOGEN_PATTERNS:
        if re.search(pattern, lowered):
            return name
    return None


class USDARecallScraper:
    """
    Collects USDA FSIS recalls and public health alerts for meat, poultry and
    egg products.

    Unlike the FDA and CDC scrapers, this source is a single JSON API that
    returns the agency's whole recall history in one response, so there is no
    listing page to walk, no per-record page fetch, and no inter-request delay
    to respect — one request replaces what would otherwise be ~1,200 of them.
    That also means there is nothing for --max-age-days to save, so this
    scraper has no incremental mode; it always refreshes every record.
    """

    def __init__(self):
        self.api_url = _FSIS_RECALL_API
        self._playwright = None
        self._browser = None
        self._context = None

    def _ensure_context(self):
        """Lazily start a Playwright browser context and return it."""
        if self._context is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=_FSIS_USER_AGENT,
                extra_http_headers=_FSIS_HEADERS
            )
            self._context.set_default_timeout(60000)
        return self._context

    def close(self):
        """Tear down the Playwright browser, if one was started."""
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._context = self._browser = self._playwright = None

    def fetch_recalls(self) -> List[Dict]:
        """
        Fetch every recall record from the FSIS API in a single request.

        Uses the browser context's request API rather than page navigation: it
        carries the same accepted fingerprint and headers, but hands back the
        raw JSON instead of a ~13 MB DOM wrapping it.
        """
        log.info("Fetching USDA FSIS recalls: %s", self.api_url)
        context = self._ensure_context()
        response = context.request.get(self.api_url, timeout=60000)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status} fetching FSIS recall API {self.api_url}")
        records = response.json()
        if not isinstance(records, list):
            raise RuntimeError(f"Expected a JSON list from {self.api_url}, got {type(records).__name__}")
        log.info("FSIS API returned %d records", len(records))
        return records

    def normalize_recall(self, record: Dict) -> Dict:
        """Convert one raw FSIS API record into this project's record shape."""
        title = html.unescape(record.get('field_title') or '').strip()
        summary = strip_html(record.get('field_summary'))
        # Every list field can carry HTML entities and markup, not just the
        # product blurbs — establishment names arrive as e.g. 'Boar&#039;s Head'.
        products = [v for v in (strip_html(p) for p in (record.get('field_product_items') or [])) if v]
        reasons = [v for v in (strip_html(r) for r in (record.get('field_recall_reason') or [])) if v]
        establishments = [v for v in (strip_html(e) for e in (record.get('field_establishment') or [])) if v]
        processing = [v for v in (strip_html(p) for p in (record.get('field_processing') or [])) if v]

        raw_states = record.get('field_states') or []
        states = sorted({_STATE_ABBREV[s] for s in raw_states if s in _STATE_ABBREV})
        distribution = sorted({s for s in raw_states if s in _DISTRIBUTION_AREAS})

        # The organism is only named in prose, so search every text field.
        pathogen = detect_pathogen(' '.join([title, summary or '', ' '.join(reasons), ' '.join(products)]))

        url = (record.get('field_recall_url') or '').strip()
        if url.startswith('http://'):  # FSIS returns http:// but redirects to https
            url = 'https://' + url[len('http://'):]

        return {
            'recall_id': (record.get('field_recall_number') or '').strip() or url.rstrip('/').split('/')[-1],
            'source': 'USDA',
            'title': title,
            'url': url,
            'pathogen': pathogen,
            'recall_date': record.get('field_recall_date') or None,
            'last_modified_date': record.get('field_last_modified_date') or None,
            'year': record.get('field_year') or None,
            'recall_class': record.get('field_recall_classification') or None,
            'risk_level': record.get('field_risk_level') or None,
            'recall_type': record.get('field_recall_type') or None,
            'reasons': reasons,
            'products': products,
            'establishment': establishments,
            'processing': processing,
            'states_affected': states,
            'state_count': len(states),
            'distribution': distribution,
            'active': record.get('field_active_notice') == 'True',
            'archived': record.get('field_archive_recall') == 'True',
            'related_to_outbreak': record.get('field_related_to_outbreak') == 'True',
            'summary': summary,
            'scraped_at': datetime.now(timezone.utc).isoformat()
        }

    def scrape_all(self, existing: Optional[Dict[str, Dict]] = None) -> List[Dict]:
        """
        Fetch and normalize every FSIS recall, newest first.

        Args:
            existing: Previously-saved recalls keyed by recall_id. Used to carry
                forward recalls FSIS has dropped from the API, and to preserve
                the related_outbreaks cross-links that build_combined() writes
                (they're computed against outbreak data this scraper never sees,
                so a refresh must not wipe them).
        """
        existing = existing or {}
        try:
            records = self.fetch_recalls()
        finally:
            # Always release the Playwright browser used for the fetch.
            self.close()

        # FSIS publishes each notice twice, once per language. Keep English so
        # recall numbers stay unique — the Spanish rows duplicate them exactly.
        english = [r for r in records if r.get('langcode') == 'English']
        log.info("Keeping %d English records (dropped %d Spanish duplicates)",
                 len(english), len(records) - len(english))

        recalls = []
        for record in english:
            normalized = self.normalize_recall(record)
            prior = existing.get(normalized['recall_id'])
            if prior and prior.get('related_outbreaks'):
                normalized['related_outbreaks'] = prior['related_outbreaks']
            recalls.append(normalized)

        # Carry forward anything FSIS no longer returns so history isn't lost.
        current_ids = {r['recall_id'] for r in recalls}
        carried = [r for rid, r in existing.items() if rid not in current_ids]
        if carried:
            log.info("Carrying forward %d recalls no longer returned by the API", len(carried))
            recalls.extend(carried)

        recalls.sort(key=lambda r: r.get('recall_date') or '', reverse=True)

        with_pathogen = sum(1 for r in recalls if r.get('pathogen'))
        outbreak_linked = sum(1 for r in recalls if r.get('related_to_outbreak'))
        log.info("Collected %d USDA recalls (%d name a pathogen, %d flagged outbreak-related)",
                 len(recalls), with_pathogen, outbreak_linked)
        return recalls

    def save_to_json(self, recalls: List[Dict], filename: str = 'data/raw/usda_recalls.json'):
        """Save scraped data to JSON file"""
        # Ensure directory exists
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(recalls, f, indent=2, ensure_ascii=False)
        log.info("Saved %d USDA recalls to %s", len(recalls), filename)


def load_existing_recalls_by_id(path: str) -> Dict[str, Dict]:
    """Load a previously-saved recalls JSON file, keyed by recall_id."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except Exception as e:
        log.warning("Could not load existing recalls from %s: %s", path, e)
        return {}
    return {r['recall_id']: r for r in records if r.get('recall_id')}


# Food vehicles shared between a recall notice and an outbreak investigation.
# Deliberately a short, specific vocabulary rather than generic word overlap:
# matching on any common token linked unrelated events, since almost every
# record contains words like 'products' or 'ready-to-eat'.
_FOOD_TERMS = {
    'charcuterie': r'charcuterie',
    'liverwurst': r'liverwurst',
    'deli meat': r'deli[- ]?(?:sliced )?meat|meats sliced at deli',
    'ground beef': r'ground beef',
    'ground turkey': r'ground turkey',
    'salami': r'salam[ei]',
    'poultry': r'poultry',
    'chicken': r'chicken',
    'onion': r'onion',
    'walnut': r'walnut',
    'cantaloupe': r'cantaloupe',
    'queso fresco': r'queso fresco',
}


def food_terms(text: str) -> set:
    """Food vehicles named in a piece of text, from the _FOOD_TERMS vocabulary."""
    lowered = (text or '').lower()
    return {term for term, pattern in _FOOD_TERMS.items() if re.search(pattern, lowered)}


def infer_record_year(record: Dict) -> Optional[int]:
    """
    Best-effort year for an outbreak record, checked title-first.

    Title before posted_date is deliberate: CDC titles lead with the outbreak's
    own year ('2022E. coliOutbreak Linked to Ground Beef') while posted_date is
    when the page was last published, which can be a year or more later. Using
    posted_date first mis-dated that outbreak to 2023 and produced a false link.
    Note the regex has no trailing \\b — CDC runs the year straight into the next
    word, so '2019SalmonellaInfections' has no word boundary after the digits.
    """
    for field in ('title', 'posted_date', 'url', 'id'):
        match = re.search(r'\b(20\d{2})', str(record.get(field) or ''))
        if match:
            return int(match.group(1))
    return None


def link_recalls_to_outbreaks(outbreaks: List[Dict], recalls: List[Dict]) -> int:
    """
    Cross-reference FSIS recalls against the combined outbreak records, adding
    'related_recalls' to outbreaks and 'related_outbreaks' to recalls.

    Only recalls FSIS itself flags as outbreak-related are considered, so this
    never invents a link where the agency doesn't already assert one; all it
    does is work out *which* investigation the flagged recall belongs to, which
    FSIS does not say. A pair is linked when the pathogen matches and both name
    the same food vehicle. Confidence records how well the dates agree:

      'corroborated' - both years known and within a year of each other
      'weak'         - the outbreak record has no determinable year

    'weak' exists because many CDC records carry no date at all; requiring a
    year dropped correct 2024 links (Boar's Head, Yu Shang), while allowing
    them recovers those at the cost of occasionally reaching across years.
    Consumers that want only firm links should filter to 'corroborated'.
    """
    flagged = [r for r in recalls if r.get('related_to_outbreak')]
    if not flagged:
        return 0

    # Clear prior links so a rerun can't accumulate stale duplicates.
    for outbreak in outbreaks:
        outbreak.pop('related_recalls', None)
    for recall in recalls:
        recall.pop('related_outbreaks', None)

    indexed = []
    for outbreak in outbreaks:
        indexed.append((
            outbreak,
            detect_pathogen(outbreak.get('pathogen') or ''),
            food_terms(f"{outbreak.get('title') or ''} {outbreak.get('food_source') or ''}"),
            infer_record_year(outbreak)
        ))

    link_count = 0
    for recall in flagged:
        recall_foods = food_terms(f"{recall.get('title') or ''} {' '.join(recall.get('products') or [])}")
        recall_year = int(recall['year']) if str(recall.get('year') or '').isdigit() else None
        if not recall.get('pathogen') or not recall_foods:
            continue

        for outbreak, pathogen, foods, outbreak_year in indexed:
            if pathogen != recall['pathogen'] or not (recall_foods & foods):
                continue
            if outbreak_year is None:
                confidence = 'weak'
            elif recall_year is not None and abs(outbreak_year - recall_year) <= 1:
                confidence = 'corroborated'
            else:
                continue

            recall_link = {
                'recall_id': recall['recall_id'],
                'title': recall['title'],
                'url': recall['url'],
                'recall_date': recall.get('recall_date'),
                'match_confidence': confidence
            }
            outbreak_link = {
                'id': outbreak.get('id'),
                'source': outbreak.get('source'),
                'title': outbreak.get('title'),
                'url': outbreak.get('url'),
                'match_confidence': confidence
            }
            # Skip an identical entry rather than deduplicating by id alone:
            # the combined data holds distinct investigations that share an id,
            # and separate CDC pages for one outbreak (e.g. a '-part2' follow-up)
            # are genuinely separate links worth keeping.
            existing_links = outbreak.setdefault('related_recalls', [])
            if recall_link not in existing_links:
                existing_links.append(recall_link)
            existing_back_links = recall.setdefault('related_outbreaks', [])
            if outbreak_link not in existing_back_links:
                existing_back_links.append(outbreak_link)
            link_count += 1

    linked_recalls = sum(1 for r in flagged if r.get('related_outbreaks'))
    log.info("Cross-linked %d of %d outbreak-related recalls to outbreak records (%d links)",
             linked_recalls, len(flagged), link_count)
    return link_count


class OutbreakAggregator:
    """
    Combines outbreak data from multiple sources (FDA, CDC) into a unified format
    """

    def __init__(self):
        self.fda_scraper = FDAOutbreakScraper()
        self.cdc_scraper = CDCOutbreakScraper()
        self.usda_scraper = USDARecallScraper()

    def scrape_all_sources(self, fda_limit: Optional[int] = None, delay: float = 1.5, cdc_known_urls: Optional[List[str]] = None) -> Dict[str, List[Dict]]:
        """
        Scrape data from all sources

        Args:
            fda_limit: Limit number of FDA outbreaks to scrape (None for all)
            delay: Delay between requests in seconds
            cdc_known_urls: Optional list of known CDC investigation URLs

        Returns:
            Dictionary with 'fda' and 'cdc' keys containing outbreak lists
        """
        log.info("[1/2] Scraping FDA outbreaks...")
        fda_outbreaks = self.fda_scraper.scrape_all(limit=fda_limit, delay=delay)

        log.info("[2/2] Scraping CDC outbreaks...")
        cdc_outbreaks = self.cdc_scraper.scrape_all_pathogens(delay=delay, known_urls=cdc_known_urls)

        return {
            'fda': fda_outbreaks,
            'cdc': cdc_outbreaks
        }

    def normalize_outbreak(self, outbreak: Dict) -> Dict:
        """
        Normalize outbreak data to a common format regardless of source
        """
        normalized = {
            'id': outbreak.get('outbreak_id'),
            'source': outbreak.get('source', 'FDA' if 'fda.gov' in outbreak.get('url', '') else 'Unknown'),
            'title': outbreak.get('title'),
            'url': outbreak.get('url'),
            'pathogen': outbreak.get('pathogen'),
            'food_source': outbreak.get('food_item') or outbreak.get('food_source'),
            'status': outbreak.get('status', 'unknown'),
            'case_count': outbreak.get('case_count'),
            'deaths': outbreak.get('deaths'),
            'hospitalizations': outbreak.get('hospitalizations'),
            'states_affected': outbreak.get('states_affected', []),
            'state_count': outbreak.get('state_count') or (len(outbreak.get('states_affected', [])) if outbreak.get('states_affected') else None),
            'posted_date': outbreak.get('date_str') or outbreak.get('posted_date'),
            'consumer_advice': outbreak.get('consumer_advice'),
            'scraped_at': outbreak.get('scraped_at')
        }

        # Remove None values
        return {k: v for k, v in normalized.items() if v is not None}

    def combine_and_normalize(self, all_data: Dict[str, List[Dict]]) -> List[Dict]:
        """
        Combine all outbreak data and normalize to common format
        """
        combined = []

        for source, outbreaks in all_data.items():
            for outbreak in outbreaks:
                normalized = self.normalize_outbreak(outbreak)
                combined.append(normalized)

        # Sort by case count (descending) for priority
        combined.sort(key=lambda x: x.get('case_count', 0), reverse=True)

        return combined

    def get_summary_stats(self, combined_data: List[Dict]) -> Dict:
        """
        Generate summary statistics across all outbreaks
        """
        total_outbreaks = len(combined_data)
        total_cases = sum(o.get('case_count', 0) for o in combined_data)
        total_deaths = sum(o.get('deaths', 0) for o in combined_data)
        total_hospitalizations = sum(o.get('hospitalizations', 0) for o in combined_data)

        # Count by source
        by_source = {}
        for outbreak in combined_data:
            source = outbreak.get('source', 'Unknown')
            by_source[source] = by_source.get(source, 0) + 1

        # Count by pathogen
        by_pathogen = {}
        for outbreak in combined_data:
            pathogen = outbreak.get('pathogen', 'Unknown')
            by_pathogen[pathogen] = by_pathogen.get(pathogen, 0) + 1

        # Count by status
        by_status = {}
        for outbreak in combined_data:
            status = outbreak.get('status', 'unknown')
            by_status[status] = by_status.get(status, 0) + 1

        # Unique states affected
        all_states = set()
        for outbreak in combined_data:
            states = outbreak.get('states_affected', [])
            all_states.update(states)

        return {
            'total_outbreaks': total_outbreaks,
            'total_cases': total_cases,
            'total_deaths': total_deaths,
            'total_hospitalizations': total_hospitalizations,
            'outbreaks_by_source': by_source,
            'outbreaks_by_pathogen': by_pathogen,
            'outbreaks_by_status': by_status,
            'unique_states_affected': len(all_states),
            'states_list': sorted(all_states)
        }

    def save_combined_data(self, combined_data: List[Dict], stats: Dict, filename: str = 'data/raw/combined_outbreaks.json'):
        """Save combined data with summary stats"""
        # Ensure directory exists
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        output = {
            'summary': stats,
            'outbreaks': combined_data,
            'generated_at': datetime.now(timezone.utc).isoformat()
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        log.info("Saved combined data (%d outbreaks) to %s", len(combined_data), filename)


def build_combined(aggregator: OutbreakAggregator,
                   fda_file: str = 'data/raw/fda_outbreaks.json',
                   cdc_file: str = 'data/raw/cdc_outbreaks.json',
                   usda_file: str = 'data/raw/usda_recalls.json'):
    """
    Build data/raw/combined_outbreaks.json from the on-disk source files.

    This is the ONLY place the combined file is written. Because it always
    reads both source files from disk (rather than whatever was scraped this
    run), refreshing a single source can never drop the other source from the
    combined output.

    USDA recalls are deliberately NOT combined in: a recall is a different kind
    of event from an outbreak investigation and carries no case, death or
    hospitalization counts, so folding ~1,200 of them into 313 outbreaks would
    swamp every headline figure and breakdown. They keep their own file, and
    are joined to outbreaks only by the cross-links written here — which is why
    this function also rewrites usda_file with the recall side of those links.
    """
    log.info("=" * 60)
    log.info("COMBINING AND NORMALIZING DATA")

    all_data = {'fda': [], 'cdc': []}
    for key, path in (('fda', fda_file), ('cdc', cdc_file)):
        if Path(path).exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    all_data[key] = json.load(f)
                log.info("Loaded %d %s outbreaks from %s", len(all_data[key]), key.upper(), path)
            except Exception as e:
                log.error("Error loading %s data from %s: %s", key.upper(), path, e)
        else:
            log.warning("%s not found, skipping %s data", path, key.upper())

    combined = aggregator.combine_and_normalize(all_data)
    stats = aggregator.get_summary_stats(combined)

    # Display summary
    log.info("-" * 60)
    log.info("SUMMARY STATISTICS")
    log.info("Total Outbreaks: %s", stats['total_outbreaks'])
    log.info("Total Cases: %s", stats['total_cases'])
    log.info("Total Deaths: %s", stats['total_deaths'])
    log.info("Total Hospitalizations: %s", stats['total_hospitalizations'])
    log.info("States Affected: %s", stats['unique_states_affected'])

    log.info("Outbreaks by Source:")
    for source, count in stats['outbreaks_by_source'].items():
        log.info("  %s: %d", source, count)

    log.info("Outbreaks by Pathogen:")
    for pathogen, count in sorted(stats['outbreaks_by_pathogen'].items(), key=lambda x: x[1], reverse=True):
        log.info("  %s: %d", pathogen, count)

    log.info("Outbreaks by Status:")
    for status, count in stats['outbreaks_by_status'].items():
        log.info("  %s: %d", status, count)

    # Show top 5 outbreaks by case count
    log.info("-" * 60)
    log.info("TOP 5 OUTBREAKS BY CASE COUNT")
    for i, outbreak in enumerate(combined[:5], 1):
        log.info("%d. %s [%s] pathogen=%s food=%s cases=%s deaths=%s states=%s status=%s",
                 i,
                 outbreak.get('title', 'Unknown'),
                 outbreak.get('source'),
                 outbreak.get('pathogen', 'Unknown'),
                 outbreak.get('food_source', 'Unknown'),
                 outbreak.get('case_count', 'N/A'),
                 outbreak.get('deaths', 'N/A'),
                 outbreak.get('state_count', 'N/A'),
                 outbreak.get('status', 'unknown'))

    # Cross-link FSIS recalls to the outbreaks they belong to, writing both
    # sides: the outbreak side into the combined file saved below, the recall
    # side back into usda_file.
    recalls = []
    if Path(usda_file).exists():
        try:
            with open(usda_file, 'r', encoding='utf-8') as f:
                recalls = json.load(f)
            log.info("Loaded %d USDA recalls from %s", len(recalls), usda_file)
        except Exception as e:
            log.error("Error loading USDA recalls from %s: %s", usda_file, e)
    else:
        log.warning("%s not found, skipping recall cross-linking", usda_file)

    if recalls:
        link_recalls_to_outbreaks(combined, recalls)
        aggregator.usda_scraper.save_to_json(recalls, usda_file)

    if combined:
        aggregator.save_combined_data(combined, stats)

    return combined, stats


def main():
    """
    Main function demonstrating combined FDA, CDC and USDA scraping
    Supports command-line arguments to scrape any subset of the sources
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Scrape foodborne outbreak data from FDA and/or CDC, and food recalls from USDA FSIS',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                     # Scrape all sources, incrementally (default)
  python main.py --fda               # Scrape FDA only, incrementally
  python main.py --cdc               # Scrape CDC only, incrementally
  python main.py --usda              # Fetch USDA FSIS recalls only
  python main.py --fda --cdc         # Scrape both FDA and CDC
  python main.py --full              # Re-scrape everything, ignoring prior data
  python main.py --max-age-days 180  # Treat outbreaks as stable after 180 days
  python main.py --combine           # Combine existing JSON files, no scraping

By default, an outbreak already scraped successfully and older than
--max-age-days (365 by default) is reused from data/raw/*.json instead of
being re-fetched, since FDA and CDC don't update old outbreak pages. Pass
--full to scrape everything regardless of age. USDA is one API request for
the full recall history, so it has no incremental mode and ignores both flags.

USDA recalls are written to their own data/raw/usda_recalls.json and are not
merged into combined_outbreaks.json; the two datasets are joined by the
cross-links --combine writes between them.
        """
    )
    parser.add_argument('--fda', action='store_true', help='Scrape FDA outbreak data')
    parser.add_argument('--cdc', action='store_true', help='Scrape CDC outbreak data')
    parser.add_argument('--usda', action='store_true', help='Fetch USDA FSIS recall data')
    parser.add_argument('--combine', action='store_true', help='Combine existing JSON files without scraping')
    parser.add_argument('--delay', type=float, default=2.0, help='Delay between requests in seconds (default: 2.0)')
    parser.add_argument('--no-playwright', action='store_true', help='Disable Playwright and use simple HTTP requests (may find fewer outbreaks)')
    parser.add_argument('--use-known-urls', action='store_true', help='Use hardcoded list of known CDC URLs instead of auto-discovery')
    parser.add_argument('--full', action='store_true',
                         help='Re-scrape every outbreak, including old ones already scraped successfully. '
                              'By default, outbreaks older than --max-age-days are assumed stable and are '
                              'reused from data/raw/*.json instead of being re-fetched.')
    parser.add_argument('--max-age-days', type=int, default=365,
                         help='Skip re-fetching a previously-successful outbreak once it is older than this '
                              'many days (default: 365). Ignored when --full is given.')

    args = parser.parse_args()

    # Configure console + file logging so every run leaves a reviewable log.
    setup_logging()

    # If --combine is specified, don't scrape
    if args.combine:
        scrape_fda = scrape_cdc = scrape_usda = False
    else:
        # If no source flags specified, scrape every source
        no_source_selected = not (args.fda or args.cdc or args.usda)
        scrape_fda = args.fda or no_source_selected
        scrape_cdc = args.cdc or no_source_selected
        scrape_usda = args.usda or no_source_selected

    # Initialize scrapers
    aggregator = OutbreakAggregator()

    # Example known CDC investigation URLs (since CDC pages load dynamically)
    # You can find recent outbreaks at: https://www.cdc.gov/foodborne-outbreaks/outbreaks/
    known_cdc_urls = [
        # Salmonella outbreaks (2025)
        'https://www.cdc.gov/salmonella/outbreaks/cotham-11-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/supplement-10-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/homedeliverymeals-09-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/eggs-08-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/sproutedbeans-07-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/pistachiocream-06-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/eggs-06-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/whole-cucumbers-05-25/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/mbandaka-05-01/investigation.html',
        'https://www.cdc.gov/salmonella/outbreaks/muenchen-03-25/investigation.html',

        # Listeria outbreaks (2024-2025)
        'https://www.cdc.gov/listeria/outbreaks/chicken-fettuccine-alfredo-06-25/investigation.html',
        'https://www.cdc.gov/listeria/outbreaks/ready-to-eat-foods-may-2025/investigation.html',
        'https://www.cdc.gov/listeria/outbreaks/shakes-022025/investigation.html',
        'https://www.cdc.gov/listeria/outbreaks/meat-and-poultry-products-11-24/investigation.html',
        'https://www.cdc.gov/listeria/outbreaks/delimeats-7-24/investigation.html',

        # E. coli outbreaks (2024)
        'https://www.cdc.gov/ecoli/outbreaks/investigation-update-e-coli-o157-2024.html',
        'https://www.cdc.gov/ecoli/outbreaks/investigation-update-e-coli-o121.html',
        'https://www.cdc.gov/ecoli/outbreaks/details-organic-walnuts-04-24.html',
    ]

    modes = [m for m, on in (("FDA", scrape_fda), ("CDC", scrape_cdc),
                             ("USDA", scrape_usda), ("combine", args.combine)) if on]
    log.info("=" * 60)
    log.info("FOODBORNE OUTBREAK ALERT SYSTEM — mode: %s", ", ".join(modes) or "none")
    log.info("=" * 60)

    # Each scrape mode writes ONLY its own source file. The combined file is
    # never touched by a scrape, so FDA and CDC can never clobber each other;
    # it is rebuilt from disk by build_combined() below.
    files_created = []

    max_age_days = None if args.full else args.max_age_days

    if scrape_fda:
        log.info("[FDA] Scraping FDA outbreaks%s...", "" if args.full else f" (incremental, max-age {args.max_age_days}d)")
        existing_fda = {} if args.full else load_existing_by_id('data/raw/fda_outbreaks.json')
        fda_outbreaks = aggregator.fda_scraper.scrape_all(
            limit=None, delay=args.delay, existing=existing_fda, max_age_days=max_age_days
        )
        if fda_outbreaks:
            aggregator.fda_scraper.save_to_json(fda_outbreaks)
            files_created.append("  - data/raw/fda_outbreaks.json (FDA data only)")

    if scrape_cdc:
        log.info("[CDC] Scraping CDC outbreaks%s...", "" if args.full else f" (incremental, max-age {args.max_age_days}d)")
        # Determine which URLs to use
        urls_to_use = known_cdc_urls if args.use_known_urls else None
        # Use Playwright unless disabled
        use_pw = not args.no_playwright
        existing_cdc = {} if args.full else load_existing_by_id('data/raw/cdc_outbreaks.json')
        cdc_outbreaks = aggregator.cdc_scraper.scrape_all_pathogens(
            delay=args.delay,
            use_playwright=use_pw,
            known_urls=urls_to_use,
            existing=existing_cdc,
            max_age_days=max_age_days
        )
        if cdc_outbreaks:
            aggregator.cdc_scraper.save_to_json(cdc_outbreaks)
            files_created.append("  - data/raw/cdc_outbreaks.json (CDC data only)")

    if scrape_usda:
        log.info("[USDA] Fetching USDA FSIS recalls (single API request, full history)...")
        existing_usda = {} if args.full else load_existing_recalls_by_id('data/raw/usda_recalls.json')
        try:
            usda_recalls = aggregator.usda_scraper.scrape_all(existing=existing_usda)
        except Exception as e:
            log.error("[USDA] Failed to fetch FSIS recalls: %s", e)
            usda_recalls = []
        if usda_recalls:
            aggregator.usda_scraper.save_to_json(usda_recalls)
            files_created.append("  - data/raw/usda_recalls.json (USDA FSIS recalls only)")

    # Rebuild the combined file only when explicitly requested (--combine) or
    # when both outbreak sources were refreshed in this run. Never after a
    # single-source scrape, so the other source's data is preserved.
    if args.combine or (scrape_fda and scrape_cdc):
        build_combined(aggregator)
        files_created.append("  - data/raw/combined_outbreaks.json (unified data with statistics)")

    log.info("=" * 60)
    log.info("SCRAPING COMPLETE")
    if files_created:
        log.info("Files written:")
        for file in files_created:
            log.info(file)
    else:
        log.warning("No files written (no data scraped or combined)")


if __name__ == "__main__":
    main()
