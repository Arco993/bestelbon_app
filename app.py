from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Order, OrderLine, Supplier
import secrets
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bestelbonnen.db'
app.config['SECRET_KEY'] = 'supergeheim-delacroix'

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index(): 
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.password == password:
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

# --- BESTELBON AANMAKEN ---
@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def new_order():
    if request.method == 'POST':
        action = request.form.get('action')
        gen_ref = request.form.get('generated_order_number')
        
        # Controleer of het nummer al bestaat
        existing_order = Order.query.filter_by(order_number=gen_ref).first()
        if existing_order:
            gen_ref = f"{gen_ref}-{secrets.token_hex(2).upper()}"

        # Leverancier opslaan of ophalen
        s_name = request.form.get('supplier_name')
        supplier = Supplier.query.filter_by(name=s_name).first()
        if not supplier:
            supplier = Supplier(
                name=s_name, 
                street=request.form.get('street'),
                house_number=request.form.get('house_number'), 
                zip_code=request.form.get('zip_code'),
                city=request.form.get('city'), 
                country='België',
                vat_number=request.form.get('supplier_vat')
            )
            db.session.add(supplier)
            db.session.flush()

        # Status bepalen
        status = 'Concept'
        if action == 'submit':
            if current_user.role == 'BO':
                status = 'Wachten op Directie'
            else:
                status = 'Wachten op BO'

        # Bon aanmaken
        new_bon = Order(
            order_number=gen_ref, 
            reference=request.form.get('reference'), 
            user_id=current_user.id, 
            supplier_id=supplier.id, 
            status=status
        )
        db.session.add(new_bon)
        db.session.flush()

        # Bestellijnen verwerken
        descs = request.form.getlist('desc[]')
        qtys = request.form.getlist('qty[]')
        prices = request.form.getlist('price[]')
        taxes = request.form.getlist('tax[]')
        
        total_inc = 0
        for i in range(len(descs)):
            q = float(qtys[i] or 0)
            p = float(prices[i] or 0)
            t = float(taxes[i] or 0)
            line_total = (q * p) * (1 + (t/100))
            total_inc += line_total
            
            line = OrderLine(
                order_id=new_bon.id, 
                description=descs[i], 
                quantity=int(q), 
                unit_price=p, 
                tax_rate=t
            )
            db.session.add(line)

        new_bon.total_amount = total_inc
        db.session.commit()
        
        flash(f'Bestelbon {gen_ref} succesvol verwerkt.', 'success')
        return redirect(url_for('my_orders'))

    # GET Methode: Bereid het formulier voor
    dept_code = current_user.department_code or "GEN"
    next_ref = f"{dept_code}-{datetime.now().year}-{secrets.token_hex(2).upper()}"
    
    approver_name = "Directie"
    if current_user.approver_id:
        ap = User.query.get(current_user.approver_id)
        if ap: 
            approver_name = ap.username
            
    return render_template('new_order.html', next_ref=next_ref, approver_name=approver_name)

# --- OVERZICHTEN ---
@app.route('/my_orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/order/<int:order_id>')
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template('order_detail.html', order=order)

@app.route('/approve_list')
@login_required
def approve_list():
    if current_user.role not in ['BO', 'Directie', 'Admin']: 
        return redirect(url_for('dashboard'))
        
    if current_user.role == 'Directie':
        orders = Order.query.filter_by(status='Wachten op Directie').all()
    elif current_user.role == 'BO':
        sub_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        orders = Order.query.filter(Order.user_id.in_(sub_ids), Order.status == 'Wachten op BO').all()
    else:
        orders = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).all()
        
    return render_template('approve_list.html', orders=orders)

# --- GOEDKEUREN & AFWIJZEN ---
@app.route('/order/approve/<int:order_id>')
@login_required
def approve_order(order_id):
    order = Order.query.get_or_404(order_id)
    stamp = secrets.token_hex(4).upper()
    now = datetime.now()
    
    if current_user.role == 'BO' and order.status == 'Wachten op BO':
        order.bo_approval_code = stamp
        order.bo_approval_date = now
        order.bo_name = current_user.username
        
        if order.total_amount > order.user.max_bo_limit:
            order.status = 'Wachten op Directie'
        else:
            order.status = 'Goedgekeurd'
            
    elif current_user.role == 'Directie' and order.status == 'Wachten op Directie':
        order.dir_approval_code = stamp
        order.dir_approval_date = now
        order.dir_name = current_user.username
        order.status = 'Goedgekeurd'
        
    db.session.commit()
    flash(f'Bon {order.order_number} goedgekeurd.', 'success')
    return redirect(url_for('approve_list'))

@app.route('/order/reject/<int:order_id>', methods=['POST'])
@login_required
def reject_order(order_id):
    order = Order.query.get_or_404(order_id)
    reason = request.form.get('reason')
    
    if not reason:
        flash('Een reden voor afwijzing is verplicht.', 'danger')
        return redirect(url_for('order_detail', order_id=order.id))
        
    order.status = 'Afgewezen'
    order.rejection_reason = reason
    db.session.commit()
    
    flash(f'Bon {order.order_number} is afgewezen.', 'warning')
    return redirect(url_for('approve_list'))

@app.route('/search_supplier')
def search_supplier():
    q = request.args.get('q', '').lower()
    suppliers = Supplier.query.filter(Supplier.name.ilike(f'%{q}%')).all()
    
    results = []
    for s in suppliers:
        results.append({
            'name': s.name, 
            'street': s.street, 
            'num': s.house_number, 
            'zip': s.zip_code, 
            'city': s.city, 
            'vat': s.vat_number
        })
    return jsonify(results)

# --- ADMIN BEHEER ---
@app.route('/setup')
@login_required
def setup():
    if current_user.role != 'Admin': 
        return redirect(url_for('dashboard'))
        
    users = User.query.all()
    approvers = User.query.filter(User.role.in_(['BO', 'Directie', 'Admin'])).all()
    return render_template('setup.html', users=users, approvers=approvers)

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    approver_val = request.form.get('approver_id')
    approver_id = int(approver_val) if approver_val else None

    u = User(
        username=request.form.get('username'),
        email=request.form.get('email'),
        password=request.form.get('password'),
        role=request.form.get('role'),
        department=request.form.get('department'),
        department_code=request.form.get('department_code'),
        min_attachment_limit=float(request.form.get('min_attachment_limit') or 500.0),
        max_bo_limit=float(request.form.get('max_bo_limit') or 1000.0),
        approver_id=approver_id
    )
    db.session.add(u)
    db.session.commit()
    
    flash(f'Gebruiker {u.username} is succesvol toegevoegd.', 'success')
    return redirect(url_for('setup'))

@app.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user(user_id):
    u = User.query.get_or_404(user_id)
    
    u.username = request.form.get('username')
    u.email = request.form.get('email')
    u.role = request.form.get('role')
    u.department = request.form.get('department')
    u.department_code = request.form.get('department_code')
    u.min_attachment_limit = float(request.form.get('min_attachment_limit') or 500.0)
    u.max_bo_limit = float(request.form.get('max_bo_limit') or 1000.0)
    
    approver_val = request.form.get('approver_id')
    u.approver_id = int(approver_val) if approver_val else None
    
    new_password = request.form.get('password')
    if new_password and new_password.strip():
        u.password = new_password
        
    db.session.commit()
    flash(f'Wijzigingen voor {u.username} zijn succesvol opgeslagen.', 'success')
    return redirect(url_for('setup'))

if __name__ == '__main__': 
    app.run(debug=True)