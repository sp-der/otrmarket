from __future__ import annotations

from collections import defaultdict


def _core_metrics(trades, equity=()):
    closed=[t for t in trades if t.get("status")=="CLOSED"]
    wins=[t for t in closed if (t.get("net_pnl") or 0)>0]; losses=[t for t in closed if (t.get("net_pnl") or 0)<0]
    gross_win=sum(t["net_pnl"] for t in wins); gross_loss=-sum(t["net_pnl"] for t in losses)
    total=len(closed); net=sum(t.get("net_pnl") or 0 for t in closed); gross=sum(t.get("gross_pnl") or 0 for t in closed)
    rs=[t.get("realized_r") for t in closed if t.get("realized_r") is not None]
    return {"net_pnl":net,"gross_pnl":gross,"win_rate":100*len(wins)/total if total else 0,
      "loss_rate":100*len(losses)/total if total else 0,"profit_factor":gross_win/gross_loss if gross_loss else None,
      "expectancy_dollars":net/total if total else 0,"expectancy_r":sum(rs)/len(rs) if rs else 0,
      "average_r":sum(rs)/len(rs) if rs else 0,"average_winner":gross_win/len(wins) if wins else 0,
      "average_loser":-gross_loss/len(losses) if losses else 0,
      "payoff_ratio":(gross_win/len(wins))/(gross_loss/len(losses)) if wins and losses else None,
      "maximum_drawdown_dollars":max([0]+[p["drawdown_dollars"] for p in equity]),
      "maximum_drawdown_percent":max([0]+[p["drawdown_percent"] for p in equity]),
      "maximum_intraday_drawdown":max([0]+[p["drawdown_dollars"] for p in equity]),
      "total_trades":total,"long_trades":sum(t.get("direction")=="bullish" for t in closed),
      "short_trades":sum(t.get("direction")=="bearish" for t in closed)}


def raw_metrics(trades, equity=()):
    closed=[t for t in trades if t.get("status")=="CLOSED"]
    core=_core_metrics(closed,equity)
    segments={}
    for field in ("symbol","direction","strategy_type","setup_grade","timeframe","session","recovery_state"):
        values=defaultdict(list)
        for trade in closed: values[str(trade.get(field) or "UNKNOWN")].append(trade)
        segments[field]={key:_core_metrics(items,()) for key,items in values.items()}
    return core,segments
