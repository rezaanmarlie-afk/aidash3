from app.compliance import ComplianceEngine, rich_text_to_text


def adf_paragraph(text: str) -> dict:
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}


def test_jira_cloud_adf_preserves_dor_dod_and_acceptance_headings():
    description = {
        'type': 'doc',
        'version': 1,
        'content': [
            adf_paragraph('Summary'),
            adf_paragraph('The task is to discover the work needed to migrate ACMP to Nexus.'),
            adf_paragraph('Context'),
            adf_paragraph('This issue involves understanding the migration requirements.'),
            adf_paragraph('Acceptance criteria'),
            adf_paragraph('Complete the discovery of the work required for migration.'),
            adf_paragraph('Other information'),
            adf_paragraph('Definition of Ready (DoR)'),
            adf_paragraph('Covers prerequisites like:'),
            adf_paragraph('Business workflow completion'),
            adf_paragraph('Technical dependency discovery'),
            adf_paragraph('Definition of Done (DoD)'),
            adf_paragraph('Covers deliverables like:'),
            adf_paragraph('A full ACMP component inventory'),
            adf_paragraph('A phased migration plan'),
            adf_paragraph('Business owner sign-off'),
        ],
    }
    issue = {
        'key': 'NMGOS-3021',
        'fields': {
            'summary': 'Discover ACMP Nexus migration work',
            'description': description,
            'issuetype': {'name': 'Task'},
            'status': {'name': 'Open'},
            'issuelinks': [],
            'timetracking': {},
        },
    }

    text = rich_text_to_text(description)
    assert 'Acceptance criteria\nComplete the discovery' in text
    assert 'Definition of Ready (DoR)\nCovers prerequisites' in text
    assert 'Definition of Done (DoD)\nCovers deliverables' in text

    result = ComplianceEngine({}, ['NMGOS'], allow_description_fallback=True).evaluate_issue(issue)
    checks = {check['key']: check for check in result['checks']}
    assert checks['dor']['passed'] is True
    assert checks['dod']['passed'] is True
    assert checks['acceptance_criteria']['passed'] is True
    assert 'Other information' not in checks['acceptance_criteria']['evidence']
    assert checks['dependencies']['passed'] is False
    assert result['score'] == 75.0


def test_adf_marked_text_fragments_do_not_gain_spaces():
    description = {
        'type': 'doc', 'version': 1,
        'content': [{
            'type': 'paragraph',
            'content': [
                {'type': 'text', 'text': 'Definition of '},
                {'type': 'text', 'text': 'Ready', 'marks': [{'type': 'strong'}]},
                {'type': 'text', 'text': ' (DoR)'},
            ],
        }, adf_paragraph('Ready evidence')],
    }
    assert rich_text_to_text(description) == 'Definition of Ready (DoR)\nReady evidence'
