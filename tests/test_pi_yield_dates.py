from app.main import _initiative_yield_completion, _pi_yield_metrics


def result(target='2026-06-12', resolution=None, status='Done', key='A'):
    return {
        'initiative': {
            'key': key,
            'status': status,
            'fields': {
                'customfield_10023': target,
                'resolutiondate': resolution,
            },
            '_target_end_field_id': 'customfield_10023',
            '_target_end_field_name': 'Target end',
        },
        'story_points_total': 6,
        'epics': [],
        'direct_stories': [],
        'additional_descendants': [],
    }


def test_resolution_on_target_passes():
    c = _initiative_yield_completion(result(resolution='2026-06-12T17:12:30.318+0200'))
    assert c['completed']
    assert c['days_from_target'] == 0
    assert c['allowed_completion_date'] == '2026-06-14'


def test_resolution_one_day_after_target_passes():
    c = _initiative_yield_completion(result(resolution='2026-06-13T11:55:25.820+0200'))
    assert c['completed']
    assert c['days_from_target'] == 1


def test_resolution_two_days_after_target_passes():
    c = _initiative_yield_completion(result(resolution='2026-06-14T23:59:00+0200'))
    assert c['completed']
    assert c['days_from_target'] == 2


def test_resolution_three_days_after_target_fails():
    c = _initiative_yield_completion(result(resolution='2026-06-15T01:00:00+0200'))
    assert not c['completed']
    assert c['days_from_target'] == 3


def test_missing_resolution_fails():
    c = _initiative_yield_completion(result(resolution=None, status='In Progress'))
    assert not c['completed']
    assert 'resolution date' in c['reason'].lower()


def test_missing_target_fails():
    c = _initiative_yield_completion(result(target=None, resolution='2026-06-13T11:55:25.820+0200'))
    assert not c['completed']
    assert 'target end' in c['reason'].lower()


def test_child_dates_do_not_affect_yield():
    r = result(resolution='2026-06-13T11:55:25.820+0200')
    r['epics'] = [{
        'key': 'E-1',
        'issue_type': 'Epic',
        'stories': [{
            'key': 'S-1',
            'issue_type': 'Story',
            'status': 'In Progress',
            'fields': {},
        }],
    }]
    assert _initiative_yield_completion(r)['completed']


def test_yield_counts_initiatives_resolved_within_tolerance():
    art = {'id': 1, 'name': 'ART', 'pi_value': 'PI25'}
    good = result(resolution='2026-06-13T11:55:25.820+0200', key='A')
    bad = result(resolution='2026-06-15T11:55:25.820+0200', key='B')
    scan = {'results': [good, bad]}
    baseline = {'created_at': 'now', 'snapshot': {'tickets': [{'key': 'A'}, {'key': 'B'}]}}
    m = _pi_yield_metrics(art, scan, baseline)
    assert m['completed_count'] == 1
    assert m['yield_percent'] == 50.0


def test_diagnostic_example_nmogs_3264_passes():
    r = result(target='2026-06-12', resolution='2026-06-13T11:55:25.820+0200', key='NMGOS-3264')
    c = _initiative_yield_completion(r)
    assert c['completed']
    assert c['resolution_date'] == '2026-06-13'
    assert c['allowed_completion_date'] == '2026-06-14'


def test_configurable_zero_day_tolerance():
    c = _initiative_yield_completion(result(resolution='2026-06-13T11:55:25.820+0200'), allowed_days=0)
    assert not c['completed']
    assert c['allowed_completion_date'] == '2026-06-12'
    assert c['allowed_days'] == 0


def test_configurable_five_day_tolerance():
    c = _initiative_yield_completion(result(resolution='2026-06-15T11:55:25.820+0200'), allowed_days=5)
    assert c['completed']
    assert c['allowed_completion_date'] == '2026-06-17'
    assert c['allowed_days'] == 5


def test_metrics_preserve_selected_allowed_days():
    art = {'id': 1, 'name': 'ART', 'pi_value': 'PI25'}
    scan = {'results': [result(resolution='2026-06-15T11:55:25.820+0200', key='A')]}
    baseline = {'created_at': 'now', 'snapshot': {'tickets': [{'key': 'A'}]}}
    m = _pi_yield_metrics(art, scan, baseline, allowed_days=3)
    assert m['allowed_days'] == 3
    assert m['completed_count'] == 1
