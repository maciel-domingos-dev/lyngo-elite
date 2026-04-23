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
    # ── Plano / Trial ─────────────────────────────────────────────────────────
    trial_inicio    = Column(DateTime, nullable=True)
    trial_expira    = Column(DateTime, nullable=True)
    plano_status    = Column(String(20), nullable=False, default="trial")
    vibel_consultas = Column(Integer, nullable=False, default=0)

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
    """Verifica se o schema base está completo (usuario + senha_hash).
    Se faltar, faz DROP CASCADE e retorna True para recriar tudo."""
    with engine.connect() as conn:
        tbl = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios'"
        )).fetchone()

        if not tbl:
            return False

        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usuarios)"))}

        if "usuario" in cols and "senha_hash" in cols:
            return False

        conn.execute(text("PRAGMA foreign_keys = OFF"))
        for tbl_name in ["click_events", "sessao_tokens", "vendas", "links", "produtos", "usuarios"]:
            conn.execute(text(f"DROP TABLE IF EXISTS {tbl_name}"))
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.commit()
        return True


def _migrate_plano() -> None:
    """Adiciona colunas de plano na tabela usuarios sem perder dados existentes."""
    with engine.connect() as conn:
        tbl = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usuarios'"
        )).fetchone()
        if not tbl:
            return

        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(usuarios)"))}

        _now = datetime.utcnow().isoformat()
        _exp = (datetime.utcnow() + timedelta(days=30)).isoformat()

        if "trial_inicio" not in cols:
            conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN trial_inicio DATETIME DEFAULT '{_now}'"))
        if "trial_expira" not in cols:
            conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN trial_expira DATETIME DEFAULT '{_exp}'"))
        if "plano_status" not in cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN plano_status VARCHAR(20) DEFAULT 'trial'"))
        if "vibel_consultas" not in cols:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN vibel_consultas INTEGER DEFAULT 0"))

        conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    if _migrate_usuarios():
        Base.metadata.create_all(bind=engine)
    _migrate_plano()
    _seed_admin()


def _seed_admin():
    """Garante que o usuário administrador padrão exista."""
    db = SessionLocal()
    try:
        existe = db.query(Usuario).filter(Usuario.usuario == "admin").first()
        if not existe:
            _now = datetime.utcnow()
            admin = Usuario(
                nome="Administrador",
                email="admin@linkguard.local",
                usuario="admin",
                senha_hash=_hash("linkguard2025"),
                plano_status="elite",
                trial_inicio=_now,
                trial_expira=_now + timedelta(days=36500),
                vibel_consultas=0,
            )
            db.add(admin)
            db.commit()
        else:
            # Garante que admin existente tenha plano elite
            if not existe.plano_status or existe.plano_status == "trial":
                existe.plano_status = "elite"
                if not existe.trial_inicio:
                    existe.trial_inicio = datetime.utcnow()
                if not existe.trial_expira:
                    existe.trial_expira = datetime.utcnow() + timedelta(days=36500)
                db.commit()
    finally:
        db.close()


# ── Helpers de plano ──────────────────────────────────────────────────────────

LIMITE_TRIAL = {"produtos": 3, "links": 15, "vibel": 5}


def verificar_plano(usuario_id: int) -> dict:
    """Retorna o status do plano e contadores do usuário.

    Retorna dict com:
        plano: 'trial' | 'elite' | 'expirado'
        dias_restantes: int (só para trial)
        pode_produto: bool
        pode_link: bool
        pode_vibel: bool
        total_produtos: int
        total_links: int
        vibel_consultas: int
        alerta_expiracao: bool  (True nos últimos 7 dias do trial)
    """
    db = SessionLocal()
    try:
        u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
        if not u:
            return {"plano": "expirado"}

        # Plano elite — sem restrições
        if u.plano_status == "elite":
            return {
                "plano": "elite",
                "dias_restantes": None,
                "pode_produto": True,
                "pode_link": True,
                "pode_vibel": True,
                "total_produtos": 0,
                "total_links": 0,
                "vibel_consultas": u.vibel_consultas or 0,
                "alerta_expiracao": False,
            }

        # Calcula dias restantes do trial
        agora = datetime.utcnow()
        expira = u.trial_expira or (agora - timedelta(days=1))
        dias_restantes = max(0, (expira - agora).days)
        expirado = agora > expira

        if expirado:
            return {
                "plano": "expirado",
                "dias_restantes": 0,
                "pode_produto": False,
                "pode_link": False,
                "pode_vibel": False,
                "total_produtos": 0,
                "total_links": 0,
                "vibel_consultas": u.vibel_consultas or 0,
                "alerta_expiracao": False,
            }

        # Conta recursos usados
        total_produtos = (
            db.query(Produto).filter(Produto.user_id == usuario_id).count()
        )
        total_links = (
            db.query(Link)
            .join(Produto, Link.produto_id == Produto.id)
            .filter(Produto.user_id == usuario_id)
            .count()
        )
        vibel = u.vibel_consultas or 0

        return {
            "plano": "trial",
            "dias_restantes": dias_restantes,
            "pode_produto": total_produtos < LIMITE_TRIAL["produtos"],
            "pode_link": total_links < LIMITE_TRIAL["links"],
            "pode_vibel": vibel < LIMITE_TRIAL["vibel"],
            "total_produtos": total_produtos,
            "total_links": total_links,
            "vibel_consultas": vibel,
            "alerta_expiracao": dias_restantes <= 7,
        }
    finally:
        db.close()


def incrementar_vibel(usuario_id: int) -> None:
    """Incrementa o contador de consultas VIBEL do usuário."""
    db = SessionLocal()
    try:
        u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
        if u:
            u.vibel_consultas = (u.vibel_consultas or 0) + 1
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
