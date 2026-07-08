from app.compliance import ComplianceEngine
from app.jira_client import JiraClient


def ready_issue() -> dict:
    return {
        'key': 'NMGOS-3021',
        'fields': {
            'summary': 'ACMP to Nexus discovery',
            'issuetype': {'name': 'Task'},
            'status': {'name': 'Open'},
            'assignee': {'displayName': 'Owner'},
            'description': '',
            'issuelinks': [],
            'timetracking': {},
            'customfield_dor': 'Architecture and stakeholders ready',
            'customfield_dod': 'Migration plan and risks documented',
            'customfield_ac': 'Discovery outputs approved',
            'customfield_dep': 'No known dependencies',
            'customfield_owner': {'displayName': 'Business Owner'},
            'customfield_risk': {'value': 'Low'},
            'customfield_percent': 80,
        },
    }


def test_additional_jira_fields_are_scored_and_level_scoped():
    criteria = [
        {'id': 'owner', 'field_id': 'customfield_owner', 'field_name': 'Business Owner', 'label': 'Business owner assigned', 'rule': 'required', 'expected': '', 'applies_to': 'all'},
        {'id': 'risk', 'field_id': 'customfield_risk', 'field_name': 'Risk Rating', 'label': 'Risk rating acceptable', 'rule': 'one_of', 'expected': 'Low, Medium', 'applies_to': 'top_level'},
        {'id': 'percent', 'field_id': 'customfield_percent', 'field_name': 'Analysis Complete %', 'label': 'Analysis at least 75%', 'rule': 'numeric_min', 'expected': '75', 'applies_to': 'top_level'},
        {'id': 'story-only', 'field_id': 'customfield_owner', 'field_name': 'Business Owner', 'label': 'Story owner', 'rule': 'required', 'expected': '', 'applies_to': 'story'},
    ]
    engine = ComplianceEngine(
        {
            'dor': 'customfield_dor', 'dod': 'customfield_dod',
            'acceptance_criteria': 'customfield_ac', 'dependencies': 'customfield_dep',
        },
        ['NMGOS'],
        additional_criteria=criteria,
    )
    result = engine.evaluate_issue(ready_issue(), 'top_level')
    checks = {check['key']: check for check in result['checks']}

    assert checks['custom:owner']['passed'] is True
    assert checks['custom:risk']['passed'] is True
    assert checks['custom:percent']['passed'] is True
    assert checks['custom:story-only']['applicable'] is False
    assert result['score'] == 100.0


def test_additional_field_can_be_excluded_without_blocking():
    criterion = {
        'id': 'approval', 'field_id': 'customfield_approval', 'field_name': 'Architecture Approval',
        'label': 'Architecture approved', 'rule': 'boolean_true', 'expected': '', 'applies_to': 'all',
    }
    engine = ComplianceEngine(
        {
            'dor': 'customfield_dor', 'dod': 'customfield_dod',
            'acceptance_criteria': 'customfield_ac', 'dependencies': 'customfield_dep',
        },
        ['NMGOS'],
        excluded_criteria={'custom:approval'},
        additional_criteria=[criterion],
    )
    result = engine.evaluate_issue(ready_issue(), 'top_level')
    check = next(item for item in result['checks'] if item['key'] == 'custom:approval')
    assert check['excluded'] is True
    assert check['applicable'] is False
    assert result['passed'] is True


def test_project_create_metadata_fields_are_merged_across_issue_types():
    payload = {
        'projects': [{
            'key': 'NMGOS',
            'issuetypes': [
                {'id': '1', 'fields': {
                    'customfield_1': {'name': 'Business Owner', 'required': True, 'schema': {'type': 'user'}},
                }},
                {'id': '2', 'fields': {
                    'customfield_1': {'name': 'Business Owner', 'required': False, 'schema': {'type': 'user'}},
                    'customfield_2': {'name': 'Risk Rating', 'required': False, 'schema': {'type': 'option'}},
                }},
            ],
        }],
    }
    global_catalog = {
        'customfield_1': {'id': 'customfield_1', 'name': 'Business Owner', 'clauseNames': ['Business Owner']},
        'customfield_2': {'id': 'customfield_2', 'name': 'Risk Rating', 'clauseNames': ['Risk Rating']},
    }
    fields = JiraClient._extract_create_meta_fields(payload, global_catalog)
    by_id = {field['id']: field for field in fields}
    assert set(by_id) == {'customfield_1', 'customfield_2'}
    assert by_id['customfield_1']['required'] is True
    assert by_id['customfield_2']['schema_type'] == 'option'
