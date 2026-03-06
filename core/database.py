import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Float, DateTime, func
import datetime

# Определяем путь к папке проекта (на два уровня выше от database.py, если database.py лежит в core/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корень проекта
DB_PATH = os.path.join(BASE_DIR, 'trades.db')
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)
    side = Column(String)
    price = Column(Float)
    amount = Column(Float)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    strategy = Column(String)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session