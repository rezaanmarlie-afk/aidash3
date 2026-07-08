import json
from pathlib import Path

from app.compliance import ComplianceEngine


MOCK = json.loads((Path(__file__).parents[1] / 'app' / 'mock_data.json').read_text(encoding='utf-8'))
MAPPING = {
    'dor': 'customfield_11003',
    'dod': 'customfield_11004',
    'acceptance_criteria': 'customfield_11005',
    'dependencies': 'customfield_11006',
    'story_points': 'customfield_11007',
}


def test_compliant_initiative_tree():
    engine = ComplianceEngine(MAPPING, ['NMGOS'])
    initiative = MOCK['initiatives'][0]
    epics = MOCK['children']['NMGOS-100']
    stories = {'NMGOS-110': MOCK['children']['NMGOS-110']}
    result = engine.evaluate_tree(initiative, epics, stories)
    assert result['compliant'] is True
    assert result['score'] == 100.0


def test_incomplete_tree_is_blocked():
    engine = ComplianceEngine(MAPPING, ['NMGOS'])
    initiative = MOCK['initiatives'][1]
    epics = MOCK['children']['NMGOS-200']
    stories = {'NMGOS-210': MOCK['children']['NMGOS-210']}
    result = engine.evaluate_tree(initiative, epics, stories)
    assert result['compliant'] is False
    assert result['failure_count'] > 0


def test_dependency_declaration_requires_link():
    engine = ComplianceEngine(MAPPING, ['NMGOS'])
    issue = MOCK['children']['NMGOS-210'][0]
    result = engine.evaluate_issue(issue)
    dependency = next(c for c in result['checks'] if c['key'] == 'dependencies')
    assert dependency['passed'] is False
