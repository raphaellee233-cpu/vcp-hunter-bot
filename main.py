"""
VCP Hunter Bot - Automated Market Scanner
Runs daily via GitHub Actions to scan US stocks and send Telegram alerts
"""

import pandas as pd
import numpy as np
import requests
import os
from datetime import datetime, timedelta
import alpaca_trade_api as tradeapi

# ========== CONFIG (從 GitHub Secrets 讀取) ==========
API_KEY = os.environ.get('ALPACA_API_KEY')
SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY')
BASE_URL = 'https://paper-api.alpaca.markets'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

CONFIG = {
    'ACCOUNT_SIZE': 100000,
    'RISK_PER_TRADE': 0.02,
    'MAX_POSITION_SIZE': 0.25,
    'MIN_PRICE': 10.0,
    'TOP_RS_COUNT': 100
}

# ========== NOTIFICATION ==========
def send_telegram(message):
    """Send message to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ Telegram notification sent successfully.")
        else:
            print(f"⚠️ Telegram API returned: {response.status_code}")
    except Exception as e:
        print(f"❌ Failed to send Telegram: {e}")

# ========== CORE FUNCTIONS ==========
def get_all_us_stocks(api):
    """Get list of tradable US stocks"""
    print("🔄 Fetching market universe...")
    assets = api.list_assets(status='active', asset_class='us_equity')
    tradable = [a.symbol for a in assets if a.exchange in ['NYSE', 'NASDAQ'] and a.tradable and a.marginable]
    print(f"✅ Found {len(tradable)} tradable stocks.")
    return tradable

def get_top_rs_stocks(api, symbols):
    """Calculate RS and return top performers"""
    print(f"🔄 Calculating momentum (RS Score)...")
    universe = symbols[:500]  # Scan first 500 for speed
    end = datetime.now()
    start = end - timedelta(days=100)
    rs_data = []
    
    chunk_size = 100
    for i in range(0, len(universe), chunk_size):
        chunk = universe[i:i+chunk_size]
        try:
            bars = api.get_bars(chunk, tradeapi.TimeFrame.Day, 
                              start=start.strftime('%Y-%m-%d'), 
                              end=end.strftime('%Y-%m-%d'), 
                              adjustment='all', feed='iex').df
            if not bars.empty:
                pivot = bars.pivot_table(index=bars.index, columns='symbol', values='close')
                if len(pivot) > 60:
                    momentum = (pivot.iloc[-1] - pivot.iloc[0]) / pivot.iloc[0]
                    rs_data.append(momentum)
        except: 
            pass
            
    if not rs_data: 
        return []
    
    full_rs = pd.concat(rs_data).sort_values(ascending=False)
    top_list = full_rs.head(CONFIG['TOP_RS_COUNT']).index.tolist()
    print(f"🔥 Locked in top {len(top_list)} RS leaders.")
    return top_list

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
    
    try:
        api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')
        
        # 1. Get universe
        all_symbols = get_all_us_stocks(api)
        
        # 2. Get top RS stocks
        top_stocks = get_top_rs_stocks(api, all_symbols)
        
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
        msg += "="*30 + "\n\n"
        
        if signals:
            msg += f"🚨 Found {len(signals)} potential setups:\n\n"
            for s in signals[:10]:  # Limit to top 10 to avoid spam
                msg += f"🚀 *{s['Ticker']}*\n"
                msg += f"Buy Stop: `${s['Buy']}`\n"
                msg += f"Stop Loss: `${s['SL']}`\n"
                msg += f"Size: `{s['Qty']}` shares\n"
                msg += "-"*20 + "\n"
            
            if len(signals) > 10:
                msg += f"\n_({len(signals)-10} more signals available)_"
        else:
            msg += "😴 No VCP breakouts today.\nMarket resting, stay patient."
        
        print(msg)
        send_telegram(msg)
        print("✅ Scan completed successfully.")
        
    except Exception as e:
        error_msg = f"❌ *Scanner Error*\n{str(e)}"
        print(error_msg)
        send_telegram(error_msg)

if __name__ == "__main__":
    run_vcp_scanner()
