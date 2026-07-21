from app.main import _initiative_target_date


def test_exact_nmogs_target_end_custom_field_is_used():
    issue = {
        'fields': {'customfield_10023': '2026-07-20'},
        '_target_end_field_id': 'customfield_10023',
        '_target_end_field_name': 'Target end',
        'target_end_date': '2026-07-20',
    }
    value, source, raw = _initiative_target_date(issue)
    assert value.isoformat() == '2026-07-20'
    assert source == 'Target end (customfield_10023)'
    assert raw == '2026-07-20'


def test_exact_nmogs_field_works_without_saved_mapping_metadata():
    issue = {'fields': {'customfield_10023': '2026-07-20'}}
    value, source, _ = _initiative_target_date(issue)
    assert value.isoformat() == '2026-07-20'
    assert source == 'Target end (customfield_10023)'
