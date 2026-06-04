import os
import pandas as pd
import numpy as np
import json
import plotly.graph_objects as go
import plotly.utils
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import sys
import warnings
import datetime
import subprocess
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from .env file
load_dotenv()

warnings.filterwarnings('ignore')

# Add project root directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from model import Kronos, KronosTokenizer, KronosPredictor
    MODEL_AVAILABLE = True
except ImportError:
    MODEL_AVAILABLE = False
    print("Warning: Kronos model cannot be imported, will use simulated data for demonstration")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "default-secret-key-change-me")
CORS(app)

# Initialize Supabase client
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY and "your-project-id" not in SUPABASE_URL:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("🚀 Supabase client initialized successfully")
    except Exception as e:
        print(f"❌ Error initializing Supabase client: {str(e)}")
else:
    print("⚠️ Supabase credentials not configured in .env. Auth and DB features will be unavailable.")


# Global variables to store models
tokenizer = None
model = None
predictor = None

# Global state for background fine-tuning
finetune_process = None
finetune_state = {
    'is_running': False,
    'pid': None,
    'start_time': None,
    'epochs': 0,
    'current_epoch': 0,
    'progress_percent': 0,
    'log_file': None,
    'error': None,
    'success': False
}

def load_model_by_key(model_key, device='cpu'):
    """Load model programmatically by key"""
    global tokenizer, model, predictor
    if not MODEL_AVAILABLE:
        raise RuntimeError("Kronos model library not available")
    if model_key not in AVAILABLE_MODELS:
        raise ValueError(f"Unsupported model: {model_key}")
    
    model_config = AVAILABLE_MODELS[model_key]
    tokenizer = KronosTokenizer.from_pretrained(model_config['tokenizer_id'])
    model = Kronos.from_pretrained(model_config['model_id'])
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=model_config['context_length'])
    return model_config

# Project root directory for resolving local model paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Available model configurations
AVAILABLE_MODELS = {
    'kronos-mini': {
        'name': 'Kronos-mini',
        'model_id': 'NeoQuasar/Kronos-mini',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-2k',
        'context_length': 2048,
        'params': '4.1M',
        'description': 'Легка модель, підходить для швидкого прогнозування'
    },
    'kronos-small': {
        'name': 'Kronos-small',
        'model_id': 'NeoQuasar/Kronos-small',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '24.7M',
        'description': 'Мала модель, збалансована швидкість та якість'
    },
    'kronos-base': {
        'name': 'Kronos-base',
        'model_id': 'NeoQuasar/Kronos-base',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '102.3M',
        'description': 'Базова модель, забезпечує кращу якість прогнозування'
    },
    'btc-weekly-finetuned': {
        'name': 'BTC Weekly (Донавчена)',
        'model_id': os.path.join(PROJECT_ROOT, 'finetune_csv', 'finetuned', 'btc_weekly_finetuned', 'basemodel', 'best_model'),
        'tokenizer_id': os.path.join(PROJECT_ROOT, 'finetune_csv', 'finetuned', 'btc_weekly_finetuned', 'tokenizer', 'best_model'),
        'context_length': 512,
        'params': 'Локальна 24.7M',
        'description': 'Ваша локально донавчена модель для тижневих даних BTC'
    },
    'btc-daily-finetuned': {
        'name': 'BTC Daily (Донавчена)',
        'model_id': os.path.join(PROJECT_ROOT, 'finetune_csv', 'finetuned', 'btc_daily_finetuned', 'basemodel', 'best_model'),
        'tokenizer_id': os.path.join(PROJECT_ROOT, 'finetune_csv', 'finetuned', 'btc_daily_finetuned', 'tokenizer', 'best_model'),
        'context_length': 512,
        'params': 'Локальна 24.7M',
        'description': 'Ваша локально донавчена модель для денних даних BTC'
    }
}

def load_data_files():
    """Scan data directory and return available data files"""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    data_files = []
    
    if os.path.exists(data_dir):
        for file in os.listdir(data_dir):
            if file.endswith(('.csv', '.feather')):
                file_path = os.path.join(data_dir, file)
                file_size = os.path.getsize(file_path)
                data_files.append({
                    'name': file,
                    'path': file_path,
                    'size': f"{file_size / 1024:.1f} KB" if file_size < 1024*1024 else f"{file_size / (1024*1024):.1f} MB"
                })
    
    return data_files

def load_data_file(file_path):
    """Load data file"""
    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith('.feather'):
            df = pd.read_feather(file_path)
        else:
            return None, "Unsupported file format"
        
        # Check required columns
        required_cols = ['open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            return None, f"Missing required columns: {required_cols}"
        
        # Process timestamp column
        if 'timestamps' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamps'])
        elif 'timestamp' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamp'])
        elif 'date' in df.columns:
            # If column name is 'date', rename it to 'timestamps'
            df['timestamps'] = pd.to_datetime(df['date'])
        else:
            # If no timestamp column exists, create one
            df['timestamps'] = pd.date_range(start='2024-01-01', periods=len(df), freq='1H')
        
        # Ensure numeric columns are numeric type
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Process volume column (optional)
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
        
        # Process amount column (optional, but not used for prediction)
        if 'amount' in df.columns:
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        
        # Remove rows containing NaN values
        df = df.dropna()
        
        return df, None
        
    except Exception as e:
        return None, f"Failed to load file: {str(e)}"

def save_prediction_results(file_path, prediction_type, prediction_results, actual_data, input_data, prediction_params):
    """Save prediction results to file"""
    try:
        # Create prediction results directory
        results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prediction_results')
        os.makedirs(results_dir, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'prediction_{timestamp}.json'
        filepath = os.path.join(results_dir, filename)
        
        # Prepare data for saving
        save_data = {
            'timestamp': datetime.datetime.now().isoformat(),
            'file_path': file_path,
            'prediction_type': prediction_type,
            'prediction_params': prediction_params,
            'input_data_summary': {
                'rows': len(input_data),
                'columns': list(input_data.columns),
                'price_range': {
                    'open': {'min': float(input_data['open'].min()), 'max': float(input_data['open'].max())},
                    'high': {'min': float(input_data['high'].min()), 'max': float(input_data['high'].max())},
                    'low': {'min': float(input_data['low'].min()), 'max': float(input_data['low'].max())},
                    'close': {'min': float(input_data['close'].min()), 'max': float(input_data['close'].max())}
                },
                'last_values': {
                    'open': float(input_data['open'].iloc[-1]),
                    'high': float(input_data['high'].iloc[-1]),
                    'low': float(input_data['low'].iloc[-1]),
                    'close': float(input_data['close'].iloc[-1])
                }
            },
            'prediction_results': prediction_results,
            'actual_data': actual_data,
            'analysis': {}
        }
        
        # If actual data exists, perform comparison analysis
        if actual_data and len(actual_data) > 0:
            # Calculate continuity analysis
            if len(prediction_results) > 0 and len(actual_data) > 0:
                last_pred = prediction_results[0]  # First prediction point
            first_actual = actual_data[0]      # First actual point
                
            save_data['analysis']['continuity'] = {
                    'last_prediction': {
                        'open': last_pred['open'],
                        'high': last_pred['high'],
                        'low': last_pred['low'],
                        'close': last_pred['close']
                    },
                    'first_actual': {
                        'open': first_actual['open'],
                        'high': first_actual['high'],
                        'low': first_actual['low'],
                        'close': first_actual['close']
                    },
                    'gaps': {
                        'open_gap': abs(last_pred['open'] - first_actual['open']),
                        'high_gap': abs(last_pred['high'] - first_actual['high']),
                        'low_gap': abs(last_pred['low'] - first_actual['low']),
                        'close_gap': abs(last_pred['close'] - first_actual['close'])
                    },
                    'gap_percentages': {
                        'open_gap_pct': (abs(last_pred['open'] - first_actual['open']) / first_actual['open']) * 100,
                        'high_gap_pct': (abs(last_pred['high'] - first_actual['high']) / first_actual['high']) * 100,
                        'low_gap_pct': (abs(last_pred['low'] - first_actual['low']) / first_actual['low']) * 100,
                        'close_gap_pct': (abs(last_pred['close'] - first_actual['close']) / first_actual['close']) * 100
                    }
                }
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)
        
        print(f"Prediction results saved to: {filepath}")
        return filepath
        
    except Exception as e:
        print(f"Failed to save prediction results: {e}")
        return None

def save_forecast_markdown_table(file_path, prediction_results, last_historical_close, bias_correction_info=None):
    try:
        # Create forecasts directory in project root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        forecasts_dir = os.path.join(project_root, 'forecasts')
        os.makedirs(forecasts_dir, exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        # Extract base file name to distinguish daily and weekly
        base_name = os.path.basename(file_path).split('.')[0]
        filename = f'forecast_{base_name}_{timestamp}.md'
        filepath = os.path.join(forecasts_dir, filename)
        
        # Generate markdown content
        md_content = []
        md_content.append(f"# 🔮 Прогноз курсу Bitcoin ({base_name})")
        md_content.append("")
        md_content.append(f"*Розраховано: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}*")
        md_content.append(f"*Вхідний файл: {os.path.basename(file_path)}*")
        
        if bias_correction_info:
            md_content.append("")
            md_content.append("### ⚙️ Автокорекція систематичної похибки (Bias Correction)")
            md_content.append("До значень моделі було застосовано наступне зміщення (зсув):")
            md_content.append(f"- **Відкриття (Open):** {bias_correction_info['open']:+.2f}$")
            md_content.append(f"- **Максимум (High):** {bias_correction_info['high']:+.2f}$")
            md_content.append(f"- **Мінімум (Low):** {bias_correction_info['low']:+.2f}$")
            md_content.append(f"- **Закриття (Close):** {bias_correction_info['close']:+.2f}$")
            
        md_content.append("")
        md_content.append("## 📊 Зведена таблиця прогнозів")
        md_content.append("")
        md_content.append("| Дата | Ціна відкриття ($) | Максимум ($) | Мінімум ($) | Ціна закриття ($) | Добова зміна (%) |")
        md_content.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
        
        prev_close = last_historical_close
        for pred in prediction_results:
            # Parse timestamp
            dt_raw = pred['timestamp']
            try:
                dt = datetime.datetime.fromisoformat(dt_raw).strftime('%d.%m.%Y')
            except ValueError:
                dt = dt_raw
                
            op = pred['open']
            hi = pred['high']
            lo = pred['low']
            cl = pred['close']
            
            pct_change = ((cl - prev_close) / prev_close) * 100
            change_str = f"{pct_change:+.2f}%"
            if pct_change > 0:
                change_str = "🟢 " + change_str
            elif pct_change < 0:
                change_str = "🔴 " + change_str
            else:
                change_str = "⚪ " + change_str
                
            md_content.append(f"| {dt} | {op:,.2f} | {hi:,.2f} | {lo:,.2f} | {cl:,.2f} | {change_str} |")
            prev_close = cl
            
        # Append summary analysis
        closes = [p['close'] for p in prediction_results]
        max_close = max(closes)
        min_close = min(closes)
        max_idx = closes.index(max_close)
        min_idx = closes.index(min_close)
        
        try:
            max_date = datetime.datetime.fromisoformat(prediction_results[max_idx]['timestamp']).strftime('%d.%m.%Y')
            min_date = datetime.datetime.fromisoformat(prediction_results[min_idx]['timestamp']).strftime('%d.%m.%Y')
        except ValueError:
            max_date = f"Крок {max_idx}"
            min_date = f"Крок {min_idx}"
            
        md_content.append("")
        md_content.append("## 🔍 Аналіз ключових тенденцій прогнозу")
        md_content.append("")
        md_content.append(f"- **Початкова ціна:** ${last_historical_close:,.2f}")
        md_content.append(f"- **Прогнозований пік (максимум):** **${max_close:,.2f}** (очікується **{max_date}**)")
        md_content.append(f"- **Прогнозоване дно (мінімум):** **${min_close:,.2f}** (очікується **{min_date}**)")
        md_content.append(f"- **Кінцева ціна прогнозу:** **${closes[-1]:,.2f}** (загальна зміна: **{((closes[-1] - last_historical_close) / last_historical_close)*100:+.2f}%**)")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_content))
            
        print(f"Forecast markdown table saved to: {filepath}")
        return filepath
    except Exception as e:
        print(f"Failed to save forecast markdown table: {e}")
        return None

def create_prediction_chart(df, pred_df, lookback, pred_len, actual_df=None, historical_start_idx=0):
    """Create prediction chart"""
    # Use specified historical data start position, not always from the beginning of df
    if historical_start_idx + lookback + pred_len <= len(df):
        # Display lookback historical points + pred_len prediction points starting from specified position
        historical_df = df.iloc[historical_start_idx:historical_start_idx+lookback]
        prediction_range = range(historical_start_idx+lookback, historical_start_idx+lookback+pred_len)
    else:
        # If data is insufficient, adjust to maximum available range
        available_lookback = min(lookback, len(df) - historical_start_idx)
        available_pred_len = min(pred_len, max(0, len(df) - historical_start_idx - available_lookback))
        historical_df = df.iloc[historical_start_idx:historical_start_idx+available_lookback]
        prediction_range = range(historical_start_idx+available_lookback, historical_start_idx+available_lookback+available_pred_len)
    
    # Create chart
    fig = go.Figure()
    
    # Add prediction data (candlestick chart)
    if pred_df is not None and len(pred_df) > 0:
        # Calculate prediction data timestamps - ensure continuity with historical data
        if 'timestamps' in df.columns and len(historical_df) > 0:
            # Start from the last timestamp of historical data, create prediction timestamps with the same time interval
            last_timestamp = historical_df['timestamps'].iloc[-1]
            time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0] if len(df) > 1 else pd.Timedelta(hours=1)
            
            pred_timestamps = pd.date_range(
                start=last_timestamp + time_diff,
                periods=len(pred_df),
                freq=time_diff
            )
        else:
            # If no timestamps, use index
            pred_timestamps = range(len(historical_df), len(historical_df) + len(pred_df))
        
        fig.add_trace(go.Candlestick(
            x=pred_timestamps,
            open=pred_df['open'],
            high=pred_df['high'],
            low=pred_df['low'],
            close=pred_df['close'],
            name=f'Прогноз ({pred_len} точок)',
            increasing_line_color='#66BB6A',
            decreasing_line_color='#FF7043'
        ))
    
    # Add actual data for comparison (if exists)
    if actual_df is not None and len(actual_df) > 0:
        # Actual data should be in the same time period as prediction data
        if 'timestamps' in df.columns:
            # Actual data should use the same timestamps as prediction data to ensure time alignment
            if 'pred_timestamps' in locals():
                actual_timestamps = pred_timestamps
            else:
                # If no prediction timestamps, calculate from the last timestamp of historical data
                if len(historical_df) > 0:
                    last_timestamp = historical_df['timestamps'].iloc[-1]
                    time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0] if len(df) > 1 else pd.Timedelta(hours=1)
                    actual_timestamps = pd.date_range(
                        start=last_timestamp + time_diff,
                        periods=len(actual_df),
                        freq=time_diff
                    )
                else:
                    actual_timestamps = range(len(historical_df), len(historical_df) + len(actual_df))
        else:
            actual_timestamps = range(len(historical_df), len(historical_df) + len(actual_df))
        
        fig.add_trace(go.Candlestick(
            x=actual_timestamps,
            open=actual_df['open'],
            high=actual_df['high'],
            low=actual_df['low'],
            close=actual_df['close'],
            name=f'Реальні дані ({len(actual_df)} точок)',
            increasing_line_color='#FF9800',
            decreasing_line_color='#F44336'
        ))
    
    # Update layout
    fig.update_layout(
        title=f'Результати фінансового прогнозування Kronos - {lookback} точок історії + {pred_len} точок прогнозу',
        xaxis_title='Час',
        yaxis_title='Ціна',
        template='plotly_white',
        height=600,
        showlegend=True
    )
    
    # Ensure x-axis time continuity
    all_timestamps = []
    if 'pred_timestamps' in locals():
        all_timestamps.extend(pred_timestamps)
    if 'actual_timestamps' in locals():
        all_timestamps.extend(actual_timestamps)
    
    if all_timestamps:
        all_timestamps = sorted(all_timestamps)
        fig.update_xaxes(
            range=[all_timestamps[0], all_timestamps[-1]],
            rangeslider_visible=False,
            type='date'
        )
    
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

# --- Authentication Helper and Endpoints ---

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'error': 'Unauthorized. Please login first.'}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    if not supabase:
        return jsonify({'error': 'Supabase is not configured'}), 503
    
    data = request.json or {}
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
        
    try:
        res = supabase.auth.sign_up({
            "email": email,
            "password": password
        })
        user = getattr(res, 'user', None)
        if user:
            return jsonify({'message': 'Registration successful! Proceed to login.'}), 201
        else:
            return jsonify({'error': 'Registration failed'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    if not supabase:
        return jsonify({'error': 'Supabase is not configured'}), 503
        
    data = request.json or {}
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
        
    try:
        res = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        user = getattr(res, 'user', None)
        sess = getattr(res, 'session', None)
        if user and sess:
            # Save user info in flask session
            session['user_id'] = user.id
            session['user_email'] = user.email
            session['access_token'] = sess.access_token
            return jsonify({
                'message': 'Login successful!',
                'user': {
                    'id': user.id,
                    'email': user.email
                }
            }), 200
        else:
            return jsonify({'error': 'Invalid credentials'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 401

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'message': 'Logged out successfully!'}), 200

@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    user_id = session.get('user_id')
    user_email = session.get('user_email')
    if user_id:
        return jsonify({
            'logged_in': True,
            'user': {
                'id': user_id,
                'email': user_email
            }
        }), 200
    else:
        return jsonify({
            'logged_in': False
        }), 200

# --- Exchange Game Endpoints (with Supabase / Flask Session fallback) ---

@app.route('/api/exchange/wallet', methods=['GET'])
@login_required
def exchange_wallet():
    user_id = session.get('user_id')
    
    # 1. Try Supabase
    if supabase:
        try:
            res = supabase.table('wallets').select('*').eq('user_id', user_id).execute()
            data = getattr(res, 'data', [])
            if data:
                wallet = data[0]
                return jsonify({
                    'usd_balance': float(wallet['usd_balance']),
                    'btc_balance': float(wallet['btc_balance']),
                    'avg_buy_price': float(wallet['avg_buy_price'])
                }), 200
            else:
                new_wallet = {
                    'user_id': user_id,
                    'usd_balance': 100.0,
                    'btc_balance': 0.0,
                    'avg_buy_price': 0.0
                }
                supabase.table('wallets').insert(new_wallet).execute()
                return jsonify({
                    'usd_balance': 100.0,
                    'btc_balance': 0.0,
                    'avg_buy_price': 0.0
                }), 200
        except Exception as e:
            print(f"Error fetching wallet from Supabase: {str(e)}")
            
    # 2. Fallback to Flask session
    if 'wallet' not in session:
        session['wallet'] = {
            'usd_balance': 100.0,
            'btc_balance': 0.0,
            'avg_buy_price': 0.0
        }
    return jsonify(session['wallet']), 200

@app.route('/api/exchange/history', methods=['GET'])
@login_required
def exchange_history():
    user_id = session.get('user_id')
    
    if supabase:
        try:
            res = supabase.table('trades').select('*').eq('user_id', user_id).order('timestamp', desc=True).execute()
            data = getattr(res, 'data', [])
            return jsonify(data), 200
        except Exception as e:
            print(f"Error fetching history from Supabase: {str(e)}")
            
    # Fallback to session
    if 'trades' not in session:
        session['trades'] = []
    return jsonify(session['trades']), 200

@app.route('/api/exchange/trade', methods=['POST'])
@login_required
def exchange_trade():
    user_id = session.get('user_id')
    data = request.json or {}
    trade_type = data.get('type')  # 'buy' or 'sell'
    btc_amount = data.get('amount')
    price = data.get('price')      # Price per BTC
    
    if not trade_type or btc_amount is None or not price:
        return jsonify({'error': 'Missing transaction details'}), 400
        
    try:
        btc_amount = float(btc_amount)
        price = float(price)
    except ValueError:
        return jsonify({'error': 'Invalid number format'}), 400
        
    if btc_amount <= 0 or price <= 0:
        return jsonify({'error': 'Amount and price must be greater than zero'}), 400

    fee_rate = 0.001  # 0.1% fee
    
    # 1. Process via Supabase
    if supabase:
        try:
            res = supabase.table('wallets').select('*').eq('user_id', user_id).execute()
            wallets_data = getattr(res, 'data', [])
            if not wallets_data:
                return jsonify({'error': 'Wallet not found'}), 404
            
            wallet = wallets_data[0]
            usd_balance = float(wallet['usd_balance'])
            btc_balance = float(wallet['btc_balance'])
            avg_buy_price = float(wallet['avg_buy_price'])
            
            fee = btc_amount * price * fee_rate
            total_usd_value = btc_amount * price
            
            if trade_type == 'buy':
                total_cost = total_usd_value + fee
                if usd_balance < total_cost:
                    return jsonify({'error': 'Недостатньо USD для купівлі'}), 400
                    
                new_usd_balance = usd_balance - total_cost
                new_btc_balance = btc_balance + btc_amount
                if new_btc_balance > 0:
                    new_avg_buy_price = ((btc_balance * avg_buy_price) + (btc_amount * price)) / new_btc_balance
                else:
                    new_avg_buy_price = 0.0
                    
            elif trade_type == 'sell':
                if btc_balance < btc_amount:
                    return jsonify({'error': 'Недостатньо BTC для продажу'}), 400
                    
                new_usd_balance = usd_balance + (total_usd_value - fee)
                new_btc_balance = btc_balance - btc_amount
                new_avg_buy_price = avg_buy_price if new_btc_balance > 0 else 0.0
            else:
                return jsonify({'error': 'Invalid trade type'}), 400
                
            supabase.table('wallets').update({
                'usd_balance': new_usd_balance,
                'btc_balance': new_btc_balance,
                'avg_buy_price': new_avg_buy_price,
                'updated_at': datetime.datetime.now().isoformat()
            }).eq('user_id', user_id).execute()
            
            trade_log = {
                'user_id': user_id,
                'type': trade_type,
                'btc_amount': btc_amount,
                'price': price,
                'fee': fee,
                'timestamp': datetime.datetime.now().isoformat()
            }
            supabase.table('trades').insert(trade_log).execute()
            
            return jsonify({
                'usd_balance': new_usd_balance,
                'btc_balance': new_btc_balance,
                'avg_buy_price': new_avg_buy_price,
                'message': 'Угоду успішно виконано!'
            }), 200
            
        except Exception as e:
            print(f"Supabase trade failed, falling back to session: {str(e)}")

    # 2. Fallback to Flask session
    if 'wallet' not in session:
        session['wallet'] = {
            'usd_balance': 100.0,
            'btc_balance': 0.0,
            'avg_buy_price': 0.0
        }
    if 'trades' not in session:
        session['trades'] = []
        
    wallet = session['wallet']
    usd_balance = float(wallet['usd_balance'])
    btc_balance = float(wallet['btc_balance'])
    avg_buy_price = float(wallet['avg_buy_price'])
    
    fee = btc_amount * price * fee_rate
    total_usd_value = btc_amount * price
    
    if trade_type == 'buy':
        total_cost = total_usd_value + fee
        if usd_balance < total_cost:
            return jsonify({'error': 'Недостатньо USD для купівлі'}), 400
            
        new_usd_balance = usd_balance - total_cost
        new_btc_balance = btc_balance + btc_amount
        if new_btc_balance > 0:
            new_avg_buy_price = ((btc_balance * avg_buy_price) + (btc_amount * price)) / new_btc_balance
        else:
            new_avg_buy_price = 0.0
            
    elif trade_type == 'sell':
        if btc_balance < btc_amount:
            return jsonify({'error': 'Недостатньо BTC для продажу'}), 400
            
        new_usd_balance = usd_balance + (total_usd_value - fee)
        new_btc_balance = btc_balance - btc_amount
        new_avg_buy_price = avg_buy_price if new_btc_balance > 0 else 0.0
    else:
        return jsonify({'error': 'Invalid trade type'}), 400
        
    session['wallet'] = {
        'usd_balance': new_usd_balance,
        'btc_balance': new_btc_balance,
        'avg_buy_price': new_avg_buy_price
    }
    
    trade_log = {
        'id': len(session['trades']) + 1,
        'user_id': user_id,
        'type': trade_type,
        'btc_amount': btc_amount,
        'price': price,
        'fee': fee,
        'timestamp': datetime.datetime.now().isoformat()
    }
    session['trades'].insert(0, trade_log)
    session.modified = True
    
    return jsonify({
        'usd_balance': new_usd_balance,
        'btc_balance': new_btc_balance,
        'avg_buy_price': new_avg_buy_price,
        'message': 'Угоду успішно виконано! (Збережено в сесії)'
    }), 200

@app.route('/api/exchange/reset', methods=['POST'])
@login_required
def exchange_reset():
    user_id = session.get('user_id')
    
    if supabase:
        try:
            supabase.table('wallets').update({
                'usd_balance': 100.0,
                'btc_balance': 0.0,
                'avg_buy_price': 0.0,
                'updated_at': datetime.datetime.now().isoformat()
            }).eq('user_id', user_id).execute()
            
            supabase.table('trades').delete().eq('user_id', user_id).execute()
            
            return jsonify({
                'usd_balance': 100.0,
                'btc_balance': 0.0,
                'avg_buy_price': 0.0,
                'message': 'Баланс успішно скинуто, історію очищено!'
            }), 200
        except Exception as e:
            print(f"Supabase reset failed: {str(e)}")
            
    session['wallet'] = {
        'usd_balance': 100.0,
        'btc_balance': 0.0,
        'avg_buy_price': 0.0
    }
    session['trades'] = []
    session.modified = True
    return jsonify({
        'usd_balance': 100.0,
        'btc_balance': 0.0,
        'avg_buy_price': 0.0,
        'message': 'Баланс успішно скинуто! (Очищено в сесії)'
    }), 200

@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')

@app.route('/api/data-files')
def get_data_files():
    """Get available data file list"""
    data_files = load_data_files()
    return jsonify(data_files)

@app.route('/api/load-data', methods=['POST'])
def load_data():
    """Load data file"""
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        
        if not file_path:
            return jsonify({'error': 'File path cannot be empty'}), 400
        
        df, error = load_data_file(file_path)
        if error:
            return jsonify({'error': error}), 400
        
        # Detect data time frequency
        def detect_timeframe(df):
            if len(df) < 2:
                return "Unknown"
            
            time_diffs = []
            for i in range(1, min(10, len(df))):  # Check first 10 time differences
                diff = df['timestamps'].iloc[i] - df['timestamps'].iloc[i-1]
                time_diffs.append(diff)
            
            if not time_diffs:
                return "Unknown"
            
            # Calculate average time difference
            avg_diff = sum(time_diffs, pd.Timedelta(0)) / len(time_diffs)
            
            # Convert to readable format
            if avg_diff < pd.Timedelta(minutes=1):
                return f"{avg_diff.total_seconds():.0f} seconds"
            elif avg_diff < pd.Timedelta(hours=1):
                return f"{avg_diff.total_seconds() / 60:.0f} minutes"
            elif avg_diff < pd.Timedelta(days=1):
                return f"{avg_diff.total_seconds() / 3600:.0f} hours"
            else:
                return f"{avg_diff.days} days"
        
        # Return data information
        data_info = {
            'rows': len(df),
            'columns': list(df.columns),
            'start_date': df['timestamps'].min().isoformat() if 'timestamps' in df.columns else 'N/A',
            'end_date': df['timestamps'].max().isoformat() if 'timestamps' in df.columns else 'N/A',
            'price_range': {
                'min': float(df[['open', 'high', 'low', 'close']].min().min()),
                'max': float(df[['open', 'high', 'low', 'close']].max().max())
            },
            'prediction_columns': ['open', 'high', 'low', 'close'] + (['volume'] if 'volume' in df.columns else []),
            'timeframe': detect_timeframe(df)
        }
        
        return jsonify({
            'success': True,
            'data_info': data_info,
            'message': f'Successfully loaded data, total {len(df)} rows'
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to load data: {str(e)}'}), 500

def check_and_update_btc_data(current_file_path):
    """
    Checks if there is new daily BTC data on Binance API since the last date in current_file_path.
    If so, fetches the new data, merges it, and saves to btc_history_daily_latest.csv.
    """
    import urllib.request
    import json
    import pandas as pd
    import datetime
    import os
    
    try:
        # Load the file to get the last date
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        base_file_path = os.path.join(data_dir, 'btc_history_daily.csv')
        latest_file_path = os.path.join(data_dir, 'btc_history_daily_latest.csv')
        
        file_to_read = current_file_path if os.path.exists(current_file_path) else base_file_path
        if not os.path.exists(file_to_read):
            return current_file_path, False, "Base file does not exist", None
            
        df = pd.read_csv(file_to_read)
        if 'timestamps' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamps'])
        elif 'timestamp' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamp'])
            df.rename(columns={'timestamp': 'timestamps'}, inplace=True)
        else:
            return current_file_path, False, "No timestamp column found", None
            
        last_date = df['timestamps'].max()
        
        # Get current date in UTC
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        current_date_utc = pd.to_datetime(now_utc.date())
        
        # If the last date in the CSV is strictly greater than today, we don't need to fetch
        if last_date > current_date_utc:
            return file_to_read, False, None, None
            
        # Convert last_date to millisecond timestamp (subtract 1 day to query starting from the day before last_date)
        # This ensures we re-fetch and overwrite the last recorded day, which might have been saved while in-progress.
        query_start_time = int((last_date - pd.Timedelta(days=1)).timestamp() * 1000)
        
        # Binance Kline API symbol: BTCUSDT, interval: 1d
        url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&startTime={query_start_time}&limit=1000"
        
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            klines = json.loads(response.read().decode())
            
        if not klines:
            return file_to_read, False, None, None
            
        # Parse new candles from Binance
        new_rows = []
        for k in klines:
            dt = datetime.datetime.fromtimestamp(k[0] / 1000, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
            dt_ts = pd.to_datetime(dt)
            new_rows.append({
                'timestamps': dt_ts,
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
                'amount': float(k[7]) # Quote asset volume is USDT amount
            })
                
        if not new_rows:
            return file_to_read, False, None, None
            
        new_df = pd.DataFrame(new_rows)
        
        # Combine
        df.set_index('timestamps', inplace=True)
        new_df.set_index('timestamps', inplace=True)
        
        # Update existing
        df.update(new_df)
        
        # Append new
        new_indices = new_df.index.difference(df.index)
        if len(new_indices) > 0:
            df = pd.concat([df, new_df.loc[new_indices]])
            
        df.reset_index(inplace=True)
        df.sort_values('timestamps', inplace=True)
        df['timestamps'] = df['timestamps'].dt.strftime('%Y-%m-%d')
        
        # Save to btc_history_daily_latest.csv
        df.to_csv(latest_file_path, index=False)
        print(f"✅ Created/Updated {latest_file_path} with {len(new_indices)} new rows from Binance API.")
        
        new_data_info = {
            'rows': len(df),
            'columns': list(df.columns),
            'start_date': df['timestamps'].min() + 'T00:00:00',
            'end_date': df['timestamps'].max() + 'T00:00:00',
            'price_range': {
                'min': float(df[['open', 'high', 'low', 'close']].min().min()),
                'max': float(df[['open', 'high', 'low', 'close']].max().max())
            },
            'prediction_columns': ['open', 'high', 'low', 'close'] + (['volume'] if 'volume' in df.columns else []),
            'timeframe': "1 days"
        }
        
        return latest_file_path, True, None, new_data_info
        
    except Exception as e:
        print(f"❌ Failed to check/update daily BTC data: {e}")
        return current_file_path, False, str(e), None

@app.route('/api/predict', methods=['POST'])
def predict():
    """Perform prediction"""
    try:
        data = request.get_json()
        file_path = data.get('file_path')
        lookback = int(data.get('lookback', 400))
        pred_len = int(data.get('pred_len', 120))
        
        # Get prediction quality parameters
        temperature = float(data.get('temperature', 1.0))
        top_p = float(data.get('top_p', 0.9))
        sample_count = int(data.get('sample_count', 1))
        
        if not file_path:
            return jsonify({'error': 'File path cannot be empty'}), 400
        
        # Check if the file is the daily history file and if it needs updating
        was_updated = False
        new_data_info = None
        
        is_daily = "btc_history_daily" in file_path
        if is_daily:
            updated_path, was_updated, update_err, new_data_info = check_and_update_btc_data(file_path)
            if was_updated:
                file_path = updated_path
                
        # Load data
        df, error = load_data_file(file_path)
        if error:
            return jsonify({'error': error}), 400
        
        if len(df) < lookback:
            return jsonify({'error': f'Insufficient data length, need at least {lookback} rows'}), 400
        
        # Perform prediction
        if MODEL_AVAILABLE and predictor is not None:
            try:
                # Use real Kronos model
                # Only use necessary columns: OHLCV, excluding amount
                required_cols = ['open', 'high', 'low', 'close']
                if 'volume' in df.columns:
                    required_cols.append('volume')
                
                # Always use the latest data from the file for real future forecasting
                time_diff = df['timestamps'].iloc[1] - df['timestamps'].iloc[0] if len(df) > 1 else pd.Timedelta(hours=1)
                x_df = df.iloc[-lookback:][required_cols]
                x_timestamp = df.iloc[-lookback:]['timestamps']
                
                # Generate y_timestamp for pred_len steps into the future
                last_x_ts = x_timestamp.iloc[-1]
                y_timestamp = pd.date_range(
                    start=last_x_ts + time_diff,
                    periods=pred_len,
                    freq=time_diff
                )
                
                actual_df = None
                prediction_type = "Kronos model prediction (latest data, real future forecast)"
                
                # Ensure timestamps are Series format, not DatetimeIndex, to avoid .dt attribute error in Kronos model
                if isinstance(x_timestamp, pd.DatetimeIndex):
                    x_timestamp = pd.Series(x_timestamp, name='timestamps')
                if isinstance(y_timestamp, pd.DatetimeIndex):
                    y_timestamp = pd.Series(y_timestamp, name='timestamps')
                
                # Check if bias correction is enabled
                bias_correction = data.get('bias_correction', False)
                bias_correction_applied = False
                bias_offsets = None
                
                if bias_correction and len(x_df) >= 300:
                    try:
                        val_n = 10
                        val_x_df = x_df.iloc[:-val_n]
                        val_x_timestamp = x_timestamp.iloc[:-val_n]
                        val_y_timestamp = x_timestamp.iloc[-val_n:]
                        
                        if isinstance(val_x_timestamp, pd.DatetimeIndex):
                            val_x_timestamp = pd.Series(val_x_timestamp, name='timestamps')
                        if isinstance(val_y_timestamp, pd.DatetimeIndex):
                            val_y_timestamp = pd.Series(val_y_timestamp, name='timestamps')
                            
                        # Run validation prediction
                        val_pred_df = predictor.predict(
                            df=val_x_df,
                            x_timestamp=val_x_timestamp,
                            y_timestamp=val_y_timestamp,
                            pred_len=val_n,
                            T=temperature,
                            top_p=top_p,
                            sample_count=sample_count
                        )
                        
                        # Calculate weighted errors (more recent points get higher weights)
                        val_actual_df = x_df.iloc[-val_n:]
                        
                        open_diffs = val_actual_df['open'].values - val_pred_df['open'].values
                        high_diffs = val_actual_df['high'].values - val_pred_df['high'].values
                        low_diffs = val_actual_df['low'].values - val_pred_df['low'].values
                        close_diffs = val_actual_df['close'].values - val_pred_df['close'].values
                        
                        weights = np.arange(1, val_n + 1, dtype=np.float64)
                        weights = weights / weights.sum()
                        
                        open_offset = float(np.sum(open_diffs * weights))
                        high_offset = float(np.sum(high_diffs * weights))
                        low_offset = float(np.sum(low_diffs * weights))
                        close_offset = float(np.sum(close_diffs * weights))
                        
                        bias_offsets = {
                            'open': open_offset,
                            'high': high_offset,
                            'low': low_offset,
                            'close': close_offset
                        }
                        bias_correction_applied = True
                        print(f"Calculated Bias Offsets: {bias_offsets}")
                    except Exception as val_err:
                        print(f"⚠️ Failed to calculate bias correction: {val_err}")
                        bias_correction_applied = False
                
                # Perform main prediction
                pred_df = predictor.predict(
                    df=x_df,
                    x_timestamp=x_timestamp,
                    y_timestamp=y_timestamp,
                    pred_len=pred_len,
                    T=temperature,
                    top_p=top_p,
                    sample_count=sample_count
                )
                
                # Apply bias correction if successful
                if bias_correction_applied and bias_offsets is not None:
                    pred_df['open'] += bias_offsets['open']
                    pred_df['high'] += bias_offsets['high']
                    pred_df['low'] += bias_offsets['low']
                    pred_df['close'] += bias_offsets['close']
                    
                    # Apply guardrails
                    pred_df['open'] = pred_df['open'].clip(lower=0.01)
                    pred_df['close'] = pred_df['close'].clip(lower=0.01)
                    pred_df['high'] = pred_df['high'].clip(lower=0.01)
                    pred_df['low'] = pred_df['low'].clip(lower=0.01)
                    
                    pred_df['high'] = np.maximum(pred_df['high'].values, np.maximum(pred_df['open'].values, pred_df['close'].values))
                    pred_df['low'] = np.minimum(pred_df['low'].values, np.minimum(pred_df['open'].values, pred_df['close'].values))
                
            except Exception as e:
                return jsonify({'error': f'Kronos model prediction failed: {str(e)}'}), 500
        else:
            return jsonify({'error': 'Kronos model not loaded, please load model first'}), 400
        
        # Prepare actual data for comparison (if exists)
        actual_data = []
        if actual_df is not None and len(actual_df) > 0:
            for _, row in actual_df.iterrows():
                actual_data.append({
                    'timestamp': row['timestamps'].isoformat(),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume']) if 'volume' in row else 0,
                    'amount': float(row['amount']) if 'amount' in row else 0
                })
        
        # Chart always shows the latest lookback window
        historical_start_idx = len(df) - lookback
        
        chart_json = create_prediction_chart(df, pred_df, lookback, pred_len, actual_df, historical_start_idx)
        
        future_timestamps = pred_df.index
        prediction_results = []
        for i, (_, row) in enumerate(pred_df.iterrows()):
            prediction_results.append({
                'timestamp': future_timestamps[i].isoformat() if hasattr(future_timestamps[i], 'isoformat') else f"T{i}",
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']) if 'volume' in row else 0,
                'amount': float(row['amount']) if 'amount' in row else 0
            })
        
        # Save prediction results to file
        last_hist_close = float(x_df['close'].iloc[-1])
        try:
            save_prediction_results(
                file_path=file_path,
                prediction_type=prediction_type,
                prediction_results=prediction_results,
                actual_data=actual_data,
                input_data=x_df,
                prediction_params={
                    'lookback': lookback,
                    'pred_len': pred_len,
                    'temperature': temperature,
                    'top_p': top_p,
                    'sample_count': sample_count,
                    'start_date': 'latest',
                    'bias_correction': bias_correction
                }
            )
            # Save the human-readable markdown table in the forecasts folder
            bias_info = bias_offsets if bias_correction_applied else None
            save_forecast_markdown_table(file_path, prediction_results, last_hist_close, bias_info)
        except Exception as e:
            print(f"Failed to save prediction results: {e}")
        
        return jsonify({
            'success': True,
            'prediction_type': prediction_type,
            'chart': chart_json,
            'prediction_results': prediction_results,
            'actual_data': actual_data,
            'has_comparison': len(actual_data) > 0,
            'last_historical_close': last_hist_close,
            'data_updated': was_updated,
            'new_file_path': file_path if was_updated else None,
            'new_file_name': os.path.basename(file_path) if was_updated else None,
            'new_data_info': new_data_info,
            'bias_correction_applied': bias_correction_applied,
            'bias_offsets': bias_offsets if bias_correction_applied else None,
            'message': f'Prediction completed, generated {pred_len} prediction points' + (f', including {len(actual_data)} actual data points for comparison' if len(actual_data) > 0 else '')
        })
        
    except Exception as e:
        return jsonify({'error': f'Prediction failed: {str(e)}'}), 500

@app.route('/api/load-model', methods=['POST'])
def load_model():
    """Load Kronos model"""
    try:
        if not MODEL_AVAILABLE:
            return jsonify({'error': 'Kronos model library not available'}), 400
        
        data = request.get_json()
        model_key = data.get('model_key', 'kronos-small')
        device = data.get('device', 'cpu')
        
        if model_key not in AVAILABLE_MODELS:
            return jsonify({'error': f'Unsupported model: {model_key}'}), 400
        
        model_config = load_model_by_key(model_key, device)
        
        return jsonify({
            'success': True,
            'message': f'Model loaded successfully: {model_config["name"]} ({model_config["params"]}) on {device}',
            'model_info': {
                'name': model_config['name'],
                'params': model_config['params'],
                'context_length': model_config['context_length'],
                'description': model_config['description']
            }
        })
        
    except Exception as e:
        return jsonify({'error': f'Model loading failed: {str(e)}'}), 500

@app.route('/api/available-models')
def get_available_models():
    """Get available model list"""
    return jsonify({
        'models': AVAILABLE_MODELS,
        'model_available': MODEL_AVAILABLE
    })

@app.route('/api/model-status')
def get_model_status():
    """Get model status"""
    if MODEL_AVAILABLE:
        if predictor is not None:
            return jsonify({
                'available': True,
                'loaded': True,
                'message': 'Kronos model loaded and available',
                'current_model': {
                    'name': predictor.model.__class__.__name__,
                    'device': str(next(predictor.model.parameters()).device)
                }
            })
        else:
            return jsonify({
                'available': True,
                'loaded': False,
                'message': 'Kronos model available but not loaded'
            })
    else:
        return jsonify({
            'available': False,
            'loaded': False,
            'message': 'Kronos model library not available, please install related dependencies'
        })

@app.route('/api/finetune/start', methods=['POST'])
def start_finetune():
    """Start model fine-tuning in background"""
    global finetune_process, finetune_state
    
    # Check if user is logged in and is the admin yarovision@gmail.com
    if session.get('user_email') != 'yarovision@gmail.com':
        return jsonify({'error': 'Forbidden. Only the administrator yarovision@gmail.com can start fine-tuning.'}), 403
        
    if finetune_state['is_running']:
        return jsonify({'error': 'Fine-tuning process is already running'}), 400
        
    try:
        data = request.get_json() or {}
        epochs = int(data.get('epochs', 5))
        
        # Determine the latest data file to use
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(project_root, 'data')
        latest_data = os.path.join(data_dir, 'btc_history_daily_latest.csv')
        base_data = os.path.join(data_dir, 'btc_history_daily.csv')
        
        data_path = latest_data if os.path.exists(latest_data) else base_data
        
        # Prepare cmd arguments
        train_script = os.path.join(project_root, 'finetune_csv', 'train_sequential.py')
        config_yaml = os.path.join(project_root, 'finetune_csv', 'configs', 'config_btc_daily.yaml')
        
        cmd = [
            sys.executable,
            train_script,
            '--config', config_yaml,
            '--skip-tokenizer',
            '--basemodel-epochs', str(epochs),
            '--data-path', data_path,
            '--device', 'cpu'
        ]
        
        log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'finetune_web_run.log')
        
        # Start background process
        log_file = open(log_file_path, 'w', encoding='utf-8')
        process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=project_root)
        
        # Reset and update state
        finetune_process = process
        finetune_state = {
            'is_running': True,
            'pid': process.pid,
            'start_time': datetime.datetime.now().isoformat(),
            'epochs': epochs,
            'current_epoch': 0,
            'progress_percent': 0,
            'log_file': log_file_path,
            'error': None,
            'success': False
        }
        
        return jsonify({
            'success': True,
            'message': f'Fine-tuning started in the background (PID: {process.pid}) for {epochs} epochs on {os.path.basename(data_path)}'
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to start fine-tuning: {str(e)}'}), 500

@app.route('/api/finetune/status', methods=['GET'])
def get_finetune_status():
    """Get status and logs of background fine-tuning process"""
    global finetune_process, finetune_state
    
    # 1. If running, poll the process
    if finetune_state['is_running'] and finetune_process is not None:
        poll_val = finetune_process.poll()
        if poll_val is not None:
            # Process terminated
            finetune_state['is_running'] = False
            finetune_state['pid'] = None
            
            if poll_val == 0:
                finetune_state['success'] = True
                finetune_state['progress_percent'] = 100
                finetune_state['current_epoch'] = finetune_state['epochs']
                # Try reloading the finetuned model automatically
                try:
                    load_model_by_key('btc-daily-finetuned', 'cpu')
                    print("✅ Successfully reloaded BTC Daily model post-training.")
                except Exception as load_err:
                    print(f"❌ Failed to reload model post-training: {load_err}")
            else:
                finetune_state['success'] = False
                finetune_state['error'] = f"Training script exited with code {poll_val}"
                
    # 2. Read logs and parse current progress
    log_lines = []
    log_file_path = finetune_state['log_file']
    
    if log_file_path and os.path.exists(log_file_path):
        try:
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                log_lines = f.readlines()
                
            # Parse progress from logs
            for line in reversed(log_lines):
                if "[Epoch " in line:
                    try:
                        epoch_str = line.split("[Epoch ")[1].split(",")[0].split("]")[0]
                        curr, tot = map(int, epoch_str.split("/"))
                        finetune_state['current_epoch'] = curr
                        
                        step_progress = 0
                        if "Step " in line:
                            step_str = line.split("Step ")[1].split("]")[0]
                            scurr, stot = map(int, step_str.split("/"))
                            step_progress = (scurr / stot) * (1.0 / tot)
                        
                        pct = int(((curr - 1) / tot + step_progress) * 100)
                        finetune_state['progress_percent'] = min(99, max(0, pct))
                        break
                    except Exception:
                        pass
                elif "Training completed successfully!" in line:
                    finetune_state['progress_percent'] = 100
                    finetune_state['current_epoch'] = finetune_state['epochs']
                    break
        except Exception as e:
            print(f"Error reading log file: {e}")
            
    logs_to_return = "".join(log_lines[-80:]) if log_lines else "Waiting for process output..."
    
    return jsonify({
        'is_running': finetune_state['is_running'],
        'progress_percent': finetune_state['progress_percent'],
        'current_epoch': finetune_state['current_epoch'],
        'total_epochs': finetune_state['epochs'],
        'success': finetune_state['success'],
        'error': finetune_state['error'],
        'logs': logs_to_return
    })

@app.route('/api/finetune/stop', methods=['POST'])
def stop_finetune():
    """Terminate the background fine-tuning process"""
    global finetune_process, finetune_state
    
    # Check if user is logged in and is the admin yarovision@gmail.com
    if session.get('user_email') != 'yarovision@gmail.com':
        return jsonify({'error': 'Forbidden. Only the administrator yarovision@gmail.com can stop fine-tuning.'}), 403
        
    if not finetune_state['is_running'] or finetune_process is None:
        return jsonify({'message': 'Process is not running'}), 200
        
    try:
        finetune_process.terminate()
        try:
            finetune_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            finetune_process.kill()
            
        finetune_state['is_running'] = False
        finetune_state['pid'] = None
        finetune_state['error'] = "Process terminated by user"
        finetune_state['success'] = False
        
        return jsonify({'success': True, 'message': 'Fine-tuning process terminated successfully'})
    except Exception as e:
        return jsonify({'error': f'Failed to terminate process: {str(e)}'}), 500

def get_forecasts_for_date(target_date_str):
    """
    Scans prediction_results directory and finds all predictions made for target_date_str (format YYYY-MM-DD).
    """
    import os
    import json
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prediction_results')
    forecasts = []
    
    if not os.path.exists(results_dir):
        return forecasts
        
    for file in os.listdir(results_dir):
        if file.endswith('.json'):
            filepath = os.path.join(results_dir, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Check prediction_results
                pred_results = data.get('prediction_results', [])
                for pred in pred_results:
                    pred_ts = pred.get('timestamp', '') # e.g. "2026-06-04T00:00:00"
                    if pred_ts.startswith(target_date_str):
                        run_ts = data.get('timestamp', '')
                        # Format run timestamp to readable form
                        try:
                            run_dt = datetime.datetime.fromisoformat(run_ts)
                            run_ts_formatted = run_dt.strftime('%d.%m.%Y %H:%M')
                        except Exception:
                            run_ts_formatted = run_ts
                        
                        params = data.get('prediction_params', {})
                        model_name = data.get('prediction_type', 'Модель Kronos')
                        
                        forecasts.append({
                            'run_timestamp': run_ts_formatted,
                            'model_name': model_name,
                            'open': pred.get('open'),
                            'high': pred.get('high'),
                            'low': pred.get('low'),
                            'close': pred.get('close'),
                            'bias_correction': params.get('bias_correction', False)
                        })
            except Exception as e:
                print(f"Error reading prediction file {file}: {e}")
                
    # Sort forecasts by run_timestamp descending (newest first)
    forecasts.sort(key=lambda x: x['run_timestamp'], reverse=True)
    return forecasts

@app.route('/api/today-data', methods=['GET'])
def get_today_data():
    """Get today's actual Bitcoin rate and all past forecasts made for today"""
    import os
    import pandas as pd
    
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project_root, 'data')
    latest_file_path = os.path.join(data_dir, 'btc_history_daily_latest.csv')
    base_file_path = os.path.join(data_dir, 'btc_history_daily.csv')
    
    file_path = latest_file_path if os.path.exists(latest_file_path) else base_file_path
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'Data file not found'}), 400
        
    try:
        # Update first to get the most recent daily candle
        check_and_update_btc_data(file_path)
        
        df = pd.read_csv(file_path)
        if 'timestamps' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamps'])
        elif 'timestamp' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamp'])
            df.rename(columns={'timestamp': 'timestamps'}, inplace=True)
            
        if len(df) == 0:
            return jsonify({'error': 'No data in file'}), 400
            
        # Get the last row (which represents "today")
        last_row = df.iloc[-1]
        target_date = last_row['timestamps']
        target_date_str = target_date.strftime('%Y-%m-%d')
        
        actual_rate = {
            'date': target_date_str,
            'open': float(last_row['open']),
            'high': float(last_row['high']),
            'low': float(last_row['low']),
            'close': float(last_row['close']),
            'volume': float(last_row['volume']) if 'volume' in last_row else 0
        }
        
        # Get the second-to-last row (yesterday)
        yesterday_rate = None
        if len(df) >= 2:
            yesterday_row = df.iloc[-2]
            yesterday_date = yesterday_row['timestamps']
            yesterday_rate = {
                'date': yesterday_date.strftime('%Y-%m-%d'),
                'open': float(yesterday_row['open']),
                'high': float(yesterday_row['high']),
                'low': float(yesterday_row['low']),
                'close': float(yesterday_row['close']),
                'volume': float(yesterday_row['volume']) if 'volume' in yesterday_row else 0
            }
        
        # Find all forecasts made for this target date
        forecasts = get_forecasts_for_date(target_date_str)
        
        return jsonify({
            'success': True,
            'actual_rate': actual_rate,
            'yesterday_rate': yesterday_rate,
            'forecasts': forecasts
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Starting Kronos Web UI...")
    print(f"Model availability: {MODEL_AVAILABLE}")
    if MODEL_AVAILABLE:
        print("Tip: You can load Kronos model through /api/load-model endpoint")
    else:
        print("Tip: Will use simulated data for demonstration")
    
    app.run(debug=True, host='0.0.0.0', port=7070)
