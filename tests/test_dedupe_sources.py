"""Tests for matching and merging CDC and FDA records of the same outbreak."""
from main import OutbreakAggregator, match_cdc_fda, merge_outbreak_records, title_food_words


def cdc(oid, title, cases=None, url=None, **kw):
    url = url or f'https://www.cdc.gov/x/outbreaks/{oid}/index.html'
    return {'outbreak_id': f'cdc_salmonella_{oid}', 'title': title, 'url': url, 'source': 'CDC',
            'pathogen': kw.pop('pathogen', 'salmonella'), 'case_count': cases, 'scrape_status': 'success', **kw}


def fda(slug, title, cases=None, **kw):
    return {'outbreak_id': f'outbreak-investigation-{slug}', 'title': title, 'source': 'FDA',
            'url': f'https://www.fda.gov/food/outbreaks-foodborne-illness/outbreak-investigation-{slug}',
            'case_count': cases, 'scrape_status': 'success', **kw}


def test_title_food_words_handles_both_title_styles():
    assert title_food_words('SalmonellaOutbreak Linked to Broccoli Sprouts') == {'broccoli', 'sprout'}
    assert title_food_words('Salmonella: Sprouts (September 2026)') == {'sprout'}


def test_matches_same_food_month_and_pathogen():
    c = cdc('broccoli-sprouts-09-26', 'SalmonellaOutbreak Linked to Broccoli Sprouts', 34)
    f = fda('salmonella-sprouts-september-2026', 'Salmonella: Sprouts (September 2026)', 34, date_str='September 2026')
    assert match_cdc_fda([c], [f]) == [(f, c, 'strong')]


def test_no_match_across_pathogens_or_distant_months():
    c = cdc('eggs-07-26', 'SalmonellaOutbreak Linked to Eggs', 134)
    f_listeria = fda('listeria-eggs-july-2026', 'Listeria monocytogenes: Eggs (July 2026)', 134, date_str='July 2026')
    f_later = fda('salmonella-eggs-june-2025', 'Salmonella: Eggs (June 2025)', 134, date_str='June 2025')
    assert match_cdc_fda([c], [f_listeria, f_later]) == []


def test_no_food_word_needs_identical_counts():
    # FDA "Seafood" vs CDC "Fish": same outbreak, identified by its count.
    fish = cdc('fish-10-22', '2022SalmonellaOutbreak Linked to Fish', 39)
    f = fda('salmonella-seafood-october-2022', 'Salmonella- Seafood (October 2022)', 39)
    assert len(match_cdc_fda([fish], [f])) == 1
    # Vegetable trays vs ground turkey, 5 vs 7 cases: previously a false match.
    turkey = cdc('turkey-03-19', '2019SalmonellaInfections Linked to Ground Turkey', 7)
    trays = fda('salmonella-vegetable-trays-march-2019', 'Salmonella- Vegetable Trays (March 2019)', 5)
    assert match_cdc_fda([turkey], [trays]) == []


def test_counts_decide_between_two_similar_outbreaks():
    # Two Dec 2021 packaged-salad Listeria outbreaks (10 and 18 cases).
    c10 = cdc('packaged-salad-a-12-21', 'Listeria Outbreak Linked to Packaged Salads', 10, pathogen='listeria')
    c18 = cdc('packaged-salad-b-12-21', 'Listeria Outbreak Linked to Packaged Salads', 18, pathogen='listeria')
    f10 = fda('listeria-packaged-salad-december-2021', 'Listeria- Packaged Salad (December 2021)', 10)
    f18 = fda('listeria-packaged-salad-2-december-2021', 'Listeria- Packaged Salad (2) (December 2021)', 18,
              date_str='December 2021')
    pairs = {(f['case_count'], c['case_count']) for f, c, _ in match_cdc_fda([c10, c18], [f10, f18])}
    assert pairs == {(10, 10), (18, 18)}


def test_failed_cdc_page_matched_on_listing_title():
    c = {'outbreak_id': 'cdc_salmonella_africana-06-24', 'title': 'Cucumbers', 'pathogen': 'salmonella',
         'url': 'https://www.cdc.gov/salmonella/africana-06-24/index.html', 'scrape_status': '404_not_found'}
    f = fda('salmonella-cucumbers-june-2024', 'Salmonella: Cucumbers (June 2024)', 551, date_str='June 2024')
    assert match_cdc_fda([c], [f]) == [(f, c, 'probable')]


def test_year_only_cdc_url_needs_food_match():
    c = {'outbreak_id': 'cdc_unknown_cyclosporiasis-outbreaks-2020', 'title': 'Bagged Salad Mix',
         'pathogen': 'unknown', 'url': 'https://www.cdc.gov/parasites/cyclosporiasis/outbreaks/2020/index.html',
         'scrape_status': '404_not_found'}
    f = fda('cyclospora-bagged-salads-june-2020', 'Cyclospora- Bagged Salad (June 2020)', 701)
    other = fda('cyclospora-basil-july-2019', 'Cyclospora- Imported Fresh Basil (July 2019)', 241)
    assert match_cdc_fda([c], [f, other]) == [(f, c, 'probable')]


def test_merge_takes_counts_from_more_recent_page_and_keeps_both_sources():
    c = {'id': 'cdc_x', 'source': 'CDC', 'title': 'CDC title', 'url': 'cdc-url', 'case_count': 28,
         'deaths': 0, 'status': 'unknown', 'pathogen': 'unknown'}
    f = {'id': 'fda_x', 'source': 'FDA', 'title': 'FDA title', 'url': 'fda-url', 'case_count': 30,
         'hospitalizations': 6, 'deaths': 0, 'states_affected': ['CA'], 'status': 'closed', 'pathogen': 'Salmonella'}
    m = merge_outbreak_records(c, f, 'strong')
    assert (m['id'], m['title'], m['url']) == ('cdc_x', 'CDC title', 'cdc-url')
    assert (m['case_count'], m['hospitalizations'], m['states_affected']) == (30, 6, ['CA'])
    assert m['status'] == 'closed' and m['pathogen'] == 'Salmonella'
    assert m['sources'] == ['CDC', 'FDA']
    assert [r['id'] for r in m['source_records']] == ['cdc_x', 'fda_x']


def test_combine_counts_each_outbreak_once():
    agg = OutbreakAggregator()
    c = cdc('sprouts-09-26', 'SalmonellaOutbreak Linked to Sprouts', 34, deaths=1)
    f = fda('salmonella-sprouts-september-2026', 'Salmonella: Sprouts (September 2026)', 34, deaths=1,
            date_str='September 2026')
    f_only = fda('scombrotoxin-tuna-november-2019', 'Scombrotoxin Fish Poisoning - Tuna (November 2019)', 50)
    combined = agg.combine_and_normalize({'cdc': [c], 'fda': [f, f_only]})
    stats = agg.get_summary_stats(combined)
    assert stats['total_outbreaks'] == 2
    assert stats['total_deaths'] == 1
    assert stats['outbreaks_by_source'] == {'CDC': 1, 'FDA': 2}
    assert stats['outbreaks_by_source_overlap'] == {'CDC and FDA': 1, 'FDA only': 1}
