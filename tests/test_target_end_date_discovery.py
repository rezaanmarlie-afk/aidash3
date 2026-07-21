from app.main import _initiative_target_date, _initiative_yield_completion


def test_target_end_date_candidate_accepts_string_schema_discovery():
    issue = {
        'key': 'NMGOS-1',
        'fields': {},
        '_target_end_date_candidates': [
            {'field_id': 'customfield_12345', 'name': 'Target End', 'value': '2026-09-30'}
        ],
    }
    d, source, raw = _initiative_target_date(issue)
    assert d.isoformat() == '2026-09-30'
    assert source == 'Target End'


def test_target_end_date_parses_wrapped_object():
    issue = {
        'fields': {},
        '_target_end_date_candidates': [
            {'field_id': 'customfield_1', 'name': 'Initiative Target Completion Date', 'value': {'value': '2026-09-30'}}
        ],
    }
    d, source, _ = _initiative_target_date(issue)
    assert d.isoformat() == '2026-09-30'
    assert source == 'Initiative Target Completion Date'


def test_yield_uses_target_end_candidate():
    result = {
        'initiative': {
            'key': 'NMGOS-1', 'fields': {'resolutiondate':'2026-10-01T10:00:00+00:00'},
            '_target_end_date_candidates': [{'name':'Target End','field_id':'customfield_9','value':'2026-09-30'}]
        },
        'epics': [{'key':'E-1','issue_type':'Epic','stories':[
            {'key':'S-1','issue_type':'Story','status':'Done','fields':{'resolutiondate':'2026-09-30T10:00:00+00:00'}}
        ]}],
        'direct_stories': [], 'additional_descendants': []
    }
    completion = _initiative_yield_completion(result)
    assert completion['completed'] is True
    assert completion['target_date_source'] == 'Target End'
