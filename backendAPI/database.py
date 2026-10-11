from sqlalchemy import create_engine

from config import settings

# one engine (connection pool) shared by main.py and every router
db_engine = create_engine(settings.database_url)
