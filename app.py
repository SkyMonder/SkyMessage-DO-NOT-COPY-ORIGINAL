import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_, and_
from extensions import db
from models import User, Chat, Message

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'supersecret-dev')

# --- Render DB fix: postgres:// → postgresql://
db_url = os.environ.get('DATABASE_URL', 'sqlite:///local.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# --- Utilities ---
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

# --- Routes ---
@app.get('/')
def welcome():
    return render_template('welcome.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        if not username or not password:
            return render_template('register.html', error='Введите логин и пароль')
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Логин уже занят')
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('chats'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('chats'))
        return render_template('login.html', error='Неверный логин или пароль')
    return render_template('login.html')

@app.get('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('welcome'))

@app.get('/chats')
@login_required
def chats():
    return render_template('chats.html')

# --- API ---
@app.get('/api/me')
def api_me():
    u = current_user()
    if not u:
        return jsonify({'user': None})
    return jsonify({'user': {'id': u.id, 'username': u.username}})

@app.get('/api/chats')
@login_required
def api_chats():
    u = current_user()
    chats = Chat.query.filter(or_(Chat.user1_id==u.id, Chat.user2_id==u.id)).all()
    result = []
    for c in chats:
        other_id = c.user2_id if c.user1_id == u.id else c.user1_id
        other = User.query.get(other_id)
        last = Message.query.filter_by(chat_id=c.id).order_by(Message.timestamp.desc()).first()
        result.append({
            'id': c.id,
            'peer': {'id': other.id, 'username': other.username},
            'last': {'text': last.text if last else '', 'timestamp': last.timestamp.isoformat() if last else None}
        })
    result.sort(key=lambda x: x['last']['timestamp'] or '', reverse=True)
    return jsonify(result)

@app.get('/api/messages/<int:chat_id>')
@login_required
def api_messages(chat_id):
    u = current_user()
    chat = Chat.query.get_or_404(chat_id)
    if u.id not in (chat.user1_id, chat.user2_id):
        abort(403)
    msgs = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    return jsonify([{
        'id': m.id,
        'chat_id': m.chat_id,
        'sender_id': m.sender_id,
        'text': m.text,
        'timestamp': m.timestamp.isoformat()
    } for m in msgs])

@app.post('/api/search_user')
@login_required
def api_search_user():
    u = current_user()
    data = request.json or {}
    q = (data.get('query') or '').strip()
    if not q:
        return jsonify({'error': 'empty_query'}), 400

    exact = User.query.filter(and_(User.username==q, User.id!=u.id)).first()
    if exact:
        return jsonify({'users':[{'id': exact.id, 'username': exact.username}]})

    candidates = User.query.filter(and_(User.username.ilike(f"%{q}%"), User.id!=u.id)) \
                           .order_by(User.username).limit(10).all()
    result = [{'id': c.id, 'username': c.username} for c in candidates]
    return jsonify({'users': result})

@app.post('/api/create_chat')
@login_required
def api_create_chat():
    u = current_user()
    data = request.json or {}
    peer_id = data.get('peer_id')
    if not peer_id:
        return jsonify({'error':'peer_id_required'}), 400
    if int(peer_id) == u.id:
        return jsonify({'error':'cannot_chat_with_self'}), 400
    a, b = sorted([u.id, int(peer_id)])
    chat = Chat.query.filter_by(user1_id=a, user2_id=b).first()
    if not chat:
        chat = Chat(user1_id=a, user2_id=b)
        db.session.add(chat)
        db.session.commit()
    return jsonify({'chat_id': chat.id})

@app.post('/api/send_message')
@login_required
def api_send_message():
    u = current_user()
    data = request.json or {}
    chat_id = int(data.get('chat_id', 0))
    text = (data.get('text') or '').strip()
    if not chat_id or not text:
        return jsonify({'error':'chat_id_and_text_required'}), 400
    chat = Chat.query.get_or_404(chat_id)
    if u.id not in (chat.user1_id, chat.user2_id):
        abort(403)
    msg = Message(chat_id=chat.id, sender_id=u.id, text=text)
    db.session.add(msg)
    db.session.commit()

    payload = {
        'id': msg.id,
        'chat_id': chat.id,
        'sender_id': u.id,
        'text': msg.text,
        'timestamp': msg.timestamp.isoformat()
    }

    room = f"chat_{chat.id}"
    socketio.emit('message', payload, room=room, include_self=False)  # ⚡ фиксим дубли для отправителя

    return jsonify(payload)

# --- Socket.IO events ---
@socketio.on('join_chat')
def on_join_chat(data):
    chat_id = int(data.get('chat_id', 0))
    if chat_id:
        join_room(f"chat_{chat_id}")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

