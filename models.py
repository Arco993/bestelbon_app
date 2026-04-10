from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False) 
    department = db.Column(db.String(50))
    department_code = db.Column(db.String(5))
    is_active = db.Column(db.Boolean, default=True)
    
    min_attachment_limit = db.Column(db.Float, default=500.0) 
    max_bo_limit = db.Column(db.Float, default=1000.0)
    auto_approve_limit = db.Column(db.Float, default=50.0)
            
    approver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    dark_mode = db.Column(db.Boolean, default=False)
    email_notification_freq = db.Column(db.String(20), default='Direct')
    digest_time = db.Column(db.String(5), default='08:00')
    
    orders = db.relationship('Order', backref='user', lazy=True)

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    street = db.Column(db.String(100))
    house_number = db.Column(db.String(10))
    zip_code = db.Column(db.String(20))
    city = db.Column(db.String(100))
    country = db.Column(db.String(100), default="België")
    vat_number = db.Column(db.String(20))

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(30), default='Concept')
    reference = db.Column(db.String(200))
    total_amount = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'))
    
    attachment_filename = db.Column(db.String(255))
    rejection_reason = db.Column(db.Text)
    notify_on_update = db.Column(db.Boolean, default=True)
    notification_type = db.Column(db.String(20), default='Final')

    bo_approval_code = db.Column(db.String(50))
    bo_approval_date = db.Column(db.DateTime)
    bo_name = db.Column(db.String(100))
    
    dir_approval_code = db.Column(db.String(50))
    dir_approval_date = db.Column(db.DateTime)
    dir_name = db.Column(db.String(100))

    supplier = db.relationship('Supplier', backref='orders', lazy=True)
    lines = db.relationship('OrderLine', backref='order', lazy=True)

class OrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    product_code = db.Column(db.String(50))  # Nieuw: voor productcodes
    description = db.Column(db.String(200))
    internal_note = db.Column(db.Text)      # Nieuw: het vrije veld (alleen in de tool)
    quantity = db.Column(db.Integer)
    unit_price = db.Column(db.Float)
    tax_rate = db.Column(db.Float, default=21.0)