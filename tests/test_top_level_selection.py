from app.main import select_top_level_issues


def issue(key: str, issue_type: str) -> dict:
    return {'key': key, 'fields': {'issuetype': {'name': issue_type}}}


def test_base_jql_results_are_authoritative_when_type_restriction_is_off():
    matches = [
        issue('NMGOS-1', 'Initiate'),
        issue('NMGOS-2', 'Signature Project'),
    ]

    selected = select_top_level_issues(matches, 'Initiative', False)

    assert [item['key'] for item in selected] == ['NMGOS-1', 'NMGOS-2']


def test_explicit_type_restriction_uses_exact_case_insensitive_match():
    matches = [
        issue('NMGOS-1', 'Initiative'),
        issue('NMGOS-2', 'Initiative Feature'),
        issue('NMGOS-3', 'Epic'),
    ]

    selected = select_top_level_issues(matches, 'initiative', True)

    assert [item['key'] for item in selected] == ['NMGOS-1']
