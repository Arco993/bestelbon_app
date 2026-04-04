from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False) # Bijv: 'BO', 'Directie', 'Admin'
    department = db.Column(db.String(10)) # Bijv: 'TD'
    approver_id = db.Column(db.Integer, db.ForeignKey('user.id')) # Link naar hun goedkeurder

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    address = db.Column(db.String(200))
    email = db.Column(db.String(100))

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True) # Bijv: TD-2024-001
    status = db.Column(db.String(20), default='Concept')
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    attachment_path = db.Column(db.String(200)) # Pad naar de offerte PDF
    is_printed = db.Column(db.Boolean, default=False)
    total_amount = db.Column(db.Float, default=0.0)
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'))
    
    # De lijnen die bij deze bon horen
    lines = db.relationship('OrderLine', backref='order', lazy=True)

class OrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attachment_limit = db.Column(db.Float, default=500.0)
