from core.chart.ticks import nice_step, price_ticks


def test_nice_step_picks_1_2_5():
    assert nice_step(0.7) == 1.0
    assert nice_step(1.5) == 2.0
    assert nice_step(3.0) == 5.0
    assert nice_step(7.0) == 10.0
    assert nice_step(15.0) == 20.0
    assert nice_step(150.0) == 200.0


def test_nice_step_handles_zero_or_negative():
    assert nice_step(0.0) == 1.0
    assert nice_step(-5.0) == 1.0


def test_price_ticks_inside_range():
    ticks = price_ticks(100.0, 110.0, 400.0, 5)
    assert len(ticks) > 0
    for t in ticks:
        assert 100.0 <= t.price <= 110.0
        assert 0.0 <= t.y_px <= 400.0


def test_price_ticks_higher_price_lower_y():
    ticks = price_ticks(100.0, 200.0, 1000.0, 10)
    by_y = sorted(ticks, key=lambda t: t.y_px)
    top = by_y[0]
    bot = by_y[-1]
    assert top.price > bot.price
    assert abs(top.price - 200.0) < 20.0
    assert abs(bot.price - 100.0) < 20.0


def test_price_ticks_empty_for_degenerate():
    assert price_ticks(100.0, 100.0, 400.0, 5) == []
    assert price_ticks(100.0, 110.0, 0.0, 5) == []
    assert price_ticks(100.0, 110.0, 400.0, 0) == []
