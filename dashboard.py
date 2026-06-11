"""
Terminal dashboard server for XRP/EUR Trading Bot.
Run: python dashboard.py
Visit: http://localhost:5000
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import ccxt
from flask import Flask, Response, jsonify, render_template_string
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

LOG_FILE = os.getenv("LOG_FILE", "trading_bot.log")
SYMBOL   = "XRP/EUR"

# ── Data helpers ──────────────────────────────────────────────────────────────

def fetch_market():
    try:
        exchange = ccxt.kraken({"enableRateLimit": True})
        ticker = exchange.fetch_ticker(SYMBOL)
        return {
            "price":       round(ticker["last"], 4),
            "change_pct":  round(ticker.get("percentage") or 0, 2),
            "bid":         round(ticker["bid"] or 0, 4),
            "ask":         round(ticker["ask"] or 0, 4),
            "volume_24h":  round(ticker.get("baseVolume") or 0, 0),
            "high_24h":    round(ticker.get("high") or 0, 4),
            "low_24h":     round(ticker.get("low") or 0, 4),
        }
    except Exception as e:
        return {"error": str(e)}


def parse_log(n=40):
    path = Path(LOG_FILE)
    if not path.exists():
        return [], None, None

    lines = path.read_text().splitlines()[-200:]
    entries = []

    position = {"xrp": 0.0, "entry": 0.0, "cost": 0.0}
    daily    = {"pnl": 0.0, "trades": 0, "starting": 0.0, "locked": False}
    signal   = {"value": "—", "confidence": 0.0, "reason": "—"}

    for line in lines:
        # Timestamp + level
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] \S+: (.+)", line)
        if m:
            ts, level, msg = m.groups()
            entries.append({"ts": ts[-8:], "level": level, "msg": msg[:90]})

        # Extract structured values from log messages
        if "Daily starting balance:" in line:
            mm = re.search(r"([\d.]+) EUR", line)
            if mm: daily["starting"] = float(mm.group(1))

        if "Daily PnL:" in line:
            mm = re.search(r"Daily PnL: ([+-]?[\d.]+) EUR", line)
            if mm: daily["pnl"] = float(mm.group(1))

        if "Trade closed" in line:
            daily["trades"] += 1

        if "Locking trading" in line:
            daily["locked"] = True

        if "Position opened:" in line:
            mm = re.search(r"([\d.]+) XRP @ ([\d.]+) EUR", line)
            if mm:
                position["xrp"]   = float(mm.group(1))
                position["entry"] = float(mm.group(2))

        if "Position closed" in line:
            position = {"xrp": 0.0, "entry": 0.0, "cost": 0.0}

        if "Signal:" in line:
            mm = re.search(r"Signal: (\w+) \(conf=([\d.]+)\) \| (.+)", line)
            if mm:
                signal = {"value": mm.group(1), "confidence": float(mm.group(2)), "reason": mm.group(3)[:60]}

    return entries[-n:], position, daily, signal


def build_status():
    market  = fetch_market()
    rows, position, daily, signal = parse_log()

    if position is None:
        position = {"xrp": 0.0, "entry": 0.0}
        daily    = {"pnl": 0.0, "trades": 0, "starting": 0.0, "locked": False}
        signal   = {"value": "—", "confidence": 0.0, "reason": "—"}

    price = market.get("price", 0)
    unreal_pct = 0.0
    unreal_eur = 0.0
    if position["xrp"] > 0 and position["entry"] > 0:
        unreal_eur = (price - position["entry"]) * position["xrp"]
        unreal_pct = (price - position["entry"]) / position["entry"] * 100

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    return {
        "market":   market,
        "position": {**position, "unreal_pct": round(unreal_pct, 2), "unreal_eur": round(unreal_eur, 2)},
        "daily":    daily,
        "signal":   signal,
        "log":      rows,
        "dry_run":  dry_run,
        "now":      datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

# ── HTML template ─────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XRP/EUR TRADING BOT</title>
<style>
  :root {
    --green:   #00ff41;
    --green2:  #00cc33;
    --amber:   #ffb000;
    --red:     #ff3333;
    --dim:     #006616;
    --bg:      #000000;
    --bg2:     #040d04;
    --border:  #00551a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--green);
    font-family: 'Courier New', Courier, monospace;
    font-size: 13px;
    line-height: 1.4;
    padding: 12px;
    min-height: 100vh;
  }
  .frame {
    border: 1px solid var(--border);
    background: var(--bg2);
    width: 100%;
    max-width: 1100px;
    margin: 0 auto;
  }
  .titlebar {
    border-bottom: 1px solid var(--border);
    padding: 6px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #010e01;
  }
  .title { font-size: 15px; font-weight: bold; letter-spacing: 3px; }
  .badge {
    font-size: 11px; padding: 2px 8px;
    border: 1px solid currentColor;
    letter-spacing: 1px;
  }
  .badge.dry  { color: var(--amber); border-color: var(--amber); }
  .badge.live { color: var(--green); border-color: var(--green); animation: blink 1.5s infinite; }
  @keyframes blink { 50% { opacity: 0.4; } }

  .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; border-bottom: 1px solid var(--border); }
  .cell {
    padding: 12px 14px;
    border-right: 1px solid var(--border);
  }
  .cell:last-child { border-right: none; }
  .cell-label {
    font-size: 10px; letter-spacing: 2px; color: var(--dim);
    margin-bottom: 8px; text-transform: uppercase;
  }
  .big-price { font-size: 28px; font-weight: bold; letter-spacing: 1px; }
  .sub { font-size: 12px; color: var(--green2); margin-top: 3px; }
  .pos  { color: var(--green); }
  .neg  { color: var(--red); }
  .neut { color: var(--amber); }

  .section { border-bottom: 1px solid var(--border); padding: 10px 14px; }
  .section-label {
    font-size: 10px; letter-spacing: 2px; color: var(--dim);
    margin-bottom: 8px;
  }
  .kv-row { display: flex; gap: 32px; flex-wrap: wrap; }
  .kv { display: flex; flex-direction: column; min-width: 90px; }
  .kv-key  { font-size: 10px; color: var(--dim); letter-spacing: 1px; }
  .kv-val  { font-size: 14px; font-weight: bold; margin-top: 2px; }

  .signal-bar {
    display: flex; align-items: center; gap: 16px;
    padding: 10px 14px; border-bottom: 1px solid var(--border);
  }
  .signal-label { font-size: 10px; letter-spacing: 2px; color: var(--dim); }
  .signal-val { font-size: 16px; font-weight: bold; letter-spacing: 2px; }
  .signal-BUY  { color: var(--green); }
  .signal-SELL { color: var(--red); }
  .signal-HOLD { color: var(--amber); }
  .conf-bar-wrap { flex: 1; height: 8px; background: #011201; border: 1px solid var(--border); }
  .conf-bar { height: 100%; background: var(--green); transition: width 0.5s; }
  .reason { font-size: 11px; color: var(--green2); }

  .log-area { padding: 10px 14px; }
  .log-header {
    font-size: 10px; letter-spacing: 2px; color: var(--dim);
    margin-bottom: 6px; display: flex; justify-content: space-between;
  }
  .log-row { display: flex; gap: 10px; padding: 2px 0; border-bottom: 1px solid #010e01; font-size: 12px; }
  .log-ts   { color: var(--dim); flex-shrink: 0; }
  .log-INFO  { color: #005f1a; flex-shrink: 0; width: 46px; }
  .log-WARNING { color: var(--amber); flex-shrink: 0; width: 46px; }
  .log-ERROR   { color: var(--red); flex-shrink: 0; width: 46px; }
  .log-DEBUG   { color: #003310; flex-shrink: 0; width: 46px; }
  .log-msg  { color: var(--green2); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }

  .footer {
    padding: 6px 14px; font-size: 10px; color: var(--dim);
    display: flex; justify-content: space-between; letter-spacing: 1px;
  }
  .scanline {
    pointer-events: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.05) 2px, rgba(0,0,0,0.05) 4px);
    z-index: 999;
  }
</style>
</head>
<body>
<div class="scanline"></div>
<div class="frame" id="root">
  <div class="titlebar">
    <span class="title">&#9632; XRP/EUR TRADING BOT</span>
    <span id="now" style="font-size:12px;color:var(--green2)">--:--:-- UTC</span>
    <span id="mode-badge" class="badge dry">DRY RUN</span>
  </div>

  <!-- Top 3 cells -->
  <div class="grid3">
    <div class="cell">
      <div class="cell-label">MARKET &mdash; XRP/EUR</div>
      <div class="big-price" id="price">-.----</div>
      <div class="sub" id="change">24h: -.--% &nbsp; H: -.---- &nbsp; L: -.----</div>
      <div class="sub" id="bidask" style="margin-top:4px">Bid: -.---- &nbsp; Ask: -.----</div>
    </div>
    <div class="cell">
      <div class="cell-label">OPEN POSITION</div>
      <div class="big-price" id="pos-xrp">0.0000 XRP</div>
      <div class="sub" id="pos-entry">Entry: -.----</div>
      <div class="sub" id="pos-unreal" style="margin-top:4px">Unrealized: -.--</div>
    </div>
    <div class="cell">
      <div class="cell-label">DAILY P&amp;L</div>
      <div class="big-price" id="daily-pnl">+0.00 EUR</div>
      <div class="sub" id="daily-start">Starting: -.-- EUR</div>
      <div class="sub" id="daily-status" style="margin-top:4px">Trades: 0 &nbsp; Status: ACTIVE</div>
    </div>
  </div>

  <!-- Signal bar -->
  <div class="signal-bar">
    <span class="signal-label">SIGNAL</span>
    <span class="signal-val signal-HOLD" id="sig-val">HOLD</span>
    <span style="font-size:12px;color:var(--dim)">CONF</span>
    <div class="conf-bar-wrap"><div class="conf-bar" id="conf-bar" style="width:0%"></div></div>
    <span style="font-size:12px;color:var(--dim)" id="conf-pct">0%</span>
    <span class="reason" id="sig-reason">—</span>
  </div>

  <!-- Log -->
  <div class="log-area">
    <div class="log-header">
      <span>ACTIVITY LOG</span>
      <span id="log-count">0 entries</span>
    </div>
    <div id="log-rows"></div>
  </div>

  <div class="footer">
    <span>KRAKEN EXCHANGE &nbsp;|&nbsp; 15m CANDLES &nbsp;|&nbsp; REFRESH: 30s</span>
    <span id="last-update">LAST UPDATE: --:--:--</span>
  </div>
</div>

<script>
async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    document.getElementById('now').textContent = d.now;

    const badge = document.getElementById('mode-badge');
    badge.textContent = d.dry_run ? 'DRY RUN' : '● LIVE';
    badge.className   = 'badge ' + (d.dry_run ? 'dry' : 'live');

    // Market
    const m = d.market;
    document.getElementById('price').textContent = m.price ? m.price.toFixed(4) : 'ERROR';
    const chg = m.change_pct || 0;
    const chgEl = document.getElementById('change');
    chgEl.textContent = `24h: ${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%   H: ${(m.high_24h||0).toFixed(4)}   L: ${(m.low_24h||0).toFixed(4)}`;
    chgEl.className = 'sub ' + (chg >= 0 ? 'pos' : 'neg');
    document.getElementById('bidask').textContent = `Bid: ${(m.bid||0).toFixed(4)}   Ask: ${(m.ask||0).toFixed(4)}`;

    // Position
    const p = d.position;
    document.getElementById('pos-xrp').textContent = `${(p.xrp||0).toFixed(4)} XRP`;
    document.getElementById('pos-entry').textContent = p.xrp > 0 ? `Entry: ${(p.entry||0).toFixed(4)} EUR` : 'No open position';
    const uEl = document.getElementById('pos-unreal');
    uEl.textContent = p.xrp > 0
      ? `Unrealized: ${p.unreal_eur >= 0 ? '+' : ''}${p.unreal_eur.toFixed(2)} EUR (${p.unreal_pct >= 0 ? '+' : ''}${p.unreal_pct.toFixed(2)}%)`
      : '—';
    uEl.className = 'sub ' + (p.unreal_pct >= 0 ? 'pos' : 'neg');

    // Daily
    const dy = d.daily;
    const pnlEl = document.getElementById('daily-pnl');
    pnlEl.textContent = `${dy.pnl >= 0 ? '+' : ''}${(dy.pnl||0).toFixed(2)} EUR`;
    pnlEl.className = 'big-price ' + (dy.pnl >= 0 ? 'pos' : 'neg');
    document.getElementById('daily-start').textContent = `Starting: ${(dy.starting||0).toFixed(2)} EUR`;
    const statusEl = document.getElementById('daily-status');
    statusEl.textContent = `Trades: ${dy.trades}   Status: ${dy.locked ? 'LOCKED' : 'ACTIVE'}`;
    statusEl.className = 'sub ' + (dy.locked ? 'neg' : 'pos');

    // Signal
    const sg = d.signal;
    const sigEl = document.getElementById('sig-val');
    sigEl.textContent = sg.value;
    sigEl.className = `signal-val signal-${sg.value}`;
    const confPct = Math.round((sg.confidence||0) * 100);
    document.getElementById('conf-bar').style.width = confPct + '%';
    document.getElementById('conf-pct').textContent = confPct + '%';
    document.getElementById('sig-reason').textContent = sg.reason || '—';

    // Log
    const rows = d.log || [];
    document.getElementById('log-count').textContent = `${rows.length} entries`;
    const container = document.getElementById('log-rows');
    container.innerHTML = [...rows].reverse().map(row => `
      <div class="log-row">
        <span class="log-ts">${row.ts}</span>
        <span class="log-${row.level}">${row.level.padEnd(7)}</span>
        <span class="log-msg">${row.msg}</span>
      </div>`).join('');

    document.getElementById('last-update').textContent =
      'LAST UPDATE: ' + new Date().toTimeString().slice(0,8);
  } catch(e) {
    console.error('Refresh failed:', e);
  }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/status")
def api_status():
    return jsonify(build_status())


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 5000))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
