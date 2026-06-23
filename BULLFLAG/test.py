import requests
import pandas as pd

def fetch_candles(self, instrument: str, count: int = 50, granularity: str = "M5") -> pd.DataFrame:
    """Fetch historical candles for pattern detection"""
    try:
        url = f"{self.REST_URL}/instruments/{instruments}/candles"
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
        'high': float(c['mid']['h']),  # Also, 'high' should be c['mid']['h'] instead of c['mid']['o']
        'low': float(c['mid']['l']),  # Also, 'low' should be c['mid']['l'] instead of c['mid']['o']
        'close': float(c['mid']['c']),  # Also, 'close' should be c['mid']['c'] instead of c['mid']['o']
        'volume': int(c['volume']),
        'complete': c['complete']
    } for c in candles])

        # Filter to only use complete candles for pattern detection \
        df = df[df['complete']]

        # Explicitly specify the datetime format for OANDA's RFC3339 format
        df['time'] = pd.to_datetime(df['time'], format='%Y-%m-%dT%H:%M:%S.%fZ')
        df.set_index('time', inplace=True)

        return df    
    except requests.exceptions.RequestException as e:
        self.logger.error(f"Error fetching candles for {instrument}: {e}")
        return pd.DataFrame([{
            'time': pd.Timestamp.now(),
            'open': 0,
            'high': 0,
            'low': 0,
            'close': 0,
            'volume': 0,
            'complete': False   
        }])
    
    except Exception as e:
        self.logger.error(f"Error processing candles for {instrument}: {e}")
        return pd.DataFrame([{
            'time': pd.Timestamp.now(),
            'open': 0,
            'high': 0,
            'low': 0,
            'close': 0,
            'volume': 0,
            'complete': False   
        }])
    
def get_current_price(self, instrument: str) -> float:
    url = f"{self.REST_URL}/instruments/{instrument}/prices"
    response = self.session.get(url)
    response.raise_for_status()
    data = response.json()
    return float(data['prices'][0]['bid']['o'])         