import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from decimal import Decimal, getcontext
from typing import Dict, List, Optional, Tuple

# Configure decimal precision
getcontext().prec = 6

class BullFlagTradingBot:
    def __init__(self):
        # Configuration
        self.OANDA_ACCOUNT_ID = os.getenv('OANDA_ACCOUNT_ID')
        self.OANDA_ACCESS_TOKEN = os.getenv('OANDA_ACCESS_TOKEN')
        self.TRADING_INSTRUMENTS = [i.strip() for i in os.getenv('TRADING_INSTRUMENTS', '').split(',') if i.strip()]
        self.REST_URL = "https://api-fxtrade.oanda.com/v3"
        self.STREAM_URL = "https://stream-fxtrade.oanda.com/v3"
        
        # Trading Parameters
        self.RISK_PERCENT = Decimal('2')
        self.LEVERAGE_RATIO = 50
        self.STOP_LOSS_PIPS = Decimal('15')
        self.TAKE_PROFIT_PIPS = Decimal('30')
        self.TRAILING_STOP_ACTIVATION_PIPS = Decimal('10')
        self.TRAILING_STOP_DISTANCE_PIPS = Decimal('5')
        self.MIN_SECONDS_BETWEEN_TRADES = 30
        self.MAX_TRADES_PER_DAY = 100
        self.MAX_TRADE_DURATION_MINUTES = 60
        self.MIN_TRADE_UNITS = 50
        self.MAX_TRADE_UNITS = 100
        
        # Internal state
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.OANDA_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339"
        })
        self.today_trade_count = 0
        self.last_trade_time = None
        self.active_trades = {}
        self.trade_history = []
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('bullflag.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        self.logger.info("Bull Flag Trading Bot initialized")
        self.logger.info(f"Trading instruments: {self.TRADING_INSTRUMENTS}")
    
    def fetch_candles(self, instrument: str, count: int = 50, granularity: str = "M5") -> pd.DataFrame:
        """Fetch historical candles for pattern detection"""
        try:
            url = f"{self.REST_URL}/instruments/{instrument}/candles"
            params = {
                "count": count,
                "granularity": granularity,
                "price": "MBA"
            }
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            candles = data['candles']
            df = pd.DataFrame([{
                'time': c['time'],
                'open': float(c['mid']['o']),
                'high': float(c['mid']['h']),
                'low': float(c['mid']['l']),
                'close': float(c['mid']['c']),
                'volume': int(c['volume']),
                'complete': c['complete']
            } for c in candles])
            
            # Filter to only use complete candles for pattern detection
            df = df[df['complete']]
            
            # Explicitly specify the datetime format for OANDA's RFC3339 format
            df['time'] = pd.to_datetime(df['time'], format='%Y-%m-%dT%H:%M:%S.%fZ')
            df.set_index('time', inplace=True)
            
            return df
            
        except Exception as e:
            self.logger.error(f"Error fetching candles for {instrument}: {str(e)}")
            return pd.DataFrame()
    
    def detect_bull_flag(self, df: pd.DataFrame) -> bool:
        """Detect bull flag pattern using pandas/numpy operations"""
        if len(df) < 20:
            return False
            
        # Ensure we're only working with complete candles
        if 'complete' in df.columns and not df['complete'].all():
            self.logger.warning("Incomplete candles detected in pattern analysis")
            return False
            
        # Calculate required indicators
        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['atr'] = self.calculate_atr(df, window=14)
        
        # Identify the pole (sharp upward move)
        pole_start_idx = -15
        pole_end_idx = -10
        
        pole_height = df['high'].iloc[pole_end_idx] - df['low'].iloc[pole_start_idx]
        pole_avg_volume = df['volume'].iloc[pole_start_idx:pole_end_idx+1].mean()
        
        # Flag criteria: consolidation with decreasing volume
        flag_start_idx = -10
        flag_end_idx = -1
        
        flag_high = df['high'].iloc[flag_start_idx:flag_end_idx+1].max()
        flag_low = df['low'].iloc[flag_start_idx:flag_end_idx+1].min()
        flag_range = flag_high - flag_low
        flag_avg_volume = df['volume'].iloc[flag_start_idx:flag_end_idx+1].mean()
        
        # Bull flag pattern conditions
        conditions = [
            pole_height > 2 * df['atr'].iloc[pole_end_idx],  # Significant pole
            flag_range < 0.5 * pole_height,                  # Tight consolidation
            flag_avg_volume < 0.7 * pole_avg_volume,         # Volume drying up
            df['close'].iloc[-1] > flag_high,                # Breakout
            df['close'].iloc[-1] > df['sma_20'].iloc[-1]     # Above SMA
        ]
        
        return all(conditions)
    
    def calculate_atr(self, df: pd.DataFrame, window: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=window).mean().fillna(0)
        return atr
    
    def calculate_position_size(self, instrument: str, entry_price: float, stop_loss_price: float) -> int:
        """Calculate position size based on account balance and risk parameters"""
        try:
            # Get account balance
            account_info = self.session.get(f"{self.REST_URL}/accounts/{self.OANDA_ACCOUNT_ID}").json()
            balance = Decimal(str(account_info['account']['balance']))
            
            # Calculate risk amount
            risk_amount = balance * (self.RISK_PERCENT / Decimal('100'))
            
            # Calculate pip value (assuming standard lot)
            pip_value = Decimal('10')  # $10 per pip for standard lot (100,000 units)
            if "JPY" in instrument:
                pip_value = Decimal('100') / Decimal(str(entry_price))  # For JPY pairs
            
            # Calculate pips at risk
            pips_risk = abs(Decimal(str(entry_price)) - Decimal(str(stop_loss_price)))
            if "JPY" in instrument:
                pips_risk *= Decimal('100')  # JPY pairs have 2 decimal places
            else:
                pips_risk *= Decimal('100')  # Other pairs have 4 decimal places
            
            # Calculate units
            units = (risk_amount / (pips_risk * pip_value)) * Decimal('100')
            units = int(units) // 1000 * 1000  # Round to nearest 1000 units
            
            # Apply min/max constraints
            units = max(self.MIN_TRADE_UNITS, min(units, self.MAX_TRADE_UNITS))
            
            return units
            
        except Exception as e:
            self.logger.error(f"Error calculating position size: {str(e)}")
            return self.MIN_TRADE_UNITS
    
    def execute_order(self, instrument: str, signal: str) -> Optional[Dict]:
        """Execute trade order with OANDA API"""
        if self.today_trade_count >= self.MAX_TRADES_PER_DAY:
            self.logger.warning("Max trades per day reached")
            return None
            
        if self.last_trade_time and (datetime.utcnow() - self.last_trade_time).seconds < self.MIN_SECONDS_BETWEEN_TRADES:
            self.logger.warning("Minimum time between trades not met")
            return None
            
        # Get current price
        prices = self.session.get(f"{self.REST_URL}/accounts/{self.OANDA_ACCOUNT_ID}/pricing", 
                                params={"instruments": instrument}).json()
        bid = float(prices['prices'][0]['bids'][0]['price'])
        ask = float(prices['prices'][0]['asks'][0]['price'])
        spread = ask - bid
        
        if signal == "BUY":
            entry_price = ask
            stop_loss_price = entry_price - (float(self.STOP_LOSS_PIPS) / 10000)
            take_profit_price = entry_price + (float(self.TAKE_PROFIT_PIPS) / 10000)
        else:
            self.logger.warning("Only BUY signals implemented for bull flag strategy")
            return None
            
        # Calculate position size
        units = self.calculate_position_size(instrument, entry_price, stop_loss_price)
        
        # Prepare order
        order_data = {
            "order": {
                "units": str(units),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {
                    "price": str(round(stop_loss_price, 5)),
                    "timeInForce": "GTC"
                },
                "takeProfitOnFill": {
                    "price": str(round(take_profit_price, 5)),
                    "timeInForce": "GTC"
                },
                "trailingStopLossOnFill": {
                    "distance": str(self.TRAILING_STOP_DISTANCE_PIPS),
                    "timeInForce": "GTC"
                }
            }
        }
        
        try:
            response = self.session.post(
                f"{self.REST_URL}/accounts/{self.OANDA_ACCOUNT_ID}/orders",
                json=order_data
            )
            response.raise_for_status()
            order_result = response.json()
            
            # Update trade tracking
            self.today_trade_count += 1
            self.last_trade_time = datetime.utcnow()
            
            # Store trade details
            trade = {
                'id': order_result['orderFillTransaction']['id'],
                'instrument': instrument,
                'units': units,
                'entry_price': entry_price,
                'stop_loss': stop_loss_price,
                'take_profit': take_profit_price,
                'entry_time': datetime.utcnow(),
                'status': 'OPEN',
                'trailing_stop_activated': False
            }
            self.active_trades[trade['id']] = trade
            
            self.logger.info(f"Executed {signal} order for {instrument}: {units} units at {entry_price}")
            
            return order_result
            
        except Exception as e:
            self.logger.error(f"Error executing order: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response: {e.response.text}")
            return None
    
    def monitor_trades(self):
        """Monitor active trades and manage exits"""
        if not self.active_trades:
            return
            
        for trade_id, trade in list(self.active_trades.items()):
            # Check trade duration
            if (datetime.utcnow() - trade['entry_time']).seconds > self.MAX_TRADE_DURATION_MINUTES * 60:
                self.close_trade(trade_id, reason="Max duration reached")
                continue
                
            # Check for trailing stop activation
            if not trade['trailing_stop_activated']:
                current_price = self.get_current_price(trade['instrument'])
                price_diff = (current_price - trade['entry_price']) * 10000  # in pips
                
                if price_diff >= float(self.TRAILING_STOP_ACTIVATION_PIPS):
                    self.activate_trailing_stop(trade_id)
                    trade['trailing_stop_activated'] = True
    
    def get_current_price(self, instrument: str) -> float:
        """Get current bid price for an instrument"""
        prices = self.session.get(f"{self.REST_URL}/accounts/{self.OANDA_ACCOUNT_ID}/pricing", 
                                params={"instruments": instrument}).json()
        return float(prices['prices'][0]['bids'][0]['price'])
    
    def activate_trailing_stop(self, trade_id: str):
        """Activate trailing stop for a trade"""
        try:
            trade = self.active_trades[trade_id]
            
            # Modify trade to activate trailing stop
            data = {
                "order": {
                    "type": "TRAILING_STOP_LOSS",
                    "tradeID": trade_id,
                    "distance": str(self.TRAILING_STOP_DISTANCE_PIPS),
                    "timeInForce": "GTC"
                }
            }
            
            response = self.session.put(
                f"{self.REST_URL}/accounts/{self.OANDA_ACCOUNT_ID}/trades/{trade_id}/orders",
                json=data
            )
            response.raise_for_status()
            
            self.logger.info(f"Activated trailing stop for trade {trade_id}")
            
        except Exception as e:
            self.logger.error(f"Error activating trailing stop for trade {trade_id}: {str(e)}")
    
    def close_trade(self, trade_id: str, reason: str = ""):
        """Close an active trade"""
        try:
            trade = self.active_trades[trade_id]
            
            # Get current price to calculate exit price
            current_price = self.get_current_price(trade['instrument'])
            
            # Close trade
            response = self.session.put(
                f"{self.REST_URL}/accounts/{self.OANDA_ACCOUNT_ID}/trades/{trade_id}/close",
                json={}
            )
            response.raise_for_status()
            
            # Update trade details
            trade['exit_price'] = current_price
            trade['exit_time'] = datetime.utcnow()
            trade['status'] = 'CLOSED'
            trade['close_reason'] = reason
            
            # Calculate P&L
            pips = (current_price - trade['entry_price']) * 10000
            profit = pips * (trade['units'] / 10000)  # Simplified P&L calculation
            
            trade['pips'] = pips
            trade['profit'] = profit
            
            # Move to history
            self.trade_history.append(trade)
            del self.active_trades[trade_id]
            
            # Generate trade report
            self.generate_trade_report(trade)
            
            self.logger.info(f"Closed trade {trade_id} at {current_price}. Reason: {reason}")
            
        except Exception as e:
            self.logger.error(f"Error closing trade {trade_id}: {str(e)}")
    
    def generate_trade_report(self, trade: Dict):
        """Generate a detailed trade report"""
        duration = trade['exit_time'] - trade['entry_time']
        
        report = f"""
        {'='*50}
        TRADE REPORT
        {'='*50}
        Instrument:       {trade['instrument']}
        Direction:        LONG
        Entry Price:      {trade['entry_price']:.5f}
        Exit Price:       {trade['exit_price']:.5f}
        Stop Loss:        {trade['stop_loss']:.5f}
        Take Profit:      {trade['take_profit']:.5f}
        Units:            {trade['units']}
        Duration:         {duration}
        {'-'*50}
        Pips Gained:      {trade['pips']:.1f}
        Profit/Loss:      ${trade['profit']:.2f}
        {'-'*50}
        Entry Time:       {trade['entry_time']}
        Exit Time:        {trade['exit_time']}
        Close Reason:     {trade['close_reason']}
        {'='*50}
        """
        
        self.logger.info(report)
        
        # Save to file
        with open('trade_reports.txt', 'a') as f:
            f.write(report)
    
    def run(self):
        """Main trading loop"""
        self.logger.info(f'Trailing stop distance: {self.TRAILING_STOP_DISTANCE_PIPS} pips')
        self.logger.info(f'Trailing stop activation: {self.TRAILING_STOP_ACTIVATION_PIPS} pips')
        
        try:
            while True:
                for instrument in self.TRADING_INSTRUMENTS:
                    try:
                        # Fetch candle data
                        df = self.fetch_candles(instrument)
                        if df.empty:
                            continue
                            
                        # Detect bull flag pattern
                        if self.detect_bull_flag(df):
                            self.logger.info(f"Bull flag pattern detected on {instrument}")
                            self.execute_order(instrument, "BUY")
                            
                        # Monitor active trades
                        self.monitor_trades()
                        
                    except Exception as e:
                        self.logger.error(f"Error processing {instrument}: {str(e)}")
                
                # Sleep before next iteration
                time.sleep(60)
                
        except KeyboardInterrupt:
            self.logger.info("Shutting down trading bot")
            
        except Exception as e:
            self.logger.error(f"Fatal error in trading loop: {str(e)}")


if __name__ == "__main__":
    bot = BullFlagTradingBot()
    bot.run()