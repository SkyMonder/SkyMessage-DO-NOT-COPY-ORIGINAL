from extensions import db
from datetime import datetime

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    theme = db.Column(db.String(10), default='dark')  # 'light' или 'dark'
    avatar = db.Column(db.String(256))  # ссылка на изображение

class Chat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128))  # для группы/канала
    is_group = db.Column(db.Boolean, default=False)
    members = db.relationship('User', secondary='chat_members')

class ChatMembers(db.Model):
    __tablename__ = 'chat_members'
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'))
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    text = db.Column(db.Text)
    media = db.Column(db.String(256))  # фото/видео ссылка
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Call(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chat.id'))
    caller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    callee_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    status = db.Column(db.String(20), default='pending')  # pending, accepted, rejected
