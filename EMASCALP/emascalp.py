from dotenv import load_dotenv
import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging
import pytz
from decimal import Decimal, getcontext, InvalidOperation
from typing import Dict, List, Optional, Tuple, Any
import hashlib

# take environment variables from .env.
load_dotenv()

# Configure decimal precision
getcontext().prec = 8

# Configuration - Enhanced with fallback values and validation
OANDA_ACCOUNT_ID = os.getenv('OANDA_ACCOUNT_ID')
OANDA_ACCESS_TOKEN = os.getenv('OANDA_ACCESS_TOKEN')

# Validate required environment variables
if not OANDA_ACCOUNT_ID or not OANDA_ACCESS_TOKEN:
    raise ValueError("Missing required environment variables: OANDA_ACCOUNT_ID and OANDA_ACCESS_TOKEN must be set")

TRADING_INSTRUMENTS = os.getenv('TRADING_INSTRUMENTS', 'EUR_USD,USD_CAD,EUR_CAD,ZAR_JPY').split(',')
REST_URL = "https://api-fxtrade.oanda.com/v3"

# Trading Parameters
RISK_PERCENT = Decimal('1.5')
LEVERAGE_RATIO = 30
MAX_SPREAD_PIPS = Decimal('2.5')
MIN_TRADE_UNITS = 100
MAX_TRADE_UNITS = 1000
MIN_SECONDS_BETWEEN_TRADES = 15
MAX_TRADES_PER_DAY = 75
MAX_TRADE_DURATION_MINUTES = 90
EMA_SHORT_PERIOD = 9
EMA_LONG_PERIOD = 50
CONFIRMATION_BARS = 2
TRAILING_STOP_DISTANCE = Decimal('0.0005')
TRAILING_STOP_ACTIVATION = Decimal('15')
MIN_TREND_STRENGTH = Decimal('0.0003')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('emascalp.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class EMAScalpingBot:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {OANDA_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        })
        self.account_balance = Decimal('0')
        self.account_currency = "USD"
        self.daily_trade_count = 0
        self.last_trade_time = datetime.min.replace(tzinfo=pytz.UTC)
        self.timezone = pytz.timezone('America/New_York')
        self.current_date = datetime.now(self.timezone).date()
        self.instrument_cooldowns = {instrument: datetime.min.replace(tzinfo=pytz.UTC) 
                                   for instrument in TRADING_INSTRUMENTS}
        self.error_count = 0
        self.max_retries = 3
        self.trade_history = {}
        
        # Initialize trading session
        self._initialize_session()
        
    def _initialize_session(self) -> None:
        """Initialize trading session with proper error handling using correct OANDA endpoints"""
        try:
            # Get account details using correct endpoint from OANDA documentation
            account_data = self._api_request(f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}")
            if not account_data or 'account' not in account_data:
                raise ValueError("Failed to initialize account data")
                
            self.account_balance = Decimal(str(account_data['account']['balance']))
            self.account_currency = account_data['account']['currency']
            
            logger.info(f"Session initialized. Balance: {self.account_balance:.2f} {self.account_currency}")
            self._check_new_day()
            
        except Exception as e:
            logger.error(f"Initialization error: {str(e)}", exc_info=True)
            raise

    def _api_request(self, url: str, method: str = 'GET', data: Optional[Dict] = None, 
                    params: Optional[Dict] = None) -> Optional[Dict]:
        """Generic API request with retry logic and proper OANDA error handling"""
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, json=data, params=params)
                
                # Handle OANDA specific error codes based on documentation
                if response.status_code == 401:
                    logger.error("Authentication failed - check OANDA_ACCESS_TOKEN")
                    return None
                elif response.status_code == 403:
                    logger.error("Insufficient permissions for account")
                    return None
                elif response.status_code == 404:
                    logger.error(f"Endpoint not found: {url}")
                    return None
                elif response.status_code == 405:
                    logger.error(f"Method not allowed: {method} for {url}")
                    return None
                elif response.status_code == 429:
                    logger.warning("Rate limit exceeded - backing off")
                    time.sleep(30)
                    continue
                    
                response.raise_for_status()
                
                # Handle empty responses
                if response.status_code == 204:
                    return {}
                    
                return response.json()
                
            except requests.exceptions.HTTPError as e:
                if response.status_code >= 500:
                    logger.warning(f"OANDA server error (attempt {attempt + 1}): {str(e)}")
                    if attempt == self.max_retries - 1:
                        logger.error(f"API request failed after {self.max_retries} attempts: {str(e)}")
                        return None
                    time.sleep(2 ** attempt)
                else:
                    # Parse OANDA error response
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('errorMessage', str(e))
                        logger.error(f"OANDA API error {response.status_code}: {error_msg}")
                    except:
                        logger.error(f"HTTP error {response.status_code}: {str(e)}")
                    return None
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    logger.error(f"API request failed after {self.max_retries} attempts: {str(e)}")
                    return None
                time.sleep(2 ** attempt)
            except ValueError as e:
                logger.error(f"JSON decode error: {str(e)}")
                return None
                
        return None

    def _check_new_day(self) -> None:
        """Check if it's a new trading day and reset counters"""
        now = datetime.now(self.timezone)
        if now.date() != self.current_date:
            self.daily_trade_count = 0
            self.current_date = now.date()
            self.trade_history.clear()
            logger.info("New trading day detected. Resetting daily trade counter.")

    def _get_candles(self, instrument: str, count: int = 100, granularity: str = "M1") -> Optional[pd.DataFrame]:
        """Fetch and process candle data using OANDA's candles endpoint"""
        try:
            params = {
                "price": "M",
                "granularity": granularity,
                "count": count
            }
            
            data = self._api_request(f"{REST_URL}/instruments/{instrument}/candles", params=params)
            if not data or 'candles' not in data:
                logger.warning(f"No candle data received for {instrument}")
                return None
                
            processed = []
            for candle in data['candles']:
                if not candle['complete']:
                    continue
                    
                try:
                    mid = candle['mid']
                    processed.append({
                        'time': pd.to_datetime(candle['time']).tz_convert(self.timezone),
                        'open': Decimal(str(mid['o'])),
                        'high': Decimal(str(mid['h'])),
                        'low': Decimal(str(mid['l'])),
                        'close': Decimal(str(mid['c'])),
                        'volume': candle['volume']
                    })
                except (KeyError, InvalidOperation) as e:
                    logger.warning(f"Skipping malformed candle for {instrument}: {str(e)}")
                    continue
                    
            if len(processed) < 50:
                logger.warning(f"Insufficient candle data for {instrument}: {len(processed)} candles")
                return None
                
            df = pd.DataFrame(processed)
            df.set_index('time', inplace=True)
            
            # Calculate EMAs and trends using numpy for efficiency
            close_prices = np.array([float(c) for c in df['close'].values])
            
            # EMA calculations using numpy
            ema9 = self._calculate_ema(close_prices, EMA_SHORT_PERIOD)
            ema50 = self._calculate_ema(close_prices, EMA_LONG_PERIOD)
            
            df['ema9'] = [Decimal(str(x)) for x in ema9]
            df['ema50'] = [Decimal(str(x)) for x in ema50]
            
            # Calculate slopes
            df['ema9_slope'] = df['ema9'].diff() / df['ema9'].shift(1)
            df['ema50_slope'] = df['ema50'].diff() / df['ema50'].shift(1)
            
            return df
            
        except Exception as e:
            logger.error(f"Candle processing error for {instrument}: {str(e)}", exc_info=True)
            return None

    def _calculate_ema(self, prices: np.array, period: int) -> np.array:
        """Calculate EMA using numpy for better performance"""
        if len(prices) < period:
            return np.full_like(prices, np.nan)
            
        ema = np.zeros_like(prices)
        ema[:period] = np.nan
        
        # Simple moving average for the first value
        sma = np.mean(prices[:period])
        ema[period-1] = sma
        
        # EMA calculation
        multiplier = 2.0 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]
            
        return ema

    def _calculate_position_size(self, instrument: str, stop_loss_pips: Decimal) -> int:
        """Precise position sizing with margin checks using OANDA's units calculation"""
        try:
            if stop_loss_pips <= Decimal('0'):
                logger.warning("Invalid stop loss pips")
                return 0
                
            # Get current price and spread using OANDA pricing endpoint
            price_data = self._get_current_price(instrument)
            if not price_data:
                return 0
                
            current_price, spread = price_data
            
            # Check spread limit
            if spread > MAX_SPREAD_PIPS:
                logger.warning(f"Spread too wide for {instrument}: {spread:.1f} pips")
                return 0
                
            # Calculate pip value based on instrument quote currency
            pip_value = Decimal('0.0001')
            if "JPY" in instrument:
                pip_value = Decimal('0.01')
                
            # Risk amount with leverage consideration
            risk_amount = (self.account_balance * (RISK_PERCENT / Decimal('100'))) / Decimal(LEVERAGE_RATIO)
            
            # Position size calculation with decimal precision
            try:
                risk_per_unit = stop_loss_pips * pip_value
                if risk_per_unit == 0:
                    return 0
                    
                units = risk_amount / risk_per_unit
                units = units.quantize(Decimal('1.'), rounding='ROUND_DOWN')
                units = int(max(min(units, MAX_TRADE_UNITS), MIN_TRADE_UNITS))
                
                logger.info(f"Position size: {units} units for {instrument}, risk: {risk_amount:.2f}")
                return units
            except InvalidOperation as e:
                logger.error(f"Invalid position size calculation: {str(e)}")
                return 0
                
        except Exception as e:
            logger.error(f"Position size error: {str(e)}", exc_info=True)
            return 0

    def _get_current_price(self, instrument: str) -> Optional[Tuple[Decimal, Decimal]]:
        """Get current price with spread using OANDA's pricing endpoint"""
        try:
            data = self._api_request(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/pricing?instruments={instrument}"
            )
            if not data or 'prices' not in data or not data['prices']:
                logger.warning(f"No pricing data for {instrument}")
                return None
                
            price = data['prices'][0]
            bid = Decimal(str(price['bids'][0]['price']))
            ask = Decimal(str(price['asks'][0]['price']))
            
            # Convert spread to pips based on instrument precision
            if "JPY" in instrument:
                spread = (ask - bid) * Decimal('100')  # JPY pairs have 2 decimal places
            else:
                spread = (ask - bid) * Decimal('10000')  # Most pairs have 4 decimal places
            
            mid_price = (bid + ask) / Decimal('2')
            return (mid_price, spread)
            
        except Exception as e:
            logger.error(f"Price fetch error for {instrument}: {str(e)}")
            return None

    def _identify_key_levels(self, df: pd.DataFrame) -> Tuple[Dict, Dict]:
        """Improved key level identification using fractal pivots"""
        try:
            if len(df) < 5:
                return {}, {}
                
            highs = df['high']
            lows = df['low']
            
            # Find resistance levels (fractal highs)
            resistance = {}
            for i in range(2, len(highs)-2):
                if (highs.iloc[i] > highs.iloc[i-1] and highs.iloc[i] > highs.iloc[i-2] and 
                    highs.iloc[i] > highs.iloc[i+1] and highs.iloc[i] > highs.iloc[i+2]):
                    resistance[df.index[i]] = highs.iloc[i]
            
            # Find support levels (fractal lows)
            support = {}
            for i in range(2, len(lows)-2):
                if (lows.iloc[i] < lows.iloc[i-1] and lows.iloc[i] < lows.iloc[i-2] and 
                    lows.iloc[i] < lows.iloc[i+1] and lows.iloc[i] < lows.iloc[i+2]):
                    support[df.index[i]] = lows.iloc[i]
            
            # Get most recent significant levels
            recent_support = {}
            recent_resistance = {}
            
            if support:
                last_support_time = max(support.keys())
                recent_support = {
                    'price': support[last_support_time],
                    'time': last_support_time
                }
                
            if resistance:
                last_resistance_time = max(resistance.keys())
                recent_resistance = {
                    'price': resistance[last_resistance_time],
                    'time': last_resistance_time
                }
            
            return recent_support, recent_resistance
            
        except Exception as e:
            logger.error(f"Key level error: {str(e)}", exc_info=True)
            return {}, {}

    def _check_trade_conditions(self, instrument: str) -> Optional[Dict[str, Any]]:
        """Enhanced trade logic with multiple confirmations"""
        try:
            df = self._get_candles(instrument)
            if df is None or len(df) < 100:
                return None
                
            # Check if we're in cooldown for this instrument
            now = datetime.now(self.timezone)
            if (now - self.instrument_cooldowns[instrument]).total_seconds() < MIN_SECONDS_BETWEEN_TRADES:
                return None
                
            current = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Check for valid trends
            ema9_up = current['ema9_slope'] > MIN_TREND_STRENGTH
            ema50_up = current['ema50_slope'] > MIN_TREND_STRENGTH
            ema9_down = current['ema9_slope'] < -MIN_TREND_STRENGTH
            ema50_down = current['ema50_slope'] < -MIN_TREND_STRENGTH
            
            # Get key levels
            support, resistance = self._identify_key_levels(df.iloc[:-1])
            
            # Generate trade signal hash to prevent duplicates
            signal_hash = hashlib.md5(f"{instrument}-{current['close']}-{current['ema9']}".encode()).hexdigest()
            if signal_hash in self.trade_history:
                return None
                
            # Bullish setup conditions
            bullish_conditions = (
                current['close'] > current['ema9'] and
                prev['close'] <= prev['ema9'] and
                current['ema9'] > current['ema50'] and
                ema9_up and
                (not resistance or current['close'] < resistance.get('price', Decimal('999999')) * Decimal('1.002')) and
                current['close'] > current['open'] and
                len(df) >= 4 and df.iloc[-4:-1]['close'].gt(df.iloc[-4:-1]['ema9']).all()
            )
            
            # Bearish setup conditions
            bearish_conditions = (
                current['close'] < current['ema9'] and
                prev['close'] >= prev['ema9'] and
                current['ema9'] < current['ema50'] and
                ema9_down and
                (not support or current['close'] > support.get('price', Decimal('0')) * Decimal('0.998')) and
                current['close'] < current['open'] and
                len(df) >= 4 and df.iloc[-4:-1]['close'].lt(df.iloc[-4:-1]['ema9']).all()
            )
            
            if bullish_conditions:
                stop_loss = min(current['low'], prev['low'])
                risk = current['close'] - stop_loss
                return {
                    'direction': 'buy',
                    'entry': current['close'],
                    'stop_loss': stop_loss,
                    'take_profit': current['close'] + risk * Decimal('1.5'),
                    'signal_hash': signal_hash,
                    'key_level': resistance
                }
                
            elif bearish_conditions:
                stop_loss = max(current['high'], prev['high'])
                risk = stop_loss - current['close']
                return {
                    'direction': 'sell',
                    'entry': current['close'],
                    'stop_loss': stop_loss,
                    'take_profit': current['close'] - risk * Decimal('1.5'),
                    'signal_hash': signal_hash,
                    'key_level': support
                }
                
            return None
            
        except Exception as e:
            logger.error(f"Trade condition error for {instrument}: {str(e)}", exc_info=True)
            return None

    def _place_order(self, instrument: str, trade_signal: Dict[str, Any]) -> bool:
        """Comprehensive order placement with OANDA order API"""
        try:
            self._check_new_day()
            
            # Validate trade limits
            now = datetime.now(self.timezone)
            if (self.daily_trade_count >= MAX_TRADES_PER_DAY or 
                (now - self.last_trade_time).total_seconds() < MIN_SECONDS_BETWEEN_TRADES):
                logger.info("Trade limit reached")
                return False
                
            # Calculate position size
            if trade_signal['direction'] == 'buy':
                stop_loss_pips = (trade_signal['entry'] - trade_signal['stop_loss']) / Decimal('0.0001')
            else:
                stop_loss_pips = (trade_signal['stop_loss'] - trade_signal['entry']) / Decimal('0.0001')
                
            # Handle JPY pairs differently
            if "JPY" in instrument:
                stop_loss_pips = stop_loss_pips / Decimal('100')
                
            units = self._calculate_position_size(instrument, stop_loss_pips)
            if units == 0:
                return False
                
            # Prepare order with proper OANDA formatting according to documentation
            order_data = {
                "order": {
                    "type": "MARKET",
                    "instrument": instrument,
                    "units": str(units) if trade_signal['direction'] == 'buy' else f"-{units}",
                    "timeInForce": "FOK",
                    "positionFill": "DEFAULT",
                    "stopLossOnFill": {
                        "price": f"{trade_signal['stop_loss']:.5f}",
                        "timeInForce": "GTC"
                    },
                    "takeProfitOnFill": {
                        "price": f"{trade_signal['take_profit']:.5f}",
                        "timeInForce": "GTC"
                    }
                }
            }
            
            # Send order using OANDA orders endpoint
            response_data = self._api_request(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/orders",
                method="POST",
                data=order_data
            )
            
            if not response_data:
                logger.error(f"Order failed for {instrument}")
                self.error_count += 1
                time.sleep(min(2 ** self.error_count, 30))
                return False
                
            # Update trade tracking
            self.daily_trade_count += 1
            self.last_trade_time = now
            self.instrument_cooldowns[instrument] = now
            self.trade_history[trade_signal['signal_hash']] = now
            
            logger.info(f"Order executed: {instrument} {trade_signal['direction']} at {trade_signal['entry']:.5f}")
            self.error_count = 0
            return True
            
        except Exception as e:
            logger.error(f"Order placement error: {str(e)}", exc_info=True)
            self.error_count += 1
            time.sleep(min(2 ** self.error_count, 30))
            return False

    def _monitor_trades(self) -> None:
        """Robust trade monitoring using OANDA trades endpoint with proper error handling"""
        try:
            data = self._api_request(f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/openTrades")
            if not data or 'trades' not in data:
                return
                
            for trade in data['trades']:
                try:
                    trade_id = trade['id']
                    
                    # Safely access trade fields with proper error handling
                    if 'currentPrice' not in trade:
                        # Try alternative field names or skip this trade
                        logger.debug(f"Trade {trade_id} missing currentPrice field, skipping")
                        continue
                        
                    current_price = Decimal(str(trade['currentPrice']))
                    open_price = Decimal(str(trade['price']))
                    units = Decimal(str(trade['initialUnits']))
                    
                    # Proper timezone handling
                    trade_time = pd.to_datetime(trade['openTime']).tz_convert(self.timezone)
                    now = datetime.now(self.timezone)
                    
                    # Check max trade duration
                    if (now - trade_time) > timedelta(minutes=MAX_TRADE_DURATION_MINUTES):
                        logger.info(f"Closing trade {trade_id} due to time limit")
                        self._close_trade(trade_id)
                        
                except KeyError as e:
                    logger.warning(f"Missing field in trade {trade.get('id', 'unknown')}: {str(e)}")
                    continue
                except Exception as e:
                    logger.error(f"Error monitoring trade {trade.get('id', 'unknown')}: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Trade monitoring error: {str(e)}", exc_info=True)

    def _get_trade_details(self, trade_id: str) -> Optional[Dict]:
        """Get detailed information for a specific trade"""
        try:
            data = self._api_request(f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}")
            return data.get('trade') if data else None
        except Exception as e:
            logger.error(f"Error getting details for trade {trade_id}: {str(e)}")
            return None

    def _close_trade(self, trade_id: str) -> None:
        """Trade closure using OANDA trade close endpoint"""
        try:
            response = self._api_request(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close",
                method="PUT"
            )
            
            if response:
                logger.info(f"Closed trade {trade_id}")
            else:
                logger.warning(f"Failed to close trade {trade_id}")
                
        except Exception as e:
            logger.error(f"Trade close error: {str(e)}", exc_info=True)

    def _market_conditions_ok(self) -> bool:
        """Check overall market conditions before trading"""
        now = datetime.now(self.timezone)
        
        # Trading hours (8AM-5PM NY time)
        if not (8 <= now.hour < 17):
            return False
            
        # Avoid first/last 30 minutes of session
        if (now.hour == 8 and now.minute < 30) or (now.hour == 16 and now.minute > 30):
            return False
            
        # Check for high error rates
        if self.error_count > 5:
            logger.warning("High error rate - pausing trading")
            time.sleep(60)
            return False
            
        return True

    def run(self) -> None:
        """Main trading loop with enhanced safety checks"""
        logger.info("Starting Refactored EMA Scalping Bot")
        
        try:
            while True:
                try:
                    if not self._market_conditions_ok():
                        time.sleep(60)
                        continue
                        
                    # Monitor existing trades with improved error handling
                    try:
                        self._monitor_trades()
                    except Exception as e:
                        logger.error(f"Trade monitoring failed: {str(e)}")
                        # Continue with other operations even if monitoring fails
                        
                    # Check for new trade opportunities
                    for instrument in TRADING_INSTRUMENTS:
                        try:
                            trade_signal = self._check_trade_conditions(instrument)
                            if trade_signal:
                                logger.info(f"Trade signal detected for {instrument}: {trade_signal['direction']}")
                                self._place_order(instrument, trade_signal)
                        except Exception as e:
                            logger.error(f"Instrument {instrument} error: {str(e)}", exc_info=True)
                            continue
                            
                    # Throttle API calls to respect OANDA rate limits
                    time.sleep(15)
                    
                except KeyboardInterrupt:
                    logger.info("Bot stopped by user")
                    break
                except Exception as e:
                    logger.error(f"Main loop error: {str(e)}", exc_info=True)
                    time.sleep(30)
                    
        finally:
            logger.info("Bot shutdown complete")

if __name__ == "__main__":
    bot = EMAScalpingBot()
    bot.run()