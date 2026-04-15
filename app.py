import os
import secrets
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from models import db, User, Order, OrderLine, Supplier, Department, Attachment
from sqlalchemy import func

app = Flask(__name__)

# --- CONFIGURATIE ---
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
os.makedirs(instance_path, exist_ok=True)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'bestelbonnen.db')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tijdelijke-lokale-sleutel-123')

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

# --- HELPER FUNCTIE: LEVERANCIER OPSLAAN ---
def save_supplier_from_form(form):
    s_name = form.get('supplier_name')
    supplier = Supplier.query.filter_by(name=s_name).first() or Supplier(name=s_name)
    if not supplier.id: db.session.add(supplier)
        
    supplier.street = form.get('street')
    supplier.house_number = form.get('house_number')
    supplier.zip_code = form.get('zip_code')
    supplier.city = form.get('city')
    supplier.vat_number = form.get('supplier_vat')
    supplier.email = form.get('supplier_email')
    db.session.flush()
    return supplier

def send_status_mail(recipient, subject, body):
    if recipient:
        try:
            msg = Message(subject, recipients=[recipient])
            msg.body = body
            # mail.send(msg) 
            print(f"E-mail simulatie naar {recipient}: {subject}")
        except Exception as e:
            print(f"E-mail fout: {e}")

# --- AUTOMATISCHE ADMIN CREATIE ---
def ensure_admin():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(role='Admin').first():
            db.session.add(User(
                username='Arne', password='test', role='Admin', 
                email='arne@delacroix.be', is_active=True
            ))
            db.session.commit()

# --- BASIS ROUTES ---
@app.route('/')
def index(): 
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            if not user.is_active:
                flash('Dit account is gedeactiveerd. Neem contact op met de admin.', 'danger')
                return redirect(url_for('login'))
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Foutieve inloggegevens.', 'danger')
    return render_template('login.html')

def auto_escalate_stale_orders():
    grens_datum = datetime.utcnow() - timedelta(days=5)
    stale_orders = Order.query.filter(Order.status == 'Wachten op BO', Order.created_at <= grens_datum).all()
    
    for order in stale_orders:
        order.status = 'Wachten op Directie'
        order.bo_approval_code = 'AUTO_PUSH' 
    if stale_orders: db.session.commit()

@app.route('/dashboard')
@login_required
def dashboard(): 
    auto_escalate_stale_orders()
    
    my_orders_count = Order.query.filter_by(user_id=current_user.id).count()
    my_total_spent = db.session.query(func.sum(Order.total_amount)).filter(
        Order.user_id == current_user.id, Order.status.not_in(['Concept', 'Afgewezen'])
    ).scalar() or 0.0

    pending_approvals = 0
    if current_user.role == 'Admin':
        pending_approvals = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).count()
    elif current_user.role == 'BO':
        pending_approvals = Order.query.join(User).filter(
            User.department_id == current_user.department_id, 
            Order.status == 'Wachten op BO', Order.user_id != current_user.id
        ).count()
    elif current_user.role == 'Directie':
        sub_direct_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        managed_dept_ids = [d.id for d in Department.query.filter_by(director_id=current_user.id).all()]
        dept_user_ids = [u.id for u in User.query.filter(User.department_id.in_(managed_dept_ids)).all()] if managed_dept_ids else []
        all_relevant_user_ids = list(set(sub_direct_ids + dept_user_ids))
        if all_relevant_user_ids:
            pending_approvals = Order.query.filter(Order.user_id.in_(all_relevant_user_ids), Order.status == 'Wachten op Directie').count()

    recent_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', **locals())

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- BESTELBON ROUTES ---
@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def new_order():
    if request.method == 'POST':
        gen_ref = request.form.get('generated_order_number')
        if Order.query.filter_by(order_number=gen_ref).first():
            gen_ref = f"{gen_ref}-{secrets.token_hex(2).upper()}"

        if not request.form.get('supplier_name'):
            flash('Leverancier is verplicht!', 'danger')
            return render_template('new_order.html', form_data=request.form, next_ref=gen_ref)

        supplier = save_supplier_from_form(request.form)

        descs, qtys, prices = request.form.getlist('desc[]'), request.form.getlist('qty[]'), request.form.getlist('price[]')
        total_inc = sum((float(qtys[i] or 0) * float(prices[i] or 0)) for i in range(len(descs)))

        files = request.files.getlist('attachments[]') or request.files.getlist('attachments') or request.files.getlist('attachment')
        saved_filenames, has_valid_file = [], False
        
        for file in files:
            if file and file.filename and allowed_file(file.filename):
                fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}_{secure_filename(file.filename)}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                saved_filenames.append(fname)
                has_valid_file = True

        if request.form.get('action') == 'submit' and total_inc > current_user.min_attachment_limit and not has_valid_file:
            flash(f'Minimaal één bijlage is verplicht boven € {current_user.min_attachment_limit}', 'danger')
            return render_template('new_order.html', form_data=request.form, next_ref=gen_ref)

        status = 'Concept'
        if request.form.get('action') == 'submit':
            if total_inc <= current_user.auto_approve_limit or current_user.role not in ['Personeel', 'BO']:
                status = 'Goedgekeurd'
            elif current_user.role == 'Personeel':
                status = 'Wachten op BO'
            elif current_user.role == 'BO':
                status = 'Wachten op Directie' if total_inc > current_user.max_bo_limit else 'Goedgekeurd'

        order = Order(
            order_number=gen_ref, reference=request.form.get('reference'), user_id=current_user.id, 
            supplier_id=supplier.id, status=status, total_amount=total_inc, 
            notify_on_update=(request.form.get('notify_on_update') == 'on'),
            notification_type=request.form.get('notification_type', 'Final')
        )
        db.session.add(order)
        db.session.flush()

        for fname in saved_filenames: db.session.add(Attachment(order_id=order.id, filename=fname))

        for i in range(len(descs)):
            if descs[i]:
                db.session.add(OrderLine(
                    order_id=order.id, product_code=request.form.getlist('product_code[]')[i],
                    description=descs[i], internal_note=request.form.getlist('internal_note[]')[i],
                    quantity=int(float(qtys[i] or 0)), unit_price=float(prices[i] or 0), 
                    tax_rate=float(request.form.getlist('tax[]')[i] or 0)
                ))
        db.session.commit()
        flash(f'Bon {gen_ref} verwerkt.', 'success')
        return redirect(url_for('my_orders'))

    next_ref = f"{current_user.department.code if current_user.department else 'GEN'}-{datetime.now().year}-{secrets.token_hex(2).upper()}"
    return render_template('new_order.html', form_data={}, next_ref=next_ref)

@app.route('/my_orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/order/<int:order_id>')
@login_required
def order_detail(order_id):
    return render_template('order_detail.html', order=Order.query.get_or_404(order_id))

@app.route('/order/edit/<int:order_id>', methods=['GET', 'POST'])
@login_required
def edit_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id or order.status != 'Concept':
        flash('Je kunt deze bon niet meer aanpassen.', 'danger')
        return redirect(url_for('my_orders'))

    if request.method == 'POST':
        supplier = save_supplier_from_form(request.form)
        order.supplier_id = supplier.id
        order.reference = request.form.get('reference')
        order.notify_on_update = (request.form.get('notify_on_update') == 'on')
        order.notification_type = request.form.get('notification_type', 'Final')

        OrderLine.query.filter_by(order_id=order.id).delete()
        descs, qtys, prices = request.form.getlist('desc[]'), request.form.getlist('qty[]'), request.form.getlist('price[]')
        product_codes, internal_notes, taxes = request.form.getlist('product_code[]'), request.form.getlist('internal_note[]'), request.form.getlist('tax[]')
        
        total_inc = sum((float(qtys[i] or 0) * float(prices[i] or 0)) for i in range(len(descs)))
        order.total_amount = total_inc

        for i in range(len(descs)):
            if descs[i]:
                db.session.add(OrderLine(
                    order_id=order.id, product_code=product_codes[i], description=descs[i], 
                    internal_note=internal_notes[i], quantity=int(float(qtys[i] or 0)), 
                    unit_price=float(prices[i] or 0), tax_rate=float(taxes[i] or 0)
                ))

        files = request.files.getlist('attachments') or request.files.getlist('attachments[]')
        has_valid_file = bool(order.attachments) or bool(order.attachment_filename)
        for file in files:
            if file and file.filename and allowed_file(file.filename):
                fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}_{secure_filename(file.filename)}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                db.session.add(Attachment(order_id=order.id, filename=fname))
                has_valid_file = True

        if request.form.get('action') == 'submit':
            if total_inc > current_user.min_attachment_limit and not has_valid_file:
                flash(f'Minimaal één bijlage is verplicht boven € {current_user.min_attachment_limit}', 'danger')
                return redirect(url_for('edit_order', order_id=order.id))
                
            if total_inc <= current_user.auto_approve_limit or current_user.role not in ['Personeel', 'BO']:
                order.status = 'Goedgekeurd'
            elif current_user.role == 'Personeel':
                order.status = 'Wachten op BO'
            elif current_user.role == 'BO':
                order.status = 'Wachten op Directie' if total_inc > current_user.max_bo_limit else 'Goedgekeurd'
        
        db.session.commit()
        flash(f'Bon {order.order_number} succesvol bijgewerkt.', 'success')
        return redirect(url_for('my_orders'))

    return render_template('edit_order.html', order=order)

@app.route('/order/delete/<int:order_id>', methods=['POST'])
@login_required
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id or order.status != 'Concept':
        flash('Je kunt deze bon niet verwijderen.', 'danger')
        return redirect(url_for('my_orders'))
    
    OrderLine.query.filter_by(order_id=order.id).delete()
    Attachment.query.filter_by(order_id=order.id).delete()
    db.session.delete(order)
    db.session.commit()
    flash('Concept bestelbon definitief verwijderd.', 'success')
    return redirect(url_for('my_orders'))

@app.route('/order/<int:order_id>/pdf')
@login_required
def order_pdf(order_id):
    return render_template('order_pdf.html', order=Order.query.get_or_404(order_id))

# --- GOEDKEURINGS ROUTES ---
@app.route('/approve_list')
@login_required
def approve_list():
    if current_user.role not in ['BO', 'Directie', 'Admin']: return redirect(url_for('dashboard'))

    orders = []
    if current_user.role == 'Admin':
        orders = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).all()
    elif current_user.role == 'BO':
        orders = Order.query.join(User).filter(User.department_id == current_user.department_id, Order.status == 'Wachten op BO', Order.user_id != current_user.id).all()
    elif current_user.role == 'Directie':
        sub_direct_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        managed_dept_ids = [d.id for d in Department.query.filter_by(director_id=current_user.id).all()]
        dept_user_ids = [u.id for u in User.query.filter(User.department_id.in_(managed_dept_ids)).all()] if managed_dept_ids else []
        all_relevant_user_ids = list(set(sub_direct_ids + dept_user_ids))
        if all_relevant_user_ids:
            orders = Order.query.filter(Order.user_id.in_(all_relevant_user_ids), Order.status == 'Wachten op Directie').all()

    return render_template('approve_list.html', orders=orders)

@app.route('/order/approve/<int:order_id>')
@login_required
def approve_order(order_id):
    order = Order.query.get_or_404(order_id)
    stamp, now = secrets.token_hex(4).upper(), datetime.now()
    
    if current_user.role == 'BO' and order.status == 'Wachten op BO':
        order.bo_approval_code, order.bo_approval_date, order.bo_name = stamp, now, current_user.username
        order.status = 'Wachten op Directie' if order.total_amount > order.user.max_bo_limit else 'Goedgekeurd'
    elif current_user.role == 'Directie' and order.status in ['Wachten op Directie', 'Wachten op BO']:
        order.dir_approval_code, order.dir_approval_date, order.dir_name = stamp, now, current_user.username
        order.status = 'Goedgekeurd'
    
    db.session.commit()
    return redirect(url_for('approve_list'))

@app.route('/order/escalate/<int:order_id>', methods=['POST'])
@login_required
def escalate_order(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id == current_user.id and order.status == 'Wachten op BO':
        if order.user.department and order.user.department.director:
            order.status = 'Wachten op Directie'
            flash(f'Bon gepusht naar {order.user.department.director.username}.', 'success')
        else:
            order.status = 'Wachten op Directie'
            flash('Bon gepusht naar de algemene Directie.', 'success')
        db.session.commit()
    return redirect(request.referrer or url_for('my_orders'))

@app.route('/order/reject/<int:order_id>', methods=['POST'])
@login_required
def reject_order(order_id):
    order = Order.query.get_or_404(order_id)
    order.status, order.rejection_reason = 'Afgewezen', request.form.get('reason')
    
    now = datetime.now()
    if current_user.role == 'BO':
        order.bo_name, order.bo_approval_date, order.bo_approval_code = current_user.username, now, 'AFGEWEZEN'
    elif current_user.role in ['Directie', 'Admin']:
        order.dir_name, order.dir_approval_date, order.dir_approval_code = current_user.username, now, 'AFGEWEZEN'
        
    db.session.commit()
    return redirect(url_for('approve_list'))

# --- AFDELINGSBEHEER ROUTES ---
@app.route('/add_department', methods=['POST'])
@login_required
def add_department():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    name, code, dir_id = request.form.get('name'), request.form.get('code').upper(), request.form.get('director_id')
    
    if Department.query.filter_by(code=code).first():
        flash(f'Fout: Afdelingscode {code} bestaat al!', 'danger')
    else:
        db.session.add(Department(name=name, code=code, director_id=int(dir_id) if dir_id else None))
        db.session.commit()
        flash(f'Afdeling {name} succesvol aangemaakt.', 'success')
    return redirect(url_for('setup'))

@app.route('/delete_department/<int:dept_id>', methods=['POST'])
@login_required
def delete_department(dept_id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    dept = Department.query.get_or_404(dept_id)
    if dept.members: flash('Kan afdeling niet verwijderen: er zijn nog gebruikers gekoppeld.', 'danger')
    else:
        db.session.delete(dept)
        db.session.commit()
        flash('Afdeling verwijderd.', 'success')
    return redirect(url_for('setup'))

@app.route('/edit_department/<int:dept_id>', methods=['POST'])
@login_required
def edit_department(dept_id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    dept = Department.query.get_or_404(dept_id)
    dept.name, dept.code = request.form.get('name'), request.form.get('code').upper()
    dept.director_id = int(request.form.get('director_id')) if request.form.get('director_id') else None
    
    try:
        db.session.commit()
        flash(f'Afdeling {dept.name} succesvol bijgewerkt.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Fout bij bijwerken afdeling: {str(e)}', 'danger')
    return redirect(url_for('setup'))

# --- ADMIN & BEHEER ---
@app.route('/setup')
@login_required
def setup():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    return render_template('setup.html', users=User.query.all(), departments=Department.query.all(),
                           approvers=User.query.filter(User.role.in_(['BO', 'Directie', 'Admin'])).all(),
                           directors=User.query.filter_by(role='Directie').all())

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    username = request.form.get('username')
    if User.query.filter_by(username=username).first():
        flash('Gebruikersnaam al in gebruik!', 'danger')
        return redirect(url_for('setup'))

    dept_id = request.form.get('department_id')
    db.session.add(User(
        username=username, email=request.form.get('email'), password=request.form.get('password'),
        role=request.form.get('role'), department_id=int(dept_id) if dept_id else None,
        min_attachment_limit=float(request.form.get('min_attachment_limit') or 500),
        max_bo_limit=float(request.form.get('max_bo_limit') or 1000),
        auto_approve_limit=float(request.form.get('auto_approve_limit') or 0),
        approver_id=int(request.form.get('approver_id')) if request.form.get('approver_id') else None
    ))
    db.session.commit()
    flash(f'Gebruiker toegevoegd.', 'success')
    return redirect(url_for('setup'))

@app.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user(user_id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    u = User.query.get_or_404(user_id)
    u.username, u.email, u.role = request.form.get('username'), request.form.get('email'), request.form.get('role')
    u.department_id = int(request.form.get('department_id')) if request.form.get('department_id') else None
    u.approver_id = int(request.form.get('approver_id')) if request.form.get('approver_id') else None
    u.auto_approve_limit = float(request.form.get('auto_approve_limit') or 0)
    u.min_attachment_limit = float(request.form.get('min_attachment_limit') or 0)
    u.max_bo_limit = float(request.form.get('max_bo_limit') or 0)
    u.email_notification_freq = request.form.get('email_notification_freq')
    u.digest_time = request.form.get('digest_time')
    if request.form.get('password'): u.password = request.form.get('password')
    db.session.commit()
    flash('Gebruiker bijgewerkt.', 'success')
    return redirect(url_for('setup'))

@app.route('/toggle_user_status/<int:user_id>', methods=['POST'])
@login_required
def toggle_user_status(user_id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id:
        u.is_active = not u.is_active
        db.session.commit()
    return redirect(url_for('setup'))

@app.route('/my_approvals')
@login_required
def my_approvals():
    if current_user.role not in ['BO', 'Directie', 'Admin']: return redirect(url_for('dashboard'))
    orders = Order.query.filter((Order.bo_name == current_user.username) | (Order.dir_name == current_user.username)).order_by(Order.created_at.desc()).all()
    return render_template('approved_history.html', orders=orders)

@app.route('/admin/all_orders')
@login_required
def all_orders():
    if current_user.role not in ['Directie', 'Admin']: return redirect(url_for('dashboard'))
    return render_template('all_orders.html', orders=Order.query.join(User).order_by(Order.created_at.desc()).all())

@app.route('/department/archive')
@login_required
def department_archive():
    if current_user.role not in ['BO', 'Admin', 'Directie']: return redirect(url_for('dashboard'))
    q_supplier, q_status = request.args.get('supplier', ''), request.args.get('status', '')
    query = Order.query.join(User).filter(User.department_id == current_user.department_id)
    
    if q_supplier: query = query.join(Supplier).filter(Supplier.name.ilike(f'%{q_supplier}%'))
    if q_status: query = query.filter(Order.status == q_status)

    return render_template('department_archive.html', orders=query.order_by(Order.created_at.desc()).all(), q_supplier=q_supplier, q_status=q_status)

@app.route('/search_supplier')
def search_supplier():
    q = request.args.get('q', '').lower()
    return jsonify([{
        'name': s.name, 'street': s.street, 'num': s.house_number, 'zip': s.zip_code,
        'city': s.city, 'vat': s.vat_number, 'email': getattr(s, 'email', '')
    } for s in Supplier.query.filter(Supplier.name.ilike(f'%{q}%')).all()])

if __name__ == '__main__':
    ensure_admin()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)