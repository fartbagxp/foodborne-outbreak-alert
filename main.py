"""
Foodborne Outbreak Alert System
Scrapes outbreak data from FDA and CDC public health sources
"""

import argparse
import csv
import io
import json
import re
import requests
import time

from bs4 import BeautifulSoup
from datetime import datetime, timezone
from urllib.parse import urljoin
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from typing import List, Dict, Optional

class FDAOutbreakScraper:
    def __init__(self):
        self.base_url = "https://www.fda.gov"
        self.listing_url = "https://www.fda.gov/food/outbreaks-foodborne-illness/public-health-advisories-investigations-foodborne-illness-outbreaks"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def scrape_listing_page(self) -> List[Dict]:
        """
        Scrape the main listing page for all outbreak links
        Returns a list of outbreak metadata
        """
        print(f"Fetching listing page: {self.listing_url}")
        response = self.session.get(self.listing_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        outbreaks = []

        # Find all outbreak links (they're in anchor tags)
        links = soup.find_all('a', href=re.compile(r'/food/outbreaks-foodborne-illness/outbreak-investigation-'))

        for link in links:
            outbreak_data = self._parse_listing_link(link)
            if outbreak_data:
                outbreaks.append(outbreak_data)

        print(f"Found {len(outbreaks)} outbreaks on listing page")
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
        print(f"Fetching outbreak details: {outbreak_url}")

        try:
            response = self.session.get(outbreak_url, allow_redirects=True)
            response.raise_for_status()
            final_url = response.url
            print("✓ Successfully fetched")
        except requests.exceptions.HTTPError as e:
            print(f"✗ HTTP Error {e.response.status_code}: {outbreak_url}")
            return {
                'url': outbreak_url,
                'scraped_at': datetime.now(timezone.utc).isoformat(),
                'scrape_status': f'http_error_{e.response.status_code}',
                'scrape_error': str(e)
            }
        except Exception as e:
            print(f"✗ Error fetching {outbreak_url}: {e}")
            return {
                'url': outbreak_url,
                'scraped_at': datetime.now(timezone.utc).isoformat(),
                'scrape_status': 'error',
                'scrape_error': str(e)
            }

        soup = BeautifulSoup(response.content, 'html.parser')

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

    def scrape_all(self, limit: Optional[int] = None, delay: float = 1.0) -> List[Dict]:
        """
        Scrape all outbreaks with full details

        Args:
            limit: Maximum number of outbreaks to scrape (None for all)
            delay: Delay between requests in seconds (be respectful!)
        """
        # Get listing
        outbreaks = self.scrape_listing_page()

        if limit:
            outbreaks = outbreaks[:limit]

        # Scrape details for each
        detailed_outbreaks = []
        for i, outbreak in enumerate(outbreaks, 1):
            print(f"\nScraping {i}/{len(outbreaks)}: {outbreak['title']}")

            details = self.scrape_outbreak_details(outbreak['url'])

            # Merge listing data with detailed data
            full_data = {**outbreak, **details}
            detailed_outbreaks.append(full_data)

            # Be respectful - add delay
            if i < len(outbreaks):
                time.sleep(delay)

        return detailed_outbreaks

    def save_to_json(self, outbreaks: List[Dict], filename: str = 'data/raw/fda_outbreaks.json'):
        """Save scraped data to JSON file"""
        # Ensure directory exists
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(outbreaks, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(outbreaks)} outbreaks to {filename}")


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
            print(f"Unknown pathogen: {pathogen}")
            return investigations

        url = self.pathogen_pages[pathogen]
        print(f"Checking {pathogen} outbreaks page: {url}")

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

            print(f"Found {len(investigations)} {pathogen} investigations")

        except Exception as e:
            print(f"Error discovering {pathogen} investigations: {e}")

        return investigations

    def scrape_investigation_details(self, investigation_url: str, pathogen: str) -> Dict:
        """
        Scrape detailed information from a CDC outbreak investigation page
        """
        print(f"Fetching CDC investigation: {investigation_url}")

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
                print(f"✗ Error fetching {try_url}: {e}")
                html = None
                if is_last:
                    return _error('error', str(e))
                continue

            last_status = status

            if status and 200 <= status < 300:
                print("✓ Successfully fetched from archive" if try_url != investigation_url else "✓ Successfully fetched")
                break

            # Non-success status: discard the (likely error) page body.
            html = None
            if status == 404:
                if not is_last:
                    print("  Original URL not found, trying CDC archive...")
                    continue
                print("✗ Not found in archive either")
                return _error('404_not_found', 'Page not found (tried original and archive)')

            print(f"✗ HTTP Error {status}: {try_url}")
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
        print(f"Checking main CDC outbreak page: {main_url}")
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

            print(f"Found {len(investigations)} investigations from main page")

        except Exception as e:
            print(f"Error scraping main CDC page: {e}")

        return investigations

    def discover_from_csv_file(self) -> List[Dict]:
        """
        Discover outbreaks by downloading the CSV file that the CDC uses for their DataTable
        This is faster and more reliable than scraping the paginated JavaScript table
        """
        csv_url = "https://www.cdc.gov/foodborne-outbreaks/media/files/2024/04/full-outbreak-list.csv"
        print(f"Fetching outbreak data from CSV: {csv_url}")
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

            print(f"Found {len(investigations)} outbreak entries in CSV")

        except Exception as e:
            print(f"Error fetching CSV file: {e}")

        return investigations

    def discover_from_main_page_playwright(self) -> List[Dict]:
        """
        Discover outbreaks from the main CDC foodborne outbreaks page using Playwright
        This handles JavaScript-rendered content and pagination
        """
        main_url = "https://www.cdc.gov/foodborne-outbreaks/outbreaks/index.html"
        print(f"Checking main CDC outbreak page with Playwright: {main_url}")
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
                print("Loading page...")
                try:
                    page.goto(main_url, wait_until="load", timeout=60000)
                except PlaywrightTimeoutError:
                    print("Page load timeout, trying with domcontentloaded...")
                    page.goto(main_url, wait_until="domcontentloaded", timeout=60000)

                # Wait for the table to load - the table has id or class we need to identify
                # Try multiple selectors since CDC might use different structures
                try:
                    # Wait for table to appear (adjust selector based on actual page structure)
                    page.wait_for_selector('table', timeout=20000)
                    print("Table loaded successfully")
                except PlaywrightTimeoutError:
                    print("Warning: Table did not load within timeout, proceeding anyway...")

                # Give JavaScript more time to fully render the content
                print("Waiting for JavaScript to render...")
                time.sleep(5)

                # Get all links from the table using Playwright directly (more reliable)
                print("Extracting all outbreak links from table...")
                links_found = set()  # Use set to avoid duplicates

                # Find all table rows
                table_rows = page.locator('table tbody tr').all()
                print(f"Found {len(table_rows)} rows in the initial table view")

                # Since the table is paginated, we need to get all pages
                # First, try to get the total number of entries
                try:
                    pagination_info = page.locator('.dataTables_info').inner_text()
                    print(f"Pagination info: {pagination_info}")

                    # Try to show all entries by changing the page size to maximum
                    try:
                        # Click on the page size dropdown and select a large number
                        page.select_option('select[name*="DataTables"]', '100')
                        print("Changed page size to 100")
                        time.sleep(2)  # Wait for table to reload
                    except Exception:
                        print("Could not change page size, will paginate manually")
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
                            print(f"Going to page {page_num + 1}...")
                            next_button.click()
                            page_num += 1
                            time.sleep(2)  # Wait for page to load
                        else:
                            print("Reached last page")
                            break
                    except Exception:
                        print("No more pages to paginate")
                        break

                browser.close()

                print(f"Found {len(investigations)} investigations from main page (with Playwright)")

        except Exception as e:
            print(f"Error scraping main CDC page with Playwright: {e}")
            import traceback
            traceback.print_exc()

        return investigations

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
            print(f"\nScraping {i}/{len(urls)}: {url}")

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

            # Merge data
            full_data = {**investigation, **details}
            all_outbreaks.append(full_data)

            # Be respectful - add delay
            if i < len(urls):
                time.sleep(delay)

        return all_outbreaks

    def scrape_all_pathogens(self, delay: float = 1.0, use_main_page: bool = True, use_playwright: bool = True, known_urls: Optional[List[str]] = None) -> List[Dict]:
        """
        Discover and scrape all outbreak investigations across all pathogens

        Args:
            delay: Delay between requests in seconds
            use_main_page: Try to discover from main CDC outbreak page first
            use_playwright: Use Playwright for JavaScript-rendered content (recommended)
            known_urls: Optional list of known investigation URLs to scrape directly
        """
        all_outbreaks = []

        try:
            # If known URLs provided, use those directly
            if known_urls:
                print(f"\n=== Scraping {len(known_urls)} known CDC outbreak URLs ===")
                return self.scrape_known_urls(known_urls, delay)

            # First, try to discover from main CDC page (more reliable)
            if use_main_page:
                print("\n=== Discovering from main CDC outbreak page ===")

                # Use Playwright if requested (better for JavaScript-rendered content)
                if use_playwright:
                    main_page_investigations = self.discover_from_main_page_playwright()
                else:
                    main_page_investigations = self.discover_from_main_page()

                if main_page_investigations:
                    for i, investigation in enumerate(main_page_investigations, 1):
                        print(f"\nScraping {i}/{len(main_page_investigations)}: {investigation.get('title', 'Unknown')}")

                        details = self.scrape_investigation_details(
                            investigation['url'],
                            investigation.get('pathogen', 'unknown')
                        )

                        # Merge metadata with details
                        full_data = {**investigation, **details}
                        all_outbreaks.append(full_data)

                        # Be respectful - add delay
                        if i < len(main_page_investigations):
                            time.sleep(delay)

                    return all_outbreaks

            # Fallback: Try individual pathogen pages
            print("\n=== Falling back to individual pathogen pages ===")
            for pathogen in self.pathogen_pages.keys():
                print(f"\n=== Processing {pathogen.upper()} outbreaks ===")

                # Discover investigations
                investigations = self.discover_investigation_links(pathogen)

                # Scrape each investigation
                for i, investigation in enumerate(investigations, 1):
                    print(f"Scraping {i}/{len(investigations)}: {investigation.get('title', 'Unknown')}")

                    details = self.scrape_investigation_details(investigation['url'], pathogen)

                    # Merge metadata with details
                    full_data = {**investigation, **details}
                    all_outbreaks.append(full_data)

                    # Be respectful - add delay
                    if i < len(investigations):
                        time.sleep(delay)

                # Delay between pathogens
                time.sleep(delay)

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
        print(f"\nSaved {len(outbreaks)} CDC outbreaks to {filename}")


class OutbreakAggregator:
    """
    Combines outbreak data from multiple sources (FDA, CDC) into a unified format
    """

    def __init__(self):
        self.fda_scraper = FDAOutbreakScraper()
        self.cdc_scraper = CDCOutbreakScraper()

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
        print("=" * 60)
        print("FOODBORNE OUTBREAK ALERT SYSTEM")
        print("=" * 60)

        # Scrape FDA
        print("\n[1/2] Scraping FDA Outbreaks...")
        print("-" * 60)
        fda_outbreaks = self.fda_scraper.scrape_all(limit=fda_limit, delay=delay)

        # Scrape CDC
        print("\n[2/2] Scraping CDC Outbreaks...")
        print("-" * 60)
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

        print(f"\nSaved combined data to {filename}")


def build_combined(aggregator: OutbreakAggregator,
                   fda_file: str = 'data/raw/fda_outbreaks.json',
                   cdc_file: str = 'data/raw/cdc_outbreaks.json'):
    """
    Build data/raw/combined_outbreaks.json from the on-disk source files.

    This is the ONLY place the combined file is written. Because it always
    reads both source files from disk (rather than whatever was scraped this
    run), refreshing a single source can never drop the other source from the
    combined output.
    """
    print("\n" + "=" * 60)
    print("COMBINING AND NORMALIZING DATA")
    print("=" * 60)

    all_data = {'fda': [], 'cdc': []}
    for key, path in (('fda', fda_file), ('cdc', cdc_file)):
        if Path(path).exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    all_data[key] = json.load(f)
                print(f"Loaded {len(all_data[key])} {key.upper()} outbreaks from {path}")
            except Exception as e:
                print(f"Error loading {key.upper()} data from {path}: {e}")
        else:
            print(f"Warning: {path} not found, skipping {key.upper()} data")

    combined = aggregator.combine_and_normalize(all_data)
    stats = aggregator.get_summary_stats(combined)

    # Display summary
    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)
    print(f"Total Outbreaks: {stats['total_outbreaks']}")
    print(f"Total Cases: {stats['total_cases']}")
    print(f"Total Deaths: {stats['total_deaths']}")
    print(f"Total Hospitalizations: {stats['total_hospitalizations']}")
    print(f"States Affected: {stats['unique_states_affected']}")

    print("\nOutbreaks by Source:")
    for source, count in stats['outbreaks_by_source'].items():
        print(f"  {source}: {count}")

    print("\nOutbreaks by Pathogen:")
    for pathogen, count in sorted(stats['outbreaks_by_pathogen'].items(), key=lambda x: x[1], reverse=True):
        print(f"  {pathogen}: {count}")

    print("\nOutbreaks by Status:")
    for status, count in stats['outbreaks_by_status'].items():
        print(f"  {status}: {count}")

    # Show top 5 outbreaks by case count
    print("\n" + "=" * 60)
    print("TOP 5 OUTBREAKS BY CASE COUNT")
    print("=" * 60)
    for i, outbreak in enumerate(combined[:5], 1):
        print(f"\n{i}. {outbreak.get('title', 'Unknown')}")
        print(f"   Source: {outbreak.get('source')}")
        print(f"   Pathogen: {outbreak.get('pathogen', 'Unknown')}")
        print(f"   Food: {outbreak.get('food_source', 'Unknown')}")
        print(f"   Cases: {outbreak.get('case_count', 'N/A')}")
        print(f"   Deaths: {outbreak.get('deaths', 'N/A')}")
        print(f"   States: {outbreak.get('state_count', 'N/A')}")
        print(f"   Status: {outbreak.get('status', 'unknown')}")
        print(f"   URL: {outbreak.get('url')}")

    if combined:
        aggregator.save_combined_data(combined, stats)

    return combined, stats


def main():
    """
    Main function demonstrating combined FDA and CDC outbreak scraping
    Supports command-line arguments to scrape FDA only, CDC only, or both
    """
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Scrape foodborne outbreak data from FDA and/or CDC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                # Scrape both FDA and CDC (default)
  python main.py --fda          # Scrape FDA only
  python main.py --cdc          # Scrape CDC only
  python main.py --fda --cdc    # Scrape both FDA and CDC
  python main.py --combine      # Combine existing FDA and CDC JSON files
        """
    )
    parser.add_argument('--fda', action='store_true', help='Scrape FDA outbreak data')
    parser.add_argument('--cdc', action='store_true', help='Scrape CDC outbreak data')
    parser.add_argument('--combine', action='store_true', help='Combine existing FDA and CDC JSON files without scraping')
    parser.add_argument('--delay', type=float, default=2.0, help='Delay between requests in seconds (default: 2.0)')
    parser.add_argument('--no-playwright', action='store_true', help='Disable Playwright and use simple HTTP requests (may find fewer outbreaks)')
    parser.add_argument('--use-known-urls', action='store_true', help='Use hardcoded list of known CDC URLs instead of auto-discovery')

    args = parser.parse_args()

    # If --combine is specified, don't scrape
    if args.combine:
        scrape_fda = False
        scrape_cdc = False
    else:
        # If no flags specified, scrape both
        scrape_fda = args.fda or (not args.fda and not args.cdc)
        scrape_cdc = args.cdc or (not args.fda and not args.cdc)

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

    print("=" * 60)
    print("FOODBORNE OUTBREAK ALERT SYSTEM")
    print("=" * 60)

    # Each scrape mode writes ONLY its own source file. The combined file is
    # never touched by a scrape, so FDA and CDC can never clobber each other;
    # it is rebuilt from disk by build_combined() below.
    files_created = []

    if scrape_fda:
        print("\n[FDA] Scraping FDA Outbreaks...")
        print("-" * 60)
        fda_outbreaks = aggregator.fda_scraper.scrape_all(limit=None, delay=args.delay)
        if fda_outbreaks:
            aggregator.fda_scraper.save_to_json(fda_outbreaks)
            files_created.append("  - data/raw/fda_outbreaks.json (FDA data only)")

    if scrape_cdc:
        print("\n[CDC] Scraping CDC Outbreaks...")
        print("-" * 60)
        # Determine which URLs to use
        urls_to_use = known_cdc_urls if args.use_known_urls else None
        # Use Playwright unless disabled
        use_pw = not args.no_playwright
        cdc_outbreaks = aggregator.cdc_scraper.scrape_all_pathogens(
            delay=args.delay,
            use_playwright=use_pw,
            known_urls=urls_to_use
        )
        if cdc_outbreaks:
            aggregator.cdc_scraper.save_to_json(cdc_outbreaks)
            files_created.append("  - data/raw/cdc_outbreaks.json (CDC data only)")

    # Rebuild the combined file only when explicitly requested (--combine) or
    # when both sources were refreshed in this run. Never after a single-source
    # scrape, so the other source's data is preserved.
    if args.combine or (scrape_fda and scrape_cdc):
        build_combined(aggregator)
        files_created.append("  - data/raw/combined_outbreaks.json (unified data with statistics)")

    print("\n" + "=" * 60)
    print("SCRAPING COMPLETE")
    print("=" * 60)
    print("Files created:")
    for file in files_created:
        print(file)


if __name__ == "__main__":
    main()
