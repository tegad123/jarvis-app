"""Alembic environment for the platform database. Run through
scripts/pg_tenant.py, which passes the URL and the owner role; objects are
created as that role so the app role can never alter them."""
from alembic import context
from sqlalchemy import create_engine, text

config = context.config
url = config.get_main_option("sqlalchemy.url")
owner = config.attributes.get("owner_role")

engine = create_engine(url)
with engine.connect() as conn:
    if owner:
        conn.execute(text(f'SET ROLE "{owner}"'))
    context.configure(connection=conn, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
    conn.commit()
