from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Order, OrderLine, Supplier
import secrets
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bestelbonnen.db'
app.config['SECRET_KEY'] = 'supergeheim'

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index(): return redirect(url_for('login'))

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
def dashboard(): return render_template('dashboard.html')

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
        dept_code = current_user.department_code or "GEN"
        generated_ref = f"{dept_code}-{datetime.now().year}-{secrets.token_hex(2).upper()}"

        s_name = request.form.get('supplier_name')
        supplier = Supplier.query.filter_by(name=s_name).first()
        if not supplier:
            supplier = Supplier(
                name=s_name, street=request.form.get('street'),
                house_number=request.form.get('house_number'), zip_code=request.form.get('zip_code'),
                city=request.form.get('city'), country=request.form.get('country'),
                vat_number=request.form.get('supplier_vat')
            )
            db.session.add(supplier)
            db.session.flush()

        # Bepaal status
        status = 'Concept'
        if action == 'submit':
            # Als een BO zelf bestelt, gaat het direct naar Directie. Personeel gaat naar BO.
            status = 'Wachten op Directie' if current_user.role == 'BO' else 'Wachten op BO'

        new_bon = Order(order_number=generated_ref, reference=request.form.get('reference'), 
                        user_id=current_user.id, supplier_id=supplier.id, status=status)
        db.session.add(new_bon)
        db.session.flush()

        descs, qtys, prices, taxes = request.form.getlist('desc[]'), request.form.getlist('qty[]'), request.form.getlist('price[]'), request.form.getlist('tax[]')
        total_inc = 0
        for i in range(len(descs)):
            q, p, t = float(qtys[i]), float(prices[i]), float(taxes[i])
            total_inc += (q * p) * (1 + (t/100))
            db.session.add(OrderLine(order_id=new_bon.id, description=descs[i], quantity=q, unit_price=p, tax_rate=t))

        new_bon.total_amount = total_inc
        db.session.commit()
        flash('Bon verwerkt.', 'success')
        return redirect(url_for('my_orders'))
    return render_template('new_order.html')

# --- GOEDKEURINGSLOGICA (FIXED) ---
@app.route('/approve_list')
@login_required
def approve_list():
    if current_user.role not in ['BO', 'Directie', 'Admin']: return redirect(url_for('dashboard'))
    
    if current_user.role == 'Directie':
        orders = Order.query.filter_by(status='Wachten op Directie').all()
    elif current_user.role == 'BO':
        sub_ids = [u.id for u in User.query.filter_by(approver_id=current_user.id).all()]
        orders = Order.query.filter(Order.user_id.in_(sub_ids), Order.status == 'Wachten op BO').all()
    else: # Admin ziet alle wachtende bonnen
        orders = Order.query.filter(Order.status.in_(['Wachten op BO', 'Wachten op Directie'])).all()
    
    return render_template('approve_list.html', orders=orders)

@app.route('/order/approve/<int:order_id>')
@login_required
def approve_order(order_id):
    order = Order.query.get_or_404(order_id)
    stamp = secrets.token_hex(4).upper()
    now = datetime.now()

    # Logica voor Diensthoofd (BO)
    if current_user.role == 'BO' and order.status == 'Wachten op BO':
        order.bo_approval_code, order.bo_approval_date, order.bo_name = stamp, now, current_user.username
        # Check of bedrag boven Directie-limiet van de MAKER van de bon ligt
        if order.total_amount > order.user.max_bo_limit:
            order.status = 'Wachten op Directie'
            flash(f'Bon {order.order_number} doorgezet naar Directie.', 'info')
        else:
            order.status = 'Goedgekeurd'
            flash(f'Bon {order.order_number} definitief goedgekeurd.', 'success')
            
    # Logica voor Directie
    elif current_user.role == 'Directie' and order.status == 'Wachten op Directie':
        order.dir_approval_code, order.dir_approval_date, order.dir_name = stamp, now, current_user.username
        order.status = 'Goedgekeurd'
        flash(f'Bon {order.order_number} definitief goedgekeurd door Directie.', 'success')
        
    db.session.commit()
    return redirect(url_for('approve_list'))

@app.route('/my_orders')
@login_required
def my_orders():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/search_supplier')
def search_supplier():
    q = request.args.get('q', '').lower()
    suppliers = Supplier.query.filter(Supplier.name.ilike(f'%{q}%')).all()
    return jsonify([{'name': s.name, 'street': s.street, 'num': s.house_number, 'zip': s.zip_code, 'city': s.city, 'country': s.country, 'vat': s.vat_number} for s in suppliers])

# --- ADMIN SETUP ---
@app.route('/setup')
@login_required
def setup():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    return render_template('setup.html', users=User.query.all(), approvers=User.query.filter(User.role.in_(['BO', 'Directie', 'Admin'])).all())

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    u = User(username=request.form.get('username'), email=request.form.get('email'), password=request.form.get('password'), 
             role=request.form.get('role'), department=request.form.get('department'), department_code=request.form.get('department_code'),
             min_attachment_limit=float(request.form.get('min_attachment_limit')), max_bo_limit=float(request.form.get('max_bo_limit')), 
             approver_id=request.form.get('approver_id') or None)
    db.session.add(u); db.session.commit()
    return redirect(url_for('setup'))

@app.route('/edit_user/<int:id>', methods=['POST'])
@login_required
def edit_user(id):
    u = User.query.get(id)
    u.username, u.email, u.role, u.department, u.department_code = request.form.get('username'), request.form.get('email'), request.form.get('role'), request.form.get('department'), request.form.get('department_code')
    u.min_attachment_limit, u.max_bo_limit = float(request.form.get('min_attachment_limit')), float(request.form.get('max_bo_limit'))
    u.approver_id = request.form.get('approver_id') or None
    if request.form.get('password'): u.password = request.form.get('password')
    db.session.commit()
    return redirect(url_for('setup'))

if __name__ == '__main__': app.run(debug=True)