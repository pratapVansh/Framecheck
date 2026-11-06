from inspector.benchmark import bootstrap_comparison
from inspector.models import DEFECTS


def test_group_resampling_keeps_variants_and_paired_identical_methods_have_zero_delta():
    rows=[dict(group_id=g,labels=dict.fromkeys(DEFECTS,False)) for g in ['one','one','two']]
    rows[2]['labels']['blur']=True
    predictions=[dict(status='Pass',checks=dict.fromkeys(DEFECTS,'Pass')) for _ in rows]
    predictions[2]['status']='Fail';predictions[2]['checks']['blur']='Fail'
    r=bootstrap_comparison(rows,{'baseline':predictions,'same':predictions},iterations=100)
    assert r['groups']==2
    assert r==bootstrap_comparison(rows,{'baseline':predictions,'same':predictions},iterations=100)
    for interval in r['paired_deltas_from_baseline']['same'].values():
        assert interval['lower']==interval['upper']==0


def test_missing_acceptable_support_keeps_false_reject_interval_undefined():
    row=dict(group_id='one',labels=dict.fromkeys(DEFECTS,None))
    pred=dict(status='Manual Review',checks=dict.fromkeys(DEFECTS,'Manual Review'))
    r=bootstrap_comparison([row],{'baseline':[pred]},iterations=10)
    assert r['methods']['baseline']['false_reject_rate']==dict(lower=None,upper=None,defined_replicates=0)
