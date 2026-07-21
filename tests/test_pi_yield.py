from app.main import _is_completed_status, _pi_yield_metrics

def test_completed_statuses():
    assert _is_completed_status('Done')
    assert _is_completed_status('Completed')
    assert not _is_completed_status('In Progress')

def test_yield_uses_baseline_committed_keys():
    art={'id':1,'name':'ART','pi_value':'PI26'}
    scan={'results':[{'initiative':{'key':'A','status':'Done','fields':{'duedate':'2026-09-30','resolutiondate':'2026-10-01T10:00:00+00:00'}},'story_points_total':5,'epics':[{'key':'EA','issue_type':'Epic','stories':[{'key':'SA','issue_type':'Story','status':'Done','story_points':5,'fields':{'resolutiondate':'2026-09-30T10:00:00+00:00'}}]}],'direct_stories':[],'additional_descendants':[]},{'initiative':{'key':'B','status':'In Progress','fields':{'duedate':'2026-09-30'}},'story_points_total':8,'epics':[{'key':'EB','issue_type':'Epic','stories':[{'key':'SB','issue_type':'Story','status':'In Progress','story_points':8,'fields':{}}]}],'direct_stories':[],'additional_descendants':[]}]}
    baseline={'created_at':'now','snapshot':{'tickets':[{'key':'A'},{'key':'B'}]}}
    m=_pi_yield_metrics(art,scan,baseline)
    assert m['committed_count']==2 and m['completed_count']==1 and m['yield_percent']==50.0
