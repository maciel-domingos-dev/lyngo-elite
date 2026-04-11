from datetime import datetime, timedelta
import hashlib
import uuid
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, Text, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = "sqlite:///linkguard.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Usuario(Base):
    __tablename__ = "usuarios"

    id         = Column(Integer, primary_key=True, index=True)
    nome       = Column(String(150), nullable=False)
    email      = Column(String(255), unique=True, nullable=True, index=True)
    google_id  = Column(String(255), unique=True, nullable=True)
    foto_url   = Column(Text, nullable=True)
    # ── Campos de autenticação local ──────────────────────────────────────────
    usuario    = Column(String(100), unique=True, nullable=True, index=True)
    senha_hash = Column(String(255), nullable=True)

    produtos = relationship("Produto", back_populates="usuario", cascade="all, delete-orphan")


class Produto(Base):
    __tablename__ = "produtos"

    id            = Column(Integer, primary_key=True, index=True)
    nome          = Column(String(255), nullable=False)
    descricao     = Column(Text, nullable=True)
    preco         = Column(Float, nullable=False, default=0.0)
    link_afiliado = Column(Text, nullable=True)
    user_id       = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)

    usuario = relationship("Usuario", back_populates="produtos")
    links   = relationship("Link", back_populates="produto", cascade="all, delete-orphan")


class Link(Base):
    __tablename__ = "links"

    id            = Column(Integer, primary_key=True, index=True)
    produto_id    = Column(Integer, ForeignKey("produtos.id", ondelete="CASCADE"), nullable=False)
    rotulo        = Column(String(255), nullable=True)
    url_original  = Column(Text, nullable=False)
    url_encurtada = Column(String(255), unique=True, nullable=False, index=True)
    url_bitly     = Column(Text, nullable=True)
    cliques       = Column(Integer, nullable=False, default=0)
    vendas_count  = Column(Integer, nullable=False, default=0)

    produto      = relationship("Produto", back_populates="links")
    vendas       = relationship("Venda", back_populates="link", cascade="all, delete-orphan")
    click_events = relationship("ClickEvent", back_populates="link", cascade="all, delete-orphan")


class Venda(Base):
    __tablename__ = "vendas"

    id         = Column(Integer, primary_key=True, index=True)
    link_id    = Column(Integer, ForeignKey("links.id", ondelete="CASCADE"), nullable=False)
    valor      = Column(Float, nullable=False)
    data_hora  = Column(DateTime, nullable=False, default=datetime.utcnow)

    link = relationship("Link", back_populates="vendas")


class ClickEvent(Base):
    __tablename__ = "click_events"

    id          = Column(Integer, primary_key=True, index=True)
    link_id     = Column(Integer, ForeignKey("links.id", ondelete="CASCADE"), nullable=False)
    accessed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    link = relationship("Link", back_populates="click_events")


class SessaoToken(Base):
    __tablename__ = "sessao_tokens"

    id         = Column(Integer, primary_key=True, index=True)
    token      = Column(String(64), unique=True, nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    criada_em  = Column(DateTime, nullable=False, default=datetime.utcnow)
    expira_em  = Column(DateTime, nullable=False)

    usuario = relationship("Usuario")


def _hash(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def verificar_senha(senha: str, hash_salvo: str) -> bool:
    return _hash(senha) == hash_salvo


# ── Gestão de tokens de sessão persistentes ───────────────────────────────────

def criar_token(usuario_id: int, dias: int = 30) -> str:
    """Gera um token UUID, salva no banco e retorna o valor."""
    db = SessionLocal()
    try:
        token = uuid.uuid4().hex
        db.add(SessaoToken(
            token=token,
            usuario_id=usuario_id,
            expira_em=datetime.utcnow() + timedelta(days=dias),
        ))
        db.commit()
        return token
    finally:
        db.close()


def validar_token(token: str) -> dict | None:
    """Valida o token e retorna {id, nome, usuario} se ainda for válido."""
    if not token:
        return None
    db = SessionLocal()
    try:
        sess = (
            db.query(SessaoToken)
            .filter(SessaoToken.token == token,
                    SessaoToken.expira_em > datetime.utcnow())
            .first()
        )
        if not sess:
            return None
        u = db.query(Usuario).filter(Usuario.id == sess.usuario_id).first()
        if not u:
            return None
        return {"id": u.id, "nome": u.nome, "usuario": u.usuario}
    finally:
        db.close()


def revogar_token(token: str) -> None:
    """Apaga o token do banco (logout)."""
    if not token:
        return
    db = SessionLocal()
    try:
        db.query(SessaoToken).filter(SessaoToken.token == token).delete()
        db.commit()
    finally:
        db.close()


def _migrate_usuarios() -> bool:
    """Verifica se o schema está completo.
    Se faltar usuario/senha_hash, faz DROP CASCADE de todas as tabelas e retorna True
    (sinaliza que init_db deve rodar create_all novamente)."""
    with engine.connect() as conn:
        # Verifica se a tabela existe
        tbl = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios'"
        )).fetchone()

        if not tbl:
            return False  # Será criada pelo create_all

        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usuarios)"))}

        if "usuario" in cols and "senha_hash" in cols:
            return False  # Schema OK

        # Schema incompleto — reset total (DROP CASCADE manual no SQLite)
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        for tbl_name in ["click_events", "sessao_tokens", "vendas", "links", "produtos", "usuarios"]:
            conn.execute(text(f"DROP TABLE IF EXISTS {tbl_name}"))
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.commit()
        return True  # Sinaliza que precisa recriar


def init_db():
    Base.metadata.create_all(bind=engine)
    if _migrate_usuarios():
        # Schema estava incompleto — recria todas as tabelas após o reset
        Base.metadata.create_all(bind=engine)
    _seed_admin()


def _seed_admin():
    """Garante que o usuário administrador padrão exista."""
    db = SessionLocal()
    try:
        existe = db.query(Usuario).filter(Usuario.usuario == "admin").first()
        if not existe:
            admin = Usuario(
                nome="Administrador",
                email="admin@linkguard.local",
                usuario="admin",
                senha_hash=_hash("linkguard2025"),
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("Banco de dados inicializado com sucesso.")
