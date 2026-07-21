from app.main import _parse_jira_date, _initiative_target_date


def test_target_end_exact_custom_field_is_detected():
    issue = {
        'key': 'NMGOS-3919',
        'fields': {'customfield_10023': '2026-07-20', 'description': None},
        '_target_end_field_id': 'customfield_10023',
        '_target_end_field_name': 'Target end',
    }
    date_value, source, raw = _initiative_target_date(issue)
    assert date_value.isoformat() == '2026-07-20'
    assert 'customfield_10023' in source
    assert raw == '2026-07-20'


def test_parse_jira_target_end_object():
    assert _parse_jira_date({'value': '2026-07-20'}).isoformat() == '2026-07-20'
