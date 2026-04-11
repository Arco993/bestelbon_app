import os
import secrets
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from models import db, User, Order, OrderLine, Supplier, Department, Attachment  # Attachment toegevoegd
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

# --- AUTOMATISCHE ADMIN CREATIE ---
def ensure_admin():
    with app.app_context():
        db.create_all()
        print("🔍 Checking for admin...") 
        admin = User.query.filter_by(role='Admin').first()
        if not admin:
            new_admin = User(
                username='Arne', 
                password='test', 
                role='Admin', 
                email='arne@delacroix.be',
                is_active=True
            )
            db.session.add(new_admin)
            db.session.commit()
            print("✅ Standaard Admin 'Arne' aangemaakt.")

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

@app.route('/dashboard')
@login_required
def dashboard(): 
    my_orders_count = Order.query.filter_by(user_id=current_user.id).count()
    my_total_spent = db.session.query(func.sum(Order.total_amount)).filter(
        Order.user_id == current_user.id,
        Order.status.not_in(['Concept', 'Afgewezen'])
    ).scalar() or 0.0

    pending_approvals = 0
    if current_user.role == 'Admin':
        pending_approvals = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).count()
    elif current_user.role == 'BO':
        pending_approvals = Order.query.join(User, Order.user_id == User.id)\
                                      .filter(User.department_id == current_user.department_id, 
                                              Order.status == 'Wachten op BO',
                                              Order.user_id != current_user.id).count()
    elif current_user.role == 'Directie':
        sub_direct_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        managed_depts = Department.query.filter_by(director_id=current_user.id).all()
        managed_dept_ids = [d.id for d in managed_depts]
        dept_user_ids = [u.id for u in User.query.filter(User.department_id.in_(managed_dept_ids)).all()] if managed_dept_ids else []
        
        all_relevant_user_ids = list(set(sub_direct_ids + dept_user_ids))
        if all_relevant_user_ids:
            pending_approvals = Order.query.filter(
                Order.user_id.in_(all_relevant_user_ids),
                Order.status == 'Wachten op Directie'
            ).count()

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
    form_data = {}
    if request.method == 'POST':
        form_data = request.form
        gen_ref = request.form.get('generated_order_number')
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

        descs = request.form.getlist('desc[]')
        qtys = request.form.getlist('qty[]')
        prices = request.form.getlist('price[]')
        total_inc = sum([(float(qtys[i] or 0) * float(prices[i] or 0)) for i in range(len(descs))])

        # --- BULLETPROOF BIJLAGEN LOGICA ---
        # We checken alle mogelijke manieren waarop browsers meerdere bestanden doorsturen
        files = request.files.getlist('attachments[]')
        if not files or all(f.filename == '' for f in files):
            files = request.files.getlist('attachments')
        if not files or all(f.filename == '' for f in files):
            files = request.files.getlist('attachment') # Fallback voor de zekerheid

        saved_filenames = []
        has_valid_file = False
        
        for file in files:
            if file and file.filename and allowed_file(file.filename):
                fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}_{secure_filename(file.filename)}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                saved_filenames.append(fname)
                has_valid_file = True

        if request.form.get('action') == 'submit' and total_inc > current_user.min_attachment_limit:
            if not has_valid_file:
                flash(f'Minimaal één bijlage is verplicht boven € {current_user.min_attachment_limit}', 'danger')
                return render_template('new_order.html', form_data=form_data, next_ref=gen_ref)

        status = 'Concept'
        if request.form.get('action') == 'submit':
            if total_inc <= current_user.auto_approve_limit:
                status = 'Goedgekeurd'
            else:
                if current_user.role == 'Personeel':
                    status = 'Wachten op BO'
                elif current_user.role == 'BO':
                    if total_inc > current_user.max_bo_limit:
                        status = 'Wachten op Directie'
                    else:
                        status = 'Goedgekeurd'
                else:
                    status = 'Goedgekeurd'

        order = Order(
            order_number=gen_ref, reference=request.form.get('reference'), user_id=current_user.id, 
            supplier_id=supplier.id, status=status, total_amount=total_inc, attachment_filename=None,
            notify_on_update=True if request.form.get('notify_on_update') == 'on' else False,
            notification_type=request.form.get('notification_type', 'Final')
        )
        db.session.add(order)
        db.session.flush()

        # Sla de bestanden netjes op in de database
        for fname in saved_filenames:
            db.session.add(Attachment(order_id=order.id, filename=fname))

        for i in range(len(descs)):
            if descs[i]:
                db.session.add(OrderLine(
                    order_id=order.id, product_code=request.form.getlist('product_code[]')[i],
                    description=descs[i], internal_note=request.form.getlist('internal_note[]')[i],
                    quantity=int(float(qtys[i] or 0)), unit_price=float(prices[i] or 0), tax_rate=float(request.form.getlist('tax[]')[i] or 0)
                ))
        db.session.commit()
        flash(f'Bon {gen_ref} verwerkt.', 'success')
        return redirect(url_for('my_orders'))

    dept_code = current_user.department.code if current_user.department else 'GEN'
    next_ref = f"{dept_code}-{datetime.now().year}-{secrets.token_hex(2).upper()}"
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
    
    # Beveiliging: Alleen de maker mag zijn concept aanpassen
    if order.user_id != current_user.id or order.status != 'Concept':
        flash('Je kunt deze bon niet meer aanpassen.', 'danger')
        return redirect(url_for('my_orders'))

    if request.method == 'POST':
        # 1. Update Leverancier
        s_name = request.form.get('supplier_name')
        supplier = Supplier.query.filter_by(name=s_name).first() or Supplier(
            name=s_name, street=request.form.get('street'), house_number=request.form.get('house_number'), 
            zip_code=request.form.get('zip_code'), city=request.form.get('city'), vat_number=request.form.get('supplier_vat')
        )
        if not supplier.id: 
            db.session.add(supplier)
            db.session.flush()
        
        order.supplier_id = supplier.id
        order.reference = request.form.get('reference')
        order.notify_on_update = True if request.form.get('notify_on_update') == 'on' else False
        order.notification_type = request.form.get('notification_type', 'Final')

        # 2. Update Artikelen (Wis de oude, maak nieuwe aan)
        OrderLine.query.filter_by(order_id=order.id).delete()
        
        descs = request.form.getlist('desc[]')
        qtys = request.form.getlist('qty[]')
        prices = request.form.getlist('price[]')
        product_codes = request.form.getlist('product_code[]')
        internal_notes = request.form.getlist('internal_note[]')
        taxes = request.form.getlist('tax[]')
        
        total_inc = sum([(float(qtys[i] or 0) * float(prices[i] or 0)) for i in range(len(descs))])
        order.total_amount = total_inc

        for i in range(len(descs)):
            if descs[i]:
                db.session.add(OrderLine(
                    order_id=order.id, product_code=product_codes[i],
                    description=descs[i], internal_note=internal_notes[i],
                    quantity=int(float(qtys[i] or 0)), unit_price=float(prices[i] or 0), tax_rate=float(taxes[i] or 0)
                ))

        # 3. Handle extra bijlagen
        files = request.files.getlist('attachments')
        if not files or all(f.filename == '' for f in files):
            files = request.files.getlist('attachments[]') # Fallback
            
        has_valid_file = bool(order.attachments) or bool(order.attachment_filename)
        
        for file in files:
            if file and file.filename and allowed_file(file.filename):
                fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(2)}_{secure_filename(file.filename)}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
                db.session.add(Attachment(order_id=order.id, filename=fname))
                has_valid_file = True

        # 4. Status bepalen bij Indienen
        if request.form.get('action') == 'submit':
            if total_inc > current_user.min_attachment_limit and not has_valid_file:
                flash(f'Minimaal één bijlage is verplicht boven € {current_user.min_attachment_limit}', 'danger')
                return redirect(url_for('edit_order', order_id=order.id))
                
            if total_inc <= current_user.auto_approve_limit:
                order.status = 'Goedgekeurd'
            else:
                if current_user.role == 'Personeel':
                    order.status = 'Wachten op BO'
                elif current_user.role == 'BO':
                    order.status = 'Wachten op Directie' if total_inc > current_user.max_bo_limit else 'Goedgekeurd'
                else:
                    order.status = 'Goedgekeurd'
        
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
    
    # Verwijder regels en bijlagen uit de DB, en vervolgens de bon
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
    if current_user.role not in ['BO', 'Directie', 'Admin']: 
        return redirect(url_for('dashboard'))

    if current_user.role == 'Admin':
        orders = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).all()
    elif current_user.role == 'BO':
        orders = Order.query.join(User).filter(
            User.department_id == current_user.department_id,
            Order.status == 'Wachten op BO',
            Order.user_id != current_user.id 
        ).all()
    elif current_user.role == 'Directie':
        sub_direct_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        managed_depts = Department.query.filter_by(director_id=current_user.id).all()
        managed_dept_ids = [d.id for d in managed_depts]
        dept_user_ids = [u.id for u in User.query.filter(User.department_id.in_(managed_dept_ids)).all()] if managed_dept_ids else []
        all_relevant_user_ids = list(set(sub_direct_ids + dept_user_ids))
        
        if all_relevant_user_ids:
            orders = Order.query.filter(Order.user_id.in_(all_relevant_user_ids), Order.status == 'Wachten op Directie').all()
        else:
            orders = []

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
            next_approver = order.user.department.director
            order.status = 'Wachten op Directie'
            flash(f'Bon gepusht naar {next_approver.username} (Directie {order.user.department.name}).', 'success')
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
    db.session.commit()
    return redirect(url_for('approve_list'))

# --- AFDELINGSBEHEER ROUTES ---
@app.route('/add_department', methods=['POST'])
@login_required
def add_department():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    name = request.form.get('name')
    code = request.form.get('code').upper()
    dir_id = request.form.get('director_id')
    
    if Department.query.filter_by(code=code).first():
        flash(f'Fout: Afdelingscode {code} bestaat al!', 'danger')
    else:
        new_dept = Department(name=name, code=code, director_id=int(dir_id) if dir_id else None)
        db.session.add(new_dept)
        db.session.commit()
        flash(f'Afdeling {name} succesvol aangemaakt.', 'success')
    return redirect(url_for('setup'))

@app.route('/delete_department/<int:dept_id>', methods=['POST'])
@login_required
def delete_department(dept_id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    dept = Department.query.get_or_404(dept_id)
    if dept.members:
        flash('Kan afdeling niet verwijderen: er zijn nog gebruikers aan gekoppeld.', 'danger')
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
    
    dept.name = request.form.get('name')
    dept.code = request.form.get('code').upper()
    dir_id = request.form.get('director_id')
    dept.director_id = int(dir_id) if dir_id else None
    
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
    return render_template('setup.html', 
                           users=User.query.all(), 
                           departments=Department.query.all(),
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
    u = User(
        username=username, email=request.form.get('email'), password=request.form.get('password'),
        role=request.form.get('role'), department_id=int(dept_id) if dept_id else None,
        min_attachment_limit=float(request.form.get('min_attachment_limit') or 500),
        max_bo_limit=float(request.form.get('max_bo_limit') or 1000),
        auto_approve_limit=float(request.form.get('auto_approve_limit') or 50),
        approver_id=int(request.form.get('approver_id')) if request.form.get('approver_id') else None
    )
    db.session.add(u)
    db.session.commit()
    flash(f'Gebruiker {u.username} toegevoegd.', 'success')
    return redirect(url_for('setup'))

@app.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user(user_id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    u = User.query.get_or_404(user_id)
    u.username = request.form.get('username')
    u.email = request.form.get('email')
    u.role = request.form.get('role')
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
    if current_user.role not in ['BO', 'Directie', 'Admin']: 
        return redirect(url_for('dashboard'))
    orders = Order.query.filter((Order.bo_name == current_user.username) | (Order.dir_name == current_user.username)).order_by(Order.created_at.desc()).all()
    return render_template('approved_history.html', orders=orders)

@app.route('/admin/all_orders')
@login_required
def all_orders():
    if current_user.role not in ['Directie', 'Admin']: return redirect(url_for('dashboard'))
    query = Order.query.join(User)
    return render_template('all_orders.html', orders=query.order_by(Order.created_at.desc()).all())

@app.route('/search_supplier')
def search_supplier():
    q = request.args.get('q', '').lower()
    suppliers = Supplier.query.filter(Supplier.name.ilike(f'%{q}%')).all()
    return jsonify([{'name': s.name, 'city': s.city, 'vat': s.vat_number} for s in suppliers])

if __name__ == '__main__':
    ensure_admin()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)