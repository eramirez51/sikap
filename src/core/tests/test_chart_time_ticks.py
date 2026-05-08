from core.chart.time_ticks import STEPS_SECS, nice_step


def test_nice_step_picks_next_higher():
    assert nice_step(30) == 60
    assert nice_step(120) == 300
    assert nice_step(800) == 900
    assert nice_step(3000) == 3600
    assert nice_step(20000) == 21600
    assert nice_step(80000) == 86400
    assert nice_step(500000) == 604800


def test_nice_step_floors_below_min():
    assert nice_step(0) == 60
    assert nice_step(-5) == 60


def test_nice_step_caps_above_max():
    biggest = STEPS_SECS[-1]
    assert nice_step(biggest * 10) == biggest
