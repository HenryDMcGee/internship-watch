from job.pruning import WRONG_CYCLE_REASON, decide_prune


def test_decide_prune_archives_closed_with_its_note():
    decision = decide_prune(status="closed", note="No longer listed in the company's feed.")
    assert decision.should_archive is True
    assert decision.reason == "No longer listed in the company's feed."


def test_decide_prune_keeps_open():
    decision = decide_prune(status="open")
    assert decision.should_archive is False


def test_decide_prune_wrong_cycle_overrides_status():
    decision = decide_prune(status="open", cycle_status="reject")
    assert decision.should_archive is True
    assert decision.reason == WRONG_CYCLE_REASON


def test_decide_prune_uses_default_reason_when_no_note():
    decision = decide_prune(status="closed", note=None)
    assert decision.should_archive is True
    assert decision.reason == "No longer open."
