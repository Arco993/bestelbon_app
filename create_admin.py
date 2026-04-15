from app import app, db
from models import User

def reset_and_create_admin():
    with app.app_context():
        print("Bezig met het opschonen van de database...")
        db.drop_all()
        
        print("Bezig met het aanmaken van nieuwe tabellen...")
        db.create_all()

        print("Bezig met het aanmaken van het Admin-account...")
        admin = User(
            username='admin', 
            email='admin@bedrijf.be',
            password='adminpassword', 
            role='Admin', 
            department_code='ADM', 
            min_attachment_limit=500.0, 
            max_bo_limit=1000.0
        )
        
        db.session.add(admin)
        db.session.commit()
        
        print("-------------------------------------------------")
        print("SUCCES: Database is gereset en Admin is aangemaakt!")
        print(f"Gebruikersnaam: {admin.username}")
        print(f"Wachtwoord: adminpassword")
        print(f"Afdelingscode: {admin.department_code}")
        print("-------------------------------------------------")

if __name__ == '__main__':
    reset_and_create_admin()