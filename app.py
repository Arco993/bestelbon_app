import os
import secrets
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Order, OrderLine, Supplier, Department, Attachment

app = Flask(__name__)

# --- CONFIGURATIE ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'bestelbonnen.db')
app.config['SECRET_KEY'] = 'supergeheim-delacroix-2026'
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg'}

# --- ROUTES ---
@app.route('/')
def index(): return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            login_user(user); return redirect(url_for('dashboard'))
        flash('Foutieve inloggegevens.', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard(): return render_template('dashboard.html')

@app.route('/setup')
@login_required
def setup():
    if current_user.role not in ['Admin', 'BO']: return redirect(url_for('dashboard'))
    users = User.query.all()
    depts = Department.query.all()
    approvers = User.query.filter(User.role.in_(['BO', 'Directie', 'Admin'])).all()
    return render_template('setup.html', users=users, departments=depts, approvers=approvers)

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    d_id = request.form.get('department_id')
    a_id = request.form.get('approver_id')
    u = User(
        username=request.form.get('username'),
        email=request.form.get('email'),
        password=request.form.get('password'),
        role=request.form.get('role'),
        department_id=int(d_id) if d_id and d_id != 'None' else None,
        approver_id=int(a_id) if a_id and a_id != 'None' else None,
        can_manage_staff=True if request.form.get('can_manage_staff') == 'on' else False,
        allow_auto_approve=True if request.form.get('allow_auto_approve') == 'on' else False,
        auto_approve_limit=float(request.form.get('auto_approve_limit') or 0),
        max_bo_limit=float(request.form.get('max_bo_limit') or 1000)
    )
    db.session.add(u); db.session.commit()
    flash('Gebruiker aangemaakt', 'success'); return redirect(url_for('setup'))

@app.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user(user_id):
    u = User.query.get_or_404(user_id)
    u.username = request.form.get('username')
    u.role = request.form.get('role')
    d_id = request.form.get('department_id')
    u.department_id = int(d_id) if d_id and d_id != 'None' else None
    u.can_manage_staff = True if request.form.get('can_manage_staff') == 'on' else False
    u.allow_auto_approve = True if request.form.get('allow_auto_approve') == 'on' else False
    u.auto_approve_limit = float(request.form.get('auto_approve_limit') or 0)
    db.session.commit(); flash('Gebruiker bijgewerkt', 'success'); return redirect(url_for('setup'))

@app.route('/admin/departments', methods=['GET', 'POST'])
@login_required
def manage_departments():
    if request.method == 'POST':
        dir_id = request.form.get('director_id')
        d = Department(name=request.form.get('name'), code=request.form.get('code').upper(),
                       director_id=int(dir_id) if dir_id and dir_id != 'None' else None)
        db.session.add(d); db.session.commit()
    return render_template('manage_departments.html', departments=Department.query.all(), directors=User.query.filter_by(role='Directie').all())

@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def new_order():
    if request.method == 'POST':
        # Logica voor opslaan (BTW-terugrekening & Status)
        descs, qtys, p_incls, taxes = request.form.getlist('desc[]'), request.form.getlist('qty[]'), request.form.getlist('price_incl[]'), request.form.getlist('tax[]')
        total_inc = sum(float(qtys[i] or 0) * float(p_incls[i] or 0) for i in range(len(descs)))
        status = 'Wachten op BO' if current_user.approver_id else 'Goedgekeurd'
        if current_user.allow_auto_approve and total_inc <= current_user.auto_approve_limit: status = 'Goedgekeurd'
        
        order = Order(order_number=request.form.get('generated_order_number'), user_id=current_user.id, status=status, total_amount=total_inc)
        db.session.add(order); db.session.flush()
        for i in range(len(descs)):
            p_excl = float(p_incls[i]) / (1 + (float(taxes[i])/100))
            db.session.add(OrderLine(order_id=order.id, description=descs[i], quantity=float(qtys[i]), unit_price=p_excl, tax_rate=float(taxes[i])))
        db.session.commit(); return redirect(url_for('my_orders'))

    ref = f"{current_user.dept.code if current_user.department_id else 'GEN'}-{datetime.now().year}-{secrets.token_hex(2).upper()}"
    ap_name = current_user.approver.username if current_user.approver_id else "Direct"
    return render_template('new_order.html', next_ref=ref, approver_name=ap_name)

@app.route('/my_orders')
@login_required
def my_orders():
    return render_template('my_orders.html', orders=Order.query.filter_by(user_id=current_user.id).all())

@app.route('/order/<int:order_id>')
@login_required
def order_detail(order_id):
    return render_template('order_detail.html', order=Order.query.get_or_404(order_id))

@app.route('/approve_list')
@login_required
def approve_list():
    orders = Order.query.filter(Order.status.contains('Wachten')).all()
    return render_template('approve_list.html', orders=orders)

@app.route('/order/approve/<int:order_id>')
@login_required
def approve_order(order_id):
    order = Order.query.get_or_404(order_id)
    if current_user.role == 'BO':
        order.status = 'Wachten op Directie' if order.total_amount > order.user.max_bo_limit else 'Goedgekeurd'
        order.bo_name = current_user.username
    elif current_user.role == 'Directie':
        order.status = 'Goedgekeurd'; order.dir_name = current_user.username
    db.session.commit(); return redirect(url_for('approve_list'))

@app.route('/admin/all_orders')
@login_required
def all_orders():
    return render_template('all_orders.html', orders=Order.query.all(), title="Alle Bestelbonnen")

@app.route('/bo/department_archive')
@login_required
def department_archive():
    orders = Order.query.join(User).filter(User.department_id == current_user.department_id).all()
    return render_template('all_orders.html', orders=orders, title="Afdelingsarchief")

@app.route('/my_approvals')
@login_required
def my_approvals():
    orders = Order.query.filter((Order.bo_name == current_user.username) | (Order.dir_name == current_user.username)).all()
    return render_template('approved_history.html', orders=orders)

@app.route('/logout')
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/setup-demo')
def setup_demo():
    db.drop_all(); db.create_all()
    db.session.add(User(username='Arne', password='test', role='Admin'))
    db.session.commit(); return "✅ Systeem gereset. Log in: Arne / test"

if __name__ == '__main__':
    app.run(debug=True)