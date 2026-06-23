import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging
from typing import Dict, Optional, List, Tuple
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import pytz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('fallingwedge.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration - All required OANDA API parameters
OANDA_ACCOUNT_ID = os.getenv('OANDA_ACCOUNT_ID', 'your-account-id')
OANDA_ACCESS_TOKEN = os.getenv('OANDA_ACCESS_TOKEN', 'your-access-token')
TRADING_INSTRUMENTS = os.getenv('TRADING_INSTRUMENTS', 'EUR_USD,USD_JPY,GBP_USD').split(',')
REST_URL = "https://api-fxtrade.oanda.com/v3"

RSI_PERIOD = 7
RSI_OVERSOLD = 30
SL_PIPS = 20
TP_PIPS = 40
RISK_PERCENT = 2.0
GRANULARITY = "H1"
CANDLE_COUNT = 100
MIN_ACCOUNT_BALANCE = 10
SWING_LENGTH = 5
TIMEZONE = 'America/New_York'
TRADING_TIMEZONE = pytz.timezone(TIMEZONE)

class OandaAPIClient:
    """Complete OANDA API client with all required parameters"""
    
    def __init__(self):
        self._max_retries = 3
        self._request_delay = 0.15  # 150ms between requests
        self._timeout = 10
        self._last_request = 0
        self.session = self._configure_session()
        
    def _configure_session(self) -> requests.Session:
        """Configure session with all required OANDA headers"""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=self._max_retries,
            backoff_factor=1,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"]
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
            pool_block=True
        )
        session.mount("https://", adapter)
        
        session.headers.update({
            "Authorization": f"Bearer {OANDA_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
            "Connection": "keep-alive"
        })
        
        return session
    
    def _rate_limit(self):
        """Enforce rate limiting per OANDA guidelines"""
        elapsed = time.time() - self._last_request
        if elapsed < self._request_delay:
            time.sleep(self._request_delay - elapsed)
        self._last_request = time.time()
    
    def _handle_api_error(self, error: requests.exceptions.RequestException) -> None:
        """Handle API errors according to OANDA troubleshooting guide"""
        if hasattr(error, 'response') and error.response is not None:
            try:
                error_data = error.response.json()
                logger.error(
                    f"API Error: {error_data.get('errorMessage', 'Unknown error')} "
                    f"(Code: {error_data.get('errorCode', 'N/A')})"
                )
            except ValueError:
                logger.error(f"API Error: {error.response.text[:200]}")
        else:
            logger.error(f"Network Error: {str(error)}")
    
    def get_account_details(self) -> Optional[Dict]:
        """Get complete account details with all required fields"""
        self._rate_limit()
        
        try:
            response = self.session.get(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}",
                params={
                    "fields": "orders,positions,guaranteedStopLossOrderMode"
                },
                timeout=self._timeout
            )
            response.raise_for_status()
            return response.json().get('account', {})
        except requests.exceptions.RequestException as e:
            self._handle_api_error(e)
            return None
    
    def get_account_balance(self) -> Optional[float]:
        """Get account balance with proper error handling"""
        account_details = self.get_account_details()
        if account_details:
            return float(account_details.get('balance', 0))
        return None
    
    def get_open_positions(self) -> Optional[List[Dict]]:
        """Get open positions with all required fields"""
        self._rate_limit()
        
        try:
            response = self.session.get(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/openPositions",
                timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
            return data.get('positions', [])
        except requests.exceptions.RequestException as e:
            self._handle_api_error(e)
            return None
    
    def get_pricing(self, instruments: List[str]) -> Optional[Dict]:
        """Get pricing with all required parameters"""
        self._rate_limit()
        
        try:
            response = self.session.get(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/pricing",
                params={
                    "instruments": ",".join(instruments),
                    "includeHomeConversions": "true",
                    "includeUnitsAvailable": "true"
                },
                timeout=self._timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self._handle_api_error(e)
            return None
    
    def get_candles(self, instrument: str) -> Optional[pd.DataFrame]:
        """Get candle data with all required OANDA parameters"""
        self._rate_limit()
        
        try:
            # Calculate from time based on candle count and granularity
            now = datetime.now(TRADING_TIMEZONE)
            if GRANULARITY == "H4":
                delta = timedelta(hours=CANDLE_COUNT * 4)
            elif GRANULARITY == "D":
                delta = timedelta(days=CANDLE_COUNT)
            else:
                delta = timedelta(hours=CANDLE_COUNT)
            
            from_time = (now - delta).isoformat()
            
            response = self.session.get(
                f"{REST_URL}/instruments/{instrument}/candles",
                params={
                    "granularity": GRANULARITY,
                    "price": "BA",
                    "from": from_time,
                    "count": CANDLE_COUNT,
                    "smooth": "false",
                    "dailyAlignment": "0",
                    "alignmentTimezone": TIMEZONE
                },
                timeout=self._timeout
            )
            response.raise_for_status()
            
            candles = []
            for candle in response.json().get('candles', []):
                try:
                    bid = candle['bid']
                    ask = candle['ask']
                    
                    candles.append({
                        'time': pd.to_datetime(candle['time']),
                        'open': (float(bid['o']) + float(ask['o'])) / 2,
                        'high': (float(bid['h']) + float(ask['h'])) / 2,
                        'low': (float(bid['l']) + float(ask['l'])) / 2,
                        'close': (float(bid['c']) + float(ask['c'])) / 2,
                        'volume': int(candle['volume']),
                        'complete': candle['complete'],
                        'timezone': TIMEZONE
                    })
                except (KeyError, ValueError) as e:
                    logger.warning(f"Invalid candle data: {e}")
                    continue
            
            if not candles:
                return None
                
            df = pd.DataFrame(candles).set_index('time')
            return df[['open', 'high', 'low', 'close', 'volume', 'complete', 'timezone']]
            
        except requests.exceptions.RequestException as e:
            self._handle_api_error(e)
            return None
    
    def place_market_order(self, instrument: str, units: int,
                         stop_loss: float, take_profit: float) -> bool:
        """Place market order with all required OANDA fields"""
        self._rate_limit()
        
        precision = 3 if 'JPY' in instrument else 5
        
        order = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {
                    "price": f"{stop_loss:.{precision}f}",
                    "timeInForce": "GTC",
                    "guaranteed": False
                },
                "takeProfitOnFill": {
                    "price": f"{take_profit:.{precision}f}",
                    "timeInForce": "GTC"
                },
                "clientExtensions": {
                    "id": f"FW_{instrument}_{int(time.time())}",
                    "tag": "falling_wedge_strategy",
                    "comment": "Automated falling wedge strategy"
                },
                "tradeClientExtensions": {
                    "id": f"TRADE_FW_{instrument}_{int(time.time())}",
                    "tag": "falling_wedge_trade",
                    "comment": "Automated trade from falling wedge strategy"
                }
            }
        }
        
        try:
            response = self.session.post(
                f"{REST_URL}/accounts/{OANDA_ACCOUNT_ID}/orders",
                json=order,
                timeout=self._timeout
            )
            response.raise_for_status()
            
            # Verify order fill according to OANDA specs
            order_data = response.json()
            if order_data.get('orderFillTransaction', {}).get('type') == 'ORDER_FILL':
                logger.info(f"Order executed: {order_data.get('orderFillTransaction', {}).get('id')}")
                return True
            return False
                
        except requests.exceptions.RequestException as e:
            self._handle_api_error(e)
            return False

class TechnicalAnalyzer:
    """Technical analysis with enhanced validation"""
    
    @staticmethod
    def calculate_rsi(data: pd.DataFrame, period: int = 14) -> Optional[pd.Series]:
        """Calculate RSI with proper validation"""
        if not isinstance(data, pd.DataFrame) or 'close' not in data.columns:
            logger.warning("Invalid data format for RSI calculation")
            return None
            
        if len(data) < period * 2:
            logger.warning(f"Insufficient data for RSI (needs {period*2}, got {len(data)})")
            return None
            
        try:
            close_prices = data['close'].values
            deltas = np.diff(close_prices)
            
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = pd.Series(gains).ewm(
                alpha=1/period,
                min_periods=period,
                adjust=False
            ).mean().values
            
            avg_loss = pd.Series(losses).ewm(
                alpha=1/period,
                min_periods=period,
                adjust=False
            ).mean().values
            
            rs = np.divide(
                avg_gain,
                avg_loss,
                out=np.ones_like(avg_gain),
                where=avg_loss!=0
            )
            rsi = 100 - (100 / (1 + rs))
            
            rsi_series = np.empty(len(data))
            rsi_series[:] = np.nan
            rsi_series[period:] = rsi[(period-1):]
            
            return pd.Series(rsi_series, index=data.index)
            
        except Exception as e:
            logger.error(f"RSI calculation failed: {e}")
            return None
    
    @staticmethod
    def detect_falling_wedge(data: pd.DataFrame, swing_length: int = 5) -> Tuple[bool, Dict]:
        """Detect falling wedge with detailed analysis"""
        if not isinstance(data, pd.DataFrame) or len(data) < 3 * swing_length:
            logger.debug("Insufficient data for wedge detection")
            return False, {}
            
        try:
            highs = data['high'].rolling(
                window=swing_length,
                center=True,
                min_periods=swing_length
            ).max().dropna()
            
            lows = data['low'].rolling(
                window=swing_length,
                center=True,
                min_periods=swing_length
            ).min().dropna()
            
            if len(highs) < 3 or len(lows) < 3:
                logger.debug("Not enough swings for wedge detection")
                return False, {}
                
            recent_highs = highs.iloc[-5:]
            recent_lows = lows.iloc[-5:]
            
            high_x = np.arange(len(recent_highs))
            high_slope, _ = np.polyfit(high_x, recent_highs.values, 1)
            
            low_x = np.arange(len(recent_lows))
            low_slope, _ = np.polyfit(low_x, recent_lows.values, 1)
            
            both_down = (high_slope < 0 and low_slope < 0)
            converging = (abs(high_slope) > abs(low_slope))
            height_ratio = ((recent_highs.iloc[-1] - recent_highs.iloc[0]) / 
                          (recent_lows.iloc[-1] - recent_lows.iloc[0]))
            
            wedge_detected = (both_down and converging and height_ratio < 0.8)
            
            return wedge_detected, {
                'high_slope': high_slope,
                'low_slope': low_slope,
                'height_ratio': height_ratio,
                'recent_highs': recent_highs.tolist(),
                'recent_lows': recent_lows.tolist()
            }
            
        except Exception as e:
            logger.error(f"Wedge detection failed: {e}")
            return False, {}

class TradingStrategy:
    """Complete trading strategy with all OANDA best practices"""
    
    def __init__(self):
        self.api = OandaAPIClient()
        self.tech = TechnicalAnalyzer()
        self.timezone = TRADING_TIMEZONE
        self._validate_environment()
        self._last_execution_time = None
    
    def _validate_environment(self):
        """Validate all required environment settings"""
        if not OANDA_ACCOUNT_ID or not OANDA_ACCESS_TOKEN:
            raise ValueError("Missing OANDA account credentials")
        
        if not TRADING_INSTRUMENTS:
            raise ValueError("No trading instruments specified")
        
        # Test API connectivity
        if not self.api.get_account_balance():
            raise ConnectionError("Failed to connect to OANDA API")
    
    def run(self):
        """Main trading loop with proper timezone handling"""
        logger.info("Starting strategy")
        
        try:
            while True:
                current_time = datetime.now(self.timezone)
                
                if self._should_execute(current_time):
                    self._execute_trading_cycle()
                    self._last_execution_time = current_time
                
                time.sleep(60)
                
        except KeyboardInterrupt:
            logger.info("Strategy stopped by user")
        except Exception as e:
            logger.critical(f"Fatal error: {e}", exc_info=True)
    
    def _should_execute(self, current_time: datetime) -> bool:
        """Determine if we should execute based on candle timing"""
        if self._last_execution_time is None:
            return True
            
        current_hour = current_time.hour
        if current_hour % 4 == 0 and current_time.minute < 15:
            if (current_time - self._last_execution_time) >= timedelta(hours=4):
                return True
                
        return False
    
    def _execute_trading_cycle(self):
        """Execute one complete trading cycle"""
        current_time = datetime.now(self.timezone)
        logger.info(f"\n=== Starting trading cycle at {current_time} ===")
        
        # 1. Check account balance
        balance = self.api.get_account_balance()
        if not balance:
            logger.error("Failed to get account balance")
            return
            
        if balance < MIN_ACCOUNT_BALANCE:
            logger.warning(f"Balance too low: ${balance:.2f} < ${MIN_ACCOUNT_BALANCE}")
            return
        logger.info(f"Account balance: ${balance:.2f}")

        # 2. Get open positions
        open_positions = self.api.get_open_positions()
        positioned_instruments = [p['instrument'] for p in open_positions] if open_positions else []
        logger.info(f"Current positions: {positioned_instruments or 'None'}")

        # 3. Process each instrument
        for instrument in TRADING_INSTRUMENTS:
            try:
                logger.info(f"\nAnalyzing {instrument}...")
                
                if instrument in positioned_instruments:
                    logger.info("Already in position - skipping")
                    continue
                    
                self._process_instrument(instrument, balance)
                
            except Exception as e:
                logger.error(f"Error processing {instrument}: {str(e)}", exc_info=True)
                continue

        logger.info("=== Trading cycle completed ===\n")
    
    def _process_instrument(self, instrument: str, balance: float):
        """Process an instrument with timezone-aware checking"""
        candles = self.api.get_candles(instrument)
        if candles is None:
            logger.warning("Failed to get candle data")
            return
            
        # Check candle completion with timezone context
        last_candle_time = candles.index[-1].astimezone(self.timezone)
        now = datetime.now(self.timezone)
        
        logger.info(f"Last candle time: {last_candle_time}")
        logger.info(f"Current time: {now}")
        
        if not candles.iloc[-1]['complete']:
            time_since_candle = now - last_candle_time
            logger.info(
                f"Current {GRANULARITY} candle not complete - "
                f"Started {time_since_candle.total_seconds()/3600:.1f} hours ago"
            )
            return
            
        # Calculate indicators
        candles['rsi'] = self.tech.calculate_rsi(candles)
        if candles['rsi'].isna().all():
            logger.warning("RSI calculation failed")
            return
            
        current_rsi = candles['rsi'].iloc[-1]
        logger.info(f"Current RSI: {current_rsi:.2f}")
        
        # Pattern detection
        wedge_detected, wedge_analysis = self.tech.detect_falling_wedge(candles)
        logger.info(
            f"Wedge analysis - High slope: {wedge_analysis.get('high_slope', 0):.4f}, "
            f"Low slope: {wedge_analysis.get('low_slope', 0):.4f}, "
            f"Height ratio: {wedge_analysis.get('height_ratio', 0):.2f}"
        )
        
        # Check trading conditions
        rsi_condition = current_rsi < RSI_OVERSOLD
        logger.info(f"RSI condition ({current_rsi:.2f} < {RSI_OVERSOLD}): {rsi_condition}")
        logger.info(f"Wedge detected: {wedge_detected}")
        
        if wedge_detected and rsi_condition:
            self._execute_trade(instrument, candles['close'].iloc[-1], balance)
        else:
            logger.info("No valid trade signal detected")
    
    def _execute_trade(self, instrument: str, price: float, balance: float):
        """Execute a trade with proper risk management"""
        pip_value = 0.01 if 'JPY' in instrument else 0.0001
        stop_loss = price - SL_PIPS * pip_value
        take_profit = price + TP_PIPS * pip_value
        
        # Calculate position size based on risk
        risk_amount = balance * (RISK_PERCENT / 100)
        units = int(risk_amount / (SL_PIPS * pip_value))
        
        # Round to nearest 1000 units (OANDA standard)
        units = (units // 1000) * 1000
        
        if units < 1000:
            logger.warning(f"Position size too small: {units} units")
            return
        
        logger.info(
            f"Preparing trade: {instrument} {units} units\n"
            f"Entry: {price:.5f}, SL: {stop_loss:.5f}, TP: {take_profit:.5f}\n"
            f"Risk: ${risk_amount:.2f} ({RISK_PERCENT}% of balance)"
        )
        
        success = self.api.place_market_order(
            instrument=instrument,
            units=units,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        if success:
            logger.info(f"Trade executed for {instrument}")
        else:
            logger.warning(f"Trade failed for {instrument}")

if __name__ == "__main__":
    try:
        strategy = TradingStrategy()
        strategy.run()
    except Exception as e:
        logger.critical(f"Failed to start strategy: {e}", exc_info=True)