"""Tests for CDC outbreak IDs and case/hospitalization/death/state extraction."""
from main import (apply_counts, cdc_outbreak_id, cdc_outbreak_slug, dedupe_by_id,
                  extract_counts, rekey_existing_cdc)


# --- IDs -------------------------------------------------------------------

def test_slug_uses_folder_for_index_pages():
    assert cdc_outbreak_slug('https://www.cdc.gov/salmonella/outbreaks/oysters-10-26/index.html') == 'oysters-10-26'
    assert cdc_outbreak_slug('https://www.cdc.gov/listeria/outbreaks/enoki-03-20/investigation.html') == 'enoki-03-20'


def test_slug_uses_file_name_not_year_folder():
    # Previously both became 'cdc_salmonella_2011'.
    a = cdc_outbreak_id('https://www.cdc.gov/salmonella/2011/cantaloupes-6-23-2011.html', 'salmonella')
    b = cdc_outbreak_id('https://www.cdc.gov/salmonella/2011/turkey-burgers-4-1-2011.html', 'salmonella')
    assert a == 'cdc_salmonella_cantaloupes-6-23-2011'
    assert a != b


def test_slug_skips_generic_and_year_segments():
    assert cdc_outbreak_slug('https://www.cdc.gov/ecoli/outbreaks/raw-milk-cheese-2-24.html') == 'raw-milk-cheese-2-24'
    assert (cdc_outbreak_slug('https://www.cdc.gov/parasites/cyclosporiasis/outbreaks/2019/weekly/index.html')
            == 'cyclosporiasis-outbreaks-2019-weekly')
    assert cdc_outbreak_slug('https://www.cdc.gov/vibrio/investigations/index.html') == 'vibrio-investigations'


def test_archive_url_maps_to_same_slug_as_live_page():
    live = 'https://www.cdc.gov/salmonella/2011/cantaloupes-6-23-2011.html'
    archive = 'https://archive.cdc.gov/www_cdc_gov/salmonella/2011/cantaloupes-6-23-2011.html'
    assert cdc_outbreak_slug(live) == cdc_outbreak_slug(archive)


def test_rekey_collapses_collided_records():
    url = 'https://www.cdc.gov/salmonella/2011/cantaloupes-6-23-2011.html'
    saved = {f'x{i}': {'outbreak_id': 'cdc_salmonella_2011', 'original_url': url, 'pathogen': 'salmonella'}
             for i in range(8)}
    rekeyed = rekey_existing_cdc(saved)
    assert list(rekeyed) == ['cdc_salmonella_cantaloupes-6-23-2011']
    assert rekeyed['cdc_salmonella_cantaloupes-6-23-2011']['outbreak_id'] == 'cdc_salmonella_cantaloupes-6-23-2011'


def test_dedupe_drops_repeat_listings_and_disambiguates_collisions():
    invs = [
        {'outbreak_id': 'cdc_x_a', 'url': 'https://www.cdc.gov/x/a/index.html'},
        {'outbreak_id': 'cdc_x_a', 'url': 'https://www.cdc.gov/x/a/index.html'},
        {'outbreak_id': 'cdc_x_a', 'url': 'https://www.cdc.gov/y/a/index.html'},
    ]
    out = dedupe_by_id(invs)
    assert len(out) == 2
    assert out[0]['outbreak_id'] == 'cdc_x_a'
    assert out[1]['outbreak_id'].startswith('cdc_x_a-')
    assert out[1]['outbreak_id'] == dedupe_by_id(invs)[1]['outbreak_id']  # stable across runs


# --- Counts ----------------------------------------------------------------

CDC_FAST_FACTS = ("Food safety alert\nInvestigation status:\nOpen\nUpdates\nFast Facts\nCases\n: 28 (1 new)\n"
                  "Hospitalizations\n: 5\nDeaths\n: 0\nStates\n: 13 (1 new)\nUpdates\n")


def test_cdc_fast_facts():
    # The old regexes produced deaths=5 (hospitalizations) and state_count=0 (deaths) here.
    assert extract_counts(CDC_FAST_FACTS) == {'case_count': 28, 'hospitalizations': 5, 'deaths': 0, 'state_count': 13}


def test_cdc_at_a_glance_variants():
    assert extract_counts("At A Glance\nCase Count: 26\nJurisdictions: 8\nHospitalizations: 9\nDeaths: 1") == \
        {'case_count': 26, 'state_count': 8, 'hospitalizations': 9, 'deaths': 1}
    assert extract_counts("Fast Facts\nIllnesses: 12,883\nHospitalizations: 570\nDeaths: 2\nStates: 21")['case_count'] == 12883


def test_fda_case_count_table():
    text = ("Case Counts\nTotal Illnesses: 13\nHospitalizations: 8\nHUS: 3\nDeaths: 0\n"
            "Last Illness Onset: August 26, 2026\nStates with Cases: CA, CO, GA, KY, MI, NV, OR, TN, UT\n"
            "Product Distribution: Nationwide")
    assert extract_counts(text) == {
        'case_count': 13, 'hospitalizations': 8, 'deaths': 0, 'state_count': 9,
        'states_affected': ['CA', 'CO', 'GA', 'KY', 'MI', 'NV', 'OR', 'TN', 'UT'],
    }


def test_fda_states_with_per_state_counts():
    assert extract_counts("States with Cases: AZ (1), IA (6), MN (2)\n")['states_affected'] == ['AZ', 'IA', 'MN']


def test_current_section_wins_over_previous_updates():
    text = "Total Illnesses: 76\nDeaths: 0\nPrevious Updates\nTotal Illnesses: 55\nDeaths: 0"
    assert extract_counts(text)['case_count'] == 76


def test_prose_archive_page():
    text = ("As of June 20, 2011, a total of 20 ill people infected with the outbreak strain have been "
            "reported from 10 states. Three ill people were hospitalized. No deaths were reported.")
    assert extract_counts(text) == {'case_count': 20, 'hospitalizations': 3, 'deaths': 0, 'state_count': 10}


def test_prose_compound_number_words():
    # "Sixty-two" used to be read as 2.
    text = ("Sixty-two people infected with the outbreak strain were reported from 16 states. "
            "Twenty-five people were hospitalized. One death was reported.")
    assert extract_counts(text) == {'case_count': 62, 'hospitalizations': 25, 'deaths': 1, 'state_count': 16}


def test_prose_ignores_years_and_sub_counts():
    text = "In May 2014 cases rose. Three people have been infected with both pathogens."
    assert 'case_count' not in extract_counts(text)


def test_impossible_values_dropped():
    # More hospitalizations than cases means one of them was misread.
    assert 'hospitalizations' not in extract_counts("Cases: 10\nHospitalizations: 34\nDeaths: 0")


def test_apply_counts_removes_stale_values():
    record = {'full_text': 'No numbers here.', 'case_count': 2014, 'deaths': 5, 'states_affected': ['IN']}
    apply_counts(record)
    assert 'case_count' not in record and 'deaths' not in record
    assert record['states_affected'] == ['IN']  # kept: page has no explicit list
