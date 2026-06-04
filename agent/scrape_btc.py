import os
import time
import pandas as pd
import requests
from datetime import datetime

def fetch_from_yahoo(start_time, end_time, headers):
    url = f"https://query1.finance.yahoo.com/v7/finance/download/BTC-USD?period1={start_time}&period2={end_time}&interval=1d&events=history&includeAdjustedClose=true"
    print(f"📡 Спроба завантаження денних даних BTC-USD з Yahoo Finance...")
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.content
    else:
        print(f"⚠️ Yahoo Finance повернув код помилки {response.status_code}. Переходимо на резервне джерело...")
        return None

def fetch_from_binance(limit_candles=2000):
    print(f"📡 Завантаження денних даних BTC-USDT з резервного джерела (Binance API)...")
    
    all_klines = []
    end_time = None
    
    while len(all_klines) < limit_candles:
        url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000"
        if end_time:
            url += f"&endTime={end_time}"
            
        response = requests.get(url)
        if response.status_code != 200:
            print(f"❌ Не вдалося завантажити дані з Binance API. Код помилки: {response.status_code}")
            break
            
        klines = response.json()
        if not klines:
            break
            
        # klines are in chronological order. We prepend them to our list
        all_klines = klines + all_klines
        
        # Set endTime to the open time of the oldest candle minus 1ms
        end_time = klines[0][0] - 1
        
        # If we received less than 1000 candles, there are no more historical candles
        if len(klines) < 1000:
            break
            
    # Crop to requested limit if we fetched more
    all_klines = all_klines[-limit_candles:]
    
    data_rows = []
    for k in all_klines:
        dt = datetime.utcfromtimestamp(k[0] / 1000.0).strftime('%Y-%m-%d')
        open_p = float(k[1])
        high_p = float(k[2])
        low_p = float(k[3])
        close_p = float(k[4])
        volume = float(k[5])
        amount = float(k[7]) # Quote asset volume
        
        data_rows.append({
            'Date': dt,
            'Open': open_p,
            'High': high_p,
            'Low': low_p,
            'Close': close_p,
            'Adj Close': close_p,
            'Volume': volume,
            'Amount': amount
        })
    
    df = pd.DataFrame(data_rows)
    return df.to_csv(index=False).encode('utf-8')

def scrape_btc_data():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    # Період: останні 5 років
    end_time = int(time.time())
    start_time = end_time - (5 * 365 * 24 * 60 * 60)

    # 1. Намагаємось завантажити з Yahoo
    csv_bytes = fetch_from_yahoo(start_time, end_time, headers)
    
    # 2. Якщо Yahoo заблокував (429/403), використовуємо безкоштовний Binance API
    if csv_bytes is None:
        csv_bytes = fetch_from_binance()
        
    if csv_bytes is None:
        print("❌ Не вдалося отримати дані з жодного джерела.")
        return False

    # Зберігаємо сирі дані у файл BTC-USD.csv
    raw_file = "BTC-USD.csv"
    with open(raw_file, "wb") as f:
        f.write(csv_bytes)
    print(f"✅ Сирі дані (у форматі Yahoo Finance) збережено у: {raw_file}")

    # 3. Форматуємо дані під вимоги моделі Kronos
    print("🧹 Форматування даних для моделі...")
    df = pd.read_csv(raw_file)

    # Видаляємо колонку Adj Close, якщо вона присутня
    if 'Adj Close' in df.columns:
        df = df.drop(columns=['Adj Close'])

    # Перейменовуємо колонки згідно з інструкцією
    rename_mapping = {
        'Date': 'timestamps',
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume',
        'Amount': 'amount'
    }
    df = df.rename(columns=rename_mapping)

    # Якщо колонка amount відсутня, вираховуємо її як volume * close
    if 'amount' not in df.columns:
        df['amount'] = df['volume'] * df['close']

    # Забезпечуємо вірний формат дати та сортування
    df['timestamps'] = pd.to_datetime(df['timestamps'])
    df = df.sort_values('timestamps').reset_index(drop=True)

    # Визначаємо дати початку та кінця періоду для імені файлу
    start_date_str = df['timestamps'].min().strftime('%Y%m%d')
    end_date_str = df['timestamps'].max().strftime('%Y%m%d')
    
    # Визначаємо шлях до папки data у корені проекту
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    # Формуємо стале ім'я файлу
    formatted_file_name = "btc_history_daily.csv"
    formatted_file_path = os.path.join(data_dir, formatted_file_name)
    
    df.to_csv(formatted_file_path, index=False)
    print(f"✅ Відформатовані дані збережено у: {formatted_file_path}")
    
    print("\n📊 Останні 5 записів відформатованого файлу:")
    print(df.tail())
    return True

if __name__ == "__main__":
    # Встановлюємо робочу директорію в директорію скрипта
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    scrape_btc_data()
