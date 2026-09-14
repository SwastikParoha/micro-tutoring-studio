"""WSGI entry point for production hosting.

Point your host at this file. Examples:

    gunicorn wsgi:app                     # Oracle Cloud / any Linux VM
    # PythonAnywhere: set the WSGI file to import `app` from here

On import it creates the database (if missing) and the first-run admin
account (username/password from the ADMIN_USERNAME / ADMIN_PASSWORD
environment variables, defaulting to admin / admin - change it!).
"""
from database import db_manager as db
from webapp import app, _bootstrap_admin

db.init_db()
_bootstrap_admin()

if __name__ == "__main__":
    app.run()
