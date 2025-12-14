#!/usr/bin/env python3
"""
Generate outbreak statistics and markdown tables for README.md
"""
import json
import re

from collections import defaultdict

def extract_year(outbreak):
    """Extract year from outbreak data."""
    # Try posted_date first (CDC outbreaks)
    if "posted_date" in outbreak and outbreak["posted_date"]:
        match = re.search(r'\b(20\d{2})\b', outbreak["posted_date"])
        if match:
            return int(match.group(1))

    # Try title
    if "title" in outbreak and outbreak["title"]:
        match = re.search(r'\b(20\d{2})\b', outbreak["title"])
        if match:
            return int(match.group(1))

    # Try URL
    if "url" in outbreak and outbreak["url"]:
        match = re.search(r'\b(20\d{2})\b', outbreak["url"])
        if match:
            return int(match.group(1))

    # Try ID
    if "id" in outbreak and outbreak["id"]:
        match = re.search(r'\b(20\d{2})\b', outbreak["id"])
        if match:
            return int(match.group(1))

    return None


def normalize_pathogen(pathogen):
    """Normalize pathogen names for grouping."""
    if not pathogen or pathogen == "Unknown" or pathogen == "unknown":
        return "Unknown"

    pathogen_lower = pathogen.lower()

    if "salmonella" in pathogen_lower:
        return "Salmonella"
    elif "e. coli" in pathogen_lower or "ecoli" in pathogen_lower:
        return "E. coli"
    elif "listeria" in pathogen_lower:
        return "Listeria"
    elif "botulism" in pathogen_lower:
        return "Botulism"
    elif "vibrio" in pathogen_lower:
        return "Vibrio"
    else:
        return pathogen.title()


def generate_statistics(data_path="data/raw/combined_outbreaks.json"):
    """Generate statistics from outbreak data."""
    with open(data_path, 'r') as f:
        data = json.load(f)

    summary = data.get("summary", {})
    outbreaks = data.get("outbreaks", [])

    # Count by year
    by_year = defaultdict(int)
    for outbreak in outbreaks:
        year = extract_year(outbreak)
        if year:
            by_year[year] += 1

    # Count by status
    by_status = defaultdict(int)
    for outbreak in outbreaks:
        status = outbreak.get("status", "unknown")
        by_status[status] += 1

    # Count by pathogen (normalized)
    by_pathogen = defaultdict(int)
    for outbreak in outbreaks:
        pathogen = outbreak.get("pathogen", "Unknown")
        normalized = normalize_pathogen(pathogen)
        by_pathogen[normalized] += 1

    # Count active investigations (ongoing status)
    active_count = by_status.get("ongoing", 0)

    # Get recent year range
    years = sorted([y for y in by_year.keys()])
    year_range = f"{years[0]}-{years[-1]}" if years else "N/A"

    return {
        "summary": summary,
        "by_year": dict(sorted(by_year.items(), reverse=True)),
        "by_status": dict(by_status),
        "by_pathogen": dict(sorted(by_pathogen.items(), key=lambda x: x[1], reverse=True)),
        "active_count": active_count,
        "year_range": year_range,
        "total_outbreaks": summary.get("total_outbreaks", len(outbreaks)),
    }


def format_table(headers, rows):
    """Format a markdown table with aligned columns."""
    # Calculate column widths
    col_widths = [len(h) for h in headers]

    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    # Create formatted lines
    lines = []

    # Header row
    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    lines.append(header_line)

    # Separator row
    sep_line = "| " + " | ".join("-" * w for w in col_widths) + " |"
    lines.append(sep_line)

    # Data rows
    for row in rows:
        data_line = "| " + " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        lines.append(data_line)

    return "\n".join(lines)


def generate_section_tables(stats):
    """Generate individual section tables as a dictionary."""
    sections = {}

    # Current Statistics section
    headers = ["Metric", "Count"]
    rows = [
        ["**Total Outbreaks**", stats['total_outbreaks']],
        ["**Active Investigations**", stats['active_count']],
        ["**Total Cases**", f"{stats['summary'].get('total_cases', 0):,}"],
        ["**Deaths**", f"{stats['summary'].get('total_deaths', 0):,}"],
        ["**Hospitalizations**", f"{stats['summary'].get('total_hospitalizations', 0):,}"],
        ["**States Affected**", stats['summary'].get('unique_states_affected', 0)],
    ]
    sections["## Current Statistics"] = format_table(headers, rows)

    # By Source section
    headers = ["Source", "Outbreaks", "Year Range"]
    rows = []
    by_source = stats['summary'].get('outbreaks_by_source', {})
    for source in sorted(by_source.keys()):
        count = by_source[source]
        rows.append([source, count, stats['year_range']])
    sections["## By Source"] = format_table(headers, rows)

    # By Status section
    headers = ["Status", "Count"]
    rows = []
    for status, count in sorted(stats['by_status'].items(), key=lambda x: x[1], reverse=True):
        rows.append([status.title(), count])
    sections["## By Status"] = format_table(headers, rows)

    # By Pathogen section
    headers = ["Pathogen", "Outbreaks"]
    rows = []
    top_pathogens = list(stats['by_pathogen'].items())[:10]
    for pathogen, count in top_pathogens:
        rows.append([pathogen, count])
    sections["## By Pathogen (Top 10)"] = format_table(headers, rows)

    # By Year section
    headers = ["Year", "Outbreaks"]
    rows = []
    recent_years = list(stats['by_year'].items())[:10]
    for year, count in recent_years:
        rows.append([year, count])
    sections["## By Year (Recent)"] = format_table(headers, rows)

    return sections


def update_section(content, section_title, new_table):
    """Update or insert a specific section in the README."""
    # Pattern to match section from title to next ## heading or end
    # Captures the title and everything until the next section or end
    pattern = rf'(^{re.escape(section_title)}\s*\n+)(.*?)(?=\n##|\Z)'
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)

    if match:
        # Section exists, replace just the table content
        # Keep exactly one blank line after the section title
        replacement = f"{section_title}\n\n{new_table}\n"
        content = content[:match.start()] + replacement + content[match.end():]
    else:
        # Section doesn't exist, append after Current Coverage section
        coverage_end = re.search(r'\*\*Current Coverage:\*\*.*?\n\n', content, re.DOTALL)
        if coverage_end:
            insert_pos = coverage_end.end()
            content = content[:insert_pos] + f"{section_title}\n\n{new_table}\n\n" + content[insert_pos:]

    return content


def update_readme_stats(section_tables, readme_path="README.md"):
    """Update README.md with new statistics."""
    with open(readme_path, 'r') as f:
        content = f.read()

    # Get current counts
    stats = generate_statistics()
    summary = stats['summary']
    by_source = summary.get('outbreaks_by_source', {})

    fda_count = by_source.get('FDA', 0)
    cdc_count = by_source.get('CDC', 0)
    total_count = stats['total_outbreaks']
    active_count = stats['active_count']
    year_range = stats['year_range']

    # Update the Current Coverage bullet points only
    coverage_pattern = r'(\*\*Current Coverage:\*\*\s*\n\n)(- 🏛️.*?- 📊.*?\n)(\n*)'
    coverage_bullets = f"""- 🏛️ FDA: {fda_count} outbreak investigations ({year_range})
- 🔬 CDC: {cdc_count} total investigations ({year_range})
- 🚨 Active: {active_count} ongoing investigations
- 📊 Total: {total_count} foodborne outbreak investigations
"""

    coverage_match = re.search(coverage_pattern, content, re.DOTALL)
    if coverage_match:
        # Replace with exactly one blank line after the coverage section
        content = content[:coverage_match.start(1)] + coverage_match.group(1) + coverage_bullets + "\n" + content[coverage_match.end():]

    # Update each statistics section individually
    for section_title, table_content in section_tables.items():
        content = update_section(content, section_title, table_content)

    with open(readme_path, 'w') as f:
        f.write(content)

    print("✓ Updated README.md with current statistics")
    print(f"  - Total outbreaks: {total_count}")
    print(f"  - FDA: {fda_count}, CDC: {cdc_count}")
    print(f"  - Active investigations: {active_count}")


def main():
    """Main function."""
    print("Generating outbreak statistics...")
    stats = generate_statistics()

    print(f"\nFound {stats['total_outbreaks']} total outbreaks")
    print(f"Active investigations: {stats['active_count']}")
    print(f"Year range: {stats['year_range']}")

    print("\nGenerating markdown tables...")
    section_tables = generate_section_tables(stats)

    print("\nUpdating README.md...")
    update_readme_stats(section_tables)

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
