from app import app, db
from models import User, Setting

with app.app_context():
    # Check of er al gebruikers zijn, zo niet: aanmaken
    if not User.query.filter_by(username='admin').first():
        # We maken een beheerder
        admin = User(username='admin', password='password123', role='Admin')
        
        # We maken een BudgetOwner (BO) voor de TD
        bo_td = User(username='arne_td', password='password123', role='BO', department='TD')
        
        # We maken een Directielid
        directie = User(username='directie_lisa', password='password123', role='Directie')
        
        db.session.add(admin)
        db.session.add(bo_td)
        db.session.add(directie)
        
        # Ook meteen de standaard instelling voor de bijlage-limiet (€500)
        if not Setting.query.first():
            new_setting = Setting(attachment_limit=500.0)
            db.session.add(new_setting)
            
        db.session.commit()
        print("Gebruikers succesvol aangemaakt!")
    else:
        print("Gebruikers bestonden al.")