import os
import secrets
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from models import db, User, Order, OrderLine, Supplier

app = Flask(__name__)

# --- CONFIGURATIE ---
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
os.makedirs(instance_path, exist_ok=True)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'bestelbonnen.db')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'supergeheim-delacroix')

# Mail instellingen
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

mail = Mail(app)

# Uploads
UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg'}

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def send_status_mail(recipient, subject, body):
    if recipient:
        try:
            msg = Message(subject, recipients=[recipient])
            msg.body = body
            # mail.send(msg) 
            print(f"E-mail simulatie naar {recipient}: {subject}")
        except Exception as e:
            print(f"E-mail fout: {e}")

# --- BASIS ROUTES ---
@app.route('/')
def index(): 
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Foutieve inloggegevens.', 'danger')
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard(): 
    return render_template('dashboard.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/toggle_theme', methods=['POST'])
@login_required
def toggle_theme():
    current_user.dark_mode = request.get_json().get('dark_mode', False)
    db.session.commit()
    return jsonify({'success': True})

# --- BESTELBON ROUTES ---
@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def new_order():
    if request.method == 'POST':
        gen_ref = request.form.get('generated_order_number')
        if Order.query.filter_by(order_number=gen_ref).first():
            gen_ref = f"{gen_ref}-{secrets.token_hex(2).upper()}"

        s_name = request.form.get('supplier_name')
        supplier = Supplier.query.filter_by(name=s_name).first() or Supplier(
            name=s_name, street=request.form.get('street'), house_number=request.form.get('house_number'), 
            zip_code=request.form.get('zip_code'), city=request.form.get('city'), vat_number=request.form.get('supplier_vat')
        )
        if not supplier.id: db.session.add(supplier); db.session.flush()

        descs, qtys, prices, taxes = request.form.getlist('desc[]'), request.form.getlist('qty[]'), request.form.getlist('price[]'), request.form.getlist('tax[]')
        total_inc = sum([(float(qtys[i] or 0) * float(prices[i] or 0)) * (1 + (float(taxes[i] or 0)/100)) for i in range(len(descs))])

        file = request.files.get('attachment')
        attachment_name = None
        if file and allowed_file(file.filename):
            attachment_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], attachment_name))
        elif request.form.get('action') == 'submit' and total_inc > current_user.min_attachment_limit:
            flash(f'Bijlage verplicht boven € {current_user.min_attachment_limit}', 'danger')
            return redirect(url_for('new_order'))

        status = 'Concept'
        if request.form.get('action') == 'submit':
            if not current_user.approver_id or total_inc <= current_user.auto_approve_limit:
                status = 'Goedgekeurd'
            else:
                status = 'Wachten op Directie' if current_user.role == 'BO' else 'Wachten op BO'

        order = Order(
            order_number=gen_ref, reference=request.form.get('reference'), user_id=current_user.id, 
            supplier_id=supplier.id, status=status, total_amount=total_inc, attachment_filename=attachment_name,
            notify_on_update=True if request.form.get('notify_on_update') == 'on' else False,
            notification_type=request.form.get('notification_type', 'Final')
        )
        db.session.add(order); db.session.flush()
        for i in range(len(descs)):
            db.session.add(OrderLine(order_id=order.id, description=descs[i], quantity=int(float(qtys[i])), unit_price=float(prices[i]), tax_rate=float(taxes[i])))
        
        db.session.commit()
        flash(f'Bon {gen_ref} verwerkt.', 'success')
        return redirect(url_for('my_orders'))

    next_ref = f"{current_user.department_code or 'GEN'}-{datetime.now().year}-{secrets.token_hex(2).upper()}"
    ap = User.query.get(current_user.approver_id) if current_user.approver_id else None
    return render_template('new_order.html', next_ref=next_ref, approver_name=ap.username if ap else "Direct goedgekeurd")

@app.route('/my_orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/order/<int:order_id>')
@login_required
def order_detail(order_id):
    return render_template('order_detail.html', order=Order.query.get_or_404(order_id))

@app.route('/order/<int:order_id>/pdf')
@login_required
def order_pdf(order_id):
    return render_template('order_pdf.html', order=Order.query.get_or_404(order_id))

# --- GOEDKEURINGS ROUTES ---
@app.route('/approve_list')
@login_required
def approve_list():
    if current_user.role not in ['BO', 'Directie', 'Admin']: return redirect(url_for('dashboard'))
    if current_user.role == 'Directie':
        orders = Order.query.filter_by(status='Wachten op Directie').all()
    elif current_user.role == 'BO':
        sub_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        orders = Order.query.filter(Order.user_id.in_(sub_ids), Order.status == 'Wachten op BO').all()
    else:
        orders = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).all()
    return render_template('approve_list.html', orders=orders)

@app.route('/my_approvals')
@login_required
def my_approvals():
    if current_user.role not in ['BO', 'Directie', 'Admin']: return redirect(url_for('dashboard'))
    orders = Order.query.filter((Order.bo_name == current_user.username) | (Order.dir_name == current_user.username)).order_by(Order.created_at.desc()).all()
    return render_template('approved_history.html', orders=orders)

@app.route('/order/approve/<int:order_id>')
@login_required
def approve_order(order_id):
    order = Order.query.get_or_404(order_id)
    stamp, now = secrets.token_hex(4).upper(), datetime.now()
    if current_user.role == 'BO' and order.status == 'Wachten op BO':
        order.bo_approval_code, order.bo_approval_date, order.bo_name = stamp, now, current_user.username
        order.status = 'Wachten op Directie' if order.total_amount > order.user.max_bo_limit else 'Goedgekeurd'
    elif current_user.role == 'Directie' and order.status == 'Wachten op Directie':
        order.dir_approval_code, order.dir_approval_date, order.dir_name, order.status = stamp, now, current_user.username, 'Goedgekeurd'
    
    db.session.commit()
    if order.notify_on_update and (order.status == 'Goedgekeurd' or order.notification_type == 'Every Step'):
        send_status_mail(order.user.email, f"Update {order.order_number}", f"Status: {order.status}")
    return redirect(url_for('approve_list'))

@app.route('/order/reject/<int:order_id>', methods=['POST'])
@login_required
def reject_order(order_id):
    order = Order.query.get_or_404(order_id)
    order.status, order.rejection_reason = 'Afgewezen', request.form.get('reason')
    db.session.commit()
    send_status_mail(order.user.email, f"Afgewezen: {order.order_number}", f"Reden: {order.rejection_reason}")
    return redirect(url_for('approve_list'))

# --- ADMIN & ZOEKEN ---
@app.route('/admin/all_orders')
@login_required
def all_orders():
    if current_user.role not in ['Directie', 'Admin']: return redirect(url_for('dashboard'))
    q_sup, q_st, q_dt = request.args.get('supplier', ''), request.args.get('status', ''), request.args.get('dept', '')
    query = Order.query.join(User).join(Supplier)
    if q_sup: query = query.filter(Supplier.name.ilike(f"%{q_sup}%"))
    if q_st: query = query.filter(Order.status == q_st)
    if q_dt: query = query.filter(User.department_code == q_dt)
    depts = [d[0] for d in db.session.query(User.department_code).distinct().all()]
    return render_template('all_orders.html', orders=query.order_by(Order.created_at.desc()).all(), departments=depts, q_supplier=q_sup, q_status=q_st, q_dept=q_dt)

@app.route('/search_supplier')
def search_supplier():
    q = request.args.get('q', '').lower()
    suppliers = Supplier.query.filter(Supplier.name.ilike(f'%{q}%')).all()
    return jsonify([{'name': s.name, 'street': s.street, 'num': s.house_number, 'zip': s.zip_code, 'city': s.city, 'vat': s.vat_number} for s in suppliers])

@app.route('/setup')
@login_required
def setup():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    return render_template('setup.html', users=User.query.all(), approvers=User.query.filter(User.role.in_(['BO', 'Directie', 'Admin'])).all())

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    email = request.form.get('email')
    username = request.form.get('username')

    # Check of username of email al bestaan
    existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
    if existing_user:
        flash('Fout: Gebruikersnaam of E-mailadres is al in gebruik!', 'danger')
        return redirect(url_for('setup'))

    app_id = request.form.get('approver_id')
    u = User(
        username=username, 
        email=email, 
        password=request.form.get('password'),
        role=request.form.get('role'), 
        department=request.form.get('department'), 
        department_code=request.form.get('department_code'),
        min_attachment_limit=float(request.form.get('min_attachment_limit') or 500),
        max_bo_limit=float(request.form.get('max_bo_limit') or 1000),
        auto_approve_limit=float(request.form.get('auto_approve_limit') or 50),
        approver_id=int(app_id) if app_id else None,
        email_notification_freq=request.form.get('email_notification_freq', 'Direct'),
        digest_time=request.form.get('digest_time', '08:00')
    )
    db.session.add(u)
    db.session.commit()
    flash(f'Gebruiker {u.username} succesvol toegevoegd.', 'success')
    return redirect(url_for('setup'))

@app.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user(user_id):
    u = User.query.get_or_404(user_id)
    u.username, u.email, u.role, u.department, u.department_code = request.form.get('username'), request.form.get('email'), request.form.get('role'), request.form.get('department'), request.form.get('department_code')
    u.min_attachment_limit, u.max_bo_limit, u.auto_approve_limit = float(request.form.get('min_attachment_limit') or 500), float(request.form.get('max_bo_limit') or 1000), float(request.form.get('auto_approve_limit') or 50)
    u.email_notification_freq, u.digest_time = request.form.get('email_notification_freq', 'Direct'), request.form.get('digest_time', '08:00')
    app_id = request.form.get('approver_id')
    u.approver_id = int(app_id) if app_id else None
    if request.form.get('password'): u.password = request.form.get('password')
    db.session.commit()
    return redirect(url_for('setup'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)