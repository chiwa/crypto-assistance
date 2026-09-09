from app.engines import EntryEngine, PositionExitEngine
from app.strategy import analyze


def replay(pair: str, highs: list[float], lows: list[float], closes: list[float], fee_percent=.25, slippage_percent=.1) -> dict:
    entry_engine, exit_engine = EntryEngine(), PositionExitEngine()
    cash, peak, max_dd, curve, trades, position = 100_000.0, 100_000.0, 0.0, [], [], None
    for i in range(51, len(closes)):
        a = analyze(highs[i-50:i], lows[i-50:i], closes[i-50:i])
        signals = [{"timeframe": x, "signal": a["signal"], "score": a["score"], "regime": a["regime"], "details": a} for x in ("15m","1h","4h")]
        price = closes[i]
        if not position:
            c = entry_engine.rank(pair, signals, price)
            if c.status == "BUY_NOW":
                entry = price * (1 + slippage_percent/100); starting_cash=cash; qty = cash / (entry * (1+fee_percent/100))
                position = {"entry_price":entry,"quantity":qty,"cost_basis":starting_cash,"stop_loss":c.stop_loss,"take_profit_1":c.take_profit_1,"take_profit_2":c.take_profit_2,"highest_price":entry,"effective_stop":c.stop_loss,"trailing_enabled":True,"trailing_activation_percent":2,"trailing_distance_percent":1}
        else:
            action, reason, stop = exit_engine.evaluate(price, position, signals); position["highest_price"] = max(position["highest_price"], price); position["effective_stop"] = stop
            if action in {"STOP_LOSS","SELL_NOW"}:
                exit_price=price*(1-slippage_percent/100); proceeds=position["quantity"]*exit_price*(1-fee_percent/100); pnl=proceeds-position["cost_basis"]; trades.append({"pnl":pnl,"reason":reason}); cash=proceeds; position=None
        equity = cash if not position else position["quantity"]*price
        peak=max(peak,equity); max_dd=max(max_dd,(peak-equity)/peak*100); curve.append(round(equity,2))
    pnls=[t["pnl"] for t in trades]; wins=[p for p in pnls if p>0]; losses=[p for p in pnls if p<=0]
    return {"total_trades":len(pnls),"wins":len(wins),"losses":len(losses),"win_rate":len(wins)/len(pnls)*100 if pnls else 0,"average_win":sum(wins)/len(wins) if wins else 0,"average_loss":sum(losses)/len(losses) if losses else 0,"profit_factor":sum(wins)/abs(sum(losses)) if losses else None,"expectancy":sum(pnls)/len(pnls) if pnls else 0,"maximum_drawdown_percent":max_dd,"net_pnl":cash-100_000,"equity_curve":curve,"trades":trades}
