from app.compliance import ComplianceEngine


def base_issue(fields):
    return {
        'key': 'NMGOS-1',
        'fields': {
            'summary': 'Test',
            'issuetype': {'name': 'Task'},
            'description': '',
            'issuelinks': [],
            **fields,
        },
    }


def test_known_dependencies_pass_when_mapped_business_impact_mentions_dependency():
    engine = ComplianceEngine(field_map={'business_impact': 'customfield_bi'}, internal_projects=[])
    issue = base_issue({'customfield_bi': 'Dependency on infrastructure'})
    result = engine.evaluate_issue(issue)
    check = next(c for c in result['checks'] if c['key'] == 'dependencies')
    assert check['passed'] is True
    assert 'Business Impact field' in check['evidence']


def test_known_dependencies_pass_when_business_impact_candidate_has_dependencies_with_other_words():
    engine = ComplianceEngine(
        field_map={'business_impact_candidates': ['customfield_88888']},
        internal_projects=[]
    )
    issue = base_issue({'customfield_88888': 'No dependencies and Use cases in place24Jun: create'})
    result = engine.evaluate_issue(issue)
    check = next(c for c in result['checks'] if c['key'] == 'dependencies')
    assert check['passed'] is True
    assert 'Business Impact field' in check['evidence']


def test_arbitrary_loaded_custom_field_with_dependency_no_longer_passes_v128():
    engine = ComplianceEngine(field_map={}, internal_projects=[])
    issue = base_issue({'customfield_99999': 'Dependency on infrastructure'})
    result = engine.evaluate_issue(issue)
    check = next(c for c in result['checks'] if c['key'] == 'dependencies')
    assert check['passed'] is False


def test_plain_text_without_dependency_still_fails():
    engine = ComplianceEngine(field_map={}, internal_projects=[])
    issue = base_issue({'customfield_77777': 'Use cases in place24Jun: create'})
    result = engine.evaluate_issue(issue)
    check = next(c for c in result['checks'] if c['key'] == 'dependencies')
    assert check['passed'] is False


def test_unmapped_loaded_custom_business_impact_like_field_no_longer_passes_v128():
    engine = ComplianceEngine(field_map={'business_impact': 'customfield_wrong_bi'}, internal_projects=[])
    issue = base_issue({
        'customfield_wrong_bi': '',
        'customfield_unknown_business_impact': 'Dependency on infrastructure but managed 24Jun: tickets to be ready for review Monday',
    })
    result = engine.evaluate_issue(issue)
    check = next(c for c in result['checks'] if c['key'] == 'dependencies')
    assert check['passed'] is False
