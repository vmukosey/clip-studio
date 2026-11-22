from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase): pass

class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    uri: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='created')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Clip(Base):
    __tablename__ = 'clips'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    start: Mapped[float] = mapped_column(default=0)
    end: Mapped[float] = mapped_column(default=0)
    score: Mapped[float] = mapped_column(default=0)
    confidence: Mapped[float] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(30), default='candidate')
    selection: Mapped[str] = mapped_column(String(30), default='unselected')
    title: Mapped[str] = mapped_column(String(200), default='')
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    review: Mapped[dict] = mapped_column(JSON, default=dict)

class ReviewDecision(Base):
    __tablename__ = 'review_decisions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    clip_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text, default='')
    start: Mapped[float] = mapped_column(default=0)
    end: Mapped[float] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class Campaign(Base):
    __tablename__ = 'campaigns'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class Voiceover(Base):
    __tablename__ = 'voiceovers'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    clip_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_path: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(30))
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    render_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default='')
    regions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    lines: Mapped[list] = mapped_column(JSON, default=list)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class StudioProject(Base):
    __tablename__ = 'studio_projects'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    document: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class StudioAsset(Base):
    __tablename__ = 'studio_assets'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(Text)
    source_clip_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class StudioRender(Base):
    __tablename__ = 'studio_renders'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(default=0)
    path: Mapped[str] = mapped_column(Text)
    transcript_status: Mapped[str] = mapped_column(String(20), default='idle')
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    transcript_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class HiddenItem(Base):
    __tablename__ = 'studio_hidden_items'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def init_db():
    Base.metadata.create_all(engine)
    voiceover_columns = {column['name'] for column in inspect(engine).get_columns('voiceovers')}
    with engine.begin() as connection:
        if 'project_id' not in voiceover_columns:
            connection.execute(text('ALTER TABLE voiceovers ADD COLUMN project_id VARCHAR(64)'))
        if 'render_id' not in voiceover_columns:
            connection.execute(text('ALTER TABLE voiceovers ADD COLUMN render_id VARCHAR(64)'))
        if 'prompt' not in voiceover_columns:
            connection.execute(text("ALTER TABLE voiceovers ADD COLUMN prompt TEXT DEFAULT ''"))
        if 'regions' not in voiceover_columns:
            connection.execute(text("ALTER TABLE voiceovers ADD COLUMN regions JSON DEFAULT '[]'"))
    columns = {column['name'] for column in inspect(engine).get_columns('clips')}
    if 'review' not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE clips ADD COLUMN review JSON DEFAULT '{}'"))
    if 'title' not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE clips ADD COLUMN title VARCHAR(200) DEFAULT ''"))
    if 'selection' not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE clips ADD COLUMN selection VARCHAR(30) DEFAULT 'unselected'"))

