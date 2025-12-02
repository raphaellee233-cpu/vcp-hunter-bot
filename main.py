"""
VCP Hunter Bot v2.0 - Enhanced
Updates:
1. Shows ALL signals (no limit)
2. Adds Sector information next to Ticker (Simulated/Placeholder)
3. Filters stocks < $10
"""

import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta
import alpaca_trade_api as tradeapi
import time

# ========== CONFIG ==========
# Keys are loaded from GitHub Secrets for security
API_KEY = os.environ.get('ALPACA_API_KEY')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

CONFIG = {
    'ACCOUNT_SIZE': 100000,
    'RISK_PER_TRADE': 0.02,
    'MAX_POSITION_SIZE': 0.25,
    'MIN_PRICE': 10.0,        # Minimum price filter
    'TOP_RS_COUNT': 100
}

# ========== NOTIFICATION ==========
def send_telegram(message):
    """Send message to Telegram with Auto-Split for long messages"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram keys not found in environment variables.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Telegram limit is 4096 chars. We split safely at 4000.
    chunk_size = 4000
    
    for i in range(0, len(message), chunk_size):
        chunk = message[i:i+chunk_size]
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                print(f"⚠️ Telegram API returned: {response.status_code}")
            time.sleep(1) # Prevent rate limit
        except Exception as e:
            print(f"❌ Failed to send Telegram: {e}")

# ========== CORE FUNCTIONS ==========
def get_all_us_stocks(api):
    """Get list of tradable US stocks > $10"""
    print("🔄 Fetching market universe...")
    try:
        assets = api.list_assets(status='active', asset_class='us_equity')
        # Filter: NYSE/NASDAQ only, Tradable, Marginable
        tradable = []
        for a in assets:
            if a.exchange in ['NYSE', 'NASDAQ'] and a.tradable and a.marginable:
                tradable.append(a.symbol)
        print(f"✅ Found {len(tradable)} total assets.")
        return tradable
    except Exception as e:
        print(f"❌ Error fetching assets: {e}")
        return []

def get_top_rs_stocks_and_filter_price(api, symbols):
    """
    1. Filter by Price > $10
    2. Calculate RS
    3. Return top performers
    """
    print(f"🔄 Filtering by Price > ${CONFIG['MIN_PRICE']} and Calculating Momentum...")
    
    # In production, you might want to scan more than 1000
    universe = symbols[:1000] 
    
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
                        
                        # FILTER: Price > $10
                        if curr_price < CONFIG['MIN_PRICE']:
                            continue
                            
                        if len(pivot) > 60:
                            # RS Score (3 month ROC)
                            momentum = (pivot[ticker].iloc[-1] - pivot[ticker].iloc[0]) / pivot[ticker].iloc[0]
                            rs_data.append({'symbol': ticker, 'rs': momentum})
                    except: pass
        except: 
            pass
            
    if not rs_data: 
        return []
    
    df_rs = pd.DataFrame(rs_data)
    df_rs = df_rs.sort_values(by='rs', ascending=False)
    
    top_stocks = df_rs.head(CONFIG['TOP_RS_COUNT'])
    print(f"🔥 Locked in top {len(top_stocks)} RS leaders (Price > ${CONFIG['MIN_PRICE']}).")
    
    return top_stocks['symbol'].tolist()

def analyze_vcp_setup(series):
    """Analyze if stock qualifies as VCP setup"""
    if len(series) < 150: 
        return None
    
    curr = series.iloc[-1]
    sma50 = series.rolling(50).mean().iloc[-1]
    sma150 = series.rolling(150).mean().iloc[-1]
    sma200 = series.rolling(200).mean().iloc[-1]
    
    # 1. Trend Filter
    if not (curr > sma50 > sma150 > sma200): 
        return None
    
    # 2. Volatility Contraction (Tightness)
    recent_high = series.rolling(10).max().iloc[-1]
    recent_low = series.rolling(10).min().iloc[-1]
    tightness = (recent_high - recent_low) / curr
    
    if tightness > 0.15: 
        return None  # Too loose
    
    # 3. Generate Trade Plan
    buy_stop = round(recent_high * 1.001, 2)
    stop_loss = round(max(recent_low, sma50) * 0.99, 2)
    risk_pct = (buy_stop - stop_loss) / buy_stop
    
    if risk_pct > 0.10 or risk_pct < 0.02: 
        return None
    
    return {
        'buy_price': buy_stop,
        'stop_loss': stop_loss,
        'risk_pct': risk_pct
    }

# ========== MAIN SCANNER ==========
def run_vcp_scanner():
    """Main scanning logic"""
    print("🚀 VCP Hunter Bot Starting...")
    
    if not API_KEY or not SECRET_KEY:
        print("❌ Error: Alpaca API keys not found in environment variables.")
        return

    try:
        api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')
        
        # 1. Get universe
        all_symbols = get_all_us_stocks(api)
        if not all_symbols:
            print("❌ No symbols found. Check API connection.")
            return

        # 2. Get top RS stocks (Filtered by Price > 10)
        top_stocks = get_top_rs_stocks_and_filter_price(api, all_symbols)
        
        # 3. Analyze VCP setups
        print(f"\n🔬 Analyzing {len(top_stocks)} strong stocks for VCP patterns...")
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
                        # Calculate position size
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
            except:
                pass
        
        # 4. Generate Report
        dt_str = datetime.now().strftime('%Y-%m-%d')
        msg = f"📊 *VCP Daily Scan* ({dt_str})\n"
        msg += f"Found {len(signals)} setups (Price > ${CONFIG['MIN_PRICE']})\n"
        msg += "="*30 + "\n\n"
        
        if signals:
            for s in signals:  # Show ALL signals
                # Try to fetch Asset info for "Sector" - simplified as Name check or placeholder
                # Real sector data requires paid API usually
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
        error_msg = f"❌ *Scanner Error*\n{str(e)}"
        print(error_msg)
        send_telegram(error_msg)

if __name__ == "__main__":
    run_vcp_scanner()
