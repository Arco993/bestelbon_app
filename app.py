from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db, User, Order, OrderLine, Supplier, Setting

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bestelbonnen.db'
app.config['SECRET_KEY'] = 'jouw_geheime_sleutel_hier'

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Waar moet je heen als je niet bent ingelogd?

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.password == password: # In een latere stap gaan we dit beveiligen (hashen)
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Foutieve gebruikersnaam of wachtwoord')
            
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return f"<h1>Welkom op het dashboard, {current_user.username}!</h1><p>Jouw rol is: {current_user.role}</p><a href='/logout'>Log uit</a>"

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
def index():
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)