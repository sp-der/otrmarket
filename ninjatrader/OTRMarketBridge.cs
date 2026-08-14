#region Using declarations
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class OTRMarketBridge : Indicator
    {
        private sealed class TickRecord
        {
            public string Symbol;
            public string Contract;
            public string Timestamp;
            public double Last;
            public double Bid;
            public double Ask;
            public long Volume;
        }

        private ConcurrentQueue<TickRecord> queue;
        private Timer flushTimer;
        private int flushInProgress;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Streams NinjaTrader Level I last-trade ticks to the private OTR Market bridge.";
                Name = "OTRMarketBridge";
                Calculate = Calculate.OnEachTick;
                IsOverlay = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = false;

                EndpointUrl = "http://127.0.0.1:8000/market/api/bridge/ticks";
                BridgeKey = "";
                FlushIntervalMs = 250;
                MaxBatchSize = 1000;
            }
            else if (State == State.DataLoaded)
            {
                queue = new ConcurrentQueue<TickRecord>();
            }
            else if (State == State.Realtime)
            {
                if (queue == null)
                    queue = new ConcurrentQueue<TickRecord>();

                int interval = Math.Max(100, FlushIntervalMs);
                flushTimer = new Timer(FlushQueue, null, interval, interval);
            }
            else if (State == State.Terminated)
            {
                if (flushTimer != null)
                {
                    flushTimer.Dispose();
                    flushTimer = null;
                }
            }
        }

        protected override void OnBarUpdate()
        {
            // The bridge is driven by OnMarketData(), not bar updates.
        }

        protected override void OnMarketData(MarketDataEventArgs marketDataUpdate)
        {
            if (State != State.Realtime || marketDataUpdate.MarketDataType != MarketDataType.Last)
                return;

            string symbol = NormalizeSymbol(Instrument.MasterInstrument.Name);
            if (symbol == null)
                return;

            if (queue == null)
                queue = new ConcurrentQueue<TickRecord>();

            queue.Enqueue(new TickRecord
            {
                Symbol = symbol,
                Contract = Instrument.FullName,
                Timestamp = marketDataUpdate.Time.ToUniversalTime().ToString("o", CultureInfo.InvariantCulture),
                Last = marketDataUpdate.Price,
                Bid = marketDataUpdate.Bid,
                Ask = marketDataUpdate.Ask,
                Volume = marketDataUpdate.Volume
            });
        }

        private string NormalizeSymbol(string master)
        {
            string value = (master ?? "").Trim().ToUpperInvariant();
            if (value == "NQ" || value == "MNQ")
                return "NQ";
            if (value == "ES" || value == "MES")
                return "ES";
            if (value == "GC" || value == "MGC")
                return "GC";
            return null;
        }

        private void FlushQueue(object state)
        {
            if (Interlocked.Exchange(ref flushInProgress, 1) == 1)
                return;

            try
            {
                if (queue == null || queue.IsEmpty || string.IsNullOrWhiteSpace(EndpointUrl) || string.IsNullOrWhiteSpace(BridgeKey))
                    return;

                int maxItems = Math.Max(1, Math.Min(5000, MaxBatchSize));
                List<TickRecord> batch = new List<TickRecord>();
                TickRecord item;

                while (batch.Count < maxItems && queue.TryDequeue(out item))
                    batch.Add(item);

                if (batch.Count == 0)
                    return;

                PostBatch(batch);
            }
            catch
            {
                // Operation 3 is read-only market-data transport. A failed batch is
                // intentionally dropped rather than blocking NinjaTrader's data thread.
            }
            finally
            {
                Interlocked.Exchange(ref flushInProgress, 0);
            }
        }

        private void PostBatch(List<TickRecord> batch)
        {
            string body = BuildJson(batch);
            byte[] bytes = Encoding.UTF8.GetBytes(body);

            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(EndpointUrl);
            request.Method = "POST";
            request.ContentType = "application/json";
            request.Accept = "application/json";
            request.Timeout = 5000;
            request.ReadWriteTimeout = 5000;
            request.Headers["X-OTR-Bridge-Key"] = BridgeKey;
            request.ContentLength = bytes.Length;

            using (Stream stream = request.GetRequestStream())
                stream.Write(bytes, 0, bytes.Length);

            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                if ((int)response.StatusCode < 200 || (int)response.StatusCode >= 300)
                    throw new WebException("OTR bridge returned " + response.StatusCode);
            }
        }

        private string BuildJson(List<TickRecord> batch)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"ticks\":[");

            for (int i = 0; i < batch.Count; i++)
            {
                if (i > 0)
                    sb.Append(',');

                TickRecord tick = batch[i];
                sb.Append('{');
                sb.Append("\"symbol\":\"").Append(EscapeJson(tick.Symbol)).Append("\",");
                sb.Append("\"contract\":\"").Append(EscapeJson(tick.Contract)).Append("\",");
                sb.Append("\"timestamp\":\"").Append(EscapeJson(tick.Timestamp)).Append("\",");
                sb.Append("\"last\":").Append(tick.Last.ToString("R", CultureInfo.InvariantCulture)).Append(',');
                sb.Append("\"bid\":").Append(tick.Bid.ToString("R", CultureInfo.InvariantCulture)).Append(',');
                sb.Append("\"ask\":").Append(tick.Ask.ToString("R", CultureInfo.InvariantCulture)).Append(',');
                sb.Append("\"volume\":").Append(tick.Volume.ToString(CultureInfo.InvariantCulture));
                sb.Append('}');
            }

            sb.Append("]}");
            return sb.ToString();
        }

        private string EscapeJson(string value)
        {
            if (string.IsNullOrEmpty(value))
                return "";
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }

        [NinjaScriptProperty]
        [Display(Name = "Endpoint URL", Order = 1, GroupName = "OTR Bridge")]
        public string EndpointUrl { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Bridge Key", Order = 2, GroupName = "OTR Bridge")]
        public string BridgeKey { get; set; }

        [NinjaScriptProperty]
        [Range(100, 5000)]
        [Display(Name = "Flush Interval (ms)", Order = 3, GroupName = "OTR Bridge")]
        public int FlushIntervalMs { get; set; }

        [NinjaScriptProperty]
        [Range(1, 5000)]
        [Display(Name = "Max Batch Size", Order = 4, GroupName = "OTR Bridge")]
        public int MaxBatchSize { get; set; }
    }
}
