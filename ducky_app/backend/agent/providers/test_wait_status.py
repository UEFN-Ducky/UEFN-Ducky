from backend.agent.providers.wait_status import clamp_percent, format_wait_status


def test_clamp_percent_fraction_and_percent():
    assert clamp_percent(0.69) == 69.0
    assert clamp_percent(69) == 69.0
    assert clamp_percent(150) == 100.0
    assert clamp_percent(-1) == 0.0
    assert clamp_percent(None) is None


def test_format_wait_status_core_line():
    assert format_wait_status() == "Waiting…"
    assert format_wait_status(percent=69) == "Waiting… 69%"
    assert format_wait_status(percent=0.5, detail="1,024 tokens") == "Waiting… 50% · 1,024 tokens"
    assert format_wait_status(detail="step 1") == "Waiting… step 1"
    assert format_wait_status(percent=49, detail="step 1") == "Waiting… 49% · step 1"
