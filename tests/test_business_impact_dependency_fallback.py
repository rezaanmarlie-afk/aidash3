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


def test_unmapped_custom_field_is_not_treated_as_business_impact_v128():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_unknown_business_impact'] = 'No dependencies and Use cases in place24Jun: create'
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is False


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


def test_business_impact_managed_dependency_with_other_words_passes():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='Dependency on infrastructure but managed 24Jun: tickets to be ready for review Monday has context menu',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'managed/tracked' in check['evidence']
    assert 'Dependency on infrastructure' in check['evidence']


def test_business_impact_bare_dependency_without_management_now_passes_manager_rule_v123():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='Dependency on infrastructure',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'dependency wording is documented' in check['evidence']


def test_unmapped_custom_business_impact_managed_dependency_is_ignored_v128():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_unknown_business_impact'] = 'Dependency on infrastructure but managed 24Jun: tickets to be ready for review Monday has context menu'
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is False


def test_business_impact_adf_managed_dependency_passes():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['customfield_bi'] = {
        'type': 'doc',
        'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Dependency on infrastructure but managed 24Jun: tickets to be ready for review Monday has context menu'}]}
        ],
    }
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True


def test_no_dependencies_rule_is_preserved_after_managed_dependency_enhancement():
    """Regression: managed-dependency support must not override No dependencies."""
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='No dependencies means no Known dependencies and it should pass',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'dependencies explicitly declared' in check['evidence']
    assert 'managed/tracked' not in check['evidence']


def test_no_dependencies_with_extra_text_still_takes_priority_over_dependency_words():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='No dependencies with other words, dependency context menu, review Monday',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'dependencies explicitly declared' in check['evidence']


def test_business_impact_any_dependency_word_passes_manager_rule_v123():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='Dependency on infrastructure',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'dependency wording is documented' in check['evidence']


def test_business_impact_any_dependencies_word_with_other_text_passes_manager_rule_v123():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='Dependencies to be discussed with Infrastructure squad 24Jun',
        dependencies='',
    )
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Dependencies to be discussed' in check['evidence']


def test_dedicated_dependencies_field_any_dependency_word_passes_without_link_v123():
    engine = ComplianceEngine({'dependencies': 'customfield_deps'}, ['NMGOS'])
    issue = base_issue('', 'Dependency on infrastructure')
    result = engine.evaluate_issue(issue)
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'dependency wording is documented' in check['evidence']


def test_business_impact_dependency_only_applies_to_top_level_v128():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(
        business_impact='Dependency on infrastructure',
        dependencies='',
    )
    top_result = engine.evaluate_issue(issue, 'top_level')
    top_check = dependency_check(top_result)
    assert top_check['passed'] is True

    epic_result = engine.evaluate_issue(issue, 'epic')
    epic_check = dependency_check(epic_result)
    assert epic_check['passed'] is False


def test_named_business_impact_metadata_candidate_still_passes_on_top_level_v128():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_wrong_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['_business_impact_candidates'] = [
        {
            'field_id': 'customfield_dynamic_bi',
            'name': 'Business Impact',
            'value': 'Dependency on infrastructure',
        }
    ]
    result = engine.evaluate_issue(issue, 'top_level')
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Top-level Business Impact field' in check['evidence']


def adf_paragraph(text: str) -> dict:
    return {'type': 'paragraph', 'content': [{'type': 'text', 'text': text}]}


def test_business_impact_description_section_dependency_passes_top_level_v129():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['description'] = '\n'.join([
        'Summary',
        'Integrate Atoll data.',
        'Business Impact',
        "Dependency on John's support",
        'Expected Business Value',
        'None',
    ])
    result = engine.evaluate_issue(issue, 'top_level')
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Business Impact section in Description' in check['evidence']
    assert "Dependency on John's support" in check['evidence']


def test_business_impact_description_section_no_dependencies_passes_top_level_v129():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['description'] = '\n'.join([
        'Acceptance Criteria',
        'Done criteria populated.',
        'Business Impact',
        'No dependencies but support from CMDB team',
        'Key Stakeholder/s',
        'CMDB team',
    ])
    result = engine.evaluate_issue(issue, 'top_level')
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'No dependencies but support from CMDB team' in check['evidence']
    assert 'Key Stakeholder' not in check['evidence']


def test_business_impact_adf_description_section_dependency_passes_top_level_v129():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['description'] = {
        'type': 'doc',
        'version': 1,
        'content': [
            adf_paragraph('Summary'),
            adf_paragraph('This issue involves updating active antennas.'),
            adf_paragraph('Business Impact'),
            adf_paragraph("No dependencies, John's support"),
            adf_paragraph('Expected Business Value'),
            adf_paragraph('None'),
        ],
    }
    result = engine.evaluate_issue(issue, 'top_level')
    check = dependency_check(result)
    assert check['passed'] is True
    assert "No dependencies, John's support" in check['evidence']


def test_business_impact_description_section_ignored_for_child_level_v129():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['description'] = 'Business Impact\nDependency on infrastructure'
    result = engine.evaluate_issue(issue, 'epic')
    check = dependency_check(result)
    assert check['passed'] is False


def test_business_impact_flattened_description_same_line_passes_top_level_v130():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['description'] = 'Summary Something Business Impact No dependencies but support from CMDB team Expected Business Value None'
    result = engine.evaluate_issue(issue, 'top_level')
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'No dependencies but support from CMDB team' in check['evidence']
    assert 'Expected Business Value' not in check['evidence']


def test_business_impact_flattened_adf_same_paragraph_passes_top_level_v130():
    engine = ComplianceEngine({
        'dependencies': 'customfield_deps',
        'business_impact': 'customfield_bi',
    }, ['NMGOS'])
    issue = base_issue(business_impact='', dependencies='')
    issue['fields']['description'] = {
        'type': 'doc',
        'version': 1,
        'content': [
            {
                'type': 'paragraph',
                'content': [
                    {'type': 'text', 'text': 'Business Impact'},
                    {'type': 'text', 'text': 'Dependency on infrastructure but managed 24Jun: tickets to be ready for review Monday'},
                ],
            },
            adf_paragraph('Expected Business Value'),
            adf_paragraph('None'),
        ],
    }
    result = engine.evaluate_issue(issue, 'top_level')
    check = dependency_check(result)
    assert check['passed'] is True
    assert 'Dependency on infrastructure' in check['evidence']
    assert 'Expected Business Value' not in check['evidence']
