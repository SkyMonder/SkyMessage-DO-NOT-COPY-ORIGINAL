import os
from flask import Flask, render_template, request, session, jsonify, abort
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from extensions import db
from models import User, Chat, ChatMembers, Message, Call

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY','supersecret-dev')
db_url = os.environ.get('DATABASE_URL','sqlite:///local.db')
if db_url.startswith("postgres://"): db_url = db_url.replace("postgres://","postgresql://",1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

def current_user():
    uid = session.get('user_id')
    return User.query.get(uid) if uid else None

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user(): 
            return jsonify({'error':'unauthorized'}), 401
        return fn(*args, **kwargs)
    return wrapper

# --- Frontend ---
@app.route('/', methods=['GET'])
def welcome():
    return render_template('chats.html')

# --- Auth ---
@app.route('/register', methods=['POST','GET'])
def register():
    if request.method=='GET': return render_template('register.html')
    username = request.json.get('username','').strip()
    password = request.json.get('password','').strip()
    if not username or not password: return jsonify({'error':'empty'}),400
    if User.query.filter_by(username=username).first(): return jsonify({'error':'exists'}),400
    user = User(username=username,password_hash=generate_password_hash(password))
    db.session.add(user); db.session.commit()
    session['user_id'] = user.id
    return jsonify({'ok':True})

@app.route('/login', methods=['POST','GET'])
def login():
    if request.method=='GET': return render_template('login.html')
    username = request.json.get('username','').strip()
    password = request.json.get('password','').strip()
    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password_hash,password):
        session['user_id']=user.id
        return jsonify({'ok':True})
    return jsonify({'error':'wrong'}),401

@app.route('/logout', methods=['GET','POST'])
def logout(): 
    session.pop('user_id',None)
    return jsonify({'ok':True})

# --- API ---
@app.route('/api/me', methods=['GET','POST'])
def api_me():
    u=current_user()
    return jsonify({'user':{'id':u.id,'username':u.username,'theme':u.theme,'avatar':u.avatar} if u else None})

@app.route('/api/set_theme', methods=['POST'])
@login_required
def set_theme():
    u=current_user(); t=request.json.get('theme')
    if t in ['dark','light']: u.theme=t; db.session.commit()
    return jsonify({'theme':u.theme})

@app.route('/api/chats', methods=['GET','POST'])
@login_required
def api_chats():
    u=current_user()
    chats = u.chats
    result=[]
    for c in chats:
        members=[{'id': m.id, 'username': m.username} for m in c.members if m.id != u.id]
        last = Message.query.filter_by(chat_id=c.id).order_by(Message.timestamp.desc()).first()
        result.append({'id':c.id,'name':c.name,'is_group':c.is_group,
                       'members':members,
                       'last':{'text':last.text if last else '', 'timestamp':last.timestamp.isoformat() if last else None}})
    result.sort(key=lambda x:x['last']['timestamp'] or '',reverse=True)
    return jsonify(result)

@app.route('/api/messages/<int:chat_id>', methods=['GET','POST'])
@login_required
def api_messages(chat_id):
    u=current_user(); chat=Chat.query.get_or_404(chat_id)
    if u not in chat.members: abort(403)
    msgs = Message.query.filter_by(chat_id=chat_id).order_by(Message.timestamp.asc()).all()
    return jsonify([{'id':m.id,'chat_id':m.chat_id,'sender_id':m.sender_id,'text':m.text,'media':m.media,'timestamp':m.timestamp.isoformat()} for m in msgs])

@app.route('/api/send_message', methods=['POST','GET'])
@login_required
def api_send_message():
    u=current_user(); d=request.json
    chat_id=int(d.get('chat_id',0)); text=(d.get('text') or '').strip(); media=d.get('media')
    if not chat_id or not (text or media): return jsonify({'error':'empty'}),400
    chat=Chat.query.get_or_404(chat_id)
    if u not in chat.members: abort(403)
    msg=Message(chat_id=chat.id,sender_id=u.id,text=text,media=media)
    db.session.add(msg); db.session.commit()
    payload={'id':msg.id,'chat_id':chat.id,'sender_id':u.id,'text':msg.text,'media':msg.media,'timestamp':msg.timestamp.isoformat()}
    socketio.emit('message',payload,room=f"chat_{chat.id}")
    return jsonify(payload)

@app.route('/api/search_user', methods=['POST','GET'])
@login_required
def api_search_user():
    query = request.json.get('query','').strip()
    if not query: return jsonify({'user':None})
    user = User.query.filter(User.username.ilike(f"%{query}%")).first()
    return jsonify({'user':{'id':user.id,'username':user.username} if user else None})

@app.route('/api/create_chat', methods=['POST','GET'])
@login_required
def api_create_chat():
    peer_id = request.json.get('peer_id')
    u = current_user()
    peer = User.query.get_or_404(peer_id)
    # Проверяем, есть ли уже чат 1 на 1
    chat = Chat.query.filter(Chat.is_group==False, Chat.members.any(id=u.id), Chat.members.any(id=peer.id)).first()
    if not chat:
        chat = Chat(name='',is_group=False)
        db.session.add(chat)
        db.session.commit()
        # добавляем участников
        chat.members.append(u)
        chat.members.append(peer)
        db.session.commit()
    return jsonify({'chat_id':chat.id})

# --- Socket.IO ---
@socketio.on('join_chat')
def join(data):
    join_room(f"chat_{data.get('chat_id')}")

@socketio.on('call_user')
def handle_call(data):
    callee=data.get('callee_id'); chat_id=data.get('chat_id'); caller=current_user()
    socketio.emit('incoming_call',{'caller_id':caller.id,'chat_id':chat_id},room=f"user_{callee}")

@socketio.on('answer_call')
def handle_answer(data):
    caller_id=data.get('caller_id'); chat_id=data.get('chat_id'); status=data.get('status')
    socketio.emit('call_answered',{'chat_id':chat_id,'status':status},room=f"user_{caller_id}")

# --- Init DB and Run ---
if __name__ == '__main__':
    with app.app_context(): db.create_all()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT",5000)), allow_unsafe_werkzeug=True)
