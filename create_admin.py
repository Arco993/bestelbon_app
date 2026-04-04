from app import app, db
from models import User

def reset_and_create_admin():
    with app.app_context():
        print("Bezig met het opschonen van de database...")
        # Verwijder alle oude tabellen om plaats te maken voor de nieuwe structuur
        db.drop_all()
        
        print("Bezig met het aanmaken van nieuwe tabellen...")
        # Maak alle tabellen opnieuw aan op basis van de nieuwste models.py
        db.create_all()

        print("Bezig met het aanmaken van het Admin-account...")
        # Maak de hoofd-admin aan
        # Let op: 'department_code' is nu verplicht voor de slimme referenties!
        admin = User(
            username='admin', 
            email='admin@bedrijf.be',
            password='adminpassword',  # Verander dit naar je eigen gewenste wachtwoord
            role='Admin', 
            department='Administratie',
            department_code='ADM',      # Cruciaal voor de bon-referenties (bijv. ADM-2026-XXXX)
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