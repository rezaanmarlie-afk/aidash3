from app.compliance import ComplianceEngine
from app.main import _detailed_export_rows, _summary_export_rows, apply_descendant_story_point_rollup
from app.pdf_export import build_detail_pdf, build_summary_pdf


def issue(key: str, issue_type: str, points: float | int | None = None) -> dict:
    fields = {
        'summary': f'{key} summary',
        'description': '',
        'issuetype': {'name': issue_type},
        'status': {'name': 'Open'},
        'assignee': {'displayName': 'Example Owner'},
        'issuelinks': [],
        'timetracking': {},
    }
    if points is not None:
        fields['customfield_sp'] = points
    return {'key': key, 'fields': fields}


def test_story_points_roll_from_stories_and_epics_to_parent_ticket():
    engine = ComplianceEngine({'story_points': 'customfield_sp'}, ['NMGOS'])
    root = issue('NMGOS-1', 'Initiative', 2)
    epic_a = issue('NMGOS-2', 'Epic', 5)
    epic_b = issue('NMGOS-3', 'Epic', None)
    story_a = issue('NMGOS-4', 'Story', 3)
    story_b = issue('NMGOS-5', 'Story', 8)
    story_c = issue('NMGOS-6', 'Story', 1.5)

    result = engine.evaluate_tree(root, [epic_a, epic_b], {
        'NMGOS-2': [story_a, story_b],
        'NMGOS-3': [story_c],
    })

    assert result['initiative_story_points'] == 2
    assert result['epic_story_points'] == 5
    assert result['story_story_points'] == 12.5
    assert result['story_points_total'] == 19.5
    assert result['initiative']['rolled_story_points'] == 19.5
    assert result['epics'][0]['rolled_story_points'] == 16
    assert result['epics'][1]['rolled_story_points'] == 1.5
    assert result['epics'][0]['stories'][0]['story_points'] == 3


def test_story_point_rollup_is_in_csv_and_pdf_exports():
    engine = ComplianceEngine({'story_points': 'customfield_sp'}, ['NMGOS'])
    result = engine.evaluate_tree(issue('NMGOS-1', 'Initiative'), [issue('NMGOS-2', 'Epic', 5)], {
        'NMGOS-2': [issue('NMGOS-4', 'Story', 3), issue('NMGOS-5', 'Story', 8)]
    })
    result['latest_signoff'] = None
    scan = {'results': [result], 'jql': 'project in (NMGOS)', 'summary': {'story_points_total': 16}}
    filters = {'pi_value': 'PI26', 'scrum_master_id': 'account-id', 'scrum_master_name': 'Test SM'}

    summary_rows = _summary_export_rows(scan, filters)
    detail_rows = _detailed_export_rows(scan, filters)

    assert 'Rolled-Up Story Points' in summary_rows[0]
    assert 16 in summary_rows[1]
    assert 'Issue Story Points' in detail_rows[0]
    assert 'Root Rolled-Up Story Points' in detail_rows[0]
    assert any(row[16] == 16 for row in detail_rows[1:])
    assert build_summary_pdf(scan, filters, '1.10.0').startswith(b'%PDF')
    assert build_detail_pdf(scan, filters, '1.10.0').startswith(b'%PDF')


def test_story_points_fallback_uses_populated_candidate_when_saved_mapping_is_empty():
    engine = ComplianceEngine({
        'story_points': 'customfield_wrong',
        'story_points_candidates': ['customfield_wrong', 'customfield_actual'],
    }, ['NMGOS'])
    root = issue('NMGOS-10', 'Initiative')
    epic = issue('NMGOS-11', 'Epic')
    story = issue('NMGOS-12', 'Story')
    story['fields']['customfield_wrong'] = None
    story['fields']['customfield_actual'] = 13

    result = engine.evaluate_tree(root, [epic], {'NMGOS-11': [story]})

    assert result['story_story_points'] == 13
    assert result['story_points_total'] == 13
    assert result['epics'][0]['stories'][0]['story_points'] == 13


def test_story_points_uses_issue_metadata_candidate_when_field_not_in_saved_mapping():
    engine = ComplianceEngine({'story_points': 'customfield_company_managed'}, ['NMGOS'])
    root = issue('NMGOS-20', 'Initiative')
    epic = issue('NMGOS-21', 'Epic')
    story = issue('NMGOS-22', 'Story')
    story['_story_point_candidates'] = [
        {'field_id': 'customfield_team_workspace', 'name': 'Story point estimate', 'value': 21}
    ]

    result = engine.evaluate_tree(root, [epic], {'NMGOS-21': [story]})

    assert result['story_story_points'] == 21
    assert result['story_points_total'] == 21
    assert result['epics'][0]['stories'][0]['story_points'] == 21


def test_dynamic_story_point_candidate_does_not_override_explicit_populated_mapping():
    engine = ComplianceEngine({'story_points': 'customfield_mapped'}, ['NMGOS'])
    root = issue('NMGOS-30', 'Initiative')
    epic = issue('NMGOS-31', 'Epic')
    story = issue('NMGOS-32', 'Story')
    story['fields']['customfield_mapped'] = 5
    story['_story_point_candidates'] = [
        {'field_id': 'customfield_team_workspace', 'name': 'Story point estimate', 'value': 99}
    ]

    result = engine.evaluate_tree(root, [epic], {'NMGOS-31': [story]})

    assert result['story_story_points'] == 5


def test_story_points_roll_up_from_stories_linked_directly_to_initiative():
    engine = ComplianceEngine({'story_points': 'customfield_sp'}, ['NMGOS'])
    root = issue('NMGOS-3894', 'Initiative')
    direct_story_a = issue('NMGOS-4001', 'Story', 13)
    direct_story_b = issue('NMGOS-4002', 'Story', 21)

    result = engine.evaluate_tree(root, [], {}, direct_stories=[direct_story_a, direct_story_b])

    assert result['direct_story_count'] == 2
    assert result['story_count'] == 2
    assert result['direct_story_points'] == 34
    assert result['story_story_points'] == 34
    assert result['story_points_total'] == 34
    assert result['initiative']['rolled_story_points'] == 34
    assert [story['key'] for story in result['direct_stories']] == ['NMGOS-4001', 'NMGOS-4002']



def test_story_points_roll_up_from_non_standard_descendant_work_without_changing_compliance_score():
    engine = ComplianceEngine({'story_points': 'customfield_sp'}, ['NMGOS'])
    root = issue('NMGOS-3894', 'Initiative')
    result = engine.evaluate_tree(root, [], {})
    before_score = result['hierarchy_score']
    feature = issue('NMGOS-4100', 'Feature', 8)
    task = issue('NMGOS-4101', 'Task', 13)

    result = apply_descendant_story_point_rollup(result, [feature, task], engine)

    assert result['additional_descendant_count'] == 2
    assert result['additional_descendant_story_points'] == 21
    assert result['story_points_total'] == 21
    assert result['initiative']['rolled_story_points'] == 21
    assert result['hierarchy_score'] == before_score
    assert [item['key'] for item in result['additional_descendants']] == ['NMGOS-4100', 'NMGOS-4101']
