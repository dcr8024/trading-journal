from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime, timedelta
import sqlite3
import os
import secrets
from werkzeug.utils import secure_filename
from functools import wraps
import calendar as cal

app = Flask(__name__)

# Generate or set your secret key
app.secret_key = os.environ.get('SECRET_KEY', 'my_trading_journal_secret_key_2025_' + secrets.token_hex(8))

# File upload configuration
UPLOAD_FOLDER = 'static/screenshots'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16MB max file size

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Database migration function to add user_id to existing trades
def migrate_existing_trades():
    conn = get_db_connection()
    
    # Check if trades table has user_id column
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'user_id' not in columns:
        # Add user_id column to existing trades table
        conn.execute('ALTER TABLE trades ADD COLUMN user_id INTEGER DEFAULT 1')
        print("Added user_id column to existing trades")
    
    # Ensure all existing trades are assigned to Darren (user_id = 1)
    conn.execute('UPDATE trades SET user_id = 1 WHERE user_id IS NULL OR user_id = 0')
    conn.commit()
    conn.close()

# Database setup
def init_db():
    conn = sqlite3.connect('trading_journal.db')
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Trades table (create new or update existing)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 1,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            date TEXT NOT NULL,
            outcome TEXT NOT NULL,
            close_reason TEXT NOT NULL,
            account_pnl REAL NOT NULL,
            notes TEXT,
            screenshot_filename TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Economic events table (NEW)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS economic_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_date DATE NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            importance TEXT DEFAULT 'Medium',
            source_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Create default users
    users = [
        ('darren', 'darren', 'Darren'),
        ('likith', 'likith', 'Likith'),
        ('tanish', 'tanish', 'Tanish')
    ]
    
    for username, password, display_name in users:
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, password, display_name)
            VALUES (?, ?, ?)
        ''', (username, password, display_name))
    
    conn.commit()
    conn.close()
    
    # Migrate existing trades
    migrate_existing_trades()

def get_db_connection():
    conn = sqlite3.connect('trading_journal.db')
    conn.row_factory = sqlite3.Row
    return conn

# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].lower().strip()
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['display_name'] = user['display_name']
            flash(f'Welcome back, {user["display_name"]}!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('login'))

@app.route('/switch_profile')
@login_required
def switch_profile():
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    # Get selected user for viewing (default to current user)
    view_user_id = request.args.get('user', session['user_id'], type=int)
    
    conn = get_db_connection()
    
    # Get user info for the profile being viewed
    viewed_user = conn.execute('SELECT * FROM users WHERE id = ?', (view_user_id,)).fetchone()
    if not viewed_user:
        flash('User not found', 'error')
        return redirect(url_for('index'))
    
    # Get all users for profile switcher
    all_users = conn.execute('SELECT id, username, display_name FROM users ORDER BY display_name').fetchall()
    
    trades = conn.execute('''
        SELECT * FROM trades 
        WHERE user_id = ?
        ORDER BY date DESC, created_at DESC
    ''', (view_user_id,)).fetchall()
    
    # Calculate summary stats for viewed user
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t['account_pnl'] > 0])
    losing_trades = len([t for t in trades if t['account_pnl'] < 0])
    breakeven_trades = len([t for t in trades if t['account_pnl'] == 0])
    
    # Calculate rates
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    be_rate = (breakeven_trades / total_trades * 100) if total_trades > 0 else 0
    loss_rate = (losing_trades / total_trades * 100) if total_trades > 0 else 0
    
    # Calculate P&L metrics
    total_pnl = sum([t['account_pnl'] for t in trades])
    avg_win = sum([t['account_pnl'] for t in trades if t['account_pnl'] > 0]) / winning_trades if winning_trades > 0 else 0
    avg_loss = sum([t['account_pnl'] for t in trades if t['account_pnl'] < 0]) / losing_trades if losing_trades > 0 else 0
    
    # Calculate Risk-Reward Ratio (Average Win : Average Loss)
    if avg_loss != 0:
        risk_reward_ratio = abs(avg_win / avg_loss)  # Use absolute value since avg_loss is negative
    else:
        risk_reward_ratio = avg_win if avg_win > 0 else 0
    
    conn.close()
    
    stats = {
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'breakeven_trades': breakeven_trades,
        'win_rate': round(win_rate, 1),
        'be_rate': round(be_rate, 1),
        'loss_rate': round(loss_rate, 1),
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'risk_reward_ratio': round(risk_reward_ratio, 2)
    }
    
    # Check if current user can edit (only their own trades)
    can_edit = (view_user_id == session['user_id'])
    
    return render_template('index.html', 
                         trades=trades, 
                         stats=stats, 
                         viewed_user=viewed_user,
                         all_users=all_users,
                         can_edit=can_edit,
                         current_view_user_id=view_user_id)

@app.route('/add_trade', methods=['GET', 'POST'])
@login_required
def add_trade():
    if request.method == 'POST':
        ticker = request.form['ticker'].upper().strip()
        direction = request.form['direction']
        date = request.form['date']
        outcome = request.form['outcome']
        close_reason = request.form['close_reason']
        account_pnl = float(request.form['account_pnl'])
        notes = request.form.get('notes', '').strip()
        
        # Validate required fields
        if not all([ticker, direction, date, outcome, close_reason]):
            flash('All required fields must be filled out.', 'error')
            return render_template('add_trade.html')
        
        # Handle file upload
        screenshot_filename = None
        if 'screenshot' in request.files:
            file = request.files['screenshot']
            if file and file.filename != '' and allowed_file(file.filename):
                # Create unique filename with user prefix
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                original_extension = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{session['username']}_{ticker}_{date}_{timestamp}.{original_extension}"
                filename = secure_filename(filename)
                
                try:
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    screenshot_filename = filename
                except Exception as e:
                    flash(f'Error uploading screenshot: {str(e)}', 'error')
                    return render_template('add_trade.html')
        
        # Insert into database with user_id
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO trades (user_id, ticker, direction, date, outcome, close_reason, account_pnl, notes, screenshot_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session['user_id'], ticker, direction, date, outcome, close_reason, account_pnl, notes, screenshot_filename))
        conn.commit()
        conn.close()
        
        flash(f'Trade {ticker} added successfully!', 'success')
        return redirect(url_for('index'))
    
    return render_template('add_trade.html')

@app.route('/edit_trade/<int:trade_id>', methods=['GET', 'POST'])
@login_required
def edit_trade(trade_id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        ticker = request.form['ticker'].upper().strip()
        direction = request.form['direction']
        date = request.form['date']
        outcome = request.form['outcome']
        close_reason = request.form['close_reason']
        account_pnl = float(request.form['account_pnl'])
        notes = request.form.get('notes', '').strip()
        
        # Get current trade to check for existing screenshot and ownership
        current_trade = conn.execute('SELECT screenshot_filename FROM trades WHERE id = ? AND user_id = ?', (trade_id, session['user_id'])).fetchone()
        if not current_trade:
            flash('Trade not found or access denied.', 'error')
            return redirect(url_for('index'))
            
        screenshot_filename = current_trade['screenshot_filename']
        
        # Handle file upload
        if 'screenshot' in request.files:
            file = request.files['screenshot']
            if file and file.filename != '' and allowed_file(file.filename):
                # Delete old screenshot if exists
                if screenshot_filename:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], screenshot_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                
                # Save new screenshot
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                original_extension = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{session['username']}_{ticker}_{date}_{timestamp}.{original_extension}"
                filename = secure_filename(filename)
                
                try:
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    screenshot_filename = filename
                except Exception as e:
                    flash(f'Error uploading screenshot: {str(e)}', 'error')
        
        conn.execute('''
            UPDATE trades 
            SET ticker=?, direction=?, date=?, outcome=?, close_reason=?, account_pnl=?, notes=?, screenshot_filename=?
            WHERE id=? AND user_id=?
        ''', (ticker, direction, date, outcome, close_reason, account_pnl, notes, screenshot_filename, trade_id, session['user_id']))
        conn.commit()
        conn.close()
        
        flash('Trade updated successfully!', 'success')
        return redirect(url_for('index'))
    
    trade = conn.execute('SELECT * FROM trades WHERE id = ? AND user_id = ?', (trade_id, session['user_id'])).fetchone()
    conn.close()
    
    if trade is None:
        flash('Trade not found or access denied.', 'error')
        return redirect(url_for('index'))
    
    return render_template('edit_trade.html', trade=trade)

@app.route('/delete_trade/<int:trade_id>')
@login_required
def delete_trade(trade_id):
    conn = get_db_connection()
    
    # Get screenshot filename before deleting and verify ownership
    trade = conn.execute('SELECT screenshot_filename FROM trades WHERE id = ? AND user_id = ?', (trade_id, session['user_id'])).fetchone()
    
    if not trade:
        flash('Trade not found or access denied.', 'error')
        return redirect(url_for('index'))
    
    if trade['screenshot_filename']:
        # Delete screenshot file
        screenshot_path = os.path.join(app.config['UPLOAD_FOLDER'], trade['screenshot_filename'])
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)
    
    conn.execute('DELETE FROM trades WHERE id = ? AND user_id = ?', (trade_id, session['user_id']))
    conn.commit()
    conn.close()
    
    flash('Trade deleted successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/trade_detail/<int:trade_id>')
@login_required
def trade_detail(trade_id):
    conn = get_db_connection()
    trade = conn.execute('SELECT * FROM trades WHERE id = ? AND user_id = ?', (trade_id, session['user_id'])).fetchone()
    conn.close()
    
    if trade is None:
        flash('Trade not found or access denied.', 'error')
        return redirect(url_for('index'))
    
    return render_template('trade_detail.html', trade=trade)

@app.route('/advanced_stats')
@login_required
def advanced_stats():
    # Get selected user for viewing (default to current user)
    view_user_id = request.args.get('user', session['user_id'], type=int)
    
    conn = get_db_connection()
    
    # Get user info for the profile being viewed
    viewed_user = conn.execute('SELECT * FROM users WHERE id = ?', (view_user_id,)).fetchone()
    if not viewed_user:
        # If user not found, default to current user
        view_user_id = session['user_id']
        viewed_user = conn.execute('SELECT * FROM users WHERE id = ?', (view_user_id,)).fetchone()
    
    # Get all users for profile switcher
    all_users = conn.execute('SELECT id, username, display_name FROM users ORDER BY display_name').fetchall()
    
    trades = conn.execute('''
        SELECT * FROM trades 
        WHERE user_id = ?
        ORDER BY date DESC, created_at DESC
    ''', (view_user_id,)).fetchall()
    conn.close()
    
    if not trades:
        return render_template('advanced_stats.html', 
                             trades=[], 
                             exit_stats={}, 
                             performance_trends={},
                             viewed_user=viewed_user,
                             all_users=all_users,
                             current_view_user_id=view_user_id)
    
    # Exit Reason Analysis
    exit_reasons = {}
    for trade in trades:
        reason = trade['close_reason']
        if reason not in exit_reasons:
            exit_reasons[reason] = {'trades': [], 'total_pnl': 0, 'count': 0}
        
        exit_reasons[reason]['trades'].append(trade)
        exit_reasons[reason]['total_pnl'] += trade['account_pnl']
        exit_reasons[reason]['count'] += 1
    
    # Calculate exit reason stats
    exit_stats = {}
    for reason, data in exit_reasons.items():
        wins = len([t for t in data['trades'] if t['account_pnl'] > 0])
        losses = len([t for t in data['trades'] if t['account_pnl'] < 0])
        breakevens = len([t for t in data['trades'] if t['account_pnl'] == 0])
        
        exit_stats[reason] = {
            'avg_return': round(data['total_pnl'] / data['count'], 2),
            'win_rate': round((wins / data['count']) * 100, 1),
            'frequency': round((data['count'] / len(trades)) * 100, 1),
            'count': data['count'],
            'wins': wins,
            'losses': losses,
            'breakevens': breakevens,
            'total_pnl': round(data['total_pnl'], 2)
        }
    
    # Performance Trends calculation
    from datetime import datetime
    import calendar
    
    # Monthly performance
    monthly_stats = {}
    daily_stats = {'Monday': [], 'Tuesday': [], 'Wednesday': [], 'Thursday': [], 'Friday': [], 'Saturday': [], 'Sunday': []}
    
    for trade in trades:
        # Monthly stats
        trade_date = datetime.strptime(trade['date'], '%Y-%m-%d')
        month_key = trade_date.strftime('%Y-%m')
        month_name = trade_date.strftime('%B %Y')
        
        if month_key not in monthly_stats:
            monthly_stats[month_key] = {'trades': [], 'month_name': month_name}
        monthly_stats[month_key]['trades'].append(trade)
        
        # Daily stats
        day_name = trade_date.strftime('%A')
        daily_stats[day_name].append(trade)
    
    # Calculate monthly performance
    monthly_performance = {}
    for month_key, data in monthly_stats.items():
        trades_list = data['trades']
        wins = len([t for t in trades_list if t['account_pnl'] > 0])
        total_pnl = sum([t['account_pnl'] for t in trades_list])
        
        monthly_performance[month_key] = {
            'month_name': data['month_name'],
            'total_trades': len(trades_list),
            'win_rate': round((wins / len(trades_list)) * 100, 1) if trades_list else 0,
            'total_pnl': round(total_pnl, 2),
            'avg_trade': round(total_pnl / len(trades_list), 2) if trades_list else 0
        }
    
    # Calculate daily performance
    daily_performance = {}
    for day, trades_list in daily_stats.items():
        if trades_list:
            wins = len([t for t in trades_list if t['account_pnl'] > 0])
            total_pnl = sum([t['account_pnl'] for t in trades_list])
            
            daily_performance[day] = {
                'total_trades': len(trades_list),
                'win_rate': round((wins / len(trades_list)) * 100, 1),
                'total_pnl': round(total_pnl, 2),
                'avg_trade': round(total_pnl / len(trades_list), 2)
            }
        else:
            daily_performance[day] = {
                'total_trades': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'avg_trade': 0
            }
    
    # Calculate streaks
    current_streak = 0
    longest_win_streak = 0
    longest_loss_streak = 0
    temp_win_streak = 0
    temp_loss_streak = 0
    
    # Sort trades by date for streak calculation
    sorted_trades = sorted(trades, key=lambda x: x['date'])
    
    for trade in sorted_trades:
        if trade['account_pnl'] > 0:  # Win
            temp_win_streak += 1
            temp_loss_streak = 0
            longest_win_streak = max(longest_win_streak, temp_win_streak)
        elif trade['account_pnl'] < 0:  # Loss
            temp_loss_streak += 1
            temp_win_streak = 0
            longest_loss_streak = max(longest_loss_streak, temp_loss_streak)
        # Breakeven doesn't break streaks in this logic
    
    # Current streak (last trade determines)
    if sorted_trades:
        if sorted_trades[-1]['account_pnl'] > 0:
            current_streak = temp_win_streak
        elif sorted_trades[-1]['account_pnl'] < 0:
            current_streak = -temp_loss_streak
        else:
            current_streak = 0
    
    performance_trends = {
        'monthly': dict(sorted(monthly_performance.items(), reverse=True)),
        'daily': daily_performance,
        'streaks': {
            'current': current_streak,
            'longest_win': longest_win_streak,
            'longest_loss': longest_loss_streak
        }
    }
    
    return render_template('advanced_stats.html', 
                         trades=trades, 
                         exit_stats=exit_stats, 
                         performance_trends=performance_trends,
                         viewed_user=viewed_user,
                         all_users=all_users,
                         current_view_user_id=view_user_id)

# NEW CALENDAR ROUTES

@app.route('/calendar')
@login_required
def calendar_view():
    # Get current month/year from query params or default to current
    year = request.args.get('year', datetime.now().year, type=int)
    month = request.args.get('month', datetime.now().month, type=int)
    
    # Handle month/year navigation
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1
    
    conn = get_db_connection()
    
    # Get all economic events for the current month
    events = conn.execute('''
        SELECT * FROM economic_events 
        WHERE user_id = ? AND strftime('%Y-%m', event_date) = ?
        ORDER BY event_date ASC
    ''', (session['user_id'], f"{year:04d}-{month:02d}")).fetchall()
    
    # Get trading days with P&L for the month
    trades = conn.execute('''
        SELECT date, SUM(account_pnl) as daily_pnl, COUNT(*) as trade_count
        FROM trades 
        WHERE user_id = ? AND strftime('%Y-%m', date) = ?
        GROUP BY date
        ORDER BY date ASC
    ''', (session['user_id'], f"{year:04d}-{month:02d}")).fetchall()
    
    conn.close()
    
    # Create calendar data structure
    month_calendar = cal.monthcalendar(year, month)
    
    # Convert events to dict by date for easy lookup
    events_by_date = {}
    for event in events:
        event_date = event['event_date']
        if event_date not in events_by_date:
            events_by_date[event_date] = []
        events_by_date[event_date].append(event)
    
    # Convert trades to dict by date for easy lookup
    trades_by_date = {}
    for trade in trades:
        trades_by_date[trade['date']] = {
            'pnl': trade['daily_pnl'],
            'count': trade['trade_count']
        }
    
    # Calculate navigation dates
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    return render_template('calendar.html',
                         calendar_data=month_calendar,
                         current_year=year,
                         current_month=month,
                         month_name=cal.month_name[month],
                         events_by_date=events_by_date,
                         trades_by_date=trades_by_date,
                         prev_month=prev_month,
                         prev_year=prev_year,
                         next_month=next_month,
                         next_year=next_year,
                         today=datetime.now().date())

@app.route('/add_event', methods=['GET', 'POST'])
@login_required
def add_event():
    if request.method == 'POST':
        event_type = request.form['event_type']
        event_date = request.form['event_date']
        title = request.form['title'].strip()
        description = request.form.get('description', '').strip()
        importance = request.form['importance']
        
        # Get source URL based on event type
        source_urls = {
            'FOMC': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
            'NFP': 'https://www.bls.gov/schedule/news_release/empsit.htm',
            'WASDE': 'https://www.usda.gov/about-usda/general-information/staff-offices/office-chief-economist/commodity-markets/wasde-report',
            'Petroleum': 'https://www.eia.gov/petroleum/supply/weekly/schedule.php',
            'Other': ''
        }
        source_url = source_urls.get(event_type, '')
        
        # Validate required fields
        if not all([event_type, event_date, title]):
            flash('Event type, date, and title are required.', 'error')
            return render_template('add_event.html')
        
        # Insert into database
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO economic_events (user_id, event_type, event_date, title, description, importance, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (session['user_id'], event_type, event_date, title, description, importance, source_url))
        conn.commit()
        conn.close()
        
        flash(f'Event "{title}" added successfully!', 'success')
        return redirect(url_for('calendar_view'))
    
    # For GET request, get the date from query parameter if provided
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    return render_template('add_event.html', selected_date=selected_date)

@app.route('/edit_event/<int:event_id>', methods=['GET', 'POST'])
@login_required
def edit_event(event_id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        event_type = request.form['event_type']
        event_date = request.form['event_date']
        title = request.form['title'].strip()
        description = request.form.get('description', '').strip()
        importance = request.form['importance']
        
        # Get source URL based on event type
        source_urls = {
            'FOMC': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
            'NFP': 'https://www.bls.gov/schedule/news_release/empsit.htm',
            'WASDE': 'https://www.usda.gov/about-usda/general-information/staff-offices/office-chief-economist/commodity-markets/wasde-report',
            'Petroleum': 'https://www.eia.gov/petroleum/supply/weekly/schedule.php',
            'Other': ''
        }
        source_url = source_urls.get(event_type, '')
        
        conn.execute('''
            UPDATE economic_events 
            SET event_type=?, event_date=?, title=?, description=?, importance=?, source_url=?
            WHERE id=? AND user_id=?
        ''', (event_type, event_date, title, description, importance, source_url, event_id, session['user_id']))
        conn.commit()
        conn.close()
        
        flash('Event updated successfully!', 'success')
        return redirect(url_for('calendar_view'))
    
    event = conn.execute('SELECT * FROM economic_events WHERE id = ? AND user_id = ?', (event_id, session['user_id'])).fetchone()
    conn.close()
    
    if event is None:
        flash('Event not found or access denied.', 'error')
        return redirect(url_for('calendar_view'))
    
    return render_template('edit_event.html', event=event)

@app.route('/delete_event/<int:event_id>')
@login_required
def delete_event(event_id):
    conn = get_db_connection()
    
    # Verify ownership before deleting
    event = conn.execute('SELECT title FROM economic_events WHERE id = ? AND user_id = ?', (event_id, session['user_id'])).fetchone()
    
    if not event:
        flash('Event not found or access denied.', 'error')
        return redirect(url_for('calendar_view'))
    
    conn.execute('DELETE FROM economic_events WHERE id = ? AND user_id = ?', (event_id, session['user_id']))
    conn.commit()
    conn.close()
    
    flash(f'Event "{event["title"]}" deleted successfully!', 'success')
    return redirect(url_for('calendar_view'))

if __name__ == '__main__':
    init_db()
    print(f"Starting Trading Journal App...")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    # For Railway deployment, use PORT from environment
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
