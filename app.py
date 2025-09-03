import os
from flask import Flask, render_template, request, session, jsonify, abort
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from extensions import db
from models import User, Chat, ChatMembers, Message, Call

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY','supersecret-dev')

# DATABASE_URL
db_url = os.environ.get('postgresql://skybasemessage_user:HdT8RMSKCocfaMENmYOFVr9EaUpsIHjh@dpg-d2s9bsndiees73bg55qg-a/skybasemessage')
if not db_url:
    raise RuntimeError("DATABASE_URL environment variable is required")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# --- Остальной код без изменений ---
# ... весь твой существующий код app.py остаётся
