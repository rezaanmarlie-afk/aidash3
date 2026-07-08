from app.compliance import ComplianceEngine
from app.pdf_export import build_detail_pdf, build_summary_pdf


def issue(key: str, issue_type: str = 'Task') -> dict:
    return {
        'key': key,
        'fields': {
            'summary': 'ACMP to Nexus discovery',
            'description': (
                'Acceptance Criteria\nDiscovery output is completed.\n\n'
                'Definition of Ready (DoR)\nArchitecture and stakeholders are available.\n\n'
                'Definition of Done (DoD)\nMigration plan and risks are documented.'
            ),
            'issuetype': {'name': issue_type},
            'status': {'name': 'Open'},
            'assignee': {'displayName': 'Owner'},
            'issuelinks': [],
            'timetracking': {},
        },
    }


def test_excluded_criteria_are_not_scored_or_blocking():
    engine = ComplianceEngine(
        {}, ['NMGOS'], allow_description_fallback=True,
        excluded_criteria={'dependencies', 'has_epics', 'epics_have_stories'},
    )
    result = engine.evaluate_tree(issue('NMGOS-3021'), [], {})
    checks = {check['key']: check for check in result['initiative']['checks']}
    structures = {check['key']: check for check in result['structural_checks']}

    assert checks['dependencies']['excluded'] is True
    assert checks['dependencies']['applicable'] is False
    assert structures['has_epics']['excluded'] is True
    assert structures['epics_have_stories']['excluded'] is True
    assert result['ticket_score'] == 100.0
    assert result['hierarchy_score'] == 100.0
    assert result['compliant'] is True


def test_pdf_exports_are_generated_for_summary_and_all_details():
    engine = ComplianceEngine({}, ['NMGOS'], allow_description_fallback=True, excluded_criteria={'dependencies'})
    result = engine.evaluate_tree(issue('NMGOS-3021'), [], {})
    result['latest_signoff'] = None
    scan = {
        'jql': 'project in ("NMGOS")',
        'results': [result],
        'summary': {
            'initiatives': 1, 'compliant': 0, 'blocked': 1, 'approved': 0,
            'ticket_score': result['ticket_score'], 'hierarchy_score': result['hierarchy_score'],
        },
    }
    filters = {
        'project': 'NMGOS', 'pi_value': 'PI26', 'priority': 'Critical',
        'scrum_master_id': 'account-id', 'scrum_master_name': 'Scrum Master',
        'excluded_criteria': ['dependencies'],
    }

    summary_pdf = build_summary_pdf(scan, filters, '1.8.0')
    detail_pdf = build_detail_pdf(scan, filters, '1.8.0')

    assert summary_pdf.startswith(b'%PDF-')
    assert detail_pdf.startswith(b'%PDF-')
    assert len(summary_pdf) > 2500
    assert len(detail_pdf) > 3500
