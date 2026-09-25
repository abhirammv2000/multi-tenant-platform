from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from shared.config import DATABASE_URL
from sqlalchemy.orm import DeclarativeBase

asyncio_engine=create_async_engine(DATABASE_URL)

async_session=async_sessionmaker(bind=asyncio_engine,expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with async_session() as session:
        yield session
