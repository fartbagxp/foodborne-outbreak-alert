"""Tests for outbreak status classification, using text shapes seen on real CDC/FDA pages."""
from datetime import datetime, timezone

from main import apply_derived_fields, classify_status

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
RECENT = datetime(2026, 9, 1, tzinfo=timezone.utc)
OLD = datetime(2018, 5, 1, tzinfo=timezone.utc)


def c(text, date=RECENT):
    return classify_status(text, date, NOW)


def test_cdc_status_field_open_across_newlines():
    # The old regex missed this: full_text is newline-joined and '.' doesn't cross lines.
    assert c("Food safety alert\nInvestigation status:\nOpen\nRecall issued:\nYes") == 'ongoing'


def test_cdc_status_field_closed():
    assert c("Investigation status:\nClosed\nRecall issued:\nYes") == 'closed'


def test_lead_closed_phrase_beats_stale_open_field():
    # CDC broccoli sprouts page: field still says Open, lead says it's over.
    text = "Investigation status:\nOpen\nRecall issued:\nYes\nThis outbreak is over.\nCDC investigated a multistate outbreak"
    assert c(text) == 'closed'


def test_fda_lead_line():
    assert c("Outbreak Investigation of Salmonella: Sprouts\nCDC declares outbreak over. FDA's outbreak investigation is complete.") == 'closed'
    assert c("Outbreak Investigation of E. coli: Sprouts\nDo not use recalled seeds. FDA's investigation is ongoing.") == 'ongoing'
    assert c("Outbreak Investigation of Cyclospora\nCDC announces the end of the outbreak; FDA continues its investigation.") == 'closed'


def test_fda_status_field():
    assert c("Fast Facts\nStatus\nEnded\nRecommendation") == 'closed'
    assert c("Fast Facts\nStatus\nOngoing\nRecommendation") == 'ongoing'
    assert c("Status\nCDC declares outbreak over as of March 26, 2024.\n") == 'closed'


def test_bare_word_active_is_not_ongoing():
    # The old regex matched 'active' anywhere, flagging 31 closed 2018 pages as ongoing.
    assert c("Investigation status:\nClosed\nStay active and wash your hands.") == 'closed'
    assert c("Some text mentioning active cultures.") == 'unknown'


def test_previous_updates_history_ignored():
    text = "FDA's investigation is ongoing.\nPrevious Updates\nJune 2026\nCDC declares outbreak over."
    assert c(text) == 'ongoing'


def test_old_archive_page_still_saying_investigating_is_closed():
    text = "Posted August 19, 2016\nThe Hawaii Department of Health is investigating an outbreak of hepatitis A."
    assert c(text, None) == 'closed'  # date taken from the page text
    assert c(text, OLD) == 'closed'


def test_explicit_open_field_trusted_regardless_of_age():
    assert c("Investigation status:\nOpen", OLD) == 'ongoing'


def test_no_text_no_date_is_unknown():
    assert c(None, None) == 'unknown'
    assert c("", None) == 'unknown'


def test_apply_derived_fields_skips_failed_scrapes_and_uses_slug_date():
    records = [
        {'outbreak_id': 'cdc_salmonella_africana-06-24', 'scrape_status': '404_not_found'},
        {'outbreak_id': 'cdc_salmonella_basil-04-18', 'scrape_status': 'success',
         'full_text': 'Officials are investigating an outbreak.'},
    ]
    apply_derived_fields(records, 'CDC', NOW)
    assert 'status' not in records[0]
    assert records[1]['status'] == 'closed'
