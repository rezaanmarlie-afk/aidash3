from pathlib import Path

from app.jira_client import JiraClient
from app.settings import Settings


def client() -> JiraClient:
    settings = Settings(mock_mode=True)
    return JiraClient(settings, Path(__file__).parents[1] / 'app' / 'mock_data.json')


def test_scrum_master_user_picker_clause_wins_over_duplicate_dropdown_name():
    jira = client()
    jira._fields_cache = [
        {
            'id': 'customfield_dropdown',
            'name': 'Scrum Master',
            'clauseNames': ['Scrum Master[Dropdown]', 'cf[10001]'],
        },
        {
            'id': 'customfield_userpicker',
            'name': 'Scrum Master',
            'clauseNames': ['Scrum Master[User Picker (single user)]', 'cf[10002]'],
        },
    ]

    resolved = jira.resolve_field([
        'Scrum Master[User Picker (single user)]',
        'Scrum Master',
    ])

    assert resolved['id'] == 'customfield_userpicker'


def test_jql_matches_working_scope_without_forced_issue_type():
    jira = client()
    jql = jira.build_jql(
        'NMGOS',
        'PI Priority (ASOC)',
        'PI26',
        'Critical',
        'Scrum Master[User Picker (single user)]',
        '70121:c296bec5-b136-48b7-9345-a1e16f9f38dc',
    )

    assert 'project in ("NMGOS")' in jql
    assert '"PI Priority (ASOC)" = "PI26"' in jql
    assert 'priority = "Critical"' in jql
    assert '"Scrum Master[User Picker (single user)]" = "70121:c296bec5-b136-48b7-9345-a1e16f9f38dc"' in jql
    assert 'issuetype' not in jql.lower()
    assert 'Scrum Master[Dropdown]' not in jql
