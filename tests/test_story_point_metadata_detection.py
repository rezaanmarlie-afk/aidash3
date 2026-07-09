from app.jira_client import JiraClient


def test_likely_story_point_field_detects_team_managed_estimate_names():
    assert JiraClient._likely_story_point_field(
        'customfield_12345', 'Story point estimate', {'type': 'number'}, 8
    )
    assert JiraClient._likely_story_point_field(
        'customfield_23456', 'Agile estimate points', {'type': 'number'}, 13
    )


def test_likely_story_point_field_does_not_accept_unrelated_numeric_fields():
    assert not JiraClient._likely_story_point_field(
        'customfield_34567', 'Business Value', {'type': 'number'}, 100
    )
    assert not JiraClient._likely_story_point_field(
        'customfield_45678', 'Budget Estimate', {'type': 'number'}, 5000
    )
