from app.compliance import ComplianceEngine


def base_issue(business_impact: str = '', dependencies: str = '') -> dict:
    return {
        'key': 'NMGOS-3894',
        'fields': {
            'summary': 'IQGeo delivery initiative',
            'issuetype': {'name': 'Initiative'},
            'status': {'name': 'To Do'},
            'customfield_deps': dependencies,
            'customfield_bi': business_impact,
            'issuelinks': [],
            'timetracking': {},
        },
    }


def dependency_check(result: dict) -> dict:
    return next(check for check in result['checks'] if check['key'] == 'dependencies')


def test_known_dependencies_passes_when_business_impact_declares_no_dependencies():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    result = engine.evaluate_issue(base_issue('Business Impact: no dependencies'))
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Business Impact field' in check['evidence']


def test_business_impact_none_alone_does_not_count_as_dependency_declaration():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    result = engine.evaluate_issue(base_issue('None'))
    check = dependency_check(result)
    assert check['passed'] is False


def test_dependency_field_accepts_sentence_no_known_dependencies():
    engine = ComplianceEngine({'dependencies': 'customfield_deps'}, ['NMGOS'])
    result = engine.evaluate_issue(base_issue('', 'There are no known dependencies for this initiative.'))
    check = dependency_check(result)
    assert check['passed'] is True
