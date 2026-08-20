from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import math

from src.risk.geometry import normalize_trade_prices
from .account import AccountState, PropConfig, session_name, trading_day
from .contracts import execution_contract, micro_spec


@dataclass(frozen=True)
class ExecutionConfig:
    fill_model: str = "SLIPPAGE_MODEL"
    slippage_ticks: int = 1
    round_turn_commission: float = 2.50
    round_turn_fees: float = 0.50
    ambiguity_policy: str = "STOP_FIRST"

    def __post_init__(self):
        if self.fill_model not in {"IDEAL_TOUCH","BID_ASK","SLIPPAGE_MODEL"}: raise ValueError("Unknown fill model")
        if self.ambiguity_policy not in {"STOP_FIRST","TARGET_FIRST","AMBIGUOUS_SKIP"}: raise ValueError("Unknown ambiguity policy")


@dataclass(frozen=True)
class SetupIntent:
    run_id: str; setup_id: str; symbol: str; signal_contract: str; strategy_type: str
    timeframe: str; direction: str; signal_time: datetime; planned_entry: float; stop: float; target: float
    planned_rr: float; requested_risk: float; allowed_risk: float; setup_grade: str = "A"
    quality_score: float | None = None; session: str | None = None; recovery_state: str = "NORMAL"


@dataclass
class SimTrade:
    intent: SetupIntent; execution_contract: str; stop: float; target: float; planned_entry: float
    per_contract_risk: float; quantity: int; actual_risk: float; unused_risk: float
    allowed_risk: float; recovery_state: str; status: str="PENDING"; pending_time: datetime|None=None
    actual_fill: float|None=None; fill_time: datetime|None=None; exit_fill: float|None=None; exit_time: datetime|None=None
    actual_entry_rr: float|None=None; realized_r: float|None=None; gross_pnl: float|None=None
    commission: float=0; fees: float=0; slippage_cost: float=0; gap_slippage: float=0; net_pnl: float|None=None
    adverse_slippage_cost: float=0; price_improvement: float=0
    mfe_points: float=0; mae_points: float=0; entry_reason: str=""; exit_reason: str=""
    ambiguity_flags: list=field(default_factory=list)
    excursion_quality: str=""


class FuturesExecutionSimulator:
    def __init__(self, run_id, account_profile=None, execution=None, replay_mode="CANDLE_APPROXIMATE"):
        self.run_id=run_id; self.config=execution or ExecutionConfig(); self.replay_mode=replay_mode
        self.account=AccountState(PropConfig.from_snapshot(account_profile or {})); self.pending=[]; self.open=[]; self.records=[]; self.blocks=[]
        self.risk_audits=[]

    def submit_final(self, intent: SetupIntent):
        """Accept Operation 7.0 FINAL allowed risk without applying multipliers again."""
        spec=micro_spec(intent.symbol); entry,stop,target=normalize_trade_prices(intent.symbol,intent.direction,intent.planned_entry,intent.stop,intent.target)
        allowed_risk=min(intent.allowed_risk,intent.requested_risk)
        per_contract=abs(entry-stop)*spec.point_value
        quantity=min(self.account.config.max_micros,math.floor(allowed_risk/per_contract)) if per_contract else 0
        audit={"setup_id":intent.setup_id,"base_risk":intent.requested_risk,"evaluation_available_risk":None,
          "session_multiplier":None,"grade_multiplier":None,"strategy_multiplier":None,"entry_location_multiplier":None,
          "lifetime_multiplier":None,"recovery_multiplier":None,"pacing_multiplier":None,
          "final_allowed_risk":allowed_risk,"per_contract_risk":per_contract,"quantity":quantity,
          "actual_risk":quantity*per_contract,"unused_risk":allowed_risk-quantity*per_contract,
          "source":"OPERATION_7_FINAL_ALLOWED_RISK_NO_REAPPLICATION"}
        self.risk_audits.append(audit)
        if quantity<1:
            record=self._rejected(intent,entry,stop,target,per_contract,allowed_risk,"Final allowed risk cannot fund one micro","INSUFFICIENT_RISK")
            record["risk_audit"]=audit; self.records.append(record); return record
        trade=SimTrade(intent,execution_contract(intent.symbol,intent.signal_contract),stop,target,entry,per_contract,quantity,
          quantity*per_contract,allowed_risk-quantity*per_contract,allowed_risk,intent.recovery_state,pending_time=intent.signal_time)
        self.pending.append(trade); return trade

    def submit(self, intent: SetupIntent):
        spec=micro_spec(intent.symbol); entry,stop,target=normalize_trade_prices(intent.symbol,intent.direction,intent.planned_entry,intent.stop,intent.target)
        allowed,status,reason,recovery_cap,recovery=self.account.can_open(intent.symbol,intent.setup_grade,intent.signal_time)
        if len(self.pending)+len(self.open) >= self.account.config.max_concurrent_positions:
            allowed,status,reason=False,"POSITION_LOCK","Maximum concurrent positions including pending orders reached"
        cooldown,cooldown_reason=self.account.cooldown(intent.symbol,intent.signal_time)
        account_headroom=max(0,self.account.balance-self.account.mll_floor(trading_day(intent.signal_time))-self.account.committed_risk)
        allowed_risk=min(intent.allowed_risk,intent.requested_risk,account_headroom)*recovery_cap
        per_contract=abs(entry-stop)*spec.point_value
        quantity=min(self.account.config.max_micros,math.floor(allowed_risk/per_contract)) if per_contract>0 else 0
        if not cooldown: allowed,status,reason=False,"COOLDOWN",cooldown_reason
        if not allowed or quantity<1:
            reason=reason if not allowed else "Allowed risk cannot fund one micro contract"
            record=self._rejected(intent,entry,stop,target,per_contract,allowed_risk,reason,status if not allowed else "INSUFFICIENT_RISK")
            self.records.append(record); self.blocks.append({"timestamp":intent.signal_time.isoformat(),"setup_id":intent.setup_id,"status":record["status"],"reason":reason,"snapshot":self.snapshot()})
            return record
        actual=quantity*per_contract
        trade=SimTrade(intent,execution_contract(intent.symbol,intent.signal_contract),stop,target,entry,per_contract,quantity,actual,allowed_risk-actual,allowed_risk,recovery,pending_time=intent.signal_time)
        self.pending.append(trade); return trade

    def on_tick(self,timestamp,last,bid=None,ask=None,symbol=None):
        timestamp=_aware(timestamp)
        for trade in list(self.pending):
            if symbol and trade.intent.symbol!=symbol: continue
            side=ask if trade.intent.direction=="bullish" else bid
            if self.config.fill_model=="BID_ASK":
                if side is None: continue
                touched=side<=trade.planned_entry if trade.intent.direction=="bullish" else side>=trade.planned_entry
                base=side
            else:
                touched=last<=trade.planned_entry if trade.intent.direction=="bullish" else last>=trade.planned_entry
                base=trade.planned_entry
            if touched: self._fill(trade,timestamp,base,"Tick limit touch")
        for trade in list(self.open):
            if symbol and trade.intent.symbol!=symbol: continue
            self._excursion(trade,last,last)
            stop=last<=trade.stop if trade.intent.direction=="bullish" else last>=trade.stop
            target=last>=trade.target if trade.intent.direction=="bullish" else last<=trade.target
            if stop or target: self._exit(trade,timestamp,trade.stop if stop else trade.target,"STOP" if stop else "TARGET",market=last,bid=bid,ask=ask)

    def on_candle(self,timestamp,open_price,high,low,close,symbol):
        timestamp=_aware(timestamp)
        newly_filled=set()
        for trade in list(self.pending):
            if trade.intent.symbol!=symbol: continue
            touched=low<=trade.planned_entry if trade.intent.direction=="bullish" else high>=trade.planned_entry
            if touched:
                gap=(open_price<trade.planned_entry if trade.intent.direction=="bullish" else open_price>trade.planned_entry)
                base=open_price if gap else trade.planned_entry
                self._fill(trade,timestamp,base,"Candle limit touch" + (" with favorable gap" if gap else ""))
                newly_filled.add(trade.intent.setup_id);trade.excursion_quality="ENTRY_CANDLE_AMBIGUOUS"
        for trade in list(self.open):
            if trade.intent.symbol!=symbol: continue
            entry_candle=trade.intent.setup_id in newly_filled
            if not entry_candle:self._excursion(trade,high,low)
            stop=low<=trade.stop if trade.intent.direction=="bullish" else high>=trade.stop
            target=high>=trade.target if trade.intent.direction=="bullish" else low<=trade.target
            if entry_candle and (stop or target):
                flag="ENTRY_STOP_TARGET_SAME_CANDLE" if stop and target else "ENTRY_AND_STOP_SAME_CANDLE" if stop else "ENTRY_AND_TARGET_SAME_CANDLE"
                trade.ambiguity_flags.append(flag)
                if stop and not target and self.config.ambiguity_policy!="STOP_FIRST": stop=False
                if target and not stop and self.config.ambiguity_policy!="TARGET_FIRST": target=False
            if stop and target:
                trade.ambiguity_flags.append("STOP_AND_TARGET_SAME_CANDLE")
                if self.config.ambiguity_policy=="AMBIGUOUS_SKIP": continue
                stop=self.config.ambiguity_policy=="STOP_FIRST"; target=not stop
            if stop or target:
                level=trade.stop if stop else trade.target; reason="STOP" if stop else "TARGET"
                gap=(trade.intent.direction=="bullish" and stop and open_price<trade.stop) or (trade.intent.direction=="bearish" and stop and open_price>trade.stop)
                market=open_price if gap else level
                self._exit(trade,timestamp,level,reason,market=market)

    def _fill(self,trade,timestamp,base,reason):
        spec=micro_spec(trade.intent.symbol); slip=self.config.slippage_ticks*spec.tick_size if self.config.fill_model=="SLIPPAGE_MODEL" else 0
        fill=base+slip if trade.intent.direction=="bullish" else base-slip
        fill=round(fill/spec.tick_size)*spec.tick_size
        trade.actual_fill=fill; trade.fill_time=timestamp; trade.status="OPEN"; trade.entry_reason=reason
        risk_points=abs(fill-trade.stop); reward_points=abs(trade.target-fill)
        trade.actual_risk=risk_points*spec.point_value*trade.quantity
        trade.unused_risk=trade.allowed_risk-trade.actual_risk
        trade.actual_entry_rr=reward_points/risk_points if risk_points else None
        delta=((fill-trade.planned_entry) if trade.intent.direction=="bullish" else (trade.planned_entry-fill))
        attribution=delta*spec.point_value*trade.quantity
        if attribution>=0: trade.adverse_slippage_cost+=attribution
        else: trade.price_improvement+=-attribution
        trade.slippage_cost=trade.adverse_slippage_cost
        self.pending.remove(trade); self.open.append(trade); self.account.fill(timestamp,risk_points*spec.point_value*trade.quantity)

    def _excursion(self,trade,high,low):
        if trade.actual_fill is None:return
        if trade.intent.direction=="bullish": favorable=max(0,high-trade.actual_fill); adverse=max(0,trade.actual_fill-low)
        else: favorable=max(0,trade.actual_fill-low); adverse=max(0,high-trade.actual_fill)
        trade.mfe_points=max(trade.mfe_points,favorable); trade.mae_points=max(trade.mae_points,adverse)

    def _exit(self,trade,timestamp,level,reason,market=None,bid=None,ask=None):
        spec=micro_spec(trade.intent.symbol); market=level if market is None else market
        if self.config.fill_model=="BID_ASK":
            side=bid if trade.intent.direction=="bullish" else ask
            market=side if side is not None else market
        slip=self.config.slippage_ticks*spec.tick_size if self.config.fill_model=="SLIPPAGE_MODEL" else 0
        fill=market-slip if trade.intent.direction=="bullish" else market+slip
        fill=round(fill/spec.tick_size)*spec.tick_size
        trade.gap_slippage+=abs(fill-level)*spec.point_value*trade.quantity if reason=="STOP" else 0
        delta=((level-fill) if trade.intent.direction=="bullish" else (fill-level))
        attribution=delta*spec.point_value*trade.quantity
        if attribution>=0: trade.adverse_slippage_cost+=attribution
        else: trade.price_improvement+=-attribution
        trade.slippage_cost=trade.adverse_slippage_cost
        points=(fill-trade.actual_fill) if trade.intent.direction=="bullish" else (trade.actual_fill-fill)
        trade.gross_pnl=points*spec.point_value*trade.quantity
        trade.commission=self.config.round_turn_commission*trade.quantity; trade.fees=self.config.round_turn_fees*trade.quantity
        trade.net_pnl=trade.gross_pnl-trade.commission-trade.fees
        trade.realized_r=trade.net_pnl/trade.actual_risk if trade.actual_risk else None
        trade.exit_fill=fill; trade.exit_time=timestamp; trade.exit_reason=reason; trade.status="CLOSED"
        self.open.remove(trade); self.account.close(trade.intent.symbol,timestamp,trade.net_pnl,trade.actual_risk,"WIN" if trade.net_pnl>0 else "LOSS")
        self.records.append(self._record(trade))

    def _record(self,t):
        spec=micro_spec(t.intent.symbol); risk_points=abs(t.actual_fill-t.stop) if t.actual_fill is not None else abs(t.planned_entry-t.stop)
        return {"trade_id":hashlib.sha256(f"{self.run_id}|{t.intent.setup_id}".encode()).hexdigest()[:24],"run_id":self.run_id,
          "setup_id":t.intent.setup_id,"symbol":t.intent.symbol,"execution_contract":t.execution_contract,
          "strategy_type":t.intent.strategy_type,"timeframe":t.intent.timeframe,"session":t.intent.session or session_name(t.intent.signal_time),
          "direction":t.intent.direction,"setup_grade":t.intent.setup_grade,"quality_score":t.intent.quality_score,"recovery_state":t.recovery_state,
          "signal_time":t.intent.signal_time.isoformat(),"pending_time":t.pending_time.isoformat() if t.pending_time else None,
          "fill_time":t.fill_time.isoformat() if t.fill_time else None,"exit_time":t.exit_time.isoformat() if t.exit_time else None,
          "planned_entry":t.planned_entry,"actual_fill":t.actual_fill,"stop_price":t.stop,"target_price":t.target,"exit_fill":t.exit_fill,
          "planned_rr":t.intent.planned_rr,"actual_entry_rr":t.actual_entry_rr,"realized_r":t.realized_r,
          "requested_risk":t.intent.requested_risk,"allowed_risk":t.allowed_risk,"per_contract_risk":t.per_contract_risk,
          "quantity":t.quantity,"actual_risk":t.actual_risk,"unused_risk":t.unused_risk,"gross_pnl":t.gross_pnl,
          "commission":t.commission,"fees":t.fees,"slippage_cost":t.slippage_cost,"gap_slippage":t.gap_slippage,"net_pnl":t.net_pnl,
          "adverse_slippage_cost":t.adverse_slippage_cost,"price_improvement":t.price_improvement,
          "mfe_points":t.mfe_points,"mfe_dollars":t.mfe_points*spec.point_value*t.quantity,"mfe_r":t.mfe_points/risk_points if risk_points else 0,
          "mae_points":t.mae_points,"mae_dollars":t.mae_points*spec.point_value*t.quantity,"mae_r":t.mae_points/risk_points if risk_points else 0,
          "excursion_quality":t.excursion_quality or ("TICK_EXACT" if self.replay_mode=="TICK_EXACT" else "CANDLE_APPROXIMATE"),
          "entry_reason":t.entry_reason,"exit_reason":t.exit_reason,"fill_model":self.config.fill_model,"replay_mode":self.replay_mode,
          "ambiguity_policy":self.config.ambiguity_policy,"ambiguity_flags":t.ambiguity_flags,"status":t.status}

    def cancel(self, trade, timestamp, reason, details=None):
        if trade not in self.pending: return None
        self.pending.remove(trade); trade.status="CANCELLED"; trade.exit_time=_aware(timestamp); trade.exit_reason=reason
        record=self._record(trade); record.update(details or {}); self.records.append(record); return record

    def mark_to_market(self, timestamp, prices: dict, adverse_prices: dict | None = None):
        unrealized=0.0; adverse_equity=0.0
        for trade in self.open:
            if trade.intent.symbol not in prices or trade.actual_fill is None: continue
            spec=micro_spec(trade.intent.symbol); price=prices[trade.intent.symbol]
            points=(price-trade.actual_fill) if trade.intent.direction=="bullish" else (trade.actual_fill-price)
            unrealized+=points*spec.point_value*trade.quantity
            adverse=(adverse_prices or {}).get(trade.intent.symbol)
            if adverse is not None:
                adverse_points=(adverse-trade.actual_fill) if trade.intent.direction=="bullish" else (trade.actual_fill-adverse)
                adverse_equity+=min(0,adverse_points*spec.point_value*trade.quantity)
        self.account.record(_aware(timestamp),"MARK_TO_MARKET",unrealized,abs(adverse_equity))

    def _rejected(self,i,e,s,t,p,a,reason,status):
        return {"trade_id":hashlib.sha256(f"{self.run_id}|{i.setup_id}".encode()).hexdigest()[:24],"run_id":self.run_id,"setup_id":i.setup_id,
          "symbol":i.symbol,"execution_contract":execution_contract(i.symbol,i.signal_contract),"strategy_type":i.strategy_type,"timeframe":i.timeframe,
          "session":i.session or session_name(i.signal_time),"direction":i.direction,"setup_grade":i.setup_grade,"quality_score":i.quality_score,
          "recovery_state":"BLOCKED","signal_time":i.signal_time.isoformat(),"planned_entry":e,"stop_price":s,"target_price":t,
          "planned_rr":i.planned_rr,"requested_risk":i.requested_risk,"allowed_risk":a,"per_contract_risk":p,"quantity":0,"actual_risk":0,
          "unused_risk":a,"entry_reason":reason,"exit_reason":None,"fill_model":self.config.fill_model,"replay_mode":self.replay_mode,
          "ambiguity_policy":self.config.ambiguity_policy,"ambiguity_flags":[],"status":status}

    def snapshot(self):
        return {"balance":self.account.balance,"peak_equity":self.account.peak_equity,"committed_risk":self.account.committed_risk,
          "open_positions":self.account.open_positions,"consecutive_losses":self.account.consecutive_losses}


def _aware(value):
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
