#region Using declarations
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
#endregion

// OTR Market Operation 7.2
//
// Phase-1 NinjaTrader execution adapter. This file is intentionally SIM-ONLY
// and defaults to ArmSimulationOrders=false. It will post broker snapshots while
// disarmed, but it will not poll or submit OTR commands until the user explicitly
// arms it on a chart. The server has its own independent PAPER/SIM/LIVE interlocks.
//
// NinjaTrader compile note: System.Web.Extensions must be present in NinjaScript
// References because JavaScriptSerializer lives in that framework assembly.

namespace NinjaTrader.NinjaScript.Indicators
{
    public class OTRExecutionBridge : Indicator
    {
        private sealed class CommandEnvelope
        {
            public bool ok { get; set; }
            public bool dispatch_ready { get; set; }
            public string reason { get; set; }
            public string code { get; set; }
            public string mode { get; set; }
            public string account { get; set; }
            public List<ExecutionCommand> commands { get; set; }
        }

        private sealed class ExecutionCommand
        {
            public string command_id { get; set; }
            public string setup_id { get; set; }
            public string mode { get; set; }
            public string account { get; set; }
            public string execution_contract { get; set; }
            public string side { get; set; }
            public int quantity { get; set; }
            public string order_type { get; set; }
            public double entry_price { get; set; }
            public double stop_price { get; set; }
            public double target_price { get; set; }
            public double risk_dollars { get; set; }
            public string expires_at { get; set; }
        }

        private sealed class BridgeEvent
        {
            public string event_id { get; set; }
            public string command_id { get; set; }
            public string event_type { get; set; }
            public string broker_order_id { get; set; }
            public int? quantity { get; set; }
            public int? filled_quantity { get; set; }
            public double? price { get; set; }
            public string message { get; set; }
            public string occurred_at { get; set; }
            public Dictionary<string, object> metadata { get; set; }
        }

        private sealed class PositionSnapshot
        {
            public string instrument { get; set; }
            public int quantity { get; set; }
            public double average_price { get; set; }
            public string market_position { get; set; }
        }

        private sealed class OrderSnapshot
        {
            public string broker_order_id { get; set; }
            public string command_id { get; set; }
            public string name { get; set; }
            public string instrument { get; set; }
            public string state { get; set; }
            public string action { get; set; }
            public string order_type { get; set; }
            public int quantity { get; set; }
            public int filled_quantity { get; set; }
            public double limit_price { get; set; }
            public double stop_price { get; set; }
        }

        private Account account;
        private Timer bridgeTimer;
        private int bridgeTickBusy;
        private readonly object accountSync = new object();
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private readonly ConcurrentQueue<BridgeEvent> eventQueue = new ConcurrentQueue<BridgeEvent>();
        private readonly ConcurrentQueue<ExecutionCommand> commandQueue = new ConcurrentQueue<ExecutionCommand>();
        private readonly ConcurrentDictionary<string, byte> submittedCommands = new ConcurrentDictionary<string, byte>();
        private readonly ConcurrentDictionary<string, byte> bracketsSubmitted = new ConcurrentDictionary<string, byte>();
        private readonly ConcurrentDictionary<string, ExecutionCommand> commandPlans = new ConcurrentDictionary<string, ExecutionCommand>();
        private string bridgeId;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "OTR Operation 7.2 SIM-only execution and reconciliation bridge.";
                Name = "OTRExecutionBridge";
                Calculate = Calculate.OnEachTick;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;

                BaseUrl = "http://127.0.0.1:8000/market/api/bridge/execution";
                BridgeKey = "";
                AccountName = "Sim101";
                ArmSimulationOrders = false;
                PollIntervalMs = 1000;
                HttpTimeoutMs = 5000;
            }
            else if (State == State.DataLoaded)
            {
                bridgeId = "nt8-" + Environment.MachineName + "-" + Guid.NewGuid().ToString("N").Substring(0, 10);
                EnsureAccount();
            }
            else if (State == State.Realtime)
            {
                EnsureAccount();
                int interval = Math.Max(500, PollIntervalMs);
                bridgeTimer = new Timer(BridgeTick, null, 250, interval);
            }
            else if (State == State.Terminated)
            {
                if (bridgeTimer != null)
                {
                    bridgeTimer.Dispose();
                    bridgeTimer = null;
                }
                DetachAccount();
            }
        }

        protected override void OnBarUpdate()
        {
            // Network polling and account events drive this bridge.
        }

        private bool IsSimulationAccountName(string value)
        {
            return !string.IsNullOrWhiteSpace(value)
                && value.Trim().StartsWith("Sim", StringComparison.OrdinalIgnoreCase);
        }

        private void EnsureAccount()
        {
            lock (accountSync)
            {
                if (account != null && string.Equals(account.Name, AccountName, StringComparison.Ordinal))
                    return;

                DetachAccountUnsafe();
                Account found = null;
                lock (Account.All)
                    found = Account.All.FirstOrDefault(a => string.Equals(a.Name, AccountName, StringComparison.Ordinal));

                if (found == null)
                    return;

                account = found;
                account.OrderUpdate += OnAccountOrderUpdate;
                account.PositionUpdate += OnAccountPositionUpdate;
                SeedKnownOtrOrders();
            }
        }

        private void DetachAccount()
        {
            lock (accountSync)
                DetachAccountUnsafe();
        }

        private void DetachAccountUnsafe()
        {
            if (account == null)
                return;
            account.OrderUpdate -= OnAccountOrderUpdate;
            account.PositionUpdate -= OnAccountPositionUpdate;
            account = null;
        }

        private void SeedKnownOtrOrders()
        {
            if (account == null)
                return;
            lock (account.Orders)
            {
                foreach (Order order in account.Orders)
                {
                    string commandId = CommandIdFromOrderName(order.Name);
                    if (!string.IsNullOrEmpty(commandId))
                        submittedCommands.TryAdd(commandId, 0);
                }
            }
        }

        private void BridgeTick(object state)
        {
            if (Interlocked.Exchange(ref bridgeTickBusy, 1) == 1)
                return;

            try
            {
                if (State != State.Realtime || string.IsNullOrWhiteSpace(BaseUrl) || string.IsNullOrWhiteSpace(BridgeKey))
                    return;

                EnsureAccount();
                FlushEvents();
                PostSnapshot();

                // Local interlock #1. Even if the server is armed, this adapter
                // will not ask for executable commands until explicitly armed.
                if (!ArmSimulationOrders)
                    return;

                // Local interlock #2. Operation 7.2 phase 1 can NEVER submit to a
                // non-simulation account, regardless of any server setting.
                if (account == null || !IsSimulationAccountName(account.Name))
                    return;

                PollCommands();
                if (!commandQueue.IsEmpty)
                    TriggerCustomEvent(ProcessCommandQueue, null);
            }
            catch (Exception ex)
            {
                Print("OTRExecutionBridge tick error: " + ex.Message);
            }
            finally
            {
                Interlocked.Exchange(ref bridgeTickBusy, 0);
            }
        }

        private void PollCommands()
        {
            string response = HttpGet(Endpoint("commands?limit=10"));
            if (string.IsNullOrWhiteSpace(response))
                return;

            CommandEnvelope envelope = json.Deserialize<CommandEnvelope>(response);
            if (envelope == null || !envelope.ok || !envelope.dispatch_ready || envelope.commands == null)
                return;

            foreach (ExecutionCommand command in envelope.commands)
            {
                if (command == null || string.IsNullOrWhiteSpace(command.command_id))
                    continue;
                if (!string.Equals(command.mode, "SIM_BRIDGE", StringComparison.OrdinalIgnoreCase))
                {
                    QueueEvent(command.command_id, "REJECTED", null, null, null, "NinjaTrader phase-1 bridge accepts SIM_BRIDGE commands only.");
                    continue;
                }
                if (!string.Equals(command.account, AccountName, StringComparison.Ordinal))
                {
                    QueueEvent(command.command_id, "REJECTED", null, null, null, "Command account does not match NinjaTrader bridge account.");
                    continue;
                }
                if (command.quantity != 1)
                {
                    QueueEvent(command.command_id, "REJECTED", null, command.quantity, null, "Operation 7.2 phase-1 adapter requires exactly one micro contract.");
                    continue;
                }
                DateTime expiry;
                if (!DateTime.TryParse(command.expires_at, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal, out expiry) || expiry.ToUniversalTime() <= DateTime.UtcNow)
                {
                    QueueEvent(command.command_id, "REJECTED", null, command.quantity, null, "Command expired before NinjaTrader submission.");
                    continue;
                }
                if (submittedCommands.ContainsKey(command.command_id))
                    continue;
                commandQueue.Enqueue(command);
            }
        }

        private void ProcessCommandQueue(object state)
        {
            EnsureAccount();
            if (!ArmSimulationOrders || account == null || !IsSimulationAccountName(account.Name))
                return;

            ExecutionCommand command;
            while (commandQueue.TryDequeue(out command))
                SubmitCommand(command);
        }

        private void SubmitCommand(ExecutionCommand command)
        {
            if (command == null || string.IsNullOrWhiteSpace(command.command_id))
                return;
            if (!submittedCommands.TryAdd(command.command_id, 0))
                return;

            try
            {
                Instrument instrument = Instrument.GetInstrument(command.execution_contract);
                if (instrument == null)
                    throw new InvalidOperationException("NinjaTrader could not resolve instrument " + command.execution_contract);

                OrderAction action;
                if (string.Equals(command.side, "BUY", StringComparison.OrdinalIgnoreCase))
                    action = OrderAction.Buy;
                else if (string.Equals(command.side, "SELL", StringComparison.OrdinalIgnoreCase))
                    action = OrderAction.SellShort;
                else
                    throw new InvalidOperationException("Unsupported command side " + command.side);

                commandPlans[command.command_id] = command;
                Order entry = account.CreateOrder(
                    instrument,
                    action,
                    OrderType.Limit,
                    OrderEntry.Automated,
                    TimeInForce.Day,
                    1,
                    command.entry_price,
                    0,
                    string.Empty,
                    OrderName(command.command_id, "E"),
                    Core.Globals.MaxDate,
                    null);

                QueueEvent(command.command_id, "ACKNOWLEDGED", entry.OrderId, 1, null, "NinjaTrader accepted OTR command for local submission.");
                account.Submit(new[] { entry });
            }
            catch (Exception ex)
            {
                QueueEvent(command.command_id, "REJECTED", null, command.quantity, null, "NinjaTrader submit error: " + ex.Message);
            }
        }

        private void OnAccountOrderUpdate(object sender, OrderEventArgs e)
        {
            if (e == null || e.Order == null)
                return;
            string commandId = CommandIdFromOrderName(e.Order.Name);
            string role = RoleFromOrderName(e.Order.Name);
            if (string.IsNullOrEmpty(commandId))
                return;

            if (role == "E")
            {
                if (e.OrderState == OrderState.Submitted)
                    QueueEvent(commandId, "SUBMITTED", e.Order.OrderId, e.Quantity, e.Filled, null);
                else if (e.OrderState == OrderState.Accepted || e.OrderState == OrderState.Working)
                    QueueEvent(commandId, "WORKING", e.Order.OrderId, e.Quantity, e.Filled, null);
                else if (e.OrderState == OrderState.PartFilled)
                    QueueEvent(commandId, "PARTIAL", e.Order.OrderId, e.Quantity, e.Filled, "Phase-1 adapter expects quantity=1; partial fill requires manual review.", e.AverageFillPrice);
                else if (e.OrderState == OrderState.Filled)
                {
                    QueueEvent(commandId, "FILLED", e.Order.OrderId, e.Quantity, e.Filled, null, e.AverageFillPrice);
                    SubmitProtectiveBracket(commandId, e.Order, e.Quantity);
                }
                else if (e.OrderState == OrderState.Cancelled)
                    QueueEvent(commandId, "CANCELLED", e.Order.OrderId, e.Quantity, e.Filled, "Entry order cancelled.");
                else if (e.OrderState == OrderState.Rejected)
                    QueueEvent(commandId, "REJECTED", e.Order.OrderId, e.Quantity, e.Filled, "Entry order rejected by NinjaTrader/broker.");
                return;
            }

            // Protective OCO sibling cancellation is expected after the other leg
            // fills, so bracket CANCELLED events are intentionally not promoted to
            // command-level cancellation.
            if ((role == "T" || role == "S") && e.OrderState == OrderState.Filled)
                QueueEvent(commandId, "CLOSED", e.Order.OrderId, e.Quantity, e.Filled, role == "T" ? "Profit target filled." : "Protective stop filled.", e.AverageFillPrice);
            else if ((role == "T" || role == "S") && e.OrderState == OrderState.Rejected)
                QueueEvent(commandId, "REJECTED", e.Order.OrderId, e.Quantity, e.Filled, "Protective bracket order rejected.");
        }

        private void SubmitProtectiveBracket(string commandId, Order filledEntry, int quantity)
        {
            if (filledEntry == null || quantity != 1)
                return;
            if (!bracketsSubmitted.TryAdd(commandId, 0))
                return;

            ExecutionCommand plan;
            if (!commandPlans.TryGetValue(commandId, out plan))
            {
                QueueEvent(commandId, "REJECTED", filledEntry.OrderId, quantity, quantity, "Filled entry has no retained OTR bracket plan.");
                return;
            }

            try
            {
                OrderAction exitAction = string.Equals(plan.side, "BUY", StringComparison.OrdinalIgnoreCase)
                    ? OrderAction.Sell
                    : OrderAction.BuyToCover;
                string oco = "OTR72-" + commandId + "-" + Guid.NewGuid().ToString("N").Substring(0, 8);

                Order target = account.CreateOrder(
                    filledEntry.Instrument,
                    exitAction,
                    OrderType.Limit,
                    OrderEntry.Automated,
                    TimeInForce.Day,
                    1,
                    plan.target_price,
                    0,
                    oco,
                    OrderName(commandId, "T"),
                    Core.Globals.MaxDate,
                    null);

                Order stop = account.CreateOrder(
                    filledEntry.Instrument,
                    exitAction,
                    OrderType.StopMarket,
                    OrderEntry.Automated,
                    TimeInForce.Day,
                    1,
                    0,
                    plan.stop_price,
                    oco,
                    OrderName(commandId, "S"),
                    Core.Globals.MaxDate,
                    null);

                account.Submit(new[] { target, stop });
            }
            catch (Exception ex)
            {
                QueueEvent(commandId, "REJECTED", filledEntry.OrderId, quantity, quantity, "Could not submit protective OCO bracket: " + ex.Message);
            }
        }

        private void OnAccountPositionUpdate(object sender, PositionEventArgs e)
        {
            // The next timer tick publishes complete account truth. Keeping this
            // callback non-blocking avoids network work on NinjaTrader event threads.
        }

        private void PostSnapshot()
        {
            Account current = account;
            if (current == null)
                return;

            List<PositionSnapshot> positions = new List<PositionSnapshot>();
            lock (current.Positions)
            {
                foreach (Position position in current.Positions)
                {
                    if (position == null || position.MarketPosition == MarketPosition.Flat)
                        continue;
                    int signed = position.MarketPosition == MarketPosition.Long ? position.Quantity : -position.Quantity;
                    positions.Add(new PositionSnapshot
                    {
                        instrument = position.Instrument.FullName,
                        quantity = signed,
                        average_price = position.AveragePrice,
                        market_position = position.MarketPosition.ToString()
                    });
                }
            }

            List<OrderSnapshot> orders = new List<OrderSnapshot>();
            lock (current.Orders)
            {
                foreach (Order order in current.Orders)
                {
                    if (order == null)
                        continue;
                    string commandId = CommandIdFromOrderName(order.Name);
                    if (string.IsNullOrEmpty(commandId))
                        continue;
                    orders.Add(new OrderSnapshot
                    {
                        broker_order_id = order.OrderId,
                        command_id = commandId,
                        name = order.Name,
                        instrument = order.Instrument.FullName,
                        state = order.OrderState.ToString(),
                        action = order.OrderAction.ToString(),
                        order_type = order.OrderType.ToString(),
                        quantity = order.Quantity,
                        filled_quantity = order.Filled,
                        limit_price = order.LimitPrice,
                        stop_price = order.StopPrice
                    });
                }
            }

            string body = json.Serialize(new
            {
                bridge_id = bridgeId,
                timestamp = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                account = current.Name,
                positions = positions,
                orders = orders
            });
            HttpPost(Endpoint("snapshot"), body);
        }

        private void FlushEvents()
        {
            if (eventQueue.IsEmpty)
                return;
            List<BridgeEvent> events = new List<BridgeEvent>();
            BridgeEvent item;
            while (events.Count < 200 && eventQueue.TryDequeue(out item))
                events.Add(item);
            if (events.Count == 0)
                return;

            try
            {
                HttpPost(Endpoint("events"), json.Serialize(new { events = events }));
            }
            catch
            {
                // Preserve unsent events for a later retry. event_id makes the
                // server idempotent if the response was lost after acceptance.
                foreach (BridgeEvent evt in events)
                    eventQueue.Enqueue(evt);
                throw;
            }
        }

        private void QueueEvent(string commandId, string eventType, string brokerOrderId, int? quantity, int? filledQuantity, string message, double? price = null)
        {
            eventQueue.Enqueue(new BridgeEvent
            {
                event_id = Guid.NewGuid().ToString("N"),
                command_id = commandId,
                event_type = eventType,
                broker_order_id = brokerOrderId,
                quantity = quantity,
                filled_quantity = filledQuantity,
                price = price,
                message = message,
                occurred_at = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                metadata = new Dictionary<string, object> { { "bridge_id", bridgeId }, { "sim_only", true } }
            });
        }

        private string Endpoint(string suffix)
        {
            return (BaseUrl ?? "").TrimEnd('/') + "/" + suffix.TrimStart('/');
        }

        private string HttpGet(string url)
        {
            HttpWebRequest request = CreateRequest(url, "GET", 0);
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd();
        }

        private string HttpPost(string url, string body)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(body ?? "{}");
            HttpWebRequest request = CreateRequest(url, "POST", bytes.Length);
            using (Stream stream = request.GetRequestStream())
                stream.Write(bytes, 0, bytes.Length);
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd();
        }

        private HttpWebRequest CreateRequest(string url, string method, int contentLength)
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
            request.Method = method;
            request.Accept = "application/json";
            request.ContentType = "application/json";
            request.Timeout = Math.Max(1000, HttpTimeoutMs);
            request.ReadWriteTimeout = Math.Max(1000, HttpTimeoutMs);
            request.Headers["X-OTR-Bridge-Key"] = BridgeKey;
            if (method == "POST")
                request.ContentLength = contentLength;
            return request;
        }

        private string OrderName(string commandId, string role)
        {
            return "OTR72|" + commandId + "|" + role;
        }

        private string CommandIdFromOrderName(string name)
        {
            if (string.IsNullOrWhiteSpace(name) || !name.StartsWith("OTR72|", StringComparison.Ordinal))
                return null;
            string[] parts = name.Split('|');
            return parts.Length >= 3 ? parts[1] : null;
        }

        private string RoleFromOrderName(string name)
        {
            if (string.IsNullOrWhiteSpace(name) || !name.StartsWith("OTR72|", StringComparison.Ordinal))
                return null;
            string[] parts = name.Split('|');
            return parts.Length >= 3 ? parts[2] : null;
        }

        [NinjaScriptProperty]
        [Display(Name = "Execution API Base URL", Order = 1, GroupName = "OTR 7.2 Execution")]
        public string BaseUrl { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Bridge Key", Order = 2, GroupName = "OTR 7.2 Execution")]
        public string BridgeKey { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Account", Order = 3, GroupName = "OTR 7.2 Execution")]
        public string AccountName { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ARM SIM ORDERS", Order = 4, GroupName = "OTR 7.2 Execution")]
        public bool ArmSimulationOrders { get; set; }

        [NinjaScriptProperty]
        [Range(500, 10000)]
        [Display(Name = "Poll Interval (ms)", Order = 5, GroupName = "OTR 7.2 Execution")]
        public int PollIntervalMs { get; set; }

        [NinjaScriptProperty]
        [Range(1000, 15000)]
        [Display(Name = "HTTP Timeout (ms)", Order = 6, GroupName = "OTR 7.2 Execution")]
        public int HttpTimeoutMs { get; set; }
    }
}
