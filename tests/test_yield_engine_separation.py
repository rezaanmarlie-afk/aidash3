from app.main import _closed_date, _initiative_yield_completion


def test_backlog_status_change_is_not_a_close_date():
    issue={"fields":{"status":{"name":"Backlog","statusCategory":{"name":"To Do"}},"resolutiondate":None,"statuscategorychangedate":"2026-07-03T02:07:21.582+0200"}}
    assert _closed_date(issue)[0] is None


def test_done_status_can_use_status_change_for_diagnostics_only():
    issue={"fields":{"status":{"name":"Done","statusCategory":{"name":"Done"}},"resolutiondate":None,"statuscategorychangedate":"2026-07-03T02:07:21.582+0200"}}
    assert _closed_date(issue)[0].isoformat()=="2026-07-03"


def test_yield_never_uses_status_change_without_resolutiondate():
    result={"initiative":{"key":"NMGOS-1","status":"Done","fields":{"customfield_10023":"2026-07-01","resolutiondate":None,"statuscategorychangedate":"2026-07-01T10:00:00+0200"},"_target_end_field_id":"customfield_10023","_target_end_field_name":"Target end"},"epics":[],"direct_stories":[],"additional_descendants":[]}
    outcome=_initiative_yield_completion(result,allowed_days=2)
    assert outcome["completed"] is False
    assert "resolution date" in outcome["reason"].lower()
