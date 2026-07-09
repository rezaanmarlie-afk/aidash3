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


def test_business_impact_no_dependencies_overrides_incomplete_dependency_field():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='Business Impact: No dependencies',
        dependencies='Dependency discovery is still in progress but no Jira links are attached.',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Business Impact field' in check['evidence']


def test_business_impact_no_dependencies_overrides_link_warning():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='There are no known dependencies for this initiative.',
        dependencies='',
    )
    issue['fields']['issuelinks'] = [{'outwardIssue': {'key': 'ATM-5317'}}]
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Business Impact field' in check['evidence']



def test_business_impact_candidate_field_used_when_saved_mapping_is_empty_or_wrong():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
        'business_impact_candidates': ['customfield_wrong_bi', 'customfield_project_bi'],
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_project_bi'] = 'No dependencies'
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Business Impact field' in check['evidence']


def test_business_impact_candidate_field_does_not_accept_plain_none():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact_candidates': ['customfield_project_bi'],
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_project_bi'] = 'None'
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is False


def test_business_impact_no_dependencies_with_following_text_passes():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='No dependencies and Use cases in place24Jun: create',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'No dependencies and Use cases' in check['evidence']


def test_business_impact_candidate_with_no_dependencies_beats_other_populated_duplicate():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
        'business_impact_candidates': ['customfield_wrong_bi', 'customfield_project_bi'],
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_wrong_bi'] = 'Use cases in place24Jun: create'
    issue['fields']['customfield_project_bi'] = 'No dependencies and Use cases in place24Jun: create'
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'No dependencies and Use cases' in check['evidence']


def test_business_impact_dynamic_metadata_candidate_can_pass_dependencies():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_wrong_bi'] = 'Business value captured elsewhere'
    issue['_business_impact_candidates'] = [
        {
            'field_id': 'customfield_dynamic_bi',
            'name': 'Business Impact',
            'value': 'No dependencies and Use cases in place24Jun: create',
        }
    ]
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Business Impact' in check['evidence']


def test_business_impact_no_dependencies_with_other_words_must_pass_exact_user_rule():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='No dependencies with other words',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'No dependencies with other words' in check['evidence']


def test_business_impact_no_dependencies_with_dash_and_extra_text_passes():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='No Dependencies - Use cases in place 24 Jun: create',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True


def test_unmapped_business_impact_like_field_with_no_dependencies_extra_text_passes():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_unknown_business_impact'] = 'No dependencies and Use cases in place24Jun: create'
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'No dependencies and Use cases' in check['evidence']


def test_business_impact_adf_no_dependencies_with_other_words_passes():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_bi'] = {
        'type': 'doc',
        'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'No dependencies and Use cases in place24Jun: create'}]}
        ],
    }
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
