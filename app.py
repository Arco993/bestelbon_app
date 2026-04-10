import os
import secrets
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from models import db, User, Order, OrderLine, Supplier
from sqlalchemy import func

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
    # 1. Totaal aantal eigen bonnen
    my_orders_count = Order.query.filter_by(user_id=current_user.id).count()
    
    # 2. Totaalbedrag van eigen ingediende/goedgekeurde bonnen
    my_total_spent = db.session.query(func.sum(Order.total_amount)).filter(
        Order.user_id == current_user.id,
        Order.status.in_(['Wachten op BO', 'Wachten op Directie', 'Goedgekeurd'])
    ).scalar() or 0.0

    # 3. Te keuren bonnen (voor BO, Directie en Admin)
    pending_approvals = 0
    if current_user.role == 'Directie':
        pending_approvals = Order.query.filter_by(status='Wachten op Directie').count()
    elif current_user.role == 'BO':
        sub_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        pending_approvals = Order.query.filter(Order.user_id.in_(sub_ids), Order.status == 'Wachten op BO').count()
    elif current_user.role == 'Admin':
        pending_approvals = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).count()

    # 4. Laatste 5 bonnen voor snelle toegang
    recent_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).limit(5).all()

    return render_template('dashboard.html', 
                           my_orders_count=my_orders_count, 
                           my_total_spent=my_total_spent,
                           pending_approvals=pending_approvals,
                           recent_orders=recent_orders)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- BESTELBON ROUTES ---
@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def new_order():
    form_data = {}
    if request.method == 'POST':
        form_data = request.form
        gen_ref = request.form.get('generated_order_number')
        
        # Voorkom dubbele nummers
        if Order.query.filter_by(order_number=gen_ref).first():
            gen_ref = f"{gen_ref}-{secrets.token_hex(2).upper()}"

        s_name = request.form.get('supplier_name')
        if not s_name:
            flash('Leverancier is verplicht!', 'danger')
            return render_template('new_order.html', form_data=form_data, next_ref=gen_ref)

        supplier = Supplier.query.filter_by(name=s_name).first() or Supplier(
            name=s_name, street=request.form.get('street'), house_number=request.form.get('house_number'), 
            zip_code=request.form.get('zip_code'), city=request.form.get('city'), vat_number=request.form.get('supplier_vat')
        )
        if not supplier.id: 
            db.session.add(supplier)
            db.session.flush()

        # Haal de lijsten op voor de orderregels
        p_codes = request.form.getlist('product_code[]')
        descs = request.form.getlist('desc[]')
        notes = request.form.getlist('internal_note[]')
        qtys = request.form.getlist('qty[]')
        prices = request.form.getlist('price[]')
        taxes = request.form.getlist('tax[]')
        
        # Bereken totaal
        total_inc = sum([(float(qtys[i] or 0) * float(prices[i] or 0)) for i in range(len(descs))])

        # Bijlage verwerking
        file = request.files.get('attachment')
        attachment_name = None
        if file and allowed_file(file.filename):
            attachment_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], attachment_name))
        elif request.form.get('action') == 'submit' and total_inc > current_user.min_attachment_limit:
            flash(f'Bijlage verplicht boven € {current_user.min_attachment_limit}', 'danger')
            ap = User.query.get(current_user.approver_id) if current_user.approver_id else None
            return render_template('new_order.html', form_data=form_data, next_ref=gen_ref, approver_name=ap.username if ap else "Direct goedgekeurd")

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
        db.session.add(order)
        db.session.flush()

        # Sla de lijnen op inclusief de nieuwe velden
        for i in range(len(descs)):
            if descs[i]: # Alleen opslaan als er een omschrijving is
                db.session.add(OrderLine(
                    order_id=order.id, 
                    product_code=p_codes[i],
                    description=descs[i], 
                    internal_note=notes[i],
                    quantity=int(float(qtys[i] or 0)), 
                    unit_price=float(prices[i] or 0), 
                    tax_rate=float(taxes[i] or 0)
                ))
        
        db.session.commit()
        flash(f'Bon {gen_ref} verwerkt.', 'success')
        return redirect(url_for('my_orders'))

    # GET request
    next_ref = f"{current_user.department_code or 'GEN'}-{datetime.now().year}-{secrets.token_hex(2).upper()}"
    ap = User.query.get(current_user.approver_id) if current_user.approver_id else None
    return render_template('new_order.html', form_data={}, next_ref=next_ref, approver_name=ap.username if ap else "Direct goedgekeurd")

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
    
    # 1. BO Keuring
    if current_user.role == 'BO' and order.status == 'Wachten op BO':
        order.bo_approval_code, order.bo_approval_date, order.bo_name = stamp, now, current_user.username
        order.status = 'Wachten op Directie' if order.total_amount > order.user.max_bo_limit else 'Goedgekeurd'
        
    # 2. Directie Keuring (Aangepast: Directie mag nu ALTIJD goedkeuren als hij wacht)
    elif current_user.role == 'Directie' and order.status in ['Wachten op Directie', 'Wachten op BO']:
        order.dir_approval_code, order.dir_approval_date, order.dir_name = stamp, now, current_user.username
        order.status = 'Goedgekeurd' # Directie overrulet alles
    
    db.session.commit()
    if order.notify_on_update and (order.status == 'Goedgekeurd' or order.notification_type == 'Every Step'):
        send_status_mail(order.user.email, f"Update {order.order_number}", f"Status: {order.status}")
    return redirect(url_for('approve_list'))

# --- NIEUWE ROUTE VOOR DE PUSH KNOP ---
@app.route('/order/escalate/<int:order_id>', methods=['POST'])
@login_required
def escalate_order(order_id):
    order = Order.query.get_or_404(order_id)
    
    # Check of de ingelogde gebruiker de eigenaar is EN de bon bij de BO ligt
    if order.user_id == current_user.id and order.status == 'Wachten op BO':
        order.status = 'Wachten op Directie'
        db.session.commit()
        flash(f'Bon {order.order_number} is succesvol gepusht naar de Directie.', 'success')
        
    return redirect(request.referrer or url_for('my_orders'))

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
    existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
    if existing_user:
        flash('Fout: Gebruikersnaam of E-mailadres is al in gebruik!', 'danger')
        return redirect(url_for('setup'))

    app_id = request.form.get('approver_id')
    u = User(
        username=username, email=email, password=request.form.get('password'),
        role=request.form.get('role'), department=request.form.get('department'), department_code=request.form.get('department_code'),
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
    if current_user.role != 'Admin': 
        return redirect(url_for('dashboard'))
    
    u = User.query.get_or_404(user_id)
    try:
        u.username = request.form.get('username')
        u.email = request.form.get('email')
        u.role = request.form.get('role')
        u.department_code = request.form.get('department_code')
        app_id = request.form.get('approver_id')
        u.approver_id = int(app_id) if app_id and app_id != "" else None
        
        if request.form.get('password'):
            u.password = request.form.get('password')
            
        db.session.commit()
        flash(f'Gebruiker {u.username} succesvol bijgewerkt.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Fout bij bijwerken: {str(e)}', 'danger')
        
    return redirect(url_for('setup'))

@app.route('/setup-demo')
def setup_demo():
    try:
        db.drop_all()
        db.create_all()
        
        dell = Supplier(name="Dell Technologies", city="Brussel")
        bol = Supplier(name="Bol.com", city="Antwerpen")
        db.session.add_all([dell, bol])
        db.session.commit()

        koen = User(username='Koen', password='test', role='Directie', department_code='DIR')
        db.session.add(koen)
        db.session.commit()

        bert = User(username='Bert', password='test', role='BO', department_code='TD', approver_id=1, max_bo_limit=1000.0)
        db.session.add(bert)
        db.session.commit()

        stijn = User(username='Stijn', password='test', role='Personeel', department_code='TD', approver_id=2, auto_approve_limit=50.0)
        db.session.add(stijn)
        db.session.commit()
        
        arne = User(username='Arne', password='test', role='Admin', department_code='ICT')
        db.session.add(arne)
        db.session.commit()

        b1 = Order(order_number="TD-001", reference="Batterijen", total_amount=15.0, status="Goedgekeurd", user_id=stijn.id, supplier_id=bol.id)
        b2 = Order(order_number="TD-002", reference="Boormachine", total_amount=250.0, status="Wachten op BO", user_id=stijn.id, supplier_id=bol.id)
        b3 = Order(order_number="TD-003", reference="Laptop Stijn", total_amount=1500.0, status="Wachten op BO", user_id=stijn.id, supplier_id=dell.id)
        
        db.session.add_all([b1, b2, b3])
        db.session.commit()
        
        return "<h1>✅ Database Hersteld!</h1><p>Log in met: Koen, Bert, Stijn of Arne (ww: test)</p>"
    except Exception as e:
        return f"❌ Fout: {str(e)}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)