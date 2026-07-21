from app.main import _analytics_model


def test_analytics_model_calculates_summary_and_scrum_master_rollup():
    rows = [
        {
            'pi_value': 'PI25', 'committed_count': 4, 'completed_count': 2,
            'yield_percent': 50, 'total_story_points': 100, 'completed_story_points': 60,
            'created_at': '2026-01-01T00:00:00+00:00',
            'snapshot': {'allowed_days': 2, 'scope_delta': 10, 'rows': [
                {'scrum_master': 'Mike', 'completed': True, 'total_sp': 20, 'done_sp': 20, 'resolution_date': '2026-01-01', 'days_from_target': 0},
                {'scrum_master': 'Mike', 'completed': False, 'total_sp': 30, 'done_sp': 10, 'resolution_date': '', 'days_from_target': None},
            ]}
        },
        {
            'pi_value': 'PI26', 'committed_count': 5, 'completed_count': 4,
            'yield_percent': 80, 'total_story_points': 120, 'completed_story_points': 96,
            'created_at': '2026-04-01T00:00:00+00:00',
            'snapshot': {'allowed_days': 3, 'scope_delta': 5, 'readiness_percent': 90, 'rows': []}
        },
    ]
    model = _analytics_model(rows)
    assert model['average_yield'] == 65.0
    assert model['best']['pi'] == 'PI26'
    assert model['worst']['pi'] == 'PI25'
    assert model['points'][1]['sp_completion'] == 80.0
    assert model['scrum_rows'][0]['name'] == 'Mike'
    assert model['scrum_rows'][0]['yield'] == 50.0
