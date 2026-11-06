from scripts.calibrate import calibrated_rank


def test_feasibility_precedes_f1_and_missing_acceptable_support_is_infeasible():
    feasible = dict(macro_f1=.4, defect_recall=.5, false_reject_rate=.05, manual_review_rate=.2)
    unsafe = dict(macro_f1=.9, defect_recall=.95, false_reject_rate=.7, manual_review_rate=.1)
    assert calibrated_rank(feasible) > calibrated_rank(unsafe)
    assert calibrated_rank(dict(feasible, false_reject_rate=None))[0] == 0
    assert calibrated_rank(dict(feasible, defect_recall=.6)) > calibrated_rank(feasible)
