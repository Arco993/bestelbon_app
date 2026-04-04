from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Order, OrderLine, Supplier, Setting
import uuid

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
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and user.password == request.form.get('password'):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Ongeldige inloggegevens.', 'danger')
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

# --- ORDER ROUTES ---

@app.route('/order/new', methods=['GET', 'POST'])
@login_required
def new_order():
    if request.method == 'POST':
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

        new_bon = Order(
            order_number=str(uuid.uuid4())[:8].upper(),
            reference=request.form.get('reference'),
            user_id=current_user.id,
            supplier_id=supplier.id
        )
        db.session.add(new_bon)
        db.session.flush()

        descs, qtys, prices, taxes = request.form.getlist('desc[]'), request.form.getlist('qty[]'), request.form.getlist('price[]'), request.form.getlist('tax[]')
        total_inc = 0
        for i in range(len(descs)):
            q, p, t = float(qtys[i]), float(prices[i]), float(taxes[i])
            total_inc += (q * p) * (1 + (t/100))
            line = OrderLine(order_id=new_bon.id, description=descs[i], quantity=q, unit_price=p, tax_rate=t)
            db.session.add(line)

        new_bon.total_amount = total_inc
        db.session.commit()
        flash(f'Bestelbon {new_bon.order_number} ingediend!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('new_order.html')

@app.route('/my_orders')
@login_required
def my_orders():
    # Toon alle bonnen die de huidige gebruiker zelf heeft gemaakt
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('my_orders.html', orders=orders)

@app.route('/approve_list')
@login_required
def approve_list():
    # Alleen toegankelijk voor BO, Directie of Admin
    if current_user.role not in ['BO', 'Directie', 'Admin']:
        return redirect(url_for('dashboard'))
    
    # Logica voor wie wat mag zien:
    if current_user.role == 'Directie':
        # Directie ziet alleen bonnen met status 'Wachten op Directie'
        orders = Order.query.filter_by(status='Wachten op Directie').all()
    elif current_user.role == 'BO':
        # Een BO ziet bonnen van personeelsleden waar hij 'approver' van is
        # En die de status 'Wachten op BO' hebben
        subordinates = User.query.filter_by(approver_id=current_user.id).all()
        sub_ids = [s.id for s in subordinates]
        orders = Order.query.filter(Order.user_id.in_(sub_ids), Order.status == 'Wachten op BO').all()
    else: # Admin ziet alles
        orders = Order.query.all()
        
    return render_template('approve_list.html', orders=orders)

@app.route('/search_supplier')
@login_required
def search_supplier():
    q = request.args.get('q', '').lower()
    suppliers = Supplier.query.filter(Supplier.name.ilike(f'%{q}%')).limit(5).all()
    return jsonify([{'name': s.name, 'street': s.street, 'num': s.house_number, 'zip': s.zip_code, 'city': s.city, 'country': s.country, 'vat': s.vat_number} for s in suppliers])

# --- ADMIN ROUTES ---

@app.route('/setup')
@login_required
def setup():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    users = User.query.all()
    approvers = User.query.filter(User.role.in_(['BO', 'Directie', 'Admin'])).all()
    return render_template('setup.html', users=users, approvers=approvers)

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    new_user = User(
        username=request.form.get('username'), email=request.form.get('email'),
        password=request.form.get('password'), role=request.form.get('role'),
        department=request.form.get('department'),
        min_attachment_limit=float(request.form.get('min_attachment_limit', 500)),
        max_bo_limit=float(request.form.get('max_bo_limit', 1000)),
        approver_id=request.form.get('approver_id') if request.form.get('approver_id') else None
    )
    db.session.add(new_user)
    db.session.commit()
    flash('Gebruiker toegevoegd', 'success')
    return redirect(url_for('setup'))

@app.route('/edit_user/<int:id>', methods=['POST'])
@login_required
def edit_user(id):
    if current_user.role != 'Admin': return redirect(url_for('dashboard'))
    user = User.query.get_or_404(id)
    user.username, user.email, user.role, user.department = request.form.get('username'), request.form.get('email'), request.form.get('role'), request.form.get('department')
    user.min_attachment_limit, user.max_bo_limit = float(request.form.get('min_attachment_limit')), float(request.form.get('max_bo_limit'))
    user.approver_id = request.form.get('approver_id') if request.form.get('approver_id') else None
    if request.form.get('password'): user.password = request.form.get('password')
    db.session.commit()
    flash('Gebruiker bijgewerkt', 'success')
    return redirect(url_for('setup'))

if __name__ == '__main__':
    app.run(debug=True)