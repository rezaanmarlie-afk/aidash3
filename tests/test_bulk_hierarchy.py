from pathlib import Path

from app.jira_client import JiraClient, JiraError
from app.settings import Settings


class FakeJira(JiraClient):
    def __init__(self):
        settings = Settings(mock_mode=False, jira_base_url='https://example.invalid')
        super().__init__(settings, Path(__file__).parents[1] / 'app' / 'mock_data.json')
        self.queries = []

    def search(self, jql, fields=None, max_results=500):
        self.queries.append(jql)
        if ' OR ' in jql:
            raise JiraError('Jira API 400: unsupported field')
        if jql.startswith('parent in'):
            return []
        if '"Parent Link" in' in jql:
            return [{'key': 'NMGOS-EP1', 'fields': {'issuetype': {'name': 'Epic'}}}]
        return []


def test_bulk_relation_unions_modern_and_legacy_fallbacks():
    jira = FakeJira()
    issues = jira._bulk_relation_search(['NMGOS-1'], 'Parent Link', ['summary'], 100)
    assert [issue['key'] for issue in issues] == ['NMGOS-EP1']
    assert any(query.startswith('parent in') for query in jira.queries)
    assert any('"Parent Link" in' in query for query in jira.queries)


class HierarchyJira(FakeJira):
    def search(self, jql, fields=None, max_results=500):
        self.queries.append(jql)
        if 'NMGOS-INIT-1' in jql:
            return [{
                'key': 'NMGOS-EP-1',
                'fields': {
                    'issuetype': {'name': 'Epic'},
                    'parent': {'key': 'NMGOS-INIT-1'},
                    'issuelinks': [],
                },
            }]
        if 'NMGOS-EP-1' in jql:
            return [{
                'key': 'NMGOS-ST-1',
                'fields': {
                    'issuetype': {'name': 'Story'},
                    'parent': {'key': 'NMGOS-EP-1'},
                    'issuelinks': [],
                },
            }]
        return []


def test_bulk_hierarchy_uses_portfolio_queries_not_one_query_per_ticket():
    jira = HierarchyJira()
    initiatives = [{
        'key': 'NMGOS-INIT-1',
        'fields': {'issuetype': {'name': 'Initiative'}, 'issuelinks': []},
    }]
    epics, stories, stats = jira.bulk_hierarchy(
        initiatives,
        fields=['summary', 'issuetype', 'parent', 'issuelinks'],
        parent_link_field_id=None,
        epic_link_field_id=None,
        max_results=100,
    )
    assert [issue['key'] for issue in epics['NMGOS-INIT-1']] == ['NMGOS-EP-1']
    assert [issue['key'] for issue in stories['NMGOS-EP-1']] == ['NMGOS-ST-1']
    assert stats == {'epics_loaded': 1, 'stories_loaded': 1}
    assert len(jira.queries) == 2
