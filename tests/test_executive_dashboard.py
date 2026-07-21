from app.main import _rag, _executive_metrics


def test_rag_thresholds():
    assert _rag(90, 90, 75)['code'] == 'green'
    assert _rag(80, 90, 75)['code'] == 'amber'
    assert _rag(74.9, 90, 75)['code'] == 'red'


def test_executive_metrics_uses_yield_and_scope():
    art={'id':1,'name':'ART','pi_value':'PI26'}
    result={
        'initiative': {'key':'NMGOS-1','summary':'One','status':'Done','fields':{'customfield_10023':'2026-07-10','resolutiondate':'2026-07-11T10:00:00+0200'}},
        'story_points_total':10,'ticket_score':100,'hierarchy_score':90,'compliant':True,
        'latest_signoff': {'decision':'APPROVED','is_current':True}, 'epics':[], 'direct_stories':[], 'additional_descendants':[]
    }
    scan={'results':[result], 'summary':{'initiatives':1,'compliant':1,'story_points_total':10,'ticket_score':100,'hierarchy_score':90}}
    baseline={'created_at':'now','snapshot':{'tickets':[{'key':'NMGOS-1','story_points_total':8}]}}
    metrics=_executive_metrics(art,scan,baseline,2)
    assert metrics['yield_percent'] == 100
    assert metrics['scope_delta'] == 2
    assert metrics['signoff_percent'] == 100
