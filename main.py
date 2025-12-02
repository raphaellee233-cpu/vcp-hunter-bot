"""
VCP Hunter Bot v2.1 - Ready to Run
Updates:
1. Keys Hardcoded (No environment variables needed)
2. Smart Filter: Removes ETFs, Funds, Trusts
3. Shows ALL signals
"""

import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta
import alpaca_trade_api as tradeapi
import time

# ========== CONFIG (已填入您的 Keys) ==========
API_KEY = 'PK7PDUTCS3VEBFVVHL2VONDFPF'
SECRET_KEY = '8DHcMfeYcqznFyE7UWibe6LBU1ojeRoLJVQwUiFuGmWR'
BASE_URL = 'https://paper-api.alpaca.markets'

TELEGRAM_TOKEN = '8183093878:AAHyQdT-wmAGw-6DH90rABKQl99i7eXtjnQ'
TELEGRAM_CHAT_ID = '1028223709'

CONFIG = {
    'ACCOUNT_SIZE': 100000,
    'RISK_PER_TRADE': 0.02,
    'MAX_POSITION_SIZE': 0.25,
    'MIN_PRICE': 10.0,        # Filter cheap stocks
    'TOP_RS_COUNT': 100
}

# ========== NOTIFICATION ==========
def send_telegram(message):
    """Send message to Telegram with Auto-Split"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunk_size = 4000
    
    for i in range(0, len(message), chunk_size):
        chunk = message[i:i+chunk_size]
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
            time.sleep(1) 
        except Exception as e:
            print(f"❌ Telegram Error: {e}")

# ========== CORE FUNCTIONS ==========
def get_all_us_stocks(api):
    """Get tradable US stocks > $10 (Excluding ETFs/Funds)"""
    print("🔄 Fetching market universe & filtering noise...")
    try:
        assets = api.list_assets(status='active', asset_class='us_equity')
        tradable = []
        ignored = 0
        
        # Keywords to identify ETFs and Funds
        BLACKLIST = ['ETF', 'FUND', 'TRUST', 'LP', 'DEPOSITARY', 'NOTE', 'BOND', 'MUNICIPAL', 'INCOME', 'PROSHARES', 'ISHARES', 'VANGUARD', 'SPDR', 'DIREXION']
        
        for a in assets:
            if a.exchange in ['NYSE', 'NASDAQ'] and a.tradable and a.marginable:
                # Smart Filter: Check name for fund keywords
                name_upper = a.name.upper() if hasattr(a, 'name') else ""
                is_noise = False
                for kw in BLACKLIST:
                    if kw in name_upper:
                        is_noise = True
                        break
                
                if not is_noise:
                    tradable.append(a.symbol)
                else:
                    ignored += 1
                    
        print(f"✅ Found {len(tradable)} stocks (Filtered out {ignored} ETFs/Funds).")
        return tradable
    except Exception as e:
        print(f"❌ Error fetching assets: {e}")
        return []

def get_top_rs_stocks(api, symbols):
    """Filter by Price > $10 and Calculate RS"""
    print(f"🔄 Calculating Momentum (RS Score)...")
    
    # Scanning first 2000 for better coverage
    universe = symbols[:2000] 
    
    end = datetime.now()
    start = end - timedelta(days=100)
    rs_data = []
    
    chunk_size = 200
    for i in range(0, len(universe), chunk_size):
        chunk = universe[i:i+chunk_size]
        try:
            bars = api.get_bars(chunk, tradeapi.TimeFrame.Day, 
                              start=start.strftime('%Y-%m-%d'), 
                              end=end.strftime('%Y-%m-%d'), 
                              adjustment='all', feed='iex').df
            
            if not bars.empty:
                pivot = bars.pivot_table(index=bars.index, columns='symbol', values='close')
                for ticker in pivot.columns:
                    try:
                        curr_price = pivot[ticker].iloc[-1]
                        
                        if curr_price < CONFIG['MIN_PRICE']: continue
                            
                        if len(pivot) > 60:
                            # RS Score (3 month ROC)
                            momentum = (pivot[ticker].iloc[-1] - pivot[ticker].iloc[0]) / pivot[ticker].iloc[0]
                            rs_data.append({'symbol': ticker, 'rs': momentum})
                    except: pass
        except: pass
            
    if not rs_data: return []
    
    df_rs = pd.DataFrame(rs_data).sort_values(by='rs', ascending=False)
    top_stocks = df_rs.head(CONFIG['TOP_RS_COUNT'])
    
    print(f"🔥 Locked in top {len(top_stocks)} RS leaders.")
    return top_stocks['symbol'].tolist()

def analyze_vcp_setup(series):
    """Analyze VCP Setup"""
    if len(series) < 150: return None
    
    curr = series.iloc[-1]
    sma50 = series.rolling(50).mean().iloc[-1]
    sma150 = series.rolling(150).mean().iloc[-1]
    sma200 = series.rolling(200).mean().iloc[-1]
    
    # 1. Trend Filter
    if not (curr > sma50 > sma150 > sma200): return None
    
    # 2. Tightness Filter (<15%)
    recent_high = series.rolling(10).max().iloc[-1]
    recent_low = series.rolling(10).min().iloc[-1]
    tightness = (recent_high - recent_low) / curr
    
    if tightness > 0.15: return None
    
    # 3. Trade Plan
    buy_stop = round(recent_high * 1.001, 2)
    stop_loss = round(max(recent_low, sma50) * 0.99, 2)
    risk_pct = (buy_stop - stop_loss) / buy_stop
    
    if risk_pct > 0.10 or risk_pct < 0.02: return None
    
    return {'buy_price': buy_stop, 'stop_loss': stop_loss, 'risk_pct': risk_pct}

# ========== MAIN SCANNER ==========
def run_vcp_scanner():
    print("🚀 VCP Hunter Bot Starting...")
    
    try:
        api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')
        
        # 1. Get Clean Universe
        all_symbols = get_all_us_stocks(api)
        if not all_symbols: return

        # 2. Get Leaders
        top_stocks = get_top_rs_stocks(api, all_symbols)
        
        # 3. Analyze VCP
        print(f"\n🔬 Analyzing {len(top_stocks)} strong stocks...")
        signals = []
        
        for ticker in top_stocks:
            try:
                bars = api.get_bars(ticker, tradeapi.TimeFrame.Day, 
                                  start=(datetime.now()-timedelta(days=300)).strftime('%Y-%m-%d'), 
                                  end=datetime.now().strftime('%Y-%m-%d'), 
                                  adjustment='all', feed='iex').df
                
                if not bars.empty:
                    setup = analyze_vcp_setup(bars['close'])
                    if setup:
                        risk_amt = CONFIG['ACCOUNT_SIZE'] * CONFIG['RISK_PER_TRADE']
                        shares = int(risk_amt / (setup['buy_price'] - setup['stop_loss']))
                        max_shares = int((CONFIG['ACCOUNT_SIZE'] * CONFIG['MAX_POSITION_SIZE']) / setup['buy_price'])
                        shares = min(shares, max_shares)
                        
                        signals.append({
                            'Ticker': ticker,
                            'Buy': setup['buy_price'],
                            'SL': setup['stop_loss'],
                            'Qty': shares
                        })
            except: pass
        
        # 4. Report
        dt_str = datetime.now().strftime('%Y-%m-%d')
        msg = f"📊 *VCP Daily Scan* ({dt_str})\n"
        msg += f"Found {len(signals)} high-quality setups\n"
        msg += "="*30 + "\n\n"
        
        if signals:
            for s in signals:
                msg += f"🚀 *{s['Ticker']}*\n"
                msg += f"Buy: `${s['Buy']}` | SL: `${s['SL']}`\n"
                msg += f"Size: `{s['Qty']}`\n"
                msg += "-"*20 + "\n"
        else:
            msg += "😴 No VCP breakouts today."
        
        print(msg)
        send_telegram(msg)
        print("✅ Scan completed successfully.")
        
    except Exception as e:
        err = f"❌ Scanner Error: {str(e)}"
        print(err)
        # Try to send error to Telegram so you know it failed
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                         json={"chat_id": TELEGRAM_CHAT_ID, "text": err})
        except: pass

if __name__ == "__main__":
    run_vcp_scanner()
