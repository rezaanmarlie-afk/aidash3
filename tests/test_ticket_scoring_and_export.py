from app.compliance import ComplianceEngine
from app.main import _detailed_export_rows, _summary_export_rows


NMGOS_3021_DESCRIPTION = """Summary

The task is to discover the work needed to migrate ACMP to Nexus.

Context

This issue involves understanding the requirements and tasks necessary for the migration process.

Acceptance criteria

Complete the discovery of the work required for migration.

Other information

Definition of Ready (DoR)
Covers prerequisites like:

Business workflow completion

Technical dependency discovery:

ACMP architecture

Nexus portal requirements

Jira ticket creation

System documentation

Stakeholder alignment

Developer capacity

Definition of Done (DoD)
Covers deliverables like:

A full ACMP component inventory

Documented integration points

A phased migration plan

Risk assessment

Technical Jira tickets created

Business owner sign-off
"""


def issue(key: str, issue_type: str, description: str = NMGOS_3021_DESCRIPTION) -> dict:
    return {
        'key': key,
        'fields': {
            'summary': 'Discover work needed to migrate ACMP to Nexus',
            'description': description,
            'issuetype': {'name': issue_type},
            'status': {'name': 'Open'},
            'assignee': {'displayName': 'Example Owner'},
            'issuelinks': [],
            'timetracking': {},
        },
    }


def test_nmgos_3021_description_scores_75_percent_at_ticket_level():
    engine = ComplianceEngine({}, ['NMGOS'], allow_description_fallback=True)
    result = engine.evaluate_issue(issue('NMGOS-3021', 'Task'))
    checks = {check['key']: check for check in result['checks']}

    assert checks['dor']['passed'] is True
    assert checks['dod']['passed'] is True
    assert checks['acceptance_criteria']['passed'] is True
    assert checks['dependencies']['passed'] is False
    assert checks['story_estimation']['applicable'] is False
    assert result['passed_count'] == 3
    assert result['applicable_count'] == 4
    assert result['score'] == 75.0
    assert result['passed'] is False


def test_ticket_score_is_separate_from_full_hierarchy_score():
    engine = ComplianceEngine({}, ['NMGOS'], allow_description_fallback=True)
    result = engine.evaluate_tree(issue('NMGOS-3021', 'Task'), [], {})

    assert result['ticket_score'] == 75.0
    assert result['hierarchy_score'] < result['ticket_score']
    assert result['score'] == result['hierarchy_score']
    assert result['compliant'] is False


def test_exports_contain_both_scores_and_criterion_evidence():
    engine = ComplianceEngine({}, ['NMGOS'], allow_description_fallback=True)
    result = engine.evaluate_tree(issue('NMGOS-3021', 'Task'), [], {})
    result['latest_signoff'] = None
    scan = {'results': [result], 'jql': 'project in ("NMGOS")'}
    filters = {
        'pi_value': 'PI26',
        'scrum_master_name': 'Test Scrum Master',
        'scrum_master_id': 'account-id',
    }

    summary = _summary_export_rows(scan, filters)
    detail = _detailed_export_rows(scan, filters)

    assert 'Top-Level Ticket Compliance %' in summary[0]
    assert 'Full Hierarchy Compliance %' in summary[0]
    assert 75.0 in summary[1]
    assert 'Criterion' in detail[0]
    assert any('Definition of Ready' in row for row in detail[1:])
    assert any('No usable evidence' in str(value) or 'Dependencies are neither' in str(value)
               for row in detail[1:] for value in row)
