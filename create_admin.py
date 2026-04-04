from app import app, db
from models import User, Setting

with app.app_context():
    # STAP 1: Maak de tabellen aan (als ze nog niet bestaan)
    print("Tabellen aanmaken...")
    db.create_all()
    
    # STAP 2: Controleer of de admin al bestaat
    admin_user = User.query.filter_by(username='admin').first()
    
    if not admin_user:
        print("Admin niet gevonden, bezig met aanmaken...")
        # We maken de beheerder aan
        admin = User(
            username='admin', 
            email="admin@jouwbedrijf.be",
            password='password123', 
            role='Admin',
            min_attachment_limit=500.0,
            max_bo_limit=1000.0
        )
        
        db.session.add(admin)
        
        # Ook meteen de standaard instelling aanmaken
        if not Setting.query.first():
            new_setting = Setting(global_attachment_limit=500.0)
            db.session.add(new_setting)
            
        db.session.commit()
        print("Systeem succesvol geïnitialiseerd!")
    else:
        print("Systeem was al geïnitialiseerd. Admin bestaat al.")