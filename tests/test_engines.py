from app.backtest import replay
from app.engines import EntryEngine, PositionExitEngine


def signals(kind="BUY", score=80, close=100):
    details={"close":close,"atr":1,"stop_loss":98,"take_profit":104,"relative_volume":1.3}
    return [{"timeframe":t,"signal":kind,"score":score,"regime":"UPTREND","details":details} for t in ("15m","1h","4h")]


def test_entry_wait_watch_buy_now():
    engine=EntryEngine()
    assert engine.rank("BTC/THB",signals(),100).status=="BUY_NOW"
    assert engine.rank("BTC/THB",signals("HOLD",40),100).status=="WAIT"


def test_buy_now_requires_volume_confirmation():
    weak=signals()
    for signal in weak:
        signal["details"]["relative_volume"]=0.8
    assert EntryEngine().rank("BTC/THB",weak,100).status=="WATCH"


def test_exit_lifecycle():
    plan={"entry_price":100,"stop_loss":95,"effective_stop":95,"take_profit_1":105,"take_profit_2":110,"highest_price":100,"trailing_enabled":1,"trailing_activation_percent":2,"trailing_distance_percent":1}
    e=PositionExitEngine()
    assert e.evaluate(100,plan,signals())[0]=="HOLD"
    assert e.evaluate(105,plan,signals())[0]=="TAKE_PROFIT"
    assert e.evaluate(110,plan,signals())[0]=="SELL_NOW"
    assert e.evaluate(94,plan,signals())[0]=="STOP_LOSS"
    assert e.evaluate(100,plan,signals("SELL",-80))[0]=="SELL_NOW"


def test_trailing_stop_never_moves_downward():
    plan = {
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "effective_stop": 105.0, # Trailing stop has already raised to 105
        "take_profit_1": 115.0,
        "take_profit_2": 125.0,
        "highest_price": 110.0,
        "trailing_enabled": 1,
        "trailing_activation_percent": 2.0,
        "trailing_distance_percent": 5.0,
    }
    e = PositionExitEngine()
    action, reason, effective = e.evaluate(107.0, plan, signals())
    # Even if current price dips to 107 from high of 110, effective stop remains at least 105
    assert effective >= 105.0
    assert action == "HOLD"

    # If price drops to 104 <= 105, it triggers STOP_LOSS (trailing stop hit)
    action, reason, effective = e.evaluate(104.0, plan, signals())
    assert action == "STOP_LOSS"
    assert "Trailing stop touched" in reason



def test_backtest_metrics_shape():
    closes=[100+i*.2 for i in range(150)]; result=replay("BTC/THB",[x+1 for x in closes],[x-1 for x in closes],closes)
    for key in ("total_trades","win_rate","profit_factor","expectancy","maximum_drawdown_percent","net_pnl","equity_curve"):
        assert key in result
