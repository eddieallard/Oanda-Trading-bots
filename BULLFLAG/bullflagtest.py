import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from decimal import Decimal, getcontext
from typing import Dict, List, Optional, Tuple, Any
import matplotlib.pyplot as plt
from collections import defaultdict

# Configure decimal precision
getcontext().prec = 6

class BullFlagBacktester:
    def __init__(self):
        # Configuration
        self.TRADING_INSTRUMENTS = [
            'EUR_USD'  # Testing with just EUR_USD for now
        ]
        
        # Trading Parameters
        self.STARTING_BALANCE = Decimal('500.00')
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
        self.COMMISSION_PER_TRADE = Decimal('0.01')
        
        # Backtest parameters
        self.BACKTEST_DAYS = 30
        self.CANDLE_GRANULARITY = 'M1'  # 5-minute candles
        self.CANDLES_TO_FETCH = 100  # Number of candles for pattern detection
        
        # Internal state
        self.current_balance = self.STARTING_BALANCE
        self.equity_curve = []
        self.today_trade_count = 0
        self.last_trade_time = None
        self.active_trades = {}
        self.trade_history = []
        self.instrument_data = {}  # Stores historical data for each instrument
        self.current_time = None  # Simulation time
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('bullflagtest.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Statistics
        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_profit': Decimal('0'),
            'max_drawdown': Decimal('0'),
            'max_winning_streak': 0,
            'max_losing_streak': 0,
            'profit_factor': Decimal('0'),
            'average_trade_duration': timedelta(0),
            'instrument_stats': defaultdict(lambda: {
                'trades': 0,
                'wins': 0,
                'losses': 0,
                'profit': Decimal('0')
            })
        }
        
        self.logger.info("Bull Flag Trading Backtester initialized")
        self.logger.info(f"Trading instruments: {self.TRADING_INSTRUMENTS}")
        self.logger.info(f"Starting balance: ${self.STARTING_BALANCE}")
    
    def load_historical_data(self, data_source: str = 'csv'):
        """
        Load historical 5-minute candle data for all instruments
        Options: 'csv' (pre-downloaded) or 'api' (OANDA)
        """
        self.logger.info("Loading historical data...")
        
        # Calculate date range for backtest using timezone-aware datetime
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.BACKTEST_DAYS)
        
        for instrument in self.TRADING_INSTRUMENTS:
            try:
                filename = f"historical_data/{instrument}_M5.csv"
                df = pd.read_csv(filename)
                
                # Check for different possible time column names
                time_col = None
                for possible_col in ['time', 'timestamp', 'date', 'datetime']:
                    if possible_col in df.columns:
                        time_col = possible_col
                        break
                
                if time_col is None:
                    raise ValueError(f"No time column found in {filename}")
                
                # Combine date and time columns properly
                df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'], format='%Y.%m.%d %H:%M')
                df = df.set_index('datetime')
                
                # Ensure we have the required OHLCV columns
                required_cols = ['open', 'high', 'low', 'close', 'volume']
                for col in required_cols:
                    if col not in df.columns:
                        raise ValueError(f"Missing required column: {col}")
                
                # Store in memory
                self.instrument_data[instrument] = df
                self.logger.info(f"Loaded {len(df)} candles for {instrument}")
                
            except FileNotFoundError:
                self.logger.warning(f"No data file found for {instrument}")
                continue
            except Exception as e:
                self.logger.error(f"Error loading data for {instrument}: {str(e)}")
                continue
                    
        self.logger.info("Historical data loading complete")
    
    def simulate_market(self):
        """Simulate market movement through historical data"""
        if not self.instrument_data:
            self.logger.error("No historical data loaded")
            return False
            
        # Get all unique timestamps across all instruments
        all_timestamps = set()
        for df in self.instrument_data.values():
            all_timestamps.update(df.index)
        
        # Sort timestamps chronologically
        sorted_timestamps = sorted(all_timestamps)
        
        # Process each time period
        for timestamp in sorted_timestamps:
            self.current_time = timestamp
            self.process_time_period(timestamp)
            
            # Daily reset
            if timestamp.hour == 0 and timestamp.minute == 0:
                self.today_trade_count = 0
                
        return True
    
    def process_time_period(self, timestamp: datetime):
        """Process trading logic for a specific timestamp"""
        for instrument, df in self.instrument_data.items():
            # Get candles up to current time
            historical_data = df[df.index <= timestamp]
            
            if len(historical_data) < 20:  # Need enough data for pattern detection
                continue
                
            # Check for bull flag pattern
            if self.detect_bull_flag(historical_data):
                self.logger.info(f"Bull flag pattern detected on {instrument} at {timestamp}")
                
                # Get current price (use close of last complete candle)
                current_price = historical_data.iloc[-1]['close']
                
                # Execute simulated trade
                self.execute_simulated_order(instrument, current_price, timestamp)
            
            # Monitor active trades
            self.monitor_simulated_trades(timestamp)
    
    def detect_bull_flag(self, historical_data):
        """Detect bull flag pattern in historical data"""
        try:
            # Ensure we have a proper copy of the data
            df = historical_data.copy()
            
            # Calculate indicators
            df = df.assign(
                sma_20 = df['close'].rolling(window=20).mean(),
                atr = self.calculate_atr(df, window=14)
            )
        except Exception as e:
            # Handle the exception
            self.logger.error(f"Error detecting bull flag pattern: {e}")
            # Rest of the code here should also be indented
            pole_start_idx = -15
            pole_end_idx = -10
            
            pole_height = df['high'].iloc[pole_end_idx] - df['low'].iloc[pole_start_idx]
            pole_avg_volume = df['volume'].iloc[pole_start_idx:pole_end_idx+1].mean()
            
            # Flag criteria: consolidation with decreasing volume
            flag_start_idx = -10
        
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
    
    def calculate_atr(self, df, window=14):
        """Calculate Average True Range (ATR)"""
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            
            # Calculate True Range components
            high_low = high - low
            high_close = (high - close.shift()).abs()
            low_close = (low - close.shift()).abs()
            
            # Combine and get max
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            
            # Calculate ATR
            atr = tr.rolling(window=window).mean()
            return atr
        except Exception as e:
            print(f"Error calculating ATR: {e}")
            return pd.Series(np.nan, index=df.index)
    
    def calculate_position_size(self, instrument: str, entry_price: float, stop_loss_price: float) -> int:
        """Calculate position size based on account balance and risk parameters"""
        try:
            # Calculate risk amount
            risk_amount = self.current_balance * (self.RISK_PERCENT / Decimal('100'))
            
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
    
    def execute_simulated_order(self, instrument: str, entry_price: float, timestamp: datetime):
        """Execute simulated trade in backtest environment"""
        if self.today_trade_count >= self.MAX_TRADES_PER_DAY:
            self.logger.warning("Max trades per day reached")
            return None
            
        if self.last_trade_time and (timestamp - self.last_trade_time).seconds < self.MIN_SECONDS_BETWEEN_TRADES:
            self.logger.warning("Minimum time between trades not met")
            return None
            
        # Calculate stop loss and take profit
        stop_loss_price = entry_price - (float(self.STOP_LOSS_PIPS) / 10000)
        take_profit_price = entry_price + (float(self.TAKE_PROFIT_PIPS) / 10000)
            
        # Calculate position size
        units = self.calculate_position_size(instrument, entry_price, stop_loss_price)
        
        # Create simulated trade
        trade = {
            'id': f"sim_{len(self.trade_history) + 1}",
            'instrument': instrument,
            'units': units,
            'entry_price': Decimal(str(entry_price)),
            'stop_loss': Decimal(str(stop_loss_price)),
            'take_profit': Decimal(str(take_profit_price)),
            'entry_time': timestamp,
            'status': 'OPEN',
            'trailing_stop_activated': False,
            'commission': self.COMMISSION_PER_TRADE
        }
        
        # Deduct commission
        self.current_balance -= self.COMMISSION_PER_TRADE
        
        # Update trade tracking
        self.today_trade_count += 1
        self.last_trade_time = timestamp
        self.active_trades[trade['id']] = trade
        
        self.logger.info(f"Executed simulated BUY order for {instrument}: {units} units at {entry_price}")
        
        return trade
    
    def monitor_simulated_trades(self, current_time: datetime):
        """Monitor and update simulated trades"""
        if not self.active_trades:
            return
            
        for trade_id, trade in list(self.active_trades.items()):
            # Get current price from historical data
            instrument_data = self.instrument_data[trade['instrument']]
            current_price = instrument_data[instrument_data.index <= current_time].iloc[-1]['close']
            
            # Check for stop loss hit
            if current_price <= trade['stop_loss']:
                self.close_simulated_trade(trade_id, current_price, current_time, "Stop loss triggered")
                continue
                
            # Check for take profit hit
            if current_price >= trade['take_profit']:
                self.close_simulated_trade(trade_id, current_price, current_time, "Take profit reached")
                continue
                
            # Check trade duration
            if (current_time - trade['entry_time']).total_seconds() > self.MAX_TRADE_DURATION_MINUTES * 60:
                self.close_simulated_trade(trade_id, current_price, current_time, "Max duration reached")
                continue
                
            # Check for trailing stop activation
            if not trade['trailing_stop_activated']:
                price_diff = (current_price - float(trade['entry_price'])) * 10000  # in pips
                
                if price_diff >= float(self.TRAILING_STOP_ACTIVATION_PIPS):
                    trade['trailing_stop_activated'] = True
                    trade['stop_loss'] = Decimal(str(current_price)) - (self.TRAILING_STOP_DISTANCE_PIPS / Decimal('10000'))
                    self.logger.info(f"Trailing stop activated for trade {trade_id}")
            
            # Update trailing stop if activated
            if trade['trailing_stop_activated']:
                new_stop = Decimal(str(current_price)) - (self.TRAILING_STOP_DISTANCE_PIPS / Decimal('10000'))
                if new_stop > trade['stop_loss']:
                    trade['stop_loss'] = new_stop
    
    def close_simulated_trade(self, trade_id: str, exit_price: float, exit_time: datetime, reason: str):
        """Close a simulated trade and update account balance"""
        trade = self.active_trades[trade_id]
        
        # Calculate P&L
        pips = (Decimal(str(exit_price)) - trade['entry_price']) * Decimal('10000')
        profit = pips * (Decimal(str(trade['units'])) / Decimal('10000'))  # Simplified P&L calculation
        
        # Deduct commission
        profit -= trade['commission']
        
        # Update account balance
        self.current_balance += profit
        
        # Update trade details
        trade['exit_price'] = Decimal(str(exit_price))
        trade['exit_time'] = exit_time
        trade['status'] = 'CLOSED'
        trade['close_reason'] = reason
        trade['pips'] = pips
        trade['profit'] = profit
        
        # Record equity for equity curve
        self.equity_curve.append({
            'time': exit_time,
            'balance': float(self.current_balance)
        })
        
        # Update statistics
        self.update_stats(trade)
        
        # Move to history
        self.trade_history.append(trade)
        del self.active_trades[trade_id]
        
        self.logger.info(f"Closed trade {trade_id} at {exit_price:.5f}. Reason: {reason}")
    
    def update_stats(self, trade: Dict):
        """Update backtest statistics with trade results"""
        self.stats['total_trades'] += 1
        
        # Instrument-specific stats
        instr_stats = self.stats['instrument_stats'][trade['instrument']]
        instr_stats['trades'] += 1
        
        if trade['profit'] >= 0:
            self.stats['winning_trades'] += 1
            instr_stats['wins'] += 1
        else:
            self.stats['losing_trades'] += 1
            instr_stats['losses'] += 1
            
        self.stats['total_profit'] += trade['profit']
        instr_stats['profit'] += trade['profit']
        
        # Update trade duration stats
        duration = trade['exit_time'] - trade['entry_time']
        total_seconds = self.stats['average_trade_duration'].total_seconds() * (self.stats['total_trades'] - 1)
        avg_seconds = (total_seconds + duration.total_seconds()) / self.stats['total_trades']
        self.stats['average_trade_duration'] = timedelta(seconds=avg_seconds)
        
        # Update max drawdown
        if len(self.equity_curve) >= 2:
            peak = max([x['balance'] for x in self.equity_curve])
            trough = min([x['balance'] for x in self.equity_curve])
            drawdown = (peak - trough) / peak * 100
            if drawdown > float(self.stats['max_drawdown']):
                self.stats['max_drawdown'] = Decimal(str(drawdown))
    
    def generate_report(self):
        """Generate comprehensive backtest report"""
        # Calculate additional statistics
        win_rate = (
            (self.stats['winning_trades'] / self.stats['total_trades'] * 100)
            if self.stats['total_trades'] > 0 else 0
        )

        total_gains = sum(t['profit'] for t in self.trade_history if t['profit'] > 0)
        total_losses = abs(sum(t['profit'] for t in self.trade_history if t['profit'] < 0))

        profit_factor = (
            total_gains / total_losses
            if total_losses > 0 else float('inf')
        )

        # Generate report text
        report = f"""
        {'='*80}
        BULL FLAG TRADING BOT BACKTEST REPORT
        {'='*80}
        Backtest Period:          {self.BACKTEST_DAYS} days
        Starting Balance:         ${self.STARTING_BALANCE:,.2f}
        Ending Balance:           ${self.current_balance:,.2f}
        Net Profit:               ${(self.current_balance - self.STARTING_BALANCE):,.2f}
        ROI:                      {((self.current_balance - self.STARTING_BALANCE) / self.STARTING_BALANCE * 100):.2f}%
        {'-'*80}
        Total Trades:             {self.stats['total_trades']}
        Winning Trades:           {self.stats['winning_trades']} ({win_rate:.1f}%)
        Losing Trades:            {self.stats['losing_trades']}
        Profit Factor:            {profit_factor:.2f}
        Max Drawdown:             {self.stats['max_drawdown']:.2f}%
        {'-'*80}
        Average Trade Duration:   {str(self.stats['average_trade_duration'])}
        Commission Paid:          ${self.COMMISSION_PER_TRADE * Decimal(str(self.stats['total_trades'])):,.2f}
        {'='*80}
        
        INSTRUMENT PERFORMANCE:
        {'='*80}
        {'Instrument':<10}{'Trades':>10}{'Wins':>10}{'Losses':>10}{'Win %':>10}{'Profit':>15}
        """
        
        for instrument, stats in self.stats['instrument_stats'].items():
            instr_win_rate = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
            report += f"\n{instrument:<10}{stats['trades']:>10}{stats['wins']:>10}{stats['losses']:>10}{instr_win_rate:>10.1f}%${stats['profit']:>14,.2f}"
        
        report += f"\n{'='*80}\n"
        
        # Save report to file
        with open('backtest_report.txt', 'w') as f:
            f.write(report)
            
        self.logger.info("\n" + report)
        
        # Generate equity curve plot
        self.plot_equity_curve()
        
        return report
    
    def plot_equity_curve(self):
        """Plot the equity curve from the backtest"""
        if not self.equity_curve:
            return
            
        df = pd.DataFrame(self.equity_curve)
        df.set_index('time', inplace=True)
        
        plt.figure(figsize=(12, 6))
        plt.plot(df.index, df['balance'], label='Account Balance')
        plt.title('Equity Curve')
        plt.xlabel('Time')
        plt.ylabel('Balance ($)')
        plt.grid(True)
        plt.legend()
        
        # Save plot
        plt.savefig('equity_curve.png')
        plt.close()
        
        self.logger.info("Equity curve plot saved to equity_curve.png")
    
    def run_backtest(self):
        """Run the complete backtest"""
        self.logger.info("Starting backtest...")
        start_time = time.time()
        
        # Load historical data
        self.load_historical_data()
        
        if not self.instrument_data:
            self.logger.error("No valid historical data loaded - cannot run backtest")
            return
            
        # Run simulation
        if self.simulate_market():
            # Generate report
            self.generate_report()
            
            runtime = time.time() - start_time
            self.logger.info(f"Backtest completed in {runtime:.2f} seconds")
            self.logger.info(f"Final balance: ${self.current_balance:,.2f}")
        else:
            self.logger.error("Backtest failed due to data issues")


if __name__ == "__main__":
    backtester = BullFlagBacktester()
    backtester.run_backtest()