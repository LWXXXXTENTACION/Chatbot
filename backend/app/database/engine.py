"""业务数据库的异步 SQLAlchemy Engine 与 Session 工厂。

这里只连接 ``DATABASE_URL``（默认 chatbot.db）；LangGraph checkpoint 在应用
lifespan 中由 AsyncSqliteSaver 独立持久化，避免业务事务与 Graph super-step
互相耦合。重复 head read 由 ``checkpointing.py`` 的 stream 级 hot cache 消除。
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DATABASE_URL

_engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """FastAPI 请求级 Session：路由结束后无论成功失败都关闭连接。"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
