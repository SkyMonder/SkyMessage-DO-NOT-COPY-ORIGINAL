import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort, send_from_directory
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from extensions import db
from models import User, Chat, Message
from functools import wraps

# --- App ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'supersecret-dev')

# --- DB URL fix ---
db_url = os.environ.get('DATABASE_URL', 'sqlite:///local.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# --- Uploads ---
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB limit

# --- SocketIO ---
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# --- Security helpers ---
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)

def login_required(fn):
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

# --- Media ---
@app.post('/api/upload')
@login_required
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error':'No file provided'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error':'Empty filename'}), 400
    filename = secure_filename(file.filename)
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)
    return jsonify({'url': f'/uploads/{filename}'})

@app.get('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- API and messaging ---
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
            'last': {'text': last.text if last else '', 'file_url': last.file_url if last else None,
                     'timestamp': last.timestamp.isoformat() if last else None}
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
        'file_url': m.file_url,
        'timestamp': m.timestamp.isoformat()
    } for m in msgs])

@app.post('/api/create_chat')
@login_required
def api_create_chat():
    u = current_user()
    peer_id = int((request.json or {}).get('peer_id', 0))
    if not peer_id or peer_id == u.id:
        return jsonify({'error':'Invalid peer_id'}), 400
    a, b = sorted([u.id, peer_id])
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
    chat_id = int(data.get('chat_id',0))
    text = (data.get('text') or '').strip()
    file_url = data.get('file_url')
    if not chat_id or (not text and not file_url):
        return jsonify({'error':'chat_id_and_text_or_file_required'}), 400
    chat = Chat.query.get_or_404(chat_id)
    if u.id not in (chat.user1_id, chat.user2_id):
        abort(403)
    msg = Message(chat_id=chat.id, sender_id=u.id, text=text, file_url=file_url)
    db.session.add(msg)
    db.session.commit()
    payload = {
        'id': msg.id,
        'chat_id': chat.id,
        'sender_id': u.id,
        'text': msg.text,
        'file_url': msg.file_url,
        'timestamp': msg.timestamp.isoformat()
    }
    room = f"chat_{chat.id}"
    socketio.emit('message', payload, room=room, include_self=False)  # не дублируем для отправителя
    return jsonify(payload)

# --- SocketIO ---
@socketio.on('join_chat')
def on_join_chat(data):
    chat_id = int(data.get('chat_id',0))
    if chat_id:
        join_room(f"chat_{chat_id}")

# --- WebRTC звонки ---
@socketio.on('call_offer')
def handle_call_offer(data):
    room = f"chat_{data['chat_id']}"
    socketio.emit('receive_offer', data, room=room, include_self=False)

@socketio.on('call_answer')
def handle_call_answer(data):
    room = f"chat_{data['chat_id']}"
    socketio.emit('receive_answer', data, room=room, include_self=False)

@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    room = f"chat_{data['chat_id']}"
    socketio.emit('ice_candidate', data, room=room, include_self=False)

# --- Run ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT",5000)), allow_unsafe_werkzeug=True)
