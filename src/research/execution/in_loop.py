from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.execution.paper import PaperPosition
from .account import reference_account_profile
from .simulator import ExecutionConfig, FuturesExecutionSimulator, SetupIntent

BAR_SECONDS={"1m":60,"5m":300,"15m":900,"1h":3600}


class InLoopResearchExecutor:
    """PaperExecutor-compatible adapter; sole execution authority in replay."""
    def __init__(self, run_id, account_profile, execution_config: ExecutionConfig, replay_mode, pending_lifetimes):
        self.simulator=FuturesExecutionSimulator(run_id,reference_account_profile(account_profile),execution_config,replay_mode)
        self.positions={}; self.closed=[]; self.trade_map={}; self.last_prices={}; self.pending_lifetimes=dict(pending_lifetimes);self._changed=[]

    def register_setup(self,setup,*,risk_dollars=None,guard_reason=None):
        evaluation=setup.metadata.get("evaluation_guard",{}) or {}
        final=float(risk_dollars if risk_dollars is not None else evaluation.get("risk_dollars") or 0)
        requested=float(evaluation.get("risk_cap_dollars") or final)
        context=setup.metadata.get("a_plus_context",{}) or {}; recovery=setup.metadata.get("recovery_control_70",{}) or {}
        intent=SetupIntent(self.simulator.run_id,setup.setup_id,setup.symbol,setup.metadata.get("signal_contract",setup.symbol),
          setup.metadata.get("strategy","ICT_CONFLUENCE"),setup.timeframe,setup.direction,_aware(setup.created_at),
          setup.entry_price,setup.stop_price,setup.target_price,setup.risk_reward,requested,final,
          context.get("quality_grade") or "A",context.get("quality_score"),
          (setup.metadata.get("session_consistency",{}) or {}).get("session_tier"),recovery.get("mode","NORMAL"))
        sim_trade=self.simulator.submit_final(intent)
        if isinstance(sim_trade,dict):
            raise ValueError(sim_trade["entry_reason"])
        position=PaperPosition(setup=setup,risk_dollars=sim_trade.actual_risk,guard_reason=guard_reason)
        position.research_execution=True; position.cancellation_details=None
        self.positions[setup.setup_id]=position; self.trade_map[setup.setup_id]=sim_trade
        price=self.last_prices.get(setup.symbol)
        if price is not None:
            reason=self._preentry_reason(sim_trade,price,setup.created_at,registration=True)
            if reason: self._cancel(position,sim_trade,_aware(setup.created_at),price,*reason)
        return position

    def _preentry_reason(self,trade,price,timestamp,registration=False):
        direction=trade.intent.direction
        if (price<=trade.stop if direction=="bullish" else price>=trade.stop):
            return ("STOP_BREACHED_BEFORE_ENTRY","INVALIDATED_BEFORE_ENTRY")
        distance=abs(trade.target-trade.planned_entry)
        progress=((price-trade.planned_entry) if direction=="bullish" else (trade.planned_entry-price))/distance if distance else 0
        if progress>=.75: return (("STALE_AT_REGISTRATION" if registration else "TARGET_PROGRESS_75"),"STALE_MOVE_BEFORE_ENTRY")
        seconds=BAR_SECONDS.get(trade.intent.timeframe,60)*self.pending_lifetimes.get(trade.intent.timeframe,4)
        if _aware(timestamp)>trade.intent.signal_time+timedelta(seconds=seconds): return ("PENDING_EXPIRED","EXPIRED_BEFORE_ENTRY")
        return None

    def _cancel(self,position,trade,timestamp,price,reason,result):
        bars=int(max(0,(timestamp-trade.intent.signal_time).total_seconds())/BAR_SECONDS.get(trade.intent.timeframe,60))
        distance=abs(trade.target-trade.planned_entry); progress=0 if not distance else max(0,((price-trade.planned_entry) if trade.intent.direction=="bullish" else (trade.planned_entry-price))/distance)
        details={"cancellation_reason":reason,"timeframe":trade.intent.timeframe,"bars_elapsed":bars,
          "configured_max_bars":self.pending_lifetimes.get(trade.intent.timeframe),"setup_creation_time":trade.intent.signal_time.isoformat(),
          "expiration_time":timestamp.isoformat(),"structure_valid_at_expiration":"UNKNOWN","current_price":price,
          "entry":trade.planned_entry,"stop":trade.stop,"target":trade.target,"distance_to_entry":abs(price-trade.planned_entry),
          "progress_to_target":progress,"invalidation_reason":None if reason=="PENDING_EXPIRED" else reason}
        self.simulator.cancel(trade,timestamp,reason,details); position.status="INVALIDATED"; position.closed_at=timestamp
        position.exit_price=price; position.result=result; position.result_dollars=0.0; position.cancellation_details=details
        self.positions.pop(position.setup.setup_id,None); self.closed.append(position)
        self._changed.append(position)

    def on_price(self,symbol,price,timestamp=None,bid=None,ask=None):
        timestamp=_aware(timestamp or datetime.now(timezone.utc)); self.last_prices[symbol]=price; before=self._states();self._changed=[]
        if bid is None or ask is None:
            quote=getattr(self,"_quotes",{}).get(symbol,(None,None));bid=bid if bid is not None else quote[0];ask=ask if ask is not None else quote[1]
        for setup_id,position in list(self.positions.items()):
            if position.setup.symbol!=symbol or position.status!="PENDING": continue
            trade=self.trade_map[setup_id]; reason=self._preentry_reason(trade,price,timestamp)
            if reason:self._cancel(position,trade,timestamp,price,*reason)
        self.simulator.on_tick(timestamp,price,bid,ask,symbol); changed=self._sync(before)
        self.simulator.mark_to_market(timestamp,{symbol:price})
        return self._dedupe(self._changed+changed)

    def set_quote(self,symbol,bid,ask):
        if not hasattr(self,"_quotes"):self._quotes={}
        self._quotes[symbol]=(bid,ask)

    def on_candle(self,symbol,timestamp,open_price,high,low,close):
        timestamp=_aware(timestamp); self.last_prices[symbol]=close; before=self._states();self._changed=[]
        # Pre-entry invalidations use proven extremes before any possible fill.
        for setup_id,position in list(self.positions.items()):
            if position.setup.symbol!=symbol or position.status!="PENDING":continue
            trade=self.trade_map[setup_id]
            expiry=self._preentry_reason(trade,close,timestamp)
            reason=expiry if expiry and expiry[0]=="PENDING_EXPIRED" else None
            entry_touched=low<=trade.planned_entry if trade.intent.direction=="bullish" else high>=trade.planned_entry
            if not entry_touched and reason is None:
                adverse=low if trade.intent.direction=="bullish" else high
                favorable=high if trade.intent.direction=="bullish" else low
                reason=self._preentry_reason(trade,adverse,timestamp) or self._preentry_reason(trade,favorable,timestamp)
            if reason:self._cancel(position,trade,timestamp,close,*reason)
        pending_before={t.intent.setup_id for t in self.simulator.pending}
        self.simulator.on_candle(timestamp,open_price,high,low,close,symbol)
        changed=self._sync(before)
        adverse={symbol:min(low,close) if any(t.intent.symbol==symbol and t.intent.direction=="bullish" for t in self.simulator.open) else max(high,close)}
        self.simulator.mark_to_market(timestamp,{symbol:close},adverse)
        return self._dedupe(self._changed+changed)

    @staticmethod
    def _dedupe(items):
        output=[];seen=set()
        for item in items:
            if item.setup.setup_id not in seen:output.append(item);seen.add(item.setup.setup_id)
        return output

    def _states(self):return {key:(value.status,value.result) for key,value in self.positions.items()}
    def _sync(self,before):
        changed=[]
        records={r["setup_id"]:r for r in self.simulator.records}
        for setup_id,position in list(self.positions.items()):
            trade=self.trade_map[setup_id]
            if trade.status=="OPEN":position.status="OPEN";position.opened_at=trade.fill_time
            elif trade.status=="CLOSED":
                record=records[setup_id];position.status="CLOSED";position.opened_at=trade.fill_time;position.closed_at=trade.exit_time
                position.exit_price=trade.exit_fill;position.result="WIN" if trade.net_pnl>0 else "LOSS";position.result_r=trade.realized_r;position.result_dollars=trade.net_pnl
                self.positions.pop(setup_id,None);self.closed.append(position)
            if before.get(setup_id)!=(position.status,position.result):changed.append(position)
        return changed

    @property
    def pending_count(self):return sum(p.status=="PENDING" for p in self.positions.values())
    @property
    def open_count(self):return sum(p.status=="OPEN" for p in self.positions.values())
    @property
    def total_r(self):return sum(float(p.result_r or 0) for p in self.closed)


def _aware(value):
    if isinstance(value,str):value=datetime.fromisoformat(value.replace("Z","+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
