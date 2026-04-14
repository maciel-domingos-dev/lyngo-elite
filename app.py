import streamlit as st
import requests
import json
import os
from datetime import datetime as _dt
from database import (
    init_db, SessionLocal, Produto, Usuario, Link, Venda, ClickEvent,
    verificar_senha, criar_token, validar_token, revogar_token,
)

# ── Inicializa banco ──────────────────────────────────────────────────────────
init_db()

# Modelo OpenRouter da VIBEL
VIVI_MODEL_ID = "openrouter/free"

def _vivi_generate(messages: list, system_prompt: str) -> str:
    """Gera resposta da VIBEL via OpenRouter (formato OpenAI)."""
    api_key = (st.secrets.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        api_key = st.session_state.get("gemini_api_key", "").strip()
    if not api_key:
        raise ValueError("Configure sua chave API nas Configurações para ativar a VIBEL AI.")

    print(f"[VIVI DEBUG] modelo={VIVI_MODEL_ID} | chave=...{api_key[-6:]}")

    # Monta histórico no formato OpenAI: system + mensagens alternadas
    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        openai_messages.append({"role": m["role"], "content": m["content"]})

    payload = {"model": VIVI_MODEL_ID, "max_tokens": 1024, "messages": openai_messages}
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Erro {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]

def _copy_component(url: str, key: str) -> None:
    """Botão de copiar via iframe data-URL com allow=clipboard-write.
    Funciona em qualquer contexto Streamlit (expander, colunas, etc.)."""
    import json as _json, base64 as _b64
    safe_url = _json.dumps(url)
    _html_src = (
        "<!DOCTYPE html><html><head><style>"
        "*{margin:0;padding:0;box-sizing:border-box;}"
        "body{background:transparent;display:flex;align-items:center;"
        "justify-content:center;height:26px;width:100%;}"
        "button{display:inline-flex;align-items:center;justify-content:center;"
        "width:100%;height:26px;border-radius:4px;cursor:pointer;"
        "background:transparent;border:2px solid #00f5ff;"
        "color:#00f5ff;font-size:13px;padding:0 0.25rem;"
        "box-shadow:0 0 8px rgba(0,245,255,0.35);"
        "letter-spacing:0.5px;font-family:'Rajdhani',sans-serif;font-weight:700;"
        "transition:all 0.16s ease;text-transform:uppercase;}"
        "button:hover{border-color:#00f5ff;color:#00f5ff;"
        "box-shadow:0 0 20px rgba(0,245,255,0.55),inset 0 0 10px rgba(0,245,255,0.1);"
        "text-shadow:0 0 10px #00f5ff,0 0 22px rgba(0,245,255,0.5);"
        "background:rgba(0,245,255,0.13);filter:brightness(1.15);}"
        "button.ok{border-color:#39ff14!important;color:#39ff14!important;"
        "box-shadow:0 0 12px rgba(57,255,20,0.55)!important;"
        "text-shadow:0 0 10px #39ff14!important;}"
        "</style></head><body>"
        "<button id='b' onclick='cp()' title='Copiar'>&#128203;</button>"
        "<script>"
        f"var u={safe_url};"
        "function cp(){"
        "var b=document.getElementById('b');"
        "function done(){b.textContent='\\u2714';b.classList.add('ok');"
        "setTimeout(function(){b.textContent='\\uD83D\\uDCCB';b.classList.remove('ok');},2000);}"
        "function fb(){var t=document.createElement('textarea');t.value=u;"
        "t.style.position='fixed';t.style.opacity='0';"
        "document.body.appendChild(t);t.focus();t.select();"
        "try{document.execCommand('copy');done();}catch(e){}"
        "document.body.removeChild(t);}"
        "if(navigator&&navigator.clipboard){"
        "navigator.clipboard.writeText(u).then(done).catch(fb);"
        "}else{fb();}}"
        "</script></body></html>"
    )
    _b64_src = _b64.b64encode(_html_src.encode("utf-8")).decode()
    st.markdown(
        f'<iframe src="data:text/html;base64,{_b64_src}" '
        f'id="cp-iframe-{key}" '
        f'allow="clipboard-write" '
        f'style="border:none;width:100%;height:26px;overflow:hidden;'
        f'background:transparent;display:block;" '
        f'scrolling="no"></iframe>',
        unsafe_allow_html=True,
    )

# ── Persona da VIBEL AI ───────────────────────────────────────────────────────
VIVI_SYSTEM_PROMPT = """Você é a VIBEL AI, Estrategista de Vendas Exclusiva do Lyngo Elite.

MISSÃO: Ajudar Afiliados, donos de E-commerce e Comércios Locais a organizarem seus links \
e converterem cliques em vendas reais.

ESPECIALIDADES:
1. FUNIL DE VENDAS — Indique sempre se o link deve apontar para Checkout direto, \
WhatsApp de suporte ou Landing Page, conforme o estágio do lead.
2. COPYWRITING DE ALTA CONVERSÃO — Gere scripts curtos e objetivos para WhatsApp, \
legendas de Instagram e textos de anúncios (Google Ads / Meta Ads). \
Cada copy deve ter gancho, prova social (quando disponível) e CTA claro.
3. VISÃO DE NEGÓCIO LOCAL — Para comércios locais (guincho, chaveiro, clínica, restaurante etc.), \
priorize frases que gerem contato imediato e urgência real, sem enrolação.
4. MÉTRICAS E ROI — Quando sugerir uma estratégia, mencione o impacto esperado \
em taxa de conversão, custo por lead ou ROAS quando aplicável.

TOM DE VOZ: Profissional, assertiva, técnica mas acessível. Direto ao ponto. \
Sem menções a mentorias externas ou terceiros. Foco total em resultado mensurável.

IDIOMA: Responda sempre em português brasileiro claro e objetivo. \
Use emojis apenas onde reforcem o argumento, nunca como enfeite."""

# ── Config persistente (arquivo local) ───────────────────────────────────────
_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".linkguard.cfg")


def _load_cfg() -> dict:
    """Lê o arquivo de configuração local."""
    if os.path.exists(_CFG_PATH):
        try:
            with open(_CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cfg(key: str, value: str) -> None:
    """Salva uma chave no arquivo de configuração local."""
    data = _load_cfg()
    data[key] = value
    with open(_CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _del_cfg(key: str) -> None:
    """Remove uma chave do arquivo de configuração local."""
    data = _load_cfg()
    data.pop(key, None)
    with open(_CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _init_session_from_cfg() -> None:
    """Na primeira execução da sessão, carrega o token salvo no arquivo."""
    if "cfg_loaded" not in st.session_state:
        cfg = _load_cfg()
        st.session_state.bitly_token    = cfg.get("bitly_token", "")
        st.session_state.gemini_api_key = cfg.get("gemini_api_key", "")
        st.session_state.base_url       = cfg.get("base_url", "http://localhost:8501")
        st.session_state.cfg_loaded = True

# ── Configuração da página ────────────────────────────────────────────────────
st.set_page_config(
    page_title="Lyngo Elite",
    page_icon="🔗",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── CSS Crítico Mobile — injetado imediatamente após set_page_config ──────────
# Garante que as regras de responsividade sejam as primeiras a serem aplicadas,
# evitando flash de layout desktop em telas mobile.
st.markdown("""
<style>
/* Força colunas Streamlit a empilharem no mobile (selector atualizado para v1.40+) */
@media (max-width: 768px) {
    /* Streamlit >= 1.40 usa data-testid="column"; versões anteriores usam "stColumn" */
    [data-testid="column"],
    [data-testid="stColumn"] {
        width: 100% !important;
        flex: 1 1 calc(100% - 1rem) !important;
        min-width: 100% !important;
    }
    /* Container principal sem padding lateral excessivo */
    .block-container,
    [data-testid="stMain"] .block-container {
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
        max-width: 100% !important;
    }
    /* Bloco horizontal vira coluna */
    [data-testid="stHorizontalBlock"],
    [data-testid="stColumns"] {
        flex-direction: column !important;
        flex-wrap: wrap !important;
    }
}
@media (max-width: 480px) {
    [data-testid="column"],
    [data-testid="stColumn"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 100% !important;
    }
}
</style>
""", unsafe_allow_html=True)

# ── Rastreador de cliques via ?r=slug ─────────────────────────────────────────
# Deve ficar logo após st.set_page_config (primeiro comando Streamlit)
_r_slug = st.query_params.get("r")
if _r_slug:
    print(f"[REDIRECT] Slug capturado: {_r_slug!r}")

    _rdb = SessionLocal()
    try:
        _rlink = _rdb.query(Link).filter(Link.url_encurtada == _r_slug).first()
        if _rlink:
            _rlink.cliques += 1
            _rdb.add(ClickEvent(link_id=_rlink.id, accessed_at=_dt.utcnow()))
            _rdb.commit()
            _rtarget = _rlink.url_original
            print(f"[REDIRECT] URL encontrada no banco: {_rtarget!r}")
        else:
            _rtarget = None
            print(f"[REDIRECT] Slug '{_r_slug}' NÃO encontrado no banco.")
    finally:
        _rdb.close()

    if _rtarget:
        print(f"[REDIRECT] Executando redirecionamento para: {_rtarget!r}")
        _safe_url = json.dumps(_rtarget)
        # meta refresh + JS: dupla garantia de redirecionamento
        st.markdown(
            f'<meta http-equiv="refresh" content="0; url={_rtarget}">'
            f'<script>window.location.replace({_safe_url});</script>',
            unsafe_allow_html=True,
        )
        st.stop()

    else:
        print(f"[REDIRECT] Exibindo página 404 para slug: {_r_slug!r}")
        _slug_safe = _r_slug.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        st.markdown("""<style>
[data-testid="stAppViewContainer"],[data-testid="stMain"],
section.main,.main{background:#030508!important;}
[data-testid="stToolbar"],[data-testid="stDecoration"],
[data-testid="stHeader"],[data-testid="stSidebar"],
[data-testid="stSidebarNav"],[data-testid="collapsedControl"]
{display:none!important;}
.block-container{padding:2rem!important;max-width:100%!important;}
</style>""", unsafe_allow_html=True)
        st.markdown(f"""
<div style="min-height:75vh;display:flex;flex-direction:column;
    align-items:center;justify-content:center;text-align:center;
    gap:1.1rem;font-family:'Segoe UI',system-ui,sans-serif;">
  <div style="font-size:5rem;font-weight:900;line-height:1;letter-spacing:8px;
      font-family:'Orbitron',monospace;
      background:linear-gradient(135deg,#00f5ff,#a855f7,#ff2d78);
      -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
      filter:drop-shadow(0 0 18px rgba(0,245,255,.5)) drop-shadow(0 0 30px rgba(168,85,247,.3));">
    404
  </div>
  <div style="font-size:.9rem;font-weight:800;color:#e0e6f0;
      letter-spacing:4px;text-transform:uppercase;">
    Link não encontrado
  </div>
  <div style="width:100px;height:1px;
      background:linear-gradient(90deg,transparent,#00f5ff,transparent);
      box-shadow:0 0 8px rgba(0,245,255,.5);"></div>
  <div style="font-size:.8rem;color:#4a5a80;line-height:1.75;max-width:340px;">
    O link <code style="color:#00f5ff;background:rgba(0,245,255,.08);
    padding:.1rem .45rem;border-radius:3px;">?r={_slug_safe}</code>
    não existe ou foi removido pelo proprietário.
  </div>
  <a href="/" style="margin-top:.4rem;display:inline-block;
      padding:.5rem 1.6rem;border-radius:6px;
      background:linear-gradient(#030508,#030508) padding-box,
                 linear-gradient(135deg,#00f5ff,#a855f7,#ff2d78) border-box;
      border:2px solid transparent;
      color:#00f5ff;text-decoration:none;font-size:.75rem;font-weight:800;
      letter-spacing:2px;text-transform:uppercase;
      box-shadow:0 0 20px rgba(0,245,255,.3),0 0 40px rgba(168,85,247,.15);
      text-shadow:0 0 8px rgba(0,245,255,.6);">
    ← Voltar ao início
  </a>
  <div style="font-size:.62rem;color:#1e2a40;letter-spacing:3px;margin-top:.3rem;">
    LYNGO ELITE · LINK TRACKING
  </div>
</div>""", unsafe_allow_html=True)
        st.stop()

# ── Tela de Login / Cadastro ──────────────────────────────────────────────────
# ── Auth: CSS puro (sem tags) ─────────────────────────────────────────────────
_AUTH_STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@400;600;700;900&display=swap');

html, body,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
div[class*="appview"] {
    background: #030508 !important;
    background-color: #030508 !important;
    height: 100% !important;
    min-height: 100vh !important;
    overflow-x: hidden !important;
}

[data-testid="stMain"],
section.main, .main,
div[class*="main"] {
    background: #030508 !important;
    background-color: #030508 !important;
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 100vh !important;
    padding: 0 1rem !important;
}
[data-testid="stSidebar"],
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
#MainMenu, footer { display: none !important; visibility: hidden !important; }

/* ── Auth Container Kill-Switch ── */
#auth-container,
.main .block-container,
[data-testid="stMain"] .block-container {
    max-width: min(500px, 96vw) !important;
    width: min(500px, 96vw) !important;
    margin: 0 auto !important;
}

.main .block-container {
    max-width: min(500px, 96vw) !important;
    width: min(500px, 96vw) !important;
    margin: 0 auto !important;
    padding: 2.6rem clamp(1rem, 5vw, 2.4rem) 2.2rem !important;
    background: linear-gradient(#090e1b, #060810) padding-box,
                linear-gradient(135deg, #00f5ff, #a855f7, #ff2d78) border-box !important;
    border: 2px solid transparent !important;
    border-radius: 18px !important;
    box-shadow:
        0 0 40px rgba(0,245,255,0.3),
        0 0 80px rgba(168,85,247,0.2),
        0 0 120px rgba(255,45,120,0.1),
        inset 0 0 30px rgba(0,245,255,0.03) !important;
    position: relative !important;
}

@keyframes lgAuthScan {
    0%   { top: 0%;   opacity: 0.6; }
    100% { top: 110%; opacity: 0;   }
}
.main .block-container::after {
    content: '' !important;
    position: absolute !important;
    top: 0 !important; left: 0 !important; right: 0 !important;
    height: 2px !important;
    background: linear-gradient(90deg, transparent 0%, #00f5ff 30%, #a855f7 55%, #ff2d78 80%, transparent 100%) !important;
    animation: lgAuthScan 3.5s ease-in-out infinite !important;
    pointer-events: none !important;
    border-radius: 18px 18px 0 0 !important;
}

.lg-auth-title {
    font-family: 'Orbitron', sans-serif !important;
    font-size: clamp(1.1rem, 3.5vw, 1.4rem) !important;
    font-weight: 900 !important;
    letter-spacing: 3px !important;
    text-align: center !important;
    margin-bottom: 0.15rem !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: clip !important;
    background: linear-gradient(90deg, #00f5ff 0%, #a855f7 55%, #f472b6 100%) !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    background-clip: text !important;
    filter: drop-shadow(0 0 14px rgba(0,245,255,0.5)) drop-shadow(0 0 28px rgba(168,85,247,0.3)) !important;
}
.lg-auth-sub {
    text-align: center; font-size: 0.58rem; letter-spacing: clamp(1px, 1vw, 5px);
    color: #ff2d78; margin-bottom: 2rem;
    text-transform: uppercase; font-family: 'Rajdhani', sans-serif;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    max-width: 100%;
}
.lg-auth-mode {
    text-align: center; font-size: 0.66rem; letter-spacing: 3px;
    color: rgba(168,85,247,0.9); margin-bottom: 1.4rem; text-transform: uppercase;
    font-weight: 700; font-family: 'Rajdhani', sans-serif;
    padding-bottom: 0.85rem;
    border-bottom: 1px solid rgba(168,85,247,0.2);
}

label,
p[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] span,
div[data-baseweb="form-control"] label,
.stTextInput label, .stTextInput > label,
[data-testid="stWidgetLabel"] * {
    color: #00f5ff !important;
    -webkit-text-fill-color: #00f5ff !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 0.7rem !important;
    font-weight: 700 !important;
    letter-spacing: 2.2px !important;
    text-transform: uppercase !important;
    opacity: 1 !important;
    visibility: visible !important;
}

[data-testid="stTextInput"] input,
input[type="text"], input[type="password"], input[type="email"] {
    background: #04060f !important;
    background-color: #04060f !important;
    border: 1px solid rgba(168,85,247,0.55) !important;
    border-radius: 8px !important;
    color: #d8e8ff !important;
    font-size: 16px !important;
    font-family: 'Rajdhani', sans-serif !important;
    letter-spacing: 0.5px !important;
    padding: 0.55rem 0.9rem !important;
    transition: border-color 0.22s, box-shadow 0.22s !important;
    box-shadow: 0 0 10px rgba(168,85,247,0.1), inset 0 0 8px rgba(0,0,20,0.8) !important;
    outline: none !important;
}
[data-testid="stTextInput"] input:focus,
input[type="text"]:focus, input[type="password"]:focus {
    border-color: #00f5ff !important;
    box-shadow:
        0 0 0 1px rgba(0,245,255,0.35),
        0 0 20px rgba(0,245,255,0.25),
        inset 0 0 8px rgba(0,0,20,0.8) !important;
    color: #e8f8ff !important;
}

[data-testid="stForm"] {
    background: transparent !important;
    border: none !important;
    padding: 0 !important;
    box-shadow: none !important;
}

[data-testid="stFormSubmitButton"] button,
[data-testid="stFormSubmitButton"] > button {
    width: 100% !important;
    background: transparent !important;
    background-color: transparent !important;
    border: 2px solid #a855f7 !important;
    color: #a855f7 !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    letter-spacing: 3.5px !important;
    text-transform: uppercase !important;
    border-radius: 8px !important;
    padding: 0.65rem 0 !important;
    min-height: 44px !important;
    margin-top: 0.5rem !important;
    box-shadow: 0 0 16px rgba(168,85,247,0.3) !important;
    transition: all 0.28s ease !important;
    cursor: pointer !important;
}
[data-testid="stFormSubmitButton"] button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
    background: rgba(0,245,255,0.07) !important;
    background-color: rgba(0,245,255,0.07) !important;
    border-color: #00f5ff !important;
    color: #00f5ff !important;
    box-shadow:
        0 0 20px rgba(0,245,255,0.5),
        0 0 50px rgba(0,245,255,0.3),
        0 0 90px rgba(0,245,255,0.15),
        inset 0 0 18px rgba(0,245,255,0.06) !important;
    text-shadow: 0 0 10px #00f5ff, 0 0 25px rgba(0,245,255,0.6), 0 0 50px rgba(0,245,255,0.3) !important;
    transform: translateY(-2px) !important;
}

/* Toggle btns (Cadastre-se / Já tem conta) — alvo direto no DOM do Streamlit */
[data-testid="stButton"] button,
[data-testid="stButton"] > button,
.lg-toggle-btn button,
.lg-toggle-btn [data-testid="stButton"] button {
    width: 100% !important;
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    color: #00f5ff !important;
    -webkit-text-fill-color: #00f5ff !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
    text-shadow: 0 0 8px rgba(0,245,255,0.45) !important;
    padding: 0.5rem 0 !important;
    margin-top: 0.6rem !important;
    cursor: pointer !important;
    transition: all 0.2s !important;
    border-radius: 7px !important;
}
[data-testid="stButton"] button:hover,
[data-testid="stButton"] > button:hover,
.lg-toggle-btn button:hover {
    background: rgba(0,245,255,0.05) !important;
    background-color: rgba(0,245,255,0.05) !important;
    color: #7ffbff !important;
    -webkit-text-fill-color: #7ffbff !important;
    text-shadow: 0 0 14px #00f5ff, 0 0 28px rgba(0,245,255,0.5) !important;
    box-shadow: none !important;
}

.lg-auth-error {
    background: rgba(255,0,127,0.07);
    border: 1px solid rgba(255,0,127,0.45);
    border-radius: 8px; padding: 0.55rem 1rem; margin-top: 0.7rem;
    color: #ff4da6; font-size: 0.78rem; text-align: center;
    letter-spacing: 0.5px; font-family: 'Rajdhani', sans-serif;
    box-shadow: 0 0 12px rgba(255,0,127,0.15);
}
.lg-auth-ok {
    background: rgba(57,255,20,0.07);
    border: 1px solid rgba(57,255,20,0.4);
    border-radius: 8px; padding: 0.55rem 1rem; margin-top: 0.7rem;
    color: #39ff14; font-size: 0.78rem; text-align: center;
    letter-spacing: 0.5px; font-family: 'Rajdhani', sans-serif;
    box-shadow: 0 0 12px rgba(57,255,20,0.15);
}

/* ── Divisor ── */
.lg-divider {
    display: flex; align-items: center; gap: 0.7rem;
    margin: 1.1rem 0 0.9rem 0;
}
.lg-divider-line {
    flex: 1; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(168,85,247,0.3), transparent);
}
.lg-divider-text {
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.6rem; letter-spacing: 2px; color: rgba(168,85,247,0.5);
    text-transform: uppercase; white-space: nowrap;
}

/* ── Botão Google ── */
.lg-google-btn {
    display: flex; align-items: center; justify-content: center;
    gap: 0.6rem; width: 100%;
    background: transparent;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 8px;
    padding: 0.6rem 1rem;
    cursor: pointer;
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.82rem; font-weight: 600;
    letter-spacing: 1.5px; text-transform: uppercase;
    color: rgba(220,230,255,0.75);
    transition: all 0.22s ease;
    box-shadow: 0 0 8px rgba(255,255,255,0.04);
    text-decoration: none;
    margin-top: 0;
}
.lg-google-btn:hover {
    border-color: rgba(255,255,255,0.45);
    background: rgba(255,255,255,0.04);
    color: #fff;
    box-shadow: 0 0 18px rgba(255,255,255,0.1);
}
.lg-google-icon {
    width: 16px; height: 16px; flex-shrink: 0;
}
"""

# ── Auth: JS puro (sem tags) ──────────────────────────────────────────────────
_AUTH_JS = """
(function(){
    var CYAN='#00f5ff', PURPLE='#a855f7', PINK='#f472b6';
    function fixLabels(){
        ['label','[data-testid="stWidgetLabel"]',
         '[data-testid="stWidgetLabel"] p','[data-testid="stWidgetLabel"] span',
         'div[data-baseweb="form-control"] label'].forEach(function(s){
            document.querySelectorAll(s).forEach(function(el){
                el.style.setProperty('color',CYAN,'important');
                el.style.setProperty('-webkit-text-fill-color',CYAN,'important');
                el.style.setProperty('opacity','1','important');
            });
        });
    }
    function fixBtns(){
        document.querySelectorAll('[data-testid="stFormSubmitButton"] button').forEach(function(b){
            if(b._lgA) return; b._lgA=true;
            b.style.setProperty('border','2px solid '+PURPLE,'important');
            b.style.setProperty('color',PURPLE,'important');
            b.style.setProperty('background','transparent','important');
            b.addEventListener('mouseenter',function(){
                b.style.setProperty('border-color',CYAN,'important');
                b.style.setProperty('color',CYAN,'important');
                b.style.setProperty('background','rgba(0,245,255,0.07)','important');
                b.style.setProperty('box-shadow','0 0 30px rgba(0,245,255,0.55)','important');
                b.style.setProperty('text-shadow','0 0 14px '+CYAN,'important');
            });
            b.addEventListener('mouseleave',function(){
                b.style.setProperty('border-color',PURPLE,'important');
                b.style.setProperty('color',PURPLE,'important');
                b.style.setProperty('background','transparent','important');
                b.style.setProperty('box-shadow','0 0 16px rgba(168,85,247,0.3)','important');
                b.style.setProperty('text-shadow','none','important');
            });
        });
        document.querySelectorAll('[data-testid="stButton"] button').forEach(function(b){
            if(b._lgT) return; b._lgT=true;
            b.style.setProperty('color',CYAN,'important');
            b.style.setProperty('-webkit-text-fill-color',CYAN,'important');
            b.style.setProperty('background','transparent','important');
            b.style.setProperty('background-color','transparent','important');
            b.style.setProperty('border','none','important');
            b.style.setProperty('box-shadow','none','important');
            b.style.setProperty('text-shadow','0 0 8px rgba(0,245,255,0.45)','important');
        });
    }
    function stampContainer(){
        var bc = document.querySelector('.main .block-container') ||
                 document.querySelector('[data-testid="stMain"] .block-container');
        if(bc){
            bc.id = 'auth-container';
            var _mw = Math.min(500, window.innerWidth * 0.96) + 'px';
            bc.style.setProperty('max-width',_mw,'important');
            bc.style.setProperty('width',_mw,'important');
            bc.style.setProperty('margin-left','auto','important');
            bc.style.setProperty('margin-right','auto','important');
            bc.style.setProperty('border','2px solid #00f5ff','important');
            bc.style.setProperty('box-shadow','0 0 25px rgba(0,245,255,0.4), 0 0 50px rgba(168,85,247,0.25)','important');
            bc.style.setProperty('border-radius','18px','important');
        }
    }
    function run(){ fixLabels(); fixBtns(); stampContainer(); }
    run(); setTimeout(run,300); setTimeout(run,800);
    new MutationObserver(function(ml){
        if(ml.some(function(m){return m.addedNodes.length;})) run();
    }).observe(document.body,{childList:true,subtree:true});
})();
"""

def _login_page():
    import hashlib as _hl

    if "auth_mode" not in st.session_state:
        st.session_state.auth_mode = "login"

    # 1. CSS — injetado com o padrão exato que o Streamlit aceita
    style_code = _AUTH_STYLE
    st.markdown(f'<style>{style_code}</style>', unsafe_allow_html=True)

    # 2. Logo + Título (fora de qualquer form)
    st.markdown("""
<div style="display:flex;justify-content:center;margin-bottom:1rem;">
<svg width="160" height="60" viewBox="0 0 280 104" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="la-gring" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#39ff14"/><stop offset="100%" stop-color="#a855f7"/></linearGradient>
    <linearGradient id="la-gbolt" x1="20%" y1="0%" x2="80%" y2="100%"><stop offset="0%" stop-color="#00f5ff"/><stop offset="50%" stop-color="#a855f7"/><stop offset="100%" stop-color="#ff2d78"/></linearGradient>
    <linearGradient id="la-gt" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#00f5ff"/><stop offset="35%" stop-color="#a855f7"/><stop offset="70%" stop-color="#ff2d78"/><stop offset="100%" stop-color="#39ff14"/></linearGradient>
    <linearGradient id="la-ge" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#a855f7"/><stop offset="100%" stop-color="#00f5ff"/></linearGradient>
    <clipPath id="la-clip"><rect x="98" y="4" width="84" height="48" rx="24"/></clipPath>
  </defs>
  <rect fill="#080b14" width="280" height="104" rx="0"/>
  <rect fill="#080b14" stroke="url(#la-gring)" stroke-width="3" x="98" y="4" width="84" height="48" rx="24"/>
  <rect fill="#080b14" x="112" y="16" width="56" height="24" rx="12"/>
  <g clip-path="url(#la-clip)">
    <polygon fill="url(#la-gbolt)" points="144,6 132,28 140,28 134,50 150,24 141,24"/>
  </g>
  <circle fill="#39ff14" cx="100" cy="28" r="2.5"/>
  <circle fill="#ff2d78" cx="180" cy="28" r="2.5"/>
  <text font-family="Orbitron,monospace" font-weight="900" font-size="28" fill="url(#la-gt)" x="140" y="76" text-anchor="middle" letter-spacing="3">LYNGO</text>
  <rect x="90" y="81" width="100" height="2" rx="1" fill="url(#la-ge)"/>
  <text font-family="Orbitron,monospace" font-weight="700" font-size="10" fill="url(#la-ge)" x="140" y="96" text-anchor="middle" letter-spacing="7">ELITE</text>
</svg>
</div>
<div class="lg-auth-sub" style="color:#ff2d78;">Plataforma Premium de Afiliados</div>
""", unsafe_allow_html=True)

    # ── MODO LOGIN ────────────────────────────────────────────────────────────
    if st.session_state.auth_mode == "login":
        st.markdown('<div class="lg-auth-mode">🔐 Acesso à Plataforma</div>', unsafe_allow_html=True)

        with st.form("form_login", clear_on_submit=False):
            usuario_input = st.text_input("Usuário ou E-mail", placeholder="usuário ou email@exemplo.com")
            senha_input   = st.text_input("Senha",             placeholder="••••••••", type="password")
            entrar        = st.form_submit_button("⚡ Entrar", use_container_width=True)

        if entrar:
            _ident = usuario_input.strip()
            db = SessionLocal()
            try:
                from sqlalchemy import or_
                u = db.query(Usuario).filter(
                    or_(Usuario.usuario == _ident, Usuario.email == _ident)
                ).first()
            finally:
                db.close()
            if u and u.senha_hash and verificar_senha(senha_input, u.senha_hash):
                _tok = criar_token(u.id)
                st.session_state.logged_in    = True
                st.session_state.usuario_id   = u.id
                st.session_state.usuario_nome = u.nome
                st.session_state.session_token = _tok
                st.session_state.pop("auth_mode", None)
                st.query_params["_t"] = _tok
                st.rerun()
            else:
                st.markdown(
                    '<div class="lg-auth-error">⚠ Usuário ou senha inválidos.</div>',
                    unsafe_allow_html=True,
                )

        # ── Divisor + Botão Google ────────────────────────────────────────────
        st.markdown("""
<div class="lg-divider">
  <div class="lg-divider-line"></div>
  <span class="lg-divider-text">ou continue com</span>
  <div class="lg-divider-line"></div>
</div>
<button class="lg-google-btn" onclick="alert('Integração Google em breve!')">
  <svg class="lg-google-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
  Entrar com Google
</button>
""", unsafe_allow_html=True)

        st.markdown('<div class="lg-toggle-btn">', unsafe_allow_html=True)
        if st.button("Não tem uma conta? Cadastre-se", key="go_register", use_container_width=True):
            st.session_state.auth_mode = "register"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ── MODO CADASTRO ─────────────────────────────────────────────────────────
    else:
        st.markdown('<div class="lg-auth-mode">✨ Criar Nova Conta</div>', unsafe_allow_html=True)

        with st.form("form_register", clear_on_submit=True):
            nome_input    = st.text_input("Nome completo",  placeholder="Seu nome")
            email_input   = st.text_input("E-mail",         placeholder="seu@email.com")
            usuario_new   = st.text_input("Usuário",        placeholder="nome de usuário único")
            senha_new     = st.text_input("Senha",          placeholder="mínimo 6 caracteres", type="password")
            senha_conf    = st.text_input("Confirmar senha",placeholder="repita a senha",       type="password")
            cadastrar     = st.form_submit_button("🚀 Criar Conta", use_container_width=True)

        if cadastrar:
            erro = None
            if not all([nome_input.strip(), email_input.strip(), usuario_new.strip(), senha_new]):
                erro = "Preencha todos os campos."
            elif len(senha_new) < 6:
                erro = "A senha deve ter pelo menos 6 caracteres."
            elif senha_new != senha_conf:
                erro = "As senhas não coincidem."
            else:
                db = SessionLocal()
                try:
                    if db.query(Usuario).filter(Usuario.usuario == usuario_new.strip()).first():
                        erro = f"Usuário '{usuario_new.strip()}' já está em uso."
                    elif db.query(Usuario).filter(Usuario.email == email_input.strip()).first():
                        erro = "Este e-mail já está cadastrado."
                    else:
                        novo = Usuario(
                            nome       = nome_input.strip(),
                            email      = email_input.strip(),
                            usuario    = usuario_new.strip(),
                            senha_hash = _hl.sha256(senha_new.encode("utf-8")).hexdigest(),
                        )
                        db.add(novo)
                        db.commit()
                        db.refresh(novo)
                        _tok = criar_token(novo.id)
                        st.session_state.logged_in     = True
                        st.session_state.usuario_id    = novo.id
                        st.session_state.usuario_nome  = novo.nome
                        st.session_state.session_token = _tok
                        st.session_state.pop("auth_mode", None)
                        st.query_params["_t"] = _tok
                finally:
                    db.close()

            if erro:
                st.markdown(f'<div class="lg-auth-error">⚠ {erro}</div>', unsafe_allow_html=True)
            elif st.session_state.get("logged_in"):
                st.markdown(
                    '<div class="lg-auth-ok">✔ Conta criada! Entrando...</div>',
                    unsafe_allow_html=True,
                )
                st.rerun()

        st.markdown('<div class="lg-toggle-btn">', unsafe_allow_html=True)
        if st.button("Já tem uma conta? Entrar", key="go_login", use_container_width=True):
            st.session_state.auth_mode = "login"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # 3. JS — fora de qualquer form, último elemento da página
    st.markdown(f'<script>{_AUTH_JS}</script>', unsafe_allow_html=True)

# ── Gate de autenticação (restaura sessão via token na URL ao pressionar F5) ──
if not st.session_state.get("logged_in"):
    _t = st.query_params.get("_t", "")
    if _t:
        _user_data = validar_token(_t)
        if _user_data:
            st.session_state.logged_in     = True
            st.session_state.usuario_id    = _user_data["id"]
            st.session_state.usuario_nome  = _user_data["nome"]
            st.session_state.session_token = _t
        else:
            # Token expirado ou inválido — limpa URL e pede login
            st.query_params.clear()
    if not st.session_state.get("logged_in"):
        _login_page()
        st.stop()

def _uid() -> int:
    """Retorna o ID do usuário autenticado na sessão atual."""
    return int(st.session_state.get("usuario_id", 0))

# ── CSS Cyberpunk ─────────────────────────────────────────────────────────────
CYBER_CSS = """
<link rel="stylesheet"
  href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css"
  crossorigin="anonymous" referrerpolicy="no-referrer"/>

<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;800&family=Rajdhani:wght@300;400;600&display=swap');

/* ── Reset & fundo ── */
html, body, [data-testid="stAppViewContainer"],
[data-testid="stMain"], section.main, .main {
    background: #080b14 !important;
    color: #e0e6f0 !important;
    font-family: 'Rajdhani', sans-serif;
    font-size: 16px;
}

/* ── Labels: serão reforçadas após todos os outros estilos (ver bloco abaixo) ── */
[data-testid="stHeader"] { background: transparent !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #080b14 !important;
    border-right: 1px solid rgba(0,245,255,0.15) !important;
}
[data-testid="stSidebar"] * { color: #c8d4f0 !important; }

/* ── Sidebar nav buttons — borda degradê ciano→roxo→rosa ── */
[data-testid="stSidebar"] [data-testid="stButton"] button {
    width: 100% !important;
    background: transparent !important;
    border: 2px solid transparent !important;
    background-clip: padding-box !important;
    border-radius: 6px !important;
    outline: 2px solid transparent !important;
    box-shadow: 0 0 0 1.5px rgba(0,245,255,0.35), 0 0 6px rgba(0,245,255,0.1) !important;
    color: rgba(0,245,255,0.7) !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 0.78rem !important;
    font-weight: 700 !important;
    letter-spacing: 1.2px !important;
    text-transform: uppercase !important;
    padding: 0.28rem 0.75rem !important;
    min-height: 32px !important;
    line-height: 1.2 !important;
    transition: all 0.2s ease !important;
    text-align: left !important;
    margin-bottom: 0.2rem !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
    background: rgba(168,85,247,0.07) !important;
    box-shadow: 0 0 18px rgba(168,85,247,0.4), 0 0 35px rgba(0,245,255,0.2), inset 0 0 8px rgba(168,85,247,0.05) !important;
    color: #a855f7 !important;
    text-shadow: 0 0 10px rgba(168,85,247,0.7), 0 0 22px rgba(0,245,255,0.3) !important;
    filter: brightness(1.12) !important;
}
/* Ativo — classe adicionada via JS ── */
[data-testid="stSidebar"] [data-testid="stButton"] button.lg-nav-active {
    border: 1px solid rgba(168,85,247,0.65) !important;
    color: #a855f7 !important;
    background: rgba(168,85,247,0.08) !important;
    box-shadow: 0 0 14px rgba(168,85,247,0.35), 0 0 28px rgba(0,245,255,0.15), inset 0 0 8px rgba(168,85,247,0.06) !important;
    text-shadow: 0 0 8px rgba(168,85,247,0.8) !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button.lg-nav-active:hover {
    background: rgba(168,85,247,0.14) !important;
    border-color: #a855f7 !important;
    box-shadow: 0 0 22px rgba(168,85,247,0.5), 0 0 40px rgba(0,245,255,0.2), inset 0 0 10px rgba(168,85,247,0.1) !important;
    text-shadow: 0 0 12px rgba(168,85,247,1) !important;
}

.sidebar-logo {
    text-align: center;
    padding: 1.4rem 0 1rem 0;
    font-family: 'Orbitron', sans-serif;
    font-size: 1.55rem;
    font-weight: 800;
    background: linear-gradient(90deg, #00f5ff, #a855f7, #f472b6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: 2px;
}
.sidebar-logo span {
    display: block;
    font-size: 0.65rem;
    font-family: 'Rajdhani', sans-serif;
    font-weight: 300;
    letter-spacing: 4px;
    -webkit-text-fill-color: #5a6a8a;
    margin-top: 2px;
}
.sidebar-divider {
    border: none;
    border-top: 1px solid #1e2a4a;
    margin: 0.5rem 1rem 1rem 1rem;
}

/* ── Títulos ── */
.page-title {
    font-family: 'Orbitron', sans-serif;
    font-size: 1.75rem;
    font-weight: 800;
    background: linear-gradient(90deg, #00f5ff 0%, #a855f7 50%, #ff2d78 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 0.2rem;
    letter-spacing: 1px;
    filter: drop-shadow(0 0 12px rgba(0,245,255,0.4));
}
.page-subtitle {
    color: #4a5a80;
    font-size: 0.9rem;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 1.8rem;
}

/* ── Metric cards ── */
.metric-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1rem;
    margin-bottom: 1.8rem;
}
.metric-card {
    background: linear-gradient(135deg, #0d1530 0%, #0a1020 100%);
    border: 1px solid rgba(0,245,255,0.18);
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    position: relative;
    overflow: hidden;
    transition: border-color 0.22s, box-shadow 0.22s;
    box-shadow: 0 0 14px rgba(0,245,255,0.06), inset 0 0 6px rgba(0,245,255,0.02);
}
.metric-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    border-radius: 12px 12px 0 0;
}
.metric-card.cyan::before   { background: linear-gradient(90deg, #00f5ff, transparent); }
.metric-card.purple::before { background: linear-gradient(90deg, #a855f7, transparent); }
.metric-card.pink::before   { background: linear-gradient(90deg, #f472b6, transparent); }
.metric-card.green::before  { background: linear-gradient(90deg, #34d399, transparent); }
.metric-card:hover {
    border-color: rgba(0,245,255,0.45);
    box-shadow: 0 0 22px rgba(0,245,255,0.14), inset 0 0 10px rgba(0,245,255,0.04);
}
.metric-label {
    font-size: 0.72rem;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #4a5a80;
    margin-bottom: 0.4rem;
}
.metric-value {
    font-family: 'Orbitron', sans-serif;
    font-size: 1.9rem;
    font-weight: 700;
    color: #e8eeff;
    line-height: 1;
}
.metric-delta { font-size: 0.78rem; margin-top: 0.4rem; color: #34d399; }

/* ── Panel card ── */
.panel-card {
    background: #0d1220;
    border: 1px solid rgba(0,245,255,0.2);
    border-radius: 12px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1.2rem;
    box-shadow: 0 0 20px rgba(0,245,255,0.08), 0 0 40px rgba(168,85,247,0.06), 0 0 60px rgba(255,45,120,0.04);
}
.panel-card h3 {
    font-family: 'Orbitron', sans-serif;
    font-size: 0.9rem;
    font-weight: 600;
    color: #00f5ff;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 1rem;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid;
    border-image: linear-gradient(90deg, transparent, #a855f7, #ff2d78, transparent) 1;
}

/* ── Product card (novo) ── */
.produto-card {
    background: linear-gradient(135deg, #0b1128 0%, #080d1c 100%);
    border: 1px solid #1a2545;
    border-radius: 12px;
    padding: 1.1rem 1.3rem 0.9rem 1.3rem;
    margin-bottom: 0.6rem;
    position: relative;
    overflow: hidden;
    transition: border-color 0.25s, box-shadow 0.25s;
}
.produto-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, #00f5ff 0%, #a855f7 60%, transparent 100%);
    border-radius: 12px 12px 0 0;
}
.produto-card:hover {
    border-color: rgba(0,245,255,0.28);
    box-shadow: 0 4px 24px rgba(0,200,212,0.08);
}
.pc-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 0.45rem;
}
.pc-nome {
    font-family: 'Rajdhani', sans-serif;
    font-weight: 600;
    font-size: 1.05rem;
    color: #d8e4ff;
}
.pc-preco {
    font-family: 'Orbitron', sans-serif;
    font-size: 0.95rem;
    font-weight: 700;
    color: #34d399;
    white-space: nowrap;
    margin-left: 0.8rem;
}
.pc-desc {
    font-size: 0.82rem;
    color: #4a5a80;
    margin-bottom: 0.6rem;
    line-height: 1.4;
}
.pc-link-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: rgba(0,245,255,0.05);
    border: 1px solid rgba(0,245,255,0.15);
    border-radius: 6px;
    padding: 0.25rem 0.7rem;
    font-size: 0.76rem;
    color: #00c8d4;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-bottom: 0.3rem;
}
.pc-link-chip i { font-size: 0.72rem; opacity: 0.7; flex-shrink: 0; }
.pc-id-badge {
    position: absolute;
    top: 0.7rem;
    right: 0.9rem;
    font-size: 0.65rem;
    color: #2a3860;
    font-family: 'Orbitron', sans-serif;
    letter-spacing: 1px;
}
.pc-no-link {
    font-size: 0.76rem;
    color: #2a3555;
    font-style: italic;
    margin-bottom: 0.3rem;
}

/* ── Botões de ação (wrapper + overrides) ── */

/* Editar — compacto, ciano sutil */
.action-btn-edit button {
    background: rgba(0,245,255,0.05) !important;
    border: 1px solid rgba(0,245,255,0.2) !important;
    color: #00e5f0 !important;
    font-size: 0.72rem !important;
    padding: 0.28rem 0.55rem !important;
    letter-spacing: 0.8px !important;
    border-radius: 6px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    box-shadow: none !important;
    transition: box-shadow 0.22s, border-color 0.22s, background 0.22s !important;
}
.action-btn-edit button:hover {
    background: rgba(0,245,255,0.10) !important;
    border-color: rgba(0,245,255,0.4) !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.22), 0 0 2px rgba(0,245,255,0.12) inset !important;
    text-shadow: 0 0 6px rgba(0,245,255,0.6) !important;
}

/* Excluir — compacto, vermelho sutil */
.action-btn-delete button {
    background: rgba(248,113,113,0.05) !important;
    border: 1px solid rgba(248,113,113,0.2) !important;
    color: #f87171 !important;
    font-size: 0.72rem !important;
    padding: 0.28rem 0.55rem !important;
    letter-spacing: 0.8px !important;
    border-radius: 6px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    box-shadow: none !important;
    transition: box-shadow 0.22s, border-color 0.22s, background 0.22s !important;
}
.action-btn-delete button:hover {
    background: rgba(248,113,113,0.11) !important;
    border-color: rgba(248,113,113,0.4) !important;
    box-shadow: 0 0 8px rgba(248,113,113,0.2), 0 0 2px rgba(248,113,113,0.1) inset !important;
}

/* Adicionar Link — neon ciano constante */
.action-btn-shorten button {
    background: rgba(0,245,255,0.06) !important;
    border: 1px solid rgba(0,245,255,0.35) !important;
    color: #00f5ff !important;
    font-size: 0.72rem !important;
    padding: 0.28rem 0.55rem !important;
    letter-spacing: 1px !important;
    border-radius: 6px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    box-shadow: 0 0 10px rgba(0,245,255,0.15), 0 0 3px rgba(0,245,255,0.08) inset !important;
    transition: box-shadow 0.22s, background 0.22s !important;
}
.action-btn-shorten button:hover {
    background: rgba(0,245,255,0.12) !important;
    box-shadow: 0 0 18px rgba(0,245,255,0.35), 0 0 6px rgba(0,245,255,0.18) inset !important;
    text-shadow: 0 0 8px rgba(0,245,255,0.9) !important;
}

/* ── Confirm delete strip ── */
.confirm-delete-strip {
    background: rgba(248,113,113,0.06);
    border: 1px solid rgba(248,113,113,0.2);
    border-radius: 8px;
    padding: 0.6rem 1rem;
    font-size: 0.82rem;
    color: #f87171;
    margin-bottom: 0.4rem;
}

/* ── Bitly result chip ── */
.bitly-result {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    background: rgba(168,85,247,0.08);
    border: 1px solid rgba(168,85,247,0.25);
    border-radius: 8px;
    padding: 0.4rem 0.9rem;
    font-size: 0.82rem;
    color: #c084fc;
    margin-top: 0.3rem;
    width: 100%;
    word-break: break-all;
}
.bitly-result i { font-size: 0.8rem; flex-shrink: 0; }

/* ── Links vinculados dentro do card ── */
.links-section-title {
    font-size: 0.66rem;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: rgba(0,245,255,0.25);
    margin: 0.65rem 0 0.35rem 0;
    padding-top: 0.55rem;
    border-top: 1px solid rgba(0,245,255,0.07);
    display: flex;
    align-items: center;
    gap: 0.4rem;
}

/* Link row — neon ciano constante e minimalista */
.link-inner-row {
    background: rgba(0,245,255,0.025);
    border: 1px solid rgba(0,245,255,0.18);
    border-radius: 6px;
    padding: 0.32rem 0.7rem;
    display: flex;
    align-items: center;
    gap: 0.55rem;
    min-width: 0;
    box-shadow: 0 0 8px rgba(0,245,255,0.07), 0 0 2px rgba(0,245,255,0.04) inset;
    transition: box-shadow 0.25s, border-color 0.25s;
}
.link-inner-row:hover {
    border-color: rgba(0,245,255,0.38);
    box-shadow: 0 0 16px rgba(0,245,255,0.16), 0 0 5px rgba(0,245,255,0.07) inset;
}
.link-rotulo-tag {
    font-size: 0.7rem;
    font-weight: 700;
    color: #00d4e0;
    letter-spacing: 0.5px;
    white-space: nowrap;
    flex-shrink: 0;
    text-shadow: 0 0 6px rgba(0,245,255,0.45);
}
.link-url-text {
    font-size: 0.7rem;
    color: rgba(0,245,255,0.35);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
}

/* Botão ⚡ por link — neon ciano */
.action-btn-link-shorten button {
    background: rgba(0,245,255,0.06) !important;
    border: 1px solid rgba(0,245,255,0.3) !important;
    color: #00f5ff !important;
    font-size: 0.75rem !important;
    padding: 0.2rem 0.45rem !important;
    border-radius: 5px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    line-height: 1.4 !important;
    box-shadow: 0 0 7px rgba(0,245,255,0.18) !important;
    transition: box-shadow 0.2s, background 0.2s !important;
}
.action-btn-link-shorten button:hover {
    background: rgba(0,245,255,0.13) !important;
    box-shadow: 0 0 16px rgba(0,245,255,0.38), 0 0 4px rgba(0,245,255,0.2) inset !important;
    text-shadow: 0 0 8px rgba(0,245,255,1) !important;
}

/* Botão 🗑️ por link — minimalista */
.action-btn-link-delete button {
    background: transparent !important;
    border: 1px solid rgba(248,113,113,0.15) !important;
    color: rgba(248,113,113,0.5) !important;
    font-size: 0.75rem !important;
    padding: 0.2rem 0.45rem !important;
    border-radius: 5px !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    box-shadow: none !important;
    line-height: 1.4 !important;
    transition: color 0.2s, border-color 0.2s, box-shadow 0.2s !important;
}
.action-btn-link-delete button:hover {
    color: #f87171 !important;
    border-color: rgba(248,113,113,0.4) !important;
    box-shadow: 0 0 7px rgba(248,113,113,0.18) !important;
}

/* Resultado Bitly — neon roxo */
.link-bitly-result {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.7rem;
    color: #c084fc;
    padding: 0.15rem 0.7rem 0.28rem 0.7rem;
    text-shadow: 0 0 8px rgba(168,85,247,0.55);
}

/* Formulário inline de adicionar link — borda neon ciano tracejada */
.add-link-form-wrap {
    background: rgba(0,245,255,0.02);
    border: 1px dashed rgba(0,245,255,0.2);
    border-radius: 8px;
    padding: 0.75rem 0.9rem 0.5rem 0.9rem;
    margin: 0.4rem 0 0.3rem 0;
    box-shadow: 0 0 12px rgba(0,245,255,0.04);
}
.add-link-form-wrap label {
    font-size: 0.72rem !important;
    letter-spacing: 1px !important;
}

/* ── Formulário ── */
[data-testid="stForm"] {
    background: linear-gradient(135deg, #0d1530 0%, #0a1020 100%);
    border: 1px solid #1a2545;
    border-radius: 12px;
    padding: 1.6rem !important;
}
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: #060910 !important;
    border: 1px solid #1e2d50 !important;
    border-radius: 8px !important;
    color: #e8f8ff !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 0.95rem !important;
    transition: border-color 0.2s, box-shadow 0.2s;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stNumberInput"] input:focus {
    border-color: #a855f7 !important;
    box-shadow: 0 0 0 2px rgba(168,85,247,0.2), 0 0 12px rgba(0,245,255,0.1) !important;
    color: #e8f8ff !important;
}
/* ── Botão base — padrão ciano para TODOS os botões sem classe btn-wrap ── */
[data-testid="stFormSubmitButton"] > button,
.stButton > button {
    background: transparent !important;
    border: 2px solid #00f5ff !important;
    border-radius: 6px !important;
    color: #00f5ff !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.75rem !important;
    letter-spacing: 1.4px !important;
    text-transform: uppercase !important;
    padding: 0.22rem 0.85rem !important;
    min-height: 26px !important;
    height: 26px !important;
    line-height: 1 !important;
    cursor: pointer !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.2) !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
[data-testid="stFormSubmitButton"] > button:hover,
.stButton > button:hover {
    background: rgba(0,245,255,0.09) !important;
    border-color: #00f5ff !important;
    color: #00f5ff !important;
    box-shadow: 0 0 20px rgba(0,245,255,0.5), 0 0 40px rgba(0,245,255,0.2), inset 0 0 8px rgba(0,245,255,0.08) !important;
    text-shadow: 0 0 10px #00f5ff, 0 0 22px rgba(0,245,255,0.5) !important;
    filter: brightness(1.12) !important;
}

/* ── Badges ── */
.badge {
    display: inline-block;
    padding: 0.15rem 0.6rem;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
}
.badge-cyan   { background: rgba(0,245,255,0.1);  color: #00f5ff; border: 1px solid rgba(0,245,255,0.3); }
.badge-purple { background: rgba(168,85,247,0.1); color: #a855f7; border: 1px solid rgba(168,85,247,0.3); }
.badge-pink   { background: rgba(255,45,120,0.1); color: #ff2d78; border: 1px solid rgba(255,45,120,0.3); }
.badge-green  { background: rgba(57,255,20,0.08); color: #39ff14; border: 1px solid rgba(57,255,20,0.3); }

/* ── Alerts ── */
.alert-success {
    background: rgba(52,211,153,0.08);
    border: 1px solid rgba(52,211,153,0.3);
    border-radius: 8px;
    padding: 0.8rem 1.2rem;
    color: #34d399;
    font-size: 0.9rem;
    margin: 0.8rem 0;
}
.alert-error {
    background: rgba(248,113,113,0.08);
    border: 1px solid rgba(248,113,113,0.3);
    border-radius: 8px;
    padding: 0.8rem 1.2rem;
    color: #f87171;
    font-size: 0.9rem;
    margin: 0.8rem 0;
}

/* ── Produto row (dashboard/links) ── */
.produto-row {
    display: grid;
    grid-template-columns: 2fr 3fr 1fr auto;
    align-items: center;
    gap: 0.8rem;
    padding: 0.75rem 1rem;
    border-radius: 8px;
    border: 1px solid #141d35;
    margin-bottom: 0.5rem;
    background: #080c18;
    transition: border-color 0.2s;
}
.produto-row:hover { border-color: rgba(0,245,255,0.2); }
.produto-nome  { font-weight: 600; color: #d0dcff; }
.produto-desc  { font-size: 0.85rem; color: #4a5a80; }
.produto-preco { font-family: 'Orbitron', sans-serif; font-size: 0.95rem; color: #34d399; text-align: right; }

/* ══════════════════════════════════════════════════════
   UPGRADE UI — Gestão de Produtos
   ══════════════════════════════════════════════════════ */

/* ── Expander como card de produto — neon aparente ── */
[data-testid="stExpander"] {
    background: linear-gradient(135deg, #0b1128 0%, #080c1c 100%) !important;
    border: 1px solid rgba(0,245,255,0.22) !important;
    border-radius: 10px !important;
    margin-bottom: 0.5rem !important;
    box-shadow: 0 0 18px rgba(0,245,255,0.09), inset 0 0 8px rgba(0,245,255,0.02) !important;
    transition: border-color 0.22s, box-shadow 0.22s !important;
    overflow: hidden !important;
}
[data-testid="stExpander"]:hover {
    border-color: rgba(0,245,255,0.42) !important;
    box-shadow: 0 0 28px rgba(0,245,255,0.15), inset 0 0 12px rgba(0,245,255,0.03) !important;
}
/* Linha neon no topo do expander */
[data-testid="stExpander"]::before {
    content: '' !important;
    display: block !important;
    height: 1px !important;
    background: linear-gradient(90deg, rgba(0,245,255,0.5) 0%, rgba(168,85,247,0.3) 60%, transparent 100%) !important;
}
/* Header do expander */
[data-testid="stExpander"] summary,
[data-testid="stExpander"] [role="button"] {
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 0.88rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.5px !important;
    color: #8aa0c0 !important;
    padding: 0.62rem 1rem !important;
    cursor: pointer !important;
    transition: color 0.18s, text-shadow 0.18s, background 0.18s !important;
    list-style: none !important;
    background: transparent !important;
}
/* Remove o efeito branco padrão do Streamlit no hover do título */
[data-testid="stExpander"] summary:hover,
[data-testid="stExpander"] [role="button"]:hover {
    background: transparent !important;
    background-color: transparent !important;
    color: rgba(0,245,255,0.9) !important;
    text-shadow: 0 0 8px rgba(0,245,255,0.25) !important;
}
[data-testid="stExpander"] details[open] > summary {
    color: #00f5ff !important;
    text-shadow: 0 0 10px rgba(0,245,255,0.35) !important;
    border-bottom: 1px solid rgba(0,245,255,0.08) !important;
}
[data-testid="stExpanderToggleIcon"] svg { fill: rgba(0,245,255,0.35) !important; }
[data-testid="stExpander"] details[open] [data-testid="stExpanderToggleIcon"] svg {
    fill: #00f5ff !important;
    filter: drop-shadow(0 0 3px rgba(0,245,255,0.5)) !important;
}
/* Conteúdo interno */
[data-testid="stExpander"] details > div:not(summary) {
    padding: 0.3rem 0.9rem 0.7rem 0.9rem !important;
}

/* ── Animações tecnológicas ── */
@keyframes lg-scan {
    0%   { top: -100%; opacity: 0.7; }
    100% { top: 150%;  opacity: 0;   }
}
@keyframes lg-poweron {
    0%   { box-shadow: 0 0 4px rgba(244,114,182,0.2); }
    40%  { box-shadow: 0 0 24px rgba(244,114,182,0.7), inset 0 0 14px rgba(244,114,182,0.15); }
    100% { box-shadow: 0 0 14px rgba(244,114,182,0.4), inset 0 0 6px rgba(244,114,182,0.08); }
}
@keyframes lgToastIn {
    from { opacity: 0; transform: translateY(1.2rem) scale(0.95); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* ── Botões de ação — base (CSS fallback; JS aplica inline para garantir) ── */
.btn-wrap-cyan button, .btn-wrap-cyan [data-baseweb="button"],
.btn-wrap-pink button, .btn-wrap-pink [data-baseweb="button"],
.btn-wrap-red  button, .btn-wrap-red  [data-baseweb="button"] {
    background:       transparent !important;
    background-color: transparent !important;
    padding:          0 0.6rem !important;
    font-size:        0.64rem !important;
    font-family:      'Rajdhani', sans-serif !important;
    font-weight:      700 !important;
    letter-spacing:   0.9px !important;
    border-radius:    4px !important;
    min-height:       26px !important;
    height:           26px !important;
    line-height:      1 !important;
    text-transform:   uppercase !important;
    white-space:      nowrap !important;
    display:          inline-flex !important;
    align-items:      center !important;
    justify-content:  center !important;
    gap:              0.25rem !important;
    position:         relative !important;
    overflow:         hidden !important;
    cursor:           pointer !important;
    transition:       background-color 0.16s, border-color 0.16s, box-shadow 0.16s, color 0.16s, text-shadow 0.16s !important;
}
/* ── Cor de borda: TODOS ciano neon ── */
.btn-wrap-cyan button, .btn-wrap-cyan [data-baseweb="button"] {
    border: 2px solid #00f5ff !important;
    color:  #00f5ff !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.3) !important;
}
.btn-wrap-pink button, .btn-wrap-pink [data-baseweb="button"] {
    border: 2px solid #00f5ff !important;
    color:  #f472b6 !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.25) !important;
}
.btn-wrap-red button, .btn-wrap-red [data-baseweb="button"] {
    border: 2px solid #00f5ff !important;
    color:  #ff6b9d !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.25) !important;
}
/* Scan line no hover */
.btn-wrap-cyan button::after,
.btn-wrap-pink button::after,
.btn-wrap-red  button::after {
    content: '' !important;
    position: absolute !important;
    top: -100% !important; left: 0 !important; right: 0 !important;
    height: 55% !important;
    background: linear-gradient(180deg, transparent 0%, rgba(0,245,255,0.18) 50%, transparent 100%) !important;
    pointer-events: none !important;
}
.btn-wrap-cyan button:hover::after,
.btn-wrap-pink button:hover::after,
.btn-wrap-red  button:hover::after {
    animation: lg-scan 0.28s ease-out forwards !important;
}
/* ── Hover UNIFICADO ciano neon para todos os btn-wrap ── */
.btn-wrap-cyan button:hover, .btn-wrap-cyan [data-baseweb="button"]:hover,
.btn-wrap-pink button:hover, .btn-wrap-pink [data-baseweb="button"]:hover,
.btn-wrap-red  button:hover, .btn-wrap-red  [data-baseweb="button"]:hover {
    background-color: rgba(0,245,255,0.09) !important;
    border-color: #00f5ff !important;
    color: #00f5ff !important;
    box-shadow: 0 0 20px rgba(0,245,255,0.5), 0 0 40px rgba(0,245,255,0.2), inset 0 0 10px rgba(0,245,255,0.08) !important;
    text-shadow: 0 0 10px #00f5ff, 0 0 22px rgba(0,245,255,0.5) !important;
    filter: brightness(1.12) !important;
    transition: all 0.2s ease !important;
}

/* ── Botões dentro de expanders: altura uniforme 32px, padding ajustado ── */
[data-testid="stExpander"] .btn-wrap-cyan button,
[data-testid="stExpander"] .btn-wrap-cyan [data-baseweb="button"],
[data-testid="stExpander"] .btn-wrap-pink button,
[data-testid="stExpander"] .btn-wrap-pink [data-baseweb="button"],
[data-testid="stExpander"] .btn-wrap-red  button,
[data-testid="stExpander"] .btn-wrap-red  [data-baseweb="button"] {
    height:         32px !important;
    min-height:     32px !important;
    max-height:     32px !important;
    padding:        0 0.5rem !important;
    font-size:      0.68rem !important;
    letter-spacing: 0.5px !important;
    min-width:      0 !important;
    width:          100% !important;
}
/* Iframe do botão Copiar: mesma altura dos botões no expander */
[data-testid="stExpander"] iframe[id^="cp-iframe"] {
    height: 32px !important;
}
/* Separador visual entre links do expander */
.lk-row-sep {
    height:     1px;
    background: linear-gradient(90deg, transparent, rgba(0,245,255,0.12), transparent);
    margin:     0.3rem 0;
    border:     none;
}

/* ── Atalhos VIBEL AI ── */
.btn-wrap-atalho button,
.btn-wrap-atalho [data-baseweb="button"] {
    background:       transparent !important;
    background-color: transparent !important;
    border:           2px solid #39ff14 !important;
    border-radius:    8px !important;
    color:            #39ff14 !important;
    font-family:      'Rajdhani', sans-serif !important;
    font-weight:      700 !important;
    font-size:        11px !important;
    letter-spacing:   0.5px !important;
    text-transform:   uppercase !important;
    white-space:      nowrap !important;
    word-break:       normal !important;
    height:           50px !important;
    min-height:       50px !important;
    max-height:       50px !important;
    flex:             1 !important;
    padding:          0 8px !important;
    line-height:      1 !important;
    display:          flex !important;
    align-items:      center !important;
    justify-content:  center !important;
    text-align:       center !important;
    width:            100% !important;
    box-shadow:       0 0 8px rgba(57,255,20,0.2), inset 0 0 6px rgba(57,255,20,0.02) !important;
    cursor:           pointer !important;
    transition:       all 0.2s ease !important;
    overflow:         hidden !important;
}
.btn-wrap-atalho button:hover,
.btn-wrap-atalho [data-baseweb="button"]:hover {
    background-color: rgba(57,255,20,0.06) !important;
    border-color:     #a855f7 !important;
    color:            #a855f7 !important;
    box-shadow:       0 0 20px rgba(57,255,20,0.4), 0 0 40px rgba(168,85,247,0.3), inset 0 0 10px rgba(57,255,20,0.04) !important;
    text-shadow:      0 0 10px rgba(57,255,20,0.7), 0 0 24px rgba(168,85,247,0.5) !important;
    filter:           brightness(1.15) !important;
}
/* ── Botão Limpar Chat — separado abaixo, esquerda ── */
.btn-wrap-vivi-clear button,
.btn-wrap-vivi-clear [data-baseweb="button"] {
    background:       transparent !important;
    background-color: transparent !important;
    border:           2px solid #00f5ff !important;
    border-radius:    6px !important;
    color:            #ff6b9d !important;
    font-family:      'Rajdhani', sans-serif !important;
    font-weight:      700 !important;
    font-size:        0.72rem !important;
    letter-spacing:   0.7px !important;
    text-transform:   uppercase !important;
    white-space:      nowrap !important;
    height:           40px !important;
    min-height:       40px !important;
    max-height:       40px !important;
    width:            auto !important;
    padding:          0 1rem !important;
    display:          inline-flex !important;
    align-items:      center !important;
    justify-content:  center !important;
    box-shadow:       0 0 8px rgba(0,245,255,0.2) !important;
    cursor:           pointer !important;
    transition:       all 0.2s ease !important;
}
.btn-wrap-vivi-clear button:hover,
.btn-wrap-vivi-clear [data-baseweb="button"]:hover {
    background-color: rgba(0,245,255,0.08) !important;
    border-color:     #00f5ff !important;
    color:            #00f5ff !important;
    box-shadow:       0 0 20px rgba(0,245,255,0.5), 0 0 40px rgba(0,245,255,0.2) !important;
    text-shadow:      0 0 10px #00f5ff !important;
}

/* ── Link row compacta dentro do expander ── */
.link-row-compact {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.28rem 0;
    border-bottom: 1px solid rgba(244,114,182,0.06);
    min-width: 0;
}
/* Rótulo do link — Rosa neon aceso */
.link-name-neon {
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.76rem;
    font-weight: 700;
    color: #f472b6;
    white-space: nowrap;
    letter-spacing: 0.5px;
    text-shadow: 0 0 10px rgba(244,114,182,0.75), 0 0 20px rgba(244,114,182,0.3);
    flex-shrink: 0;
}
/* URL original — ciano neon */
.link-url-dim {
    font-size:   0.78rem;
    font-weight: 600;
    color:       #00ffff;
    text-shadow: 0 0 8px #00ffff, 0 0 16px rgba(0,255,255,0.5);
    overflow:    hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex:        1;
    min-width:   0;
}
/* Link afiliado do produto — exibido no card */
.prod-link-afiliado {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    background: rgba(244,114,182,0.04);
    border: 1px solid rgba(244,114,182,0.14);
    border-radius: 5px;
    padding: 0.3rem 0.6rem;
    margin-bottom: 0.45rem;
    min-width: 0;
}
.prod-link-afiliado-label {
    font-size: 0.6rem;
    color: rgba(244,114,182,0.45);
    letter-spacing: 1.2px;
    text-transform: uppercase;
    white-space: nowrap;
    flex-shrink: 0;
}
.prod-link-afiliado-url {
    font-size: 0.65rem;
    color: rgba(244,114,182,0.6);
    text-shadow: 0 0 8px rgba(244,114,182,0.3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
}
/* Botão COPIAR — ícone clipboard minimalista */
.btn-copy-html {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: 1px solid rgba(0,245,255,0.2);
    border-radius: 3px;
    color: rgba(0,245,255,0.4);
    font-size: 0.6rem;
    padding: 0;
    cursor: pointer;
    transition: all 0.14s;
    flex-shrink: 0;
    width: 20px;
    height: 20px;
    line-height: 1;
    margin-left: 3px;
}
.btn-copy-html:hover {
    background: rgba(0,245,255,0.1);
    border-color: #00f5ff;
    color: #00f5ff;
    box-shadow: 0 0 8px rgba(0,245,255,0.35);
}
/* Bitly / link encurtado inline — ciano neon */
.bitly-inline {
    display:     flex;
    align-items: center;
    gap:         0.35rem;
    font-size:   0.78rem;
    font-family: 'Rajdhani', sans-serif;
    font-weight: 600;
    color:       #00ffff;
    text-shadow: 0 0 8px #00ffff, 0 0 16px rgba(0,255,255,0.5);
    padding:     0 0;
    margin:      0;
    height:      26px;
    line-height: 26px;
    letter-spacing: 0.3px;
    overflow:    hidden;
    white-space: nowrap;
}
/* Separador */
.expander-sep {
    border: none;
    border-top: 1px solid rgba(0,245,255,0.06);
    margin: 0.55rem 0 0.45rem 0;
}
/* Confirmação de exclusão */
.confirm-strip {
    background: rgba(248,113,113,0.05);
    border: 1px solid rgba(248,113,113,0.2);
    border-radius: 6px;
    padding: 0.4rem 0.75rem;
    font-size: 0.74rem;
    color: #f87171;
    margin: 0.3rem 0;
    display: flex;
    align-items: center;
    gap: 0.4rem;
}
/* Descrição do produto no expander */
.prod-exp-desc {
    font-size: 0.76rem;
    color: #3a4a6a;
    margin-bottom: 0.45rem;
    font-style: italic;
    line-height: 1.4;
}

/* ── Chat da VIBEL AI ── */
/* Container principal do chat — fundo escuro */
[data-testid="stChatMessage"] {
    background: #0a1120 !important;
    border: 1px solid rgba(0,245,255,0.18) !important;
    border-radius: 10px !important;
    padding: 0.65rem 0.9rem !important;
    margin-bottom: 0.5rem !important;
    color: #e0e6f0 !important;
}
/* Msg do assistente — borda roxa */
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
    border-color: rgba(168,85,247,0.3) !important;
    background: #0b0e1e !important;
}
/* Força fundo escuro em qualquer div interna do chat */
[data-testid="stChatMessage"] > div,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
    background: transparent !important;
    color: #e0e6f0 !important;
}
/* ── Chat Input — fundo escuro total, borda ciano neon ── */

/* Wrappers PAI do Streamlit (stBottom é o container fixo da base) */
[data-testid="stBottom"],
[data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"],
[data-testid="stBottomBlockContainer"] > div,
[data-testid="stChatInputContainer"],
[data-testid="stChatInputContainer"] > div,
section[data-testid="stBottom"],
.stBottom,
.stChatInputContainer {
    background:       #0a0a1a !important;
    background-color: #0a0a1a !important;
    border:           none !important;
    box-shadow:       none !important;
}

/* Container direto + filhos imediatos */
[data-testid="stChatInput"],
.stChatInput,
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] > div > div {
    background:       #0a0a1a !important;
    background-color: #0a0a1a !important;
    border-radius:    12px !important;
}

/* Borda neon no container principal */
[data-testid="stChatInput"] {
    border:     2px solid #00ffff !important;
    box-shadow: 0 0 10px rgba(0,255,255,0.15), inset 0 0 6px rgba(0,255,255,0.04) !important;
    padding:    0.15rem 0.5rem !important;
}

/* Todos os wrappers internos: BaseWeb, React divs */
[data-testid="stChatInput"] div,
[data-testid="stChatInput"] [data-baseweb="textarea"],
[data-testid="stChatInput"] [data-baseweb="base-input"],
[data-testid="stChatInput"] [class*="InputContainer"],
[data-testid="stChatInput"] [class*="Textarea"] {
    background:       #0a0a1a !important;
    background-color: #0a0a1a !important;
    border:           none !important;
    box-shadow:       none !important;
    outline:          none !important;
}

/* Textarea em si */
[data-testid="stChatInput"] textarea {
    background:       #0a0a1a !important;
    background-color: #0a0a1a !important;
    border:           none !important;
    border-radius:    10px !important;
    color:            #ffffff !important;
    font-family:      'Rajdhani', sans-serif !important;
    font-size:        0.95rem !important;
    caret-color:      #00ffff !important;
    outline:          none !important;
    box-shadow:       none !important;
    resize:           none !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color:   rgba(255,255,255,0.4) !important;
    opacity: 1 !important;
}
[data-testid="stChatInput"] textarea:focus {
    outline:    none !important;
    box-shadow: none !important;
}

/* Foco: borda e glow mais intensos */
[data-testid="stChatInput"]:focus-within {
    border-color: #00ffff !important;
    box-shadow:
        0 0 20px rgba(0,255,255,0.45),
        0 0 45px rgba(0,255,255,0.18),
        inset 0 0 10px rgba(0,255,255,0.06) !important;
}

/* Botão de envio */
[data-testid="stChatInput"] button {
    background:       #00ffff !important;
    background-color: #00ffff !important;
    border:           2px solid #00ffff !important;
    border-radius:    8px !important;
    box-shadow:       0 0 12px rgba(0,255,255,0.5) !important;
    transition:       all 0.2s ease !important;
}
[data-testid="stChatInput"] button:hover {
    background:       rgba(0,255,255,0.18) !important;
    background-color: rgba(0,255,255,0.18) !important;
    border-color:     #00ffff !important;
    box-shadow:
        0 0 24px rgba(0,255,255,0.7),
        0 0 50px rgba(0,255,255,0.3) !important;
}
[data-testid="stChatInput"] button svg,
[data-testid="stChatInput"] button svg * {
    fill:   #0a0a1a !important;
    stroke: #0a0a1a !important;
    color:  #0a0a1a !important;
}
[data-testid="stChatInput"] button:hover svg,
[data-testid="stChatInput"] button:hover svg * {
    fill:   #00ffff !important;
    stroke: #00ffff !important;
    color:  #00ffff !important;
}
/* ── Mata fundo branco dos botões kind=secondary (Streamlit padrão) ── */
button[kind="secondary"],
button[kind="secondary"]:hover,
button[kind="secondary"]:focus,
button[kind="secondary"]:active {
    background: transparent !important;
    background-color: transparent !important;
}
.btn-wrap-cyan button[kind="secondary"] {
    border: 2px solid #00f5ff !important; color: #00f5ff !important;
}
.btn-wrap-red button[kind="secondary"] {
    border: 2px solid #ff007f !important; color: #ff007f !important;
}
.btn-wrap-pink button[kind="secondary"] {
    border: 2px solid #f472b6 !important; color: #f472b6 !important;
}
.vivi-header {
    display: flex; align-items: center; gap: 0.8rem;
    background: linear-gradient(135deg, rgba(168,85,247,0.1) 0%, rgba(255,45,120,0.06) 100%);
    border: 1px solid rgba(168,85,247,0.3); border-radius: 10px;
    padding: 0.8rem 1.2rem; margin-bottom: 1.2rem;
    box-shadow: 0 0 20px rgba(168,85,247,0.08), 0 0 40px rgba(255,45,120,0.04);
}
.vivi-header-name {
    font-family: 'Orbitron', sans-serif; font-size: 1rem;
    background: linear-gradient(90deg, #a855f7, #ff2d78);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: 2px;
    filter: drop-shadow(0 0 8px rgba(168,85,247,0.5));
}
.vivi-header-sub { font-size: 0.75rem; color: #39ff14; letter-spacing: 0.8px; margin-top: 0.1rem; text-shadow: 0 0 8px rgba(57,255,20,0.5); }
.vivi-status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #39ff14; box-shadow: 0 0 8px #39ff14;
    animation: vivi-pulse 2s ease-in-out infinite;
}
@keyframes vivi-pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 8px #39ff14; }
    50%       { opacity: 0.6; box-shadow: 0 0 3px #39ff14; }
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #080b14; }
::-webkit-scrollbar-thumb { background: #1e2d50; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #00c8d4; }

/* ── Oculta elementos padrão Streamlit ── */
#MainMenu  { visibility: hidden; }
footer     { visibility: hidden; }
[data-testid="stToolbar"]    { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

/* ── Sidebar: força visibilidade em Streamlit 1.55 ── */
/* Sidebar sempre visível e expandida */
[data-testid="stSidebar"] {
    display: block !important;
    visibility: visible !important;
    transform: none !important;
    min-width: 220px !important;
}
/* Streamlit 1.55 usa aria-expanded para controlar colapso */
[data-testid="stSidebar"][aria-expanded="false"] {
    width: auto !important;
    min-width: 220px !important;
    transform: translateX(0) !important;
}
/* Botão de colapso sempre visível */
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stSidebarNavCollapsedControl"] {
    visibility: visible !important;
    display: flex !important;
    opacity: 1 !important;
    pointer-events: auto !important;
    z-index: 999 !important;
}
/* Ícone da seta */
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="collapsedControl"] svg {
    fill: rgba(0,245,255,0.5) !important;
    transition: fill 0.2s !important;
}
[data-testid="stSidebarCollapseButton"]:hover svg,
[data-testid="collapsedControl"]:hover svg {
    fill: #00f5ff !important;
    filter: drop-shadow(0 0 4px rgba(0,245,255,0.6)) !important;
}

/* ══════════════════════════════════════════════════════
   LABELS — bloco final, vence qualquer regra anterior
   ══════════════════════════════════════════════════════ */
label,
p[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] span,
.stTextInput  > label,
.stTextArea   > label,
.stSelectbox  > label,
.stNumberInput > label,
div[data-baseweb="form-control"] > label,
div[data-baseweb="form-control"] label {
    color: rgba(0,245,255,0.85) !important;
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    letter-spacing: 1.8px !important;
    text-transform: uppercase !important;
}

/* ── Botões btn-wrap: garante que a regra base (.stButton) não vença ── */
.btn-wrap-cyan button,
.btn-wrap-cyan [data-baseweb="button"] {
    border: 2px solid #00f5ff !important;
    color:  #00f5ff !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.4) !important;
    padding: 0 0.35rem !important;
    font-size: 0.7rem !important;
    height: 26px !important; min-height: 26px !important;
}
.btn-wrap-pink button,
.btn-wrap-pink [data-baseweb="button"] {
    border: 2px solid #f472b6 !important;
    color:  #f472b6 !important;
    box-shadow: 0 0 7px rgba(244,114,182,0.35) !important;
    padding: 0 0.35rem !important;
    font-size: 0.7rem !important;
    height: 26px !important; min-height: 26px !important;
}
.btn-wrap-red button,
.btn-wrap-red [data-baseweb="button"] {
    border: 2px solid #ff007f !important;
    color:  #ff007f !important;
    box-shadow: 0 0 7px rgba(255,0,127,0.35) !important;
    padding: 0 0.35rem !important;
    font-size: 0.7rem !important;
    height: 26px !important; min-height: 26px !important;
}
.btn-wrap-cyan button:hover, .btn-wrap-cyan [data-baseweb="button"]:hover {
    background: rgba(0,245,255,0.12) !important;
    box-shadow: 0 0 22px rgba(0,245,255,0.55), inset 0 0 10px rgba(0,245,255,0.1) !important;
    text-shadow: 0 0 10px #00f5ff, 0 0 24px rgba(0,245,255,0.5) !important;
    filter: brightness(1.12) !important;
}
.btn-wrap-pink button:hover, .btn-wrap-pink [data-baseweb="button"]:hover {
    background: rgba(244,114,182,0.12) !important;
    box-shadow: 0 0 20px rgba(244,114,182,0.5), inset 0 0 10px rgba(244,114,182,0.1) !important;
    text-shadow: 0 0 10px #f472b6, 0 0 24px rgba(244,114,182,0.5) !important;
    filter: brightness(1.12) !important;
}
.btn-wrap-red button:hover, .btn-wrap-red [data-baseweb="button"]:hover {
    background: rgba(255,0,127,0.12) !important;
    box-shadow: 0 0 20px rgba(255,0,127,0.5), inset 0 0 10px rgba(255,0,127,0.1) !important;
    text-shadow: 0 0 10px #ff007f, 0 0 24px rgba(255,0,127,0.5) !important;
    filter: brightness(1.12) !important;
}

/* ══════════════════════════════════════════════════════
   MOBILE RESPONSIVO — max-width: 768px
   ══════════════════════════════════════════════════════ */
@media (max-width: 768px) {

    /* ── Container principal ── */
    .block-container,
    [data-testid="stMain"] .block-container {
        padding: 0.75rem 0.6rem 2rem 0.6rem !important;
        max-width: 100% !important;
    }

    /* ── Metric grid: 2x2 em vez de 4x1 ── */
    .metric-grid {
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 0.65rem !important;
    }
    .metric-card {
        padding: 0.85rem 0.9rem !important;
    }
    .metric-value {
        font-size: 1.35rem !important;
    }
    .metric-label {
        font-size: 0.62rem !important;
    }

    /* ── Título de página ── */
    .page-title {
        font-size: 1.25rem !important;
    }
    .page-subtitle {
        font-size: 0.72rem !important;
        margin-bottom: 1rem !important;
    }

    /* ── Colunas Streamlit: empilhar verticalmente ── */
    [data-testid="stHorizontalBlock"],
    [data-testid="stColumns"] {
        flex-direction: column !important;
        gap: 0.5rem !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"] > [data-testid="column"],
    [data-testid="stColumns"] > [data-testid="column"] {
        width: 100% !important;
        min-width: 100% !important;
        flex: 1 1 calc(100% - 1rem) !important;
    }

    /* ── Panel card ── */
    .panel-card {
        padding: 0.85rem 0.9rem !important;
        margin-bottom: 0.75rem !important;
    }
    .panel-card h3 {
        font-size: 0.75rem !important;
        letter-spacing: 1.5px !important;
    }

    /* ── Produto row: layout simplificado ── */
    .produto-row {
        grid-template-columns: 1fr auto !important;
        gap: 0.4rem !important;
        padding: 0.6rem 0.75rem !important;
    }
    .produto-desc { display: none !important; }

    /* ── Produto card ── */
    .produto-card {
        padding: 0.8rem 0.9rem 0.65rem 0.9rem !important;
    }

    /* ── Expander ── */
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] [role="button"] {
        font-size: 0.82rem !important;
        padding: 0.5rem 0.75rem !important;
    }

    /* ── Formulários ── */
    [data-testid="stForm"] {
        padding: 1rem !important;
    }

    /* ── Sidebar: tela cheia em mobile ── */
    [data-testid="stSidebar"] {
        min-width: 85vw !important;
        max-width: 85vw !important;
    }

    /* ── Auth container: sem bordas arredondadas no mobile ── */
    .main .block-container {
        max-width: 100% !important;
        width: 100% !important;
        border-radius: 12px !important;
        padding: 1.6rem 1.2rem !important;
    }

    /* ── Chat input ── */
    [data-testid="stChatInput"] {
        font-size: 0.88rem !important;
    }

    /* ── Atalhos VIBEL ── */
    .btn-wrap-atalho button,
    .btn-wrap-atalho [data-baseweb="button"] {
        font-size: 9px !important;
        padding: 0 4px !important;
        letter-spacing: 0.2px !important;
        height: 44px !important;
        min-height: 44px !important;
    }

    /* ── Botões de ação compactos ── */
    .btn-wrap-cyan button, .btn-wrap-pink button, .btn-wrap-red button {
        font-size: 0.6rem !important;
        padding: 0 0.4rem !important;
        min-height: 30px !important;
        height: 30px !important;
    }

    /* ── Texto truncado nos links ── */
    .link-url-dim, .link-url-text, .pc-link-chip {
        font-size: 0.65rem !important;
    }

    /* ── Badges ── */
    .badge {
        font-size: 0.62rem !important;
        padding: 0.1rem 0.45rem !important;
    }

    /* ── Botão menu fixo: mais visível ── */
    #lg-menu-btn {
        font-size: 0.72rem !important;
        padding: 0.3rem 0.65rem !important;
        top: 0.5rem !important;
        left: 0.5rem !important;
    }
}
</style>
"""

st.markdown(CYBER_CSS, unsafe_allow_html=True)

# ── CSS Mobile Premium — complementa CYBER_CSS com correções para Android/iOS ──
st.markdown("""
<style>

/* ══════════════════════════════════════════════════════
   LOGO — encolhe proporcionalmente em qualquer tela
   ══════════════════════════════════════════════════════ */
[data-testid="stSidebar"] svg,
[data-testid="stSidebar"] img {
    max-width: 100% !important;
    height: auto !important;
}
/* Wrapper centralizado do logo no sidebar */
[data-testid="stSidebar"] div[style*="display:flex"][style*="justify-content:center"] {
    padding: 0.4rem 0.5rem 0.1rem 0.5rem !important;
}

/* ══════════════════════════════════════════════════════
   TABELAS — scroll horizontal sem quebrar layout
   ══════════════════════════════════════════════════════ */
[data-testid="stDataFrame"],
[data-testid="stTable"],
.stDataFrame,
.stTable {
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch !important;
    display: block !important;
}

/* ══════════════════════════════════════════════════════
   iOS — impede zoom automático ao focar inputs
   ══════════════════════════════════════════════════════ */
input, textarea, select,
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input,
[data-testid="stChatInput"] textarea {
    font-size: 16px !important;
}

/* ══════════════════════════════════════════════════════
   ATALHOS VIBEL — flex-wrap para empilhar no mobile
   ══════════════════════════════════════════════════════ */
[data-testid="stSidebar"] ~ * .btn-wrap-atalho,
.btn-wrap-atalho {
    display: contents !important;
}

/* ══════════════════════════════════════════════════════
   MOBILE 768px — melhorias complementares
   ══════════════════════════════════════════════════════ */
@media (max-width: 768px) {

    /* Auth container responsivo */
    .main .block-container {
        max-width: 95vw !important;
        width: 95vw !important;
        padding: 1.4rem 1rem 1.6rem !important;
    }

    /* Tabelas com scroll lateral */
    [data-testid="stDataFrame"],
    [data-testid="stTable"] {
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch !important;
    }

    /* Inputs com 16px para iOS não dar zoom */
    input, textarea,
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stNumberInput"] input {
        font-size: 16px !important;
    }

    /* Chat input no mobile */
    [data-testid="stChatInput"] textarea {
        font-size: 16px !important;
    }

    /* vivi-header menor no mobile */
    .vivi-header {
        padding: 0.6rem 0.85rem !important;
        gap: 0.5rem !important;
    }
    .vivi-header-name {
        font-size: 0.82rem !important;
    }
    .vivi-header-sub {
        font-size: 0.65rem !important;
    }

    /* Sidebar logo max-width */
    [data-testid="stSidebar"] svg {
        max-width: 130px !important;
        height: auto !important;
    }

    /* Logo na tela de auth */
    .main .block-container svg {
        max-width: 130px !important;
        height: auto !important;
    }

    /* Atalhos VIBEL: 1 coluna + wrap */
    [data-testid="stHorizontalBlock"]:has(.btn-wrap-atalho),
    [data-testid="stColumns"]:has(.btn-wrap-atalho) {
        flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"]:has(.btn-wrap-atalho) > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"]:has(.btn-wrap-atalho) > [data-testid="column"],
    [data-testid="stColumns"]:has(.btn-wrap-atalho) > [data-testid="column"] {
        min-width: 45% !important;
        flex: 1 1 45% !important;
    }

    /* Textos de label menores */
    label,
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p {
        font-size: 0.62rem !important;
        letter-spacing: 1.2px !important;
    }

    /* Formulários sem padding excessivo */
    [data-testid="stForm"] {
        padding: 0.9rem !important;
    }

    /* Botão menu fixo mais acessível no polegar */
    #lg-menu-btn {
        top: 0.6rem !important;
        left: 0.6rem !important;
        font-size: 0.8rem !important;
        padding: 0.4rem 0.8rem !important;
        border-radius: 6px !important;
    }

    /* Titles compactos */
    .lg-auth-title,
    .page-title {
        font-size: 1.1rem !important;
        letter-spacing: 1px !important;
    }
}

/* ══════════════════════════════════════════════════════
   MOBILE 480px — telefones pequenos
   ══════════════════════════════════════════════════════ */
@media (max-width: 480px) {

    .metric-grid {
        grid-template-columns: 1fr !important;
    }
    .metric-card {
        padding: 0.7rem 0.8rem !important;
    }

    .page-title {
        font-size: 1rem !important;
    }

    /* Sidebar ocupa tela toda em modo drawer */
    [data-testid="stSidebar"] {
        min-width: 90vw !important;
        max-width: 90vw !important;
    }

    /* Atalhos VIBEL: 1 por linha */
    [data-testid="stHorizontalBlock"]:has(.btn-wrap-atalho) > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"]:has(.btn-wrap-atalho) > [data-testid="column"],
    [data-testid="stColumns"]:has(.btn-wrap-atalho) > [data-testid="column"] {
        min-width: 100% !important;
        flex: 1 1 100% !important;
    }
    .btn-wrap-atalho button,
    .btn-wrap-atalho [data-baseweb="button"] {
        height: 40px !important;
        min-height: 40px !important;
        font-size: 10px !important;
    }

    /* Auth card sem borda arredondada extrema */
    .main .block-container {
        border-radius: 10px !important;
        padding: 1.2rem 0.85rem 1.4rem !important;
    }

    /* Chat input placeholder menor */
    [data-testid="stChatInput"] textarea::placeholder {
        font-size: 14px !important;
    }

    /* Panels sem padding excessivo */
    .panel-card {
        padding: 0.75rem 0.8rem !important;
    }
}

</style>
""", unsafe_allow_html=True)

# ── CSS nuclear — sobrescreve BaseWeb do Streamlit ────────────────────────────
st.markdown("""
<style>
/* ── NUCLEAR: mata fundo branco em QUALQUER botão Streamlit ── */
div.stButton > button,
div.stButton > button:focus,
div.stButton > button:active,
div.stButton > button:focus:not(:active),
button[kind="secondary"],
button[kind="secondary"]:focus,
button[kind="secondary"]:active {
    background-color: transparent !important;
    background:       transparent !important;
    color: inherit !important;
}
/* Botão ENCURTAR — ciano outline, sem fundo */
.btn-wrap-cyan button,
.btn-wrap-cyan [data-baseweb="button"],
.btn-wrap-cyan > div > button {
    background-color: transparent !important;
    background:       transparent !important;
    border: 2px solid #00f5ff !important;
    color:  #00f5ff !important;
    box-shadow: 0 0 8px rgba(0,245,255,0.25) !important;
}
.btn-wrap-cyan button:hover,
.btn-wrap-cyan [data-baseweb="button"]:hover,
.btn-wrap-cyan > div > button:hover {
    background-color: rgba(0,245,255,0.15) !important;
    box-shadow: 0 0 18px rgba(0,245,255,0.55), inset 0 0 10px rgba(0,245,255,0.1) !important;
    text-shadow: 0 0 8px rgba(0,245,255,1) !important;
}
/* Botão EDITAR — rosa outline */
.btn-wrap-pink button,
.btn-wrap-pink [data-baseweb="button"],
.btn-wrap-pink > div > button {
    background-color: transparent !important;
    background:       transparent !important;
    border: 2px solid #f472b6 !important;
    color:  #f472b6 !important;
    box-shadow: 0 0 6px rgba(244,114,182,0.2) !important;
}
.btn-wrap-pink button:hover,
.btn-wrap-pink [data-baseweb="button"]:hover,
.btn-wrap-pink > div > button:hover {
    background-color: rgba(244,114,182,0.14) !important;
    box-shadow: 0 0 18px rgba(244,114,182,0.55), inset 0 0 10px rgba(244,114,182,0.1) !important;
    text-shadow: 0 0 8px rgba(244,114,182,1) !important;
}
/* Botão DELETAR — rosa/vermelho outline */
.btn-wrap-red button,
.btn-wrap-red [data-baseweb="button"],
.btn-wrap-red > div > button {
    background-color: transparent !important;
    background:       transparent !important;
    border: 2px solid #ff007f !important;
    color:  #ff007f !important;
    box-shadow: 0 0 6px rgba(255,0,127,0.18) !important;
}
.btn-wrap-red button:hover,
.btn-wrap-red [data-baseweb="button"]:hover,
.btn-wrap-red > div > button:hover {
    background-color: rgba(255,0,127,0.12) !important;
    box-shadow: 0 0 18px rgba(255,0,127,0.5), inset 0 0 10px rgba(255,0,127,0.08) !important;
    text-shadow: 0 0 8px rgba(255,0,127,1) !important;
}
/* Cards de produto — borda neon forte */
[data-testid="stExpander"] {
    border: 2px solid rgba(0,245,255,0.55) !important;
    box-shadow: 0 0 28px rgba(0,245,255,0.18), 0 0 6px rgba(0,245,255,0.08), inset 0 0 14px rgba(0,245,255,0.03) !important;
}
[data-testid="stExpander"]:hover {
    border-color: rgba(0,245,255,0.85) !important;
    box-shadow: 0 0 42px rgba(0,245,255,0.28), 0 0 10px rgba(0,245,255,0.12), inset 0 0 18px rgba(0,245,255,0.05) !important;
}
</style>
""", unsafe_allow_html=True)

# ── JS + CSS definitivos: clipboard, toast, botões outline ───────────────────
st.markdown("""
<style>
/* Toast de confirmação */
@keyframes lgSlideUp {
    from { opacity:0; transform:translateY(1rem) scale(0.96); }
    to   { opacity:1; transform:translateY(0)    scale(1);    }
}
#lg-toast {
    position: fixed; bottom: 2rem; right: 2rem; z-index: 999999;
    background: #050f07; border: 1px solid #39ff14; border-radius: 7px;
    padding: 0.55rem 1.2rem; font-family: 'Rajdhani', sans-serif;
    font-size: 0.85rem; font-weight: 700; letter-spacing: 1.3px;
    text-transform: uppercase; color: #39ff14; pointer-events: none;
    box-shadow: 0 0 22px rgba(57,255,20,0.5); display: flex;
    align-items: center; gap: 0.45rem;
    animation: lgSlideUp 0.2s cubic-bezier(0.34,1.56,0.64,1) forwards;
}
#lg-toast.lg-err { border-color:#f87171; color:#f87171; box-shadow:0 0 18px rgba(248,113,113,0.45); }
/* Botão de cópia minimalista */
.lg-cp-btn {
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 3px; cursor: pointer;
    background: transparent; border: 1px solid rgba(0,245,255,0.25);
    color: rgba(0,245,255,0.45); font-size: 0.65rem; flex-shrink: 0;
    transition: border-color 0.15s, color 0.15s, box-shadow 0.15s;
    margin-left: 4px; padding: 0; line-height: 1;
    font-family: 'Font Awesome 6 Free'; font-weight: 900;
}
.lg-cp-btn:hover {
    border-color: #00f5ff; color: #00f5ff;
    box-shadow: 0 0 8px rgba(0,245,255,0.4);
}
.lg-cp-btn.ok  { border-color: #39ff14 !important; color: #39ff14 !important; box-shadow: 0 0 10px rgba(57,255,20,0.5) !important; }
.lg-cp-btn.err { border-color: #f87171 !important; color: #f87171 !important; }
</style>

<script>
/* ── copyToClipboard(text, elementId) ── */
function copyToClipboard(text, elementId) {
    var btn = document.getElementById(elementId);
    function onDone(ok) {
        /* Toast */
        var old = document.getElementById('lg-toast');
        if (old) old.remove();
        var t = document.createElement('div');
        t.id = 'lg-toast';
        if (!ok) t.className = 'lg-err';
        t.innerHTML = ok ? '&#x2714; COPIADO!' : '&#x2718; FALHA';
        document.body.appendChild(t);
        setTimeout(function(){
            t.style.transition = 'opacity 0.3s';
            t.style.opacity = '0';
            setTimeout(function(){ if (t.parentNode) t.remove(); }, 320);
        }, 1800);
        /* Feedback no botão */
        if (btn) {
            var orig = btn.innerHTML;
            btn.innerHTML = ok ? '&#xf00c;' : '&#xf00d;';
            btn.classList.add(ok ? 'ok' : 'err');
            setTimeout(function(){
                btn.innerHTML = orig;
                btn.classList.remove('ok','err');
            }, 2000);
        }
    }
    function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none;width:1px;height:1px;';
        document.body.appendChild(ta); ta.focus(); ta.select();
        try { document.execCommand('copy'); onDone(true); }
        catch(e) { onDone(false); }
        document.body.removeChild(ta);
    }
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(function(){ onDone(true); }).catch(fallback);
    } else { fallback(); }
}

/* Aliases de compatibilidade */
function copyLink(url) { copyToClipboard(url, ''); }
function lgCopy(text, btn) { copyToClipboard(text, btn ? btn.id : ''); }

/* ── Fix inline styles: botões sem fundo branco ── */
(function(){
    var CFG = [
        { cls:'btn-wrap-cyan', border:'2px solid #00f5ff', color:'#00f5ff',
          bg:'transparent', sh:'0 0 8px rgba(0,245,255,0.25)',
          hBg:'rgba(0,245,255,0.13)', hSh:'0 0 18px rgba(0,245,255,0.55),inset 0 0 8px rgba(0,245,255,0.1)',
          hTs:'0 0 8px rgba(0,245,255,1)' },
        { cls:'btn-wrap-pink', border:'2px solid #f472b6', color:'#f472b6',
          bg:'transparent', sh:'0 0 6px rgba(244,114,182,0.2)',
          hBg:'rgba(244,114,182,0.13)', hSh:'0 0 18px rgba(244,114,182,0.55),inset 0 0 8px rgba(244,114,182,0.1)',
          hTs:'0 0 8px rgba(244,114,182,1)' },
        { cls:'btn-wrap-red',  border:'2px solid #ff007f', color:'#ff007f',
          bg:'transparent', sh:'0 0 6px rgba(255,0,127,0.18)',
          hBg:'rgba(255,0,127,0.12)', hSh:'0 0 18px rgba(255,0,127,0.5),inset 0 0 8px rgba(255,0,127,0.08)',
          hTs:'0 0 8px rgba(255,0,127,1)' }
    ];
    var PROPS = [
        ['background','transparent'],['background-color','transparent'],
        ['border-radius','4px'],['font-family',"'Rajdhani',sans-serif"],
        ['font-size','0.68rem'],['font-weight','700'],['letter-spacing','0.9px'],
        ['text-transform','uppercase'],['min-height','26px'],['height','26px'],
        ['display','inline-flex'],['align-items','center'],['justify-content','center'],
        ['gap','0.28rem'],['cursor','pointer'],['white-space','nowrap'],['padding','0 0.6rem']
    ];
    function paint(btn, c, hover) {
        PROPS.forEach(function(p){ btn.style.setProperty(p[0], p[1], 'important'); });
        btn.style.setProperty('border', c.border, 'important');
        btn.style.setProperty('color', hover ? c.color : c.color, 'important');
        btn.style.setProperty('background-color', hover ? c.hBg : 'transparent', 'important');
        btn.style.setProperty('box-shadow', hover ? c.hSh : c.sh, 'important');
        btn.style.setProperty('text-shadow', hover ? c.hTs : 'none', 'important');
    }
    function run() {
        /* ── Botões de ação (Encurtar / Editar / Deletar) ── */
        CFG.forEach(function(c){
            document.querySelectorAll('.'+c.cls+' button,.'+c.cls+' [data-baseweb="button"]').forEach(function(b){
                // Repinta sempre (Streamlit recria DOM a cada rerun)
                paint(b, c, false);
                if (b._lgStyled) return;
                b._lgStyled = true;
                b.addEventListener('mouseenter', function(){ paint(b, c, true); });
                b.addEventListener('mouseleave', function(){ paint(b, c, false); });
            });
        });
        /* ── NUCLEAR: inline style em TODOS os botões Streamlit sem wrapper ── */
        document.querySelectorAll('div.stButton > div > button').forEach(function(b){
            if (b._lgNuclear) return;
            b._lgNuclear = true;
            var inWrapper = b.closest('.btn-wrap-cyan,.btn-wrap-pink,.btn-wrap-red');
            if (inWrapper) return; /* já tratado acima */
            b.style.setProperty('background-color', 'transparent', 'important');
            b.style.setProperty('background', 'transparent', 'important');
        });
        /* ── Botões de cópia: event delegation (evita bloqueio CSP de onclick) ── */
        document.querySelectorAll('.lg-cp-btn[data-url]').forEach(function(b){
            if (b._lgCpBound) return;
            b._lgCpBound = true;
            b.addEventListener('click', function(e){
                e.preventDefault();
                copyToClipboard(b.getAttribute('data-url'), b.id || '');
            });
        });
        /* ── Force label colors (cyan neon) ── */
        ['label','[data-testid="stWidgetLabel"]',
         '[data-testid="stWidgetLabel"] p','[data-testid="stWidgetLabel"] span',
         'div[data-baseweb="form-control"] label'].forEach(function(s){
            document.querySelectorAll(s).forEach(function(el){
                el.style.setProperty('color','rgba(0,245,255,0.85)','important');
                el.style.setProperty('-webkit-text-fill-color','rgba(0,245,255,0.85)','important');
                el.style.setProperty('opacity','1','important');
            });
        });
    }
    run(); setTimeout(run, 200); setTimeout(run, 600); setTimeout(run, 1400);
    new MutationObserver(function(ml){
        if (ml.some(function(m){ return m.addedNodes.length; })) run();
    }).observe(document.body, {childList:true, subtree:true});
})();
</script>
""", unsafe_allow_html=True)

# Carrega token salvo do arquivo na sessão (executa 1x por sessão)
_init_session_from_cfg()

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    return db


def _ensure_demo_user(db):
    user = db.query(Usuario).first()
    if not user:
        user = Usuario(nome="Demo User", email="demo@lyngo.com.br")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def encurtar_link(url: str) -> tuple:
    """Encurta via is.gd (primário) com TinyURL como fallback. Sem token, sem limite."""
    # — Tentativa 1: is.gd (resposta plain-text, sem redirect) —
    try:
        r = requests.get(
            "https://is.gd/create.php",
            params={"format": "simple", "url": url},
            timeout=8,
            allow_redirects=False,
        )
        resultado = r.text.strip()
        if r.status_code == 200 and resultado.startswith("https://is.gd/"):
            return True, resultado
    except Exception:
        pass

    # — Tentativa 2: TinyURL —
    try:
        r2 = requests.get(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
            timeout=8,
            allow_redirects=False,
        )
        resultado2 = r2.text.strip()
        if r2.status_code == 200 and resultado2.startswith("https://tinyurl.com/"):
            return True, resultado2
        return False, f"Falha ao encurtar (HTTP {r2.status_code})"
    except Exception as e:
        return False, f"Sem conexão: {e}"


# Alias para compatibilidade
encurtar_com_bitly = encurtar_link


# ── Sidebar ───────────────────────────────────────────────────────────────────
_LOGO_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.jpg"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.webp"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.svg"),
]
_LOGO_FILE = next((p for p in _LOGO_PATHS if os.path.exists(p)), None)

with st.sidebar:
    # Logo do topo
    st.markdown('<div style="padding:0.6rem 0.5rem 0.2rem 0.5rem;">', unsafe_allow_html=True)
    # Logo SVG inline centralizado
    st.markdown("""
    <div style="display:flex;justify-content:center;align-items:center;padding:0.4rem 0 0.2rem 0;">
    <svg width="160" height="60" viewBox="0 0 280 104" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="lg-gring" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#39ff14"/><stop offset="100%" stop-color="#a855f7"/></linearGradient>
        <linearGradient id="lg-gbolt" x1="20%" y1="0%" x2="80%" y2="100%"><stop offset="0%" stop-color="#00f5ff"/><stop offset="50%" stop-color="#a855f7"/><stop offset="100%" stop-color="#ff2d78"/></linearGradient>
        <linearGradient id="lg-gt" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#00f5ff"/><stop offset="35%" stop-color="#a855f7"/><stop offset="70%" stop-color="#ff2d78"/><stop offset="100%" stop-color="#39ff14"/></linearGradient>
        <linearGradient id="lg-ge" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#a855f7"/><stop offset="100%" stop-color="#00f5ff"/></linearGradient>
        <clipPath id="lg-clip"><rect x="98" y="4" width="84" height="48" rx="24"/></clipPath>
      </defs>
      <rect fill="#080b14" width="280" height="104" rx="0"/>
      <rect fill="#080b14" stroke="url(#lg-gring)" stroke-width="3" x="98" y="4" width="84" height="48" rx="24"/>
      <rect fill="#080b14" x="112" y="16" width="56" height="24" rx="12"/>
      <g clip-path="url(#lg-clip)">
        <polygon fill="url(#lg-gbolt)" points="144,6 132,28 140,28 134,50 150,24 141,24"/>
      </g>
      <circle fill="#39ff14" cx="100" cy="28" r="2.5"/>
      <circle fill="#ff2d78" cx="180" cy="28" r="2.5"/>
      <text font-family="Orbitron,monospace" font-weight="900" font-size="28" fill="url(#lg-gt)" x="140" y="76" text-anchor="middle" letter-spacing="3">LYNGO</text>
      <rect x="90" y="81" width="100" height="2" rx="1" fill="url(#lg-ge)"/>
      <text font-family="Orbitron,monospace" font-weight="700" font-size="10" fill="url(#lg-ge)" x="140" y="96" text-anchor="middle" letter-spacing="7">ELITE</text>
    </svg>
    </div>
    """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('<hr class="sidebar-divider" style="margin:0.3rem 0 0.6rem 0;">', unsafe_allow_html=True)

    PAGES = {
        "Dashboard":          "📊",
        "Gestão de Produtos": "📦",
        "Gerador de Links":   "🔗",
        "VIBEL AI":           "🤖",
        "Configurações":      "⚙️",
    }

    if "page" not in st.session_state:
        st.session_state.page = "Dashboard"

    for page_name, icon in PAGES.items():
        if st.button(f"{icon}  {page_name}", key=f"nav_{page_name}", use_container_width=True):
            st.session_state.page = page_name
            st.session_state.pop("editing_produto_id", None)
            st.session_state.pop("confirm_delete_prod_id", None)
            st.rerun()

    # Marca o botão ativo via JS (sem div wrappers que quebram o sidebar)
    _active_page = st.session_state.page
    st.markdown(f"""
<script>
(function() {{
    var active = {repr(_active_page)};

    /* ── Força sidebar expandida (Streamlit 1.55 usa aria-expanded) ── */
    function forceExpand() {{
        var sb = document.querySelector('[data-testid="stSidebar"]');
        if (!sb) return;
        /* Se estiver colapsada, clica no botão de colapso para reabrir */
        if (sb.getAttribute('aria-expanded') === 'false') {{
            var btn = document.querySelector('[data-testid="stSidebarCollapseButton"]')
                   || document.querySelector('[data-testid="collapsedControl"] button');
            if (btn) btn.click();
        }}
    }}
    setTimeout(forceExpand, 300);

    /* ── Marca botão ativo no menu ── */
    function markNav() {{
        var sb = document.querySelector('[data-testid="stSidebar"]');
        if (!sb && typeof parent !== 'undefined') sb = parent.document.querySelector('[data-testid="stSidebar"]');
        if (!sb) return;
        sb.querySelectorAll('[data-testid="stButton"] button').forEach(function(btn) {{
            if (btn.textContent.trim().indexOf(active) !== -1) {{
                btn.classList.add('lg-nav-active');
            }} else {{
                btn.classList.remove('lg-nav-active');
            }}
        }});
    }}
    markNav();
    setTimeout(markNav, 120);
    setTimeout(markNav, 400);
}})();
</script>
""", unsafe_allow_html=True)

    st.markdown('<hr class="sidebar-divider" style="margin-top:auto">', unsafe_allow_html=True)

    # ── Usuário logado + botão Sair ───────────────────────────────────────────
    _nome_usr = st.session_state.get("usuario_nome", "")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0.4rem;padding:0.2rem 0.1rem;">'
        f'<span style="font-size:0.65rem;color:#4a5a80;letter-spacing:1px;'
        f'text-transform:uppercase;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
        f'👤 {_nome_usr}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
    if st.button("⏻ Sair", key="btn_logout", use_container_width=True, help="Encerrar sessão"):
        revogar_token(st.session_state.get("session_token", ""))
        st.query_params.clear()
        for _k in ["logged_in", "usuario_id", "usuario_nome", "session_token",
                   "page", "vivi_messages", "vivi_generating", "vivi_produto_prompt"]:
            st.session_state.pop(_k, None)
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        '<p style="font-size:0.7rem;color:#2a3555;text-align:center;letter-spacing:1px;margin-top:0.4rem;">v1.0.0 · Lyngo Elite © 2025</p>',
        unsafe_allow_html=True,
    )

page = st.session_state.page

# ── Fallback: botão para reabrir menu se sidebar sumir ────────────────────────
st.markdown("""
<style>
#lg-menu-btn { position:fixed; top:0.4rem; left:0.4rem; z-index:99999;
    background:transparent; border:1px solid rgba(0,245,255,0.35);
    border-radius:4px; color:#00f5ff; font-size:0.7rem; padding:0.2rem 0.5rem;
    cursor:pointer; font-family:'Rajdhani',sans-serif; letter-spacing:1px; }
#lg-menu-btn:hover { background:rgba(0,245,255,0.12); box-shadow:0 0 8px rgba(0,245,255,0.4); }
/* Oculta o botão se sidebar estiver visível e expandida */
body:has([data-testid="stSidebar"][aria-expanded="true"]) #lg-menu-btn { display:none; }
</style>
<button id="lg-menu-btn" onclick="
    var sb = document.querySelector('[data-testid=\\'stSidebar\\']');
    var btn = document.querySelector('[data-testid=\\'stSidebarCollapseButton\\']')
           || document.querySelector('[data-testid=\\'collapsedControl\\'] button');
    if(btn) btn.click();
" title="Abrir Menu">☰ MENU</button>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
if page == "Dashboard":
    st.markdown('<div class="page-title">Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Visão geral da sua operação</div>', unsafe_allow_html=True)

    _u = _uid()
    db = get_db()
    total_produtos = db.query(Produto).filter(Produto.user_id == _u).count()
    _links_q       = db.query(Link).join(Produto, Link.produto_id == Produto.id).filter(Produto.user_id == _u)
    total_links    = _links_q.count()
    cliques_soma   = sum(l.cliques for l in _links_q.all())
    _vendas_q      = (db.query(Venda)
                       .join(Link,    Venda.link_id    == Link.id)
                       .join(Produto, Link.produto_id  == Produto.id)
                       .filter(Produto.user_id == _u))
    total_vendas   = _vendas_q.count()
    receita_total  = sum(v.valor for v in _vendas_q.all())
    db.close()

    st.markdown(f"""
    <div class="metric-grid">
        <div class="metric-card cyan">
            <div class="metric-label">Produtos Ativos</div>
            <div class="metric-value">{total_produtos}</div>
            <div class="metric-delta">▲ cadastrados</div>
        </div>
        <div class="metric-card purple">
            <div class="metric-label">Links Gerados</div>
            <div class="metric-value">{total_links}</div>
            <div class="metric-delta">▲ rastreáveis</div>
        </div>
        <div class="metric-card pink">
            <div class="metric-label">Total de Cliques</div>
            <div class="metric-value">{cliques_soma:,}</div>
            <div class="metric-delta">▲ acumulados</div>
        </div>
        <div class="metric-card green">
            <div class="metric-label">Receita Total</div>
            <div class="metric-value">R${receita_total:,.2f}</div>
            <div class="metric-delta">▲ {total_vendas} vendas</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1.6, 1])
    with col1:
        st.markdown('<div class="panel-card"><h3>Atividade Recente</h3>', unsafe_allow_html=True)
        db = get_db()
        vendas_recentes = (
            db.query(Venda)
            .join(Link,    Venda.link_id   == Link.id)
            .join(Produto, Link.produto_id == Produto.id)
            .filter(Produto.user_id == _u)
            .order_by(Venda.data_hora.desc())
            .limit(5).all()
        )
        db.close()
        if vendas_recentes:
            for v in vendas_recentes:
                dt = v.data_hora.strftime("%d/%m %H:%M")
                st.markdown(f"""
                <div class="produto-row">
                    <span class="produto-nome">Venda #{v.id}</span>
                    <span class="produto-desc">Link #{v.link_id} · {dt}</span>
                    <span class="produto-preco">R$ {v.valor:.2f}</span>
                    <span class="badge badge-cyan">Confirmado</span>
                </div>""", unsafe_allow_html=True)
        else:
            st.markdown('<p style="color:#2a3555;font-size:0.85rem;padding:0.5rem 0;">Nenhuma venda registrada ainda.</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="panel-card"><h3>Status do Sistema</h3>', unsafe_allow_html=True)
        st.markdown("""
        <div style="display:flex;flex-direction:column;gap:0.6rem;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Banco de Dados</span>
                <span class="badge badge-cyan">Online</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Rastreamento</span>
                <span class="badge badge-cyan">Ativo</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">VIBEL AI</span>
                <span class="badge badge-purple">Ativa</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Uptime</span>
                <span style="color:#34d399;font-size:0.85rem;font-family:'Orbitron',sans-serif;">99.9%</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Ranking + Gráfico de cliques ─────────────────────────────────────────
    st.markdown('<div style="margin-top:1.2rem;"></div>', unsafe_allow_html=True)
    rank_col, chart_col = st.columns([1, 1.4])

    with rank_col:
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-trophy" style="color:#f5e030;margin-right:0.5rem;"></i>'
            'Top Produtos</h3>',
            unsafe_allow_html=True,
        )
        db = get_db()
        from sqlalchemy import func as _sqlfunc
        _prod_cliques = (
            db.query(Produto, _sqlfunc.coalesce(_sqlfunc.sum(Link.cliques), 0).label("total"))
            .outerjoin(Link, Link.produto_id == Produto.id)
            .filter(Produto.user_id == _u)
            .group_by(Produto.id)
            .order_by(_sqlfunc.coalesce(_sqlfunc.sum(Link.cliques), 0).desc())
            .limit(3)
            .all()
        )
        db.close()

        _medals = ["🥇", "🥈", "🥉"]
        if _prod_cliques:
            for _rank_i, (_prod, _clk) in enumerate(_prod_cliques):
                _bar_pct = int((_clk / max(_prod_cliques[0][1], 1)) * 100)
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:0.7rem;
                    padding:0.45rem 0;border-bottom:1px solid rgba(0,245,255,0.06);">
                    <span style="font-size:1.2rem;width:1.6rem;text-align:center;">{_medals[_rank_i]}</span>
                    <div style="flex:1;min-width:0;">
                        <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                            font-size:0.82rem;color:#e0e6f0;white-space:nowrap;
                            overflow:hidden;text-overflow:ellipsis;">{_prod.nome}</div>
                        <div style="margin-top:0.2rem;height:4px;border-radius:2px;
                            background:rgba(0,245,255,0.08);">
                            <div style="height:100%;border-radius:2px;width:{_bar_pct}%;
                                background:linear-gradient(90deg,#00f5ff,#a855f7);
                                box-shadow:0 0 6px rgba(0,245,255,0.5);"></div>
                        </div>
                    </div>
                    <span style="font-family:'Orbitron',sans-serif;font-size:0.72rem;
                        color:#00f5ff;text-shadow:0 0 8px rgba(0,245,255,0.6);
                        white-space:nowrap;">{int(_clk):,} cliques</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown(
                '<p style="color:#2a3555;font-size:0.82rem;padding:0.4rem 0;">'
                'Nenhum dado ainda. Adicione produtos e links.</p>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    with chart_col:
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-chart-bar" style="color:#00f5ff;margin-right:0.5rem;"></i>'
            'Cliques por Produto</h3>',
            unsafe_allow_html=True,
        )
        db = get_db()
        from sqlalchemy import func as _sqlfunc2
        _chart_data_raw = (
            db.query(Produto.nome, _sqlfunc2.coalesce(_sqlfunc2.sum(Link.cliques), 0).label("cliques"))
            .outerjoin(Link, Link.produto_id == Produto.id)
            .filter(Produto.user_id == _u)
            .group_by(Produto.id)
            .order_by(_sqlfunc2.coalesce(_sqlfunc2.sum(Link.cliques), 0).desc())
            .limit(8)
            .all()
        )
        db.close()

        if _chart_data_raw:
            import pandas as _pd
            _df = _pd.DataFrame(
                {"Cliques": [int(r[1]) for r in _chart_data_raw]},
                index=[r[0][:20] for r in _chart_data_raw],
            )
            st.bar_chart(_df, color="#00f5ff", height=220)
        else:
            st.markdown(
                '<p style="color:#2a3555;font-size:0.82rem;padding:2rem 0;text-align:center;">'
                'Nenhum clique registrado ainda.</p>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Linha 2: Top Links + Cliques por Dia ────────────────────────────────
    st.markdown('<div style="margin-top:1.2rem;"></div>', unsafe_allow_html=True)
    links_col, days_col = st.columns([1, 1.4])

    with links_col:
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-link" style="color:#00f5ff;margin-right:0.5rem;"></i>'
            'Cliques por Link</h3>',
            unsafe_allow_html=True,
        )
        db = get_db()
        _top_links = (
            db.query(Link)
            .join(Produto, Link.produto_id == Produto.id)
            .filter(Produto.user_id == _u)
            .order_by(Link.cliques.desc())
            .limit(8)
            .all()
        )
        db.close()
        if _top_links:
            _max_lk = max(_top_links[0].cliques, 1)
            for _lk in _top_links:
                _lk_bar = int((_lk.cliques / _max_lk) * 100)
                _lk_label = _lk.rotulo or f"?r={_lk.url_encurtada}"
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:0.6rem;
                    padding:0.35rem 0;border-bottom:1px solid rgba(0,245,255,0.06);">
                    <div style="flex:1;min-width:0;">
                        <div style="font-family:'Rajdhani',sans-serif;font-weight:700;
                            font-size:0.78rem;color:#e0e6f0;white-space:nowrap;
                            overflow:hidden;text-overflow:ellipsis;">{_lk_label}</div>
                        <div style="margin-top:0.18rem;height:3px;border-radius:2px;
                            background:rgba(0,245,255,0.08);">
                            <div style="height:100%;border-radius:2px;width:{_lk_bar}%;
                                background:linear-gradient(90deg,#00f5ff,#a855f7);
                                box-shadow:0 0 4px rgba(0,245,255,0.4);"></div>
                        </div>
                    </div>
                    <span style="font-family:'Orbitron',sans-serif;font-size:0.68rem;
                        color:#00f5ff;white-space:nowrap;">{int(_lk.cliques):,}</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown(
                '<p style="color:#2a3555;font-size:0.82rem;padding:0.4rem 0;">'
                'Nenhum link com cliques ainda.</p>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    with days_col:
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-chart-line" style="color:#a855f7;margin-right:0.5rem;"></i>'
            'Cliques por Dia — últimos 7 dias</h3>',
            unsafe_allow_html=True,
        )
        from datetime import timedelta as _td
        from sqlalchemy import func as _sqlfunc3
        _hoje = _dt.utcnow().date()
        _7dias = [_hoje - _td(days=i) for i in range(6, -1, -1)]
        db = get_db()
        _click_rows = (
            db.query(
                _sqlfunc3.date(ClickEvent.accessed_at).label("dia"),
                _sqlfunc3.count(ClickEvent.id).label("total"),
            )
            .join(Link, ClickEvent.link_id == Link.id)
            .join(Produto, Link.produto_id == Produto.id)
            .filter(
                Produto.user_id == _u,
                ClickEvent.accessed_at >= _dt.combine(_7dias[0], _dt.min.time()),
            )
            .group_by(_sqlfunc3.date(ClickEvent.accessed_at))
            .all()
        )
        db.close()
        import pandas as _pd3
        _click_dict = {str(r[0]): int(r[1]) for r in _click_rows}
        _dias_fmt   = [d.strftime("%d/%m") for d in _7dias]
        _clks_vals  = [_click_dict.get(str(d), 0) for d in _7dias]
        _df3 = _pd3.DataFrame({"Cliques": _clks_vals}, index=_dias_fmt)
        if sum(_clks_vals) > 0:
            st.bar_chart(_df3, color="#a855f7", height=200)
        else:
            st.markdown(
                '<p style="color:#2a3555;font-size:0.82rem;padding:2rem 0;text-align:center;">'
                'Nenhum clique rastreado ainda.<br>'
                '<span style="font-size:0.75rem;color:#3a4a70;">Compartilhe seus links como '
                '<strong style="color:#00f5ff;">?r=slug</strong> para registrar cliques.</span></p>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# GESTÃO DE PRODUTOS
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Gestão de Produtos":
    st.markdown('<div class="page-title">Gestão de Produtos</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Cadastre e gerencie seus produtos</div>', unsafe_allow_html=True)

    _ss = st.session_state

    # ── Session state ────────────────────────────────────────────────────────
    for _k, _v in [
        ("editing_produto_id",    None),
        ("confirm_delete_prod_id", None),
        ("confirm_delete_link_id", None),
        ("adding_link_produto_id", None),
        ("editing_link_id",        None),
        ("bitly_links",            {}),
    ]:
        if _k not in _ss:
            _ss[_k] = _v

    col_form, col_list = st.columns([1, 1.6])

    # ── Coluna esquerda: formulário ──────────────────────────────────────────
    with col_form:
        editing_id = _ss.editing_produto_id

        produto_edit = None
        if editing_id is not None:
            db = get_db()
            produto_edit = db.query(Produto).filter(Produto.id == editing_id, Produto.user_id == _uid()).first()
            db.close()

        form_title = f"Editar Produto #{editing_id}" if produto_edit else "Novo Produto"
        icon_title = "fa-pen-to-square" if produto_edit else "fa-plus"

        st.markdown(
            f'<div class="panel-card"><h3>'
            f'<i class="fa-solid {icon_title}" style="margin-right:0.5rem;"></i>'
            f'{form_title}</h3>',
            unsafe_allow_html=True,
        )

        with st.form("form_produto", clear_on_submit=True):
            nome_val  = produto_edit.nome          if produto_edit else ""
            desc_val  = produto_edit.descricao     if produto_edit else ""
            preco_val = produto_edit.preco         if produto_edit else 0.0
            link_val  = produto_edit.link_afiliado if produto_edit else ""

            nome          = st.text_input("Nome do Produto", value=nome_val, placeholder="Ex: Curso de Marketing Digital")
            descricao     = st.text_area("Descrição", value=desc_val or "", placeholder="Descreva brevemente o produto...", height=90)
            preco         = st.number_input("Preço (R$)", value=preco_val, min_value=0.0, step=0.01, format="%.2f")
            link_afiliado = st.text_input(
                "Link de Afiliado / Destino",
                value=link_val or "",
                placeholder="https://pay.hotmart.com/seu-produto",
            )
            col_s, col_c = st.columns([2, 1])
            with col_s:
                submitted = st.form_submit_button(
                    "Salvar Produto" if not produto_edit else "Atualizar",
                    use_container_width=True,
                )
            with col_c:
                cancelled = st.form_submit_button("Cancelar", use_container_width=True)

        if cancelled:
            _ss.editing_produto_id = None
            st.rerun()

        if submitted:
            if not nome.strip():
                st.markdown('<div class="alert-error"><i class="fa-solid fa-circle-exclamation"></i> O nome do produto é obrigatório.</div>', unsafe_allow_html=True)
            else:
                db = get_db()
                try:
                    if produto_edit:
                        p = db.query(Produto).filter(Produto.id == editing_id, Produto.user_id == _uid()).first()
                        p.nome          = nome.strip()
                        p.descricao     = descricao.strip() or None
                        p.preco         = preco
                        p.link_afiliado = link_afiliado.strip() or None
                        db.commit()
                        _ss.editing_produto_id = None
                        st.markdown(f'<div class="alert-success"><i class="fa-solid fa-check"></i> Produto <strong>{nome}</strong> atualizado!</div>', unsafe_allow_html=True)
                    else:
                        p = Produto(
                            nome=nome.strip(),
                            descricao=descricao.strip() or None,
                            preco=preco,
                            link_afiliado=link_afiliado.strip() or None,
                            user_id=_uid(),
                        )
                        db.add(p)
                        db.commit()
                        st.markdown(f'<div class="alert-success"><i class="fa-solid fa-check"></i> Produto <strong>{nome}</strong> cadastrado!</div>', unsafe_allow_html=True)
                except Exception as e:
                    db.rollback()
                    st.markdown(f'<div class="alert-error"><i class="fa-solid fa-triangle-exclamation"></i> Erro: {e}</div>', unsafe_allow_html=True)
                finally:
                    db.close()
                st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

    # ── Coluna direita: produtos expansíveis ─────────────────────────────────
    with col_list:
        db = get_db()
        produtos = db.query(Produto).filter(Produto.user_id == _uid()).order_by(Produto.id.desc()).all()
        db.close()

        total_str = f'<span style="color:#4a5a80;font-size:0.75rem;font-weight:400;margin-left:0.5rem;">{len(produtos)} produtos</span>'
        st.markdown(
            f'<div class="panel-card"><h3>'
            f'<i class="fa-solid fa-boxes-stacked" style="margin-right:0.5rem;"></i>'
            f'Produtos Cadastrados {total_str}</h3>',
            unsafe_allow_html=True,
        )

        if not produtos:
            st.markdown(
                '<p style="color:#2a3555;font-size:0.85rem;padding:0.5rem 0;">'
                'Nenhum produto cadastrado ainda. Use o formulário ao lado.</p>',
                unsafe_allow_html=True,
            )
        else:
            for p in produtos:
                db = get_db()
                links_do_produto = (
                    db.query(Link)
                    .filter(Link.produto_id == p.id)
                    .order_by(Link.id.asc())
                    .all()
                )
                db.close()

                n_links    = len(links_do_produto)
                exp_label  = f"**{p.nome}** — R$ {p.preco:.2f}   •   {n_links} link{'s' if n_links != 1 else ''}"

                with st.expander(exp_label):
                    # ── Descrição ─────────────────────────────────────────
                    if p.descricao:
                        st.markdown(
                            f'<div class="prod-exp-desc">{p.descricao}</div>',
                            unsafe_allow_html=True,
                        )

                    # ── Link afiliado do produto ───────────────────────────
                    if p.link_afiliado:
                        _laf = p.link_afiliado
                        _laf_disp = (_laf[:55] + "…") if len(_laf) > 55 else _laf
                        _laf_col, _cp_col = st.columns([11, 1])
                        with _laf_col:
                            st.markdown(
                                f'<div class="prod-link-afiliado">'
                                f'<span class="prod-link-afiliado-label">'
                                f'<i class="fa-solid fa-link" style="margin-right:0.2rem;"></i>Link</span>'
                                f'<span class="prod-link-afiliado-url">{_laf_disp}</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        with _cp_col:
                            _copy_component(_laf, f"laf-{p.id}")

                    # ── Botões do produto ─────────────────────────────────
                    c_ed, c_del, c_lk, c_vivi = st.columns([1, 1, 1, 1], vertical_alignment="center")
                    with c_ed:
                        st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
                        if st.button("✏️ Editar", key=f"edit_{p.id}", help="Editar produto", use_container_width=True):
                            _ss.editing_produto_id     = p.id
                            _ss.adding_link_produto_id = None
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
                    with c_del:
                        st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
                        if st.button("🗑️ Deletar", key=f"del_{p.id}", help="Excluir produto", use_container_width=True):
                            _ss.confirm_delete_prod_id = p.id
                            _ss.confirm_delete_link_id = None
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
                    with c_lk:
                        st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
                        is_open   = _ss.adding_link_produto_id == p.id
                        add_label = "✕ Fechar" if is_open else "＋ Link"
                        if st.button(add_label, key=f"add_lk_{p.id}", help="Adicionar link ao produto", use_container_width=True):
                            _ss.adding_link_produto_id = None if is_open else p.id
                            _ss.editing_produto_id     = None
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
                    with c_vivi:
                        st.markdown('<div class="btn-wrap-pink">', unsafe_allow_html=True)
                        if st.button("✨ VIBEL AI", key=f"vivi_{p.id}", help="Gerar Copy com VIBEL AI", use_container_width=True):
                            _desc = p.descricao or "Não informada."
                            _laf  = p.link_afiliado or "Não informado."
                            _vivi_prompt = (
                                f"Preciso de uma estratégia completa para este produto:\n\n"
                                f"📦 **Produto:** {p.nome}\n"
                                f"💰 **Preço:** R$ {p.preco:.2f}\n"
                                f"📝 **Descrição:** {_desc}\n"
                                f"🔗 **Link:** {_laf}\n\n"
                                f"Me entregue:\n"
                                f"1. Recomendação de funil (checkout direto, WhatsApp ou landing page?)\n"
                                f"2. Script de WhatsApp de alta conversão (máx. 5 linhas)\n"
                                f"3. Legenda pronta para Instagram/Reels\n"
                                f"4. Headline para anúncio Meta Ads\n"
                                f"Seja direta e foque em conversão."
                            )
                            st.session_state.vivi_produto_prompt = _vivi_prompt
                            st.session_state.page = "VIBEL AI"
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)

                    # ── Confirmação de exclusão do produto ────────────────
                    if _ss.confirm_delete_prod_id == p.id:
                        st.markdown(
                            '<div class="confirm-strip">'
                            '<i class="fa-solid fa-triangle-exclamation"></i>'
                            f' Tem certeza que deseja excluir <strong>{p.nome}</strong>?'
                            ' Todos os links serão removidos.</div>',
                            unsafe_allow_html=True,
                        )
                        cc_yes, cc_no, _ = st.columns([1, 1, 2])
                        with cc_yes:
                            st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
                            if st.button("Confirmar", key=f"confirm_yes_{p.id}", use_container_width=True):
                                db = get_db()
                                try:
                                    db.query(Produto).filter(Produto.id == p.id).delete()
                                    db.commit()
                                    _ss.confirm_delete_prod_id = None
                                except Exception as e:
                                    db.rollback()
                                    st.error(str(e))
                                finally:
                                    db.close()
                                st.rerun()
                            st.markdown('</div>', unsafe_allow_html=True)
                        with cc_no:
                            st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
                            if st.button("Cancelar", key=f"confirm_no_{p.id}", use_container_width=True):
                                _ss.confirm_delete_prod_id = None
                                st.rerun()
                            st.markdown('</div>', unsafe_allow_html=True)

                    st.markdown('<div class="expander-sep"></div>', unsafe_allow_html=True)

                    # ── Links vinculados ──────────────────────────────────
                    if links_do_produto:
                        st.markdown(
                            '<div style="font-size:0.7rem;letter-spacing:1.4px;text-transform:uppercase;'
                            'color:#4a5a80;margin-bottom:0.4rem;">'
                            '<i class="fa-solid fa-link" style="margin-right:0.3rem;"></i>Links</div>',
                            unsafe_allow_html=True,
                        )
                        for _lk_idx, lk in enumerate(links_do_produto):
                            if _lk_idx > 0:
                                st.markdown('<div class="lk-row-sep"></div>', unsafe_allow_html=True)
                            rotulo_disp = lk.rotulo if lk.rotulo else "—"
                            bitly_url   = _ss.bitly_links.get(lk.id) or lk.url_bitly
                            url_to_copy = (
                                bitly_url
                                if (bitly_url and not str(bitly_url).startswith("ERRO:"))
                                else lk.url_original
                            )

                            # ── Linha única: info + copiar + encurtar + editar + deletar ──
                            _l1, _l2, _l3, _l4, _l5 = st.columns([3, 1, 2, 2, 2], vertical_alignment="center")
                            with _l1:
                                st.markdown(
                                    f'<div class="link-row-compact">'
                                    f'<span class="link-name-neon">{rotulo_disp}</span>'
                                    f'<span class="link-url-dim">'
                                    f'{lk.url_original[:32]}{"…" if len(lk.url_original) > 32 else ""}'
                                    f'</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                            with _l2:
                                _copy_component(url_to_copy, f"lk-{lk.id}")
                            with _l3:
                                st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
                                if st.button("⚡ Encurtar", key=f"sl_{lk.id}", help="Encurtar link com Bitly", use_container_width=True):
                                    ok, res = encurtar_com_bitly(lk.url_original)
                                    if ok:
                                        _ss.bitly_links[lk.id] = res
                                        db = get_db()
                                        try:
                                            lk_db = db.query(Link).filter(Link.id == lk.id).first()
                                            if lk_db:
                                                lk_db.url_bitly = res
                                            db.commit()
                                        finally:
                                            db.close()
                                    else:
                                        _ss.bitly_links[lk.id] = f"ERRO:{res}"
                                    st.rerun()
                                st.markdown('</div>', unsafe_allow_html=True)
                            with _l4:
                                st.markdown('<div class="btn-wrap-pink">', unsafe_allow_html=True)
                                if st.button("✏️ Editar", key=f"edit_lk_{lk.id}", help="Editar link", use_container_width=True):
                                    _ss.editing_link_id        = lk.id if _ss.editing_link_id != lk.id else None
                                    _ss.confirm_delete_link_id = None
                                    st.rerun()
                                st.markdown('</div>', unsafe_allow_html=True)
                            with _l5:
                                st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
                                if st.button("🗑️ Deletar", key=f"dl_{lk.id}", help="Excluir link", use_container_width=True):
                                    _ss.confirm_delete_link_id = lk.id if _ss.confirm_delete_link_id != lk.id else None
                                    _ss.editing_link_id        = None
                                    st.rerun()
                                st.markdown('</div>', unsafe_allow_html=True)

                            # URL encurtada (exibida abaixo quando disponível)
                            if bitly_url:
                                if str(bitly_url).startswith("ERRO:"):
                                    st.markdown(
                                        f'<div class="alert-error" style="margin:0.1rem 0 0.35rem;'
                                        f'padding:0.25rem 0.7rem;font-size:0.72rem;">'
                                        f'<i class="fa-solid fa-circle-xmark"></i> '
                                        f'{str(bitly_url).replace("ERRO:", "", 1)}</div>',
                                        unsafe_allow_html=True,
                                    )
                                else:
                                    _bl1, _bl2, _bl3, _bl4, _bl5 = st.columns([3, 1, 2, 2, 2], vertical_alignment="center")
                                    with _bl1:
                                        st.markdown(
                                            f'<div class="bitly-inline">'
                                            f'<i class="fa-solid fa-bolt" style="color:#a855f7;'
                                            f'font-size:0.6rem;flex-shrink:0;"></i>'
                                            f'<span style="flex:1;min-width:0;overflow:hidden;'
                                            f'text-overflow:ellipsis;white-space:nowrap;">{bitly_url}</span>'
                                            f'</div>',
                                            unsafe_allow_html=True,
                                        )
                                    with _bl2:
                                        _copy_component(str(bitly_url), f"bu-{lk.id}")

                            # Formulário inline de edição do link
                            if _ss.editing_link_id == lk.id:
                                with st.form(f"form_edit_lk_{lk.id}", clear_on_submit=False):
                                    col_er, col_eu = st.columns([1, 2])
                                    with col_er:
                                        new_rot = st.text_input("Rótulo", value=lk.rotulo or "")
                                    with col_eu:
                                        new_url = st.text_input("URL", value=lk.url_original)
                                    col_esv, col_ecn = st.columns([1, 1])
                                    with col_esv:
                                        lk_save = st.form_submit_button("✓ Salvar", use_container_width=True)
                                    with col_ecn:
                                        lk_cancel = st.form_submit_button("✗ Cancelar", use_container_width=True)

                                if lk_save and new_url.strip():
                                    db = get_db()
                                    try:
                                        lk_db = db.query(Link).filter(Link.id == lk.id).first()
                                        if lk_db:
                                            lk_db.rotulo       = new_rot.strip() or None
                                            lk_db.url_original = new_url.strip()
                                        db.commit()
                                        _ss.editing_link_id = None
                                    except Exception as e:
                                        db.rollback()
                                        st.error(str(e))
                                    finally:
                                        db.close()
                                    st.rerun()
                                if lk_cancel:
                                    _ss.editing_link_id = None
                                    st.rerun()

                            # Confirmação de exclusão do link
                            if _ss.confirm_delete_link_id == lk.id:
                                st.markdown(
                                    '<div class="confirm-strip">'
                                    '<i class="fa-solid fa-triangle-exclamation"></i>'
                                    ' Tem certeza que deseja excluir este link?</div>',
                                    unsafe_allow_html=True,
                                )
                                cd_yes, cd_no, _ = st.columns([1, 1, 2])
                                with cd_yes:
                                    st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
                                    if st.button("Confirmar", key=f"dlk_yes_{lk.id}", use_container_width=True):
                                        db = get_db()
                                        try:
                                            db.query(Link).filter(Link.id == lk.id).delete()
                                            db.commit()
                                            _ss.bitly_links.pop(lk.id, None)
                                            _ss.confirm_delete_link_id = None
                                        except Exception as e:
                                            db.rollback()
                                            st.error(str(e))
                                        finally:
                                            db.close()
                                        st.rerun()
                                    st.markdown('</div>', unsafe_allow_html=True)
                                with cd_no:
                                    st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
                                    if st.button("Cancelar", key=f"dlk_no_{lk.id}", use_container_width=True):
                                        _ss.confirm_delete_link_id = None
                                        st.rerun()
                                    st.markdown('</div>', unsafe_allow_html=True)

                    # ── Formulário: adicionar novo link ───────────────────
                    if _ss.adding_link_produto_id == p.id:
                        st.markdown('<div class="expander-sep"></div>', unsafe_allow_html=True)
                        st.markdown(
                            '<p style="font-size:0.7rem;color:#6a7aaa;letter-spacing:1.5px;'
                            'text-transform:uppercase;margin:0.3rem 0 0.5rem;">'
                            '<i class="fa-solid fa-plus" style="margin-right:0.35rem;"></i>Novo Link</p>',
                            unsafe_allow_html=True,
                        )
                        with st.form(f"form_add_link_{p.id}", clear_on_submit=True):
                            col_r, col_u = st.columns([1, 2])
                            with col_r:
                                rotulo_input = st.text_input("Rótulo", placeholder="Ex: Promoção 50%")
                            with col_u:
                                url_input = st.text_input("URL de Destino", placeholder="https://pay.hotmart.com/...")
                            col_add_btn, col_cancel_btn, _ = st.columns([1, 1, 2])
                            with col_add_btn:
                                add_submitted = st.form_submit_button("✓ Adicionar", use_container_width=True)
                            with col_cancel_btn:
                                add_cancelled = st.form_submit_button("✗ Cancelar", use_container_width=True)

                        if add_submitted:
                            if url_input.strip():
                                import hashlib as _hl, time as _tm
                                _hx: str = _hl.md5(f"{url_input}{_tm.time()}".encode()).hexdigest()
                                auto_slug = "".join(c for i, c in enumerate(_hx) if i < 8)
                                db = get_db()
                                try:
                                    novo_lk = Link(
                                        produto_id=p.id,
                                        url_original=url_input.strip(),
                                        url_encurtada=auto_slug,
                                        rotulo=rotulo_input.strip() or None,
                                    )
                                    db.add(novo_lk)
                                    db.commit()
                                    _ss.adding_link_produto_id = None
                                except Exception as e:
                                    db.rollback()
                                    st.error(str(e))
                                finally:
                                    db.close()
                            st.rerun()

                        if add_cancelled:
                            _ss.adding_link_produto_id = None
                            st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# GERADOR DE LINKS
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Gerador de Links":
    st.markdown('<div class="page-title">Gerador de Links</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Crie links rastreáveis para seus produtos</div>', unsafe_allow_html=True)

    col_form, col_list = st.columns([1, 1.4])

    with col_form:
        st.markdown('<div class="panel-card"><h3><i class="fa-solid fa-link" style="margin-right:0.5rem;"></i>Novo Link</h3>', unsafe_allow_html=True)
        db = get_db()
        produtos = db.query(Produto).filter(Produto.user_id == _uid()).all()
        db.close()

        if not produtos:
            st.markdown('<div class="alert-error">Nenhum produto cadastrado. Crie um produto primeiro.</div>', unsafe_allow_html=True)
        else:
            opcoes = {f"#{p.id} · {p.nome}": p for p in produtos}
            with st.form("form_link", clear_on_submit=True):
                produto_sel  = st.selectbox("Produto", list(opcoes.keys()))
                p_sel        = opcoes[produto_sel]
                url_original = st.text_input(
                    "URL Original",
                    value=p_sel.link_afiliado or "",
                    placeholder="https://minhapagina.com/produto",
                )
                url_custom   = st.text_input("Slug personalizado", placeholder="ex: meu-curso-2025 (opcional)")
                submitted_l  = st.form_submit_button("Gerar Link", use_container_width=True)

            if submitted_l:
                if not url_original.strip():
                    st.markdown('<div class="alert-error">Informe a URL original.</div>', unsafe_allow_html=True)
                else:
                    import hashlib, time
                    _hx: str = hashlib.md5(f"{url_original}{time.time()}".encode()).hexdigest()
                    slug: str = url_custom.strip() if url_custom.strip() else "".join(c for i, c in enumerate(_hx) if i < 8)
                    db = get_db()
                    try:
                        link = Link(produto_id=p_sel.id, url_original=url_original.strip(), url_encurtada=slug)
                        db.add(link)
                        db.commit()
                        st.markdown(f'<div class="alert-success"><i class="fa-solid fa-check"></i> Link criado: <strong>lyngo.com.br/{slug}</strong></div>', unsafe_allow_html=True)
                    except Exception as e:
                        db.rollback()
                        st.markdown(f'<div class="alert-error">Erro: {e}</div>', unsafe_allow_html=True)
                    finally:
                        db.close()
        st.markdown('</div>', unsafe_allow_html=True)

    with col_list:
        st.markdown('<div class="panel-card"><h3><i class="fa-solid fa-list" style="margin-right:0.5rem;"></i>Links Ativos</h3>', unsafe_allow_html=True)
        db = get_db()
        _gl_links = (
            db.query(Link)
            .join(Produto, Link.produto_id == Produto.id)
            .filter(Produto.user_id == _uid())
            .order_by(Link.id.desc())
            .all()
        )
        db.close()

        # Cabeçalho da lista
        if _gl_links:
            _gl_init_copy = st.session_state.get("gl_copy_id")
            _gl_init_edit = st.session_state.get("gl_edit_id")
            _gl_init_del  = st.session_state.get("gl_del_id")

            for lk in _gl_links:
                _base = st.session_state.get("base_url", "http://localhost:8501").rstrip("/")
                _copy_url = f"{_base}/?r={lk.url_encurtada}"

                # ── Linha: URL + badge + 3 botões alinhados ───────────────────
                _r1, _r2, _r3, _r4 = st.columns([4, 1, 1, 1], vertical_alignment="center")

                with _r1:
                    _lk_label = f" · {lk.rotulo}" if lk.rotulo else ""
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;">'
                        f'<span style="font-family:\'Rajdhani\',sans-serif;font-weight:700;'
                        f'font-size:0.85rem;color:#00f5ff;text-shadow:0 0 8px rgba(0,245,255,0.5);">'
                        f'?r={lk.url_encurtada}</span>'
                        f'<span class="badge badge-cyan" style="font-size:0.6rem;">{lk.cliques} cliques</span>'
                        f'{"<span style=\"font-size:0.7rem;color:#4a5a80;\">" + lk.rotulo + "</span>" if lk.rotulo else ""}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                with _r2:
                    _copy_component(_copy_url, f"gl-{lk.id}")

                with _r3:
                    st.markdown('<div class="btn-wrap-pink">', unsafe_allow_html=True)
                    if st.button("✏️", key=f"gl_edit_{lk.id}", help="Editar link", use_container_width=True):
                        st.session_state.gl_edit_id = lk.id if st.session_state.get("gl_edit_id") != lk.id else None
                        st.session_state.pop("gl_del_id", None)
                        st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)

                with _r4:
                    st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
                    if st.button("🗑️", key=f"gl_del_{lk.id}", help="Excluir link", use_container_width=True):
                        st.session_state.gl_del_id = lk.id if st.session_state.get("gl_del_id") != lk.id else None
                        st.session_state.pop("gl_edit_id", None)
                        st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)

                # ── Painel de edição inline ────────────────────────────────────
                if st.session_state.get("gl_edit_id") == lk.id:
                    with st.form(f"gl_form_edit_{lk.id}", clear_on_submit=False):
                        _new_slug = st.text_input("Slug", value=lk.url_encurtada)
                        _new_url  = st.text_input("URL original", value=lk.url_original)
                        _sv, _cn = st.columns(2)
                        with _sv:
                            _gl_save = st.form_submit_button("✓ Salvar", use_container_width=True)
                        with _cn:
                            _gl_cancel = st.form_submit_button("✕ Cancelar", use_container_width=True)
                    if _gl_save:
                        db = get_db()
                        try:
                            _lk_db = db.query(Link).filter(Link.id == lk.id).first()
                            if _lk_db:
                                _lk_db.url_encurtada = _new_slug.strip()
                                _lk_db.url_original  = _new_url.strip()
                                db.commit()
                            st.session_state.pop("gl_edit_id", None)
                        except Exception as _e:
                            db.rollback()
                            st.error(str(_e))
                        finally:
                            db.close()
                        st.rerun()
                    if _gl_cancel:
                        st.session_state.pop("gl_edit_id", None)
                        st.rerun()

                # ── Confirmação de exclusão ────────────────────────────────────
                if st.session_state.get("gl_del_id") == lk.id:
                    _dc, _dno = st.columns(2)
                    with _dc:
                        st.markdown('<div class="btn-wrap-red">', unsafe_allow_html=True)
                        if st.button("Confirmar exclusão", key=f"gl_confirm_{lk.id}", use_container_width=True):
                            db = get_db()
                            try:
                                db.query(Link).filter(Link.id == lk.id).delete()
                                db.commit()
                                st.session_state.pop("gl_del_id", None)
                            except Exception as _e:
                                db.rollback()
                                st.error(str(_e))
                            finally:
                                db.close()
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
                    with _dno:
                        st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
                        if st.button("Cancelar", key=f"gl_cancel_{lk.id}", use_container_width=True):
                            st.session_state.pop("gl_del_id", None)
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('<hr style="border:none;border-top:1px solid rgba(0,245,255,0.05);margin:0.1rem 0;">', unsafe_allow_html=True)
        else:
            st.markdown('<p style="color:#2a3555;font-size:0.85rem;">Nenhum link gerado ainda.</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# VIBEL AI
# ─────────────────────────────────────────────────────────────────────────────
elif page == "VIBEL AI":
    st.markdown('<div class="page-title">✨ VIBEL AI</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Sua mentora estratégica de copy e vendas</div>', unsafe_allow_html=True)

    _gemini_key = st.secrets.get("GEMINI_API_KEY") or st.session_state.get("gemini_api_key", "").strip()

    if not _gemini_key:
        st.markdown("""
        <div class="panel-card" style="text-align:center;padding:2.5rem 2rem;">
            <div style="font-size:3rem;margin-bottom:0.8rem;">🔑</div>
            <div style="font-family:'Orbitron',sans-serif;font-size:0.9rem;color:#a855f7;letter-spacing:2px;margin-bottom:0.6rem;">API KEY NECESSÁRIA</div>
            <div style="color:#4a5a80;font-size:0.85rem;max-width:380px;margin:0 auto;">
                Vá em <strong style="color:#00f5ff;">⚙️ Configurações</strong> e insira sua
                <strong style="color:#a855f7;">API Key (OpenRouter ou Gemini)</strong> para ativar a VIBEL AI.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # ── Header da VIBEL ───────────────────────────────────────────────────
        st.markdown("""
        <div class="vivi-header">
            <div style="font-size:2rem;">⚡</div>
            <div>
                <div class="vivi-header-name">VIBEL AI</div>
                <div class="vivi-header-sub">LYNGO ELITE</div>
            </div>
            <div style="margin-left:auto;display:flex;align-items:center;gap:0.5rem;">
                <div class="vivi-status-dot"></div>
                <span style="font-size:0.72rem;color:#39ff14;letter-spacing:1px;">ONLINE</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Inicializa histórico ──────────────────────────────────────────────
        if "vivi_messages" not in st.session_state:
            st.session_state.vivi_messages = [
                {
                    "role": "assistant",
                    "content": (
                        "⚡ **VIBEL ONLINE**\n\n"
                        "Sistema ativado. Sou a VIBEL AI — sua estrategista de vendas e copy do Lyngo Elite. "
                        "Estou pronta para turbinar seus funis, criar scripts de alta conversão e otimizar cada link. "
                        "Por onde começamos?"
                    ),
                }
            ]

        # ── Injeta prompt do produto (botão "Gerar Copy") ─────────────────────
        if st.session_state.get("vivi_produto_prompt"):
            _inj_prompt = st.session_state.pop("vivi_produto_prompt")
            st.session_state.vivi_messages.append({"role": "user", "content": _inj_prompt})

        _msgs = st.session_state.vivi_messages

        # ── Exibe histórico ───────────────────────────────────────────────────
        if not _msgs:
            st.markdown("""
            <div style="text-align:center;padding:2rem 1rem;color:#3a3a5a;">
                <div style="font-size:2rem;margin-bottom:0.5rem;">⚡</div>
                <div style="font-size:0.85rem;letter-spacing:1px;line-height:1.7;">
                    Pergunte sobre funil de vendas, copy para WhatsApp,<br>
                    anúncios Meta/Google ou estratégia de link.<br>
                    Ou clique em <strong style="color:#a855f7;">✨ Gerar Copy</strong> em qualquer produto.
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            for _m in _msgs:
                _avatar = "⚡" if _m["role"] == "assistant" else "👤"
                with st.chat_message(_m["role"], avatar=_avatar):
                    st.markdown(_m["content"])

        # ── Gera resposta — flag de guarda anti-loop ─────────────────────────
        _needs_reply = (
            bool(_msgs)
            and _msgs[-1]["role"] == "user"
            and not st.session_state.get("vivi_generating", False)
        )
        if _needs_reply:
            st.session_state.vivi_generating = True
            with st.chat_message("assistant", avatar="⚡"):
                with st.spinner("VIBEL analisando..."):
                    try:
                        _reply = _vivi_generate(_msgs, VIVI_SYSTEM_PROMPT)
                        st.session_state.vivi_messages.append(
                            {"role": "assistant", "content": _reply}
                        )
                    except Exception as _ve:
                        _err_txt = str(_ve)
                        st.session_state.vivi_messages.append(
                            {"role": "assistant", "content": f"⚠️ {_err_txt}"}
                        )
                    finally:
                        st.session_state.vivi_generating = False
            st.rerun()

        # ── Input do chat ─────────────────────────────────────────────────────
        _user_input = st.chat_input(
            "Fale com a VIBEL — funil, copy, anúncio, link...",
            key="vivi_chat_input",
        )
        if _user_input:
            # Ignora submit se ainda estiver gerando
            if not st.session_state.get("vivi_generating", False):
                st.session_state.vivi_messages.append({"role": "user", "content": _user_input})
                st.rerun()

        # ── Atalhos estratégicos ──────────────────────────────────────────────
        st.markdown(
            '<div style="font-size:0.65rem;letter-spacing:1.2px;text-transform:uppercase;'
            'color:#2a3a5a;margin:0.8rem 0 0.35rem;">Atalhos estratégicos</div>',
            unsafe_allow_html=True,
        )
        _ATALHOS = [
            ("🔥 Abordagem",       "Crie um script de abordagem inicial para um lead frio no WhatsApp para este produto. Deve ser curto, direto e gerar curiosidade."),
            ("💰 Recuperar Boleto", "Crie uma mensagem de recuperação de boleto vencido ou carrinho abandonado. Tom urgente mas sem ser agressivo."),
            ("🛡️ Quebrar Objeção",  "Liste as 3 principais objeções de compra e crie respostas prontas para cada uma, para usar no WhatsApp ou nos comentários."),
            ("📢 Roteiro de Anúncio","Crie um roteiro de vídeo curto (30 segundos) para um anúncio no Instagram Reels ou TikTok, com gancho, problema, solução e CTA."),
            ("📧 E-mail de Venda",  "Escreva um e-mail de vendas completo com assunto impactante, corpo persuasivo e CTA claro. Máximo 200 palavras."),
        ]
        _is_generating = st.session_state.get("vivi_generating", False)

        # Linha 1: Abordagem | Recuperar Boleto | Quebrar Objeção
        _at_row1 = st.columns(3)
        for _i in range(3):
            _label, _prompt = _ATALHOS[_i]
            with _at_row1[_i]:
                st.markdown('<div class="btn-wrap-atalho">', unsafe_allow_html=True)
                if st.button(_label, key=f"vivi_atalho_{_i}", use_container_width=True, disabled=_is_generating):
                    st.session_state.vivi_messages.append({"role": "user", "content": _prompt})
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

        # Linha 2: Roteiro de Anúncio | E-mail de Venda | Limpar Chat
        _at_row2 = st.columns(3)
        for _i in range(3, 5):
            _label, _prompt = _ATALHOS[_i]
            with _at_row2[_i - 3]:
                st.markdown('<div class="btn-wrap-atalho">', unsafe_allow_html=True)
                if st.button(_label, key=f"vivi_atalho_{_i}", use_container_width=True, disabled=_is_generating):
                    st.session_state.vivi_messages.append({"role": "user", "content": _prompt})
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

        with _at_row2[2]:
            st.markdown('<div class="btn-wrap-atalho">', unsafe_allow_html=True)
            if st.button("🗑️ Limpar Chat", key="vivi_clear", use_container_width=True):
                st.session_state.vivi_messages = []
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────────────────────────────────────
elif page == "Configurações":
    st.markdown('<div class="page-title">Configurações</div>', unsafe_allow_html=True)
    st.markdown('<div class="page-subtitle">Personalize sua conta e plataforma</div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="panel-card"><h3><i class="fa-solid fa-user" style="margin-right:0.5rem;"></i>Perfil</h3>', unsafe_allow_html=True)
        with st.form("form_perfil"):
            st.text_input("Nome completo", placeholder="Seu nome")
            st.text_input("E-mail", placeholder="seu@email.com")
            st.text_input("URL da foto", placeholder="https://...")
            st.form_submit_button("Salvar Perfil", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

        # ── VIBEL AI — API Key (OpenRouter ou Gemini) ─────────────────────
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-wand-magic-sparkles" style="margin-right:0.5rem;color:#a855f7;"></i>'
            'VIBEL AI</h3>',
            unsafe_allow_html=True,
        )
        _cur_key = st.session_state.get("gemini_api_key", "")
        _key_status = (
            '<span style="color:#39ff14;font-size:0.75rem;letter-spacing:1px;">'
            '<i class="fa-solid fa-circle-check"></i> Ativa</span>'
            if _cur_key else
            '<span style="color:#f87171;font-size:0.75rem;letter-spacing:1px;">'
            '<i class="fa-solid fa-circle-xmark"></i> Não configurada</span>'
        )
        st.markdown(
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.7rem;">'
            f'<span style="font-size:0.78rem;color:#4a5a80;">API Key (OpenRouter ou Gemini)</span>'
            f'{_key_status}</div>',
            unsafe_allow_html=True,
        )
        with st.form("form_gemini_key"):
            _new_key = st.text_input(
                "API Key (OpenRouter ou Gemini)",
                value=_cur_key,
                type="password",
                placeholder="sk-or-...",
                label_visibility="collapsed",
            )
            _col_save, _col_del = st.columns(2)
            with _col_save:
                _save_key = st.form_submit_button("💾 Salvar", use_container_width=True)
            with _col_del:
                _del_key = st.form_submit_button("🗑️ Remover", use_container_width=True)
        if _save_key and _new_key.strip():
            st.session_state.gemini_api_key = _new_key.strip()
            _save_cfg("gemini_api_key", _new_key.strip())
            st.success("API Key salva! VIBEL AI está ativa.")
            st.rerun()
        if _del_key:
            st.session_state.gemini_api_key = ""
            _del_cfg("gemini_api_key")
            st.info("API Key removida.")
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        # ── TinyURL Integration ────────────────────────────────────────────
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-bolt" style="margin-right:0.5rem;color:#a855f7;"></i>'
            'Encurtador de Links</h3>'
            '<div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.8rem;">'
            '<i class="fa-solid fa-circle-check" style="color:#34d399;font-size:0.8rem;"></i>'
            '<span style="font-size:0.78rem;color:#34d399;letter-spacing:1px;">TinyURL ativo — sem token, sem limite</span>'
            '</div>'
            '<p style="font-size:0.78rem;color:#4a5a80;line-height:1.55;margin:0;">'
            'O Lyngo Elite usa a API pública do <strong style="color:#7a9acc;">TinyURL</strong> '
            'para encurtar links. Gratuita, sem cadastro e sem limites de uso. '
            'Basta clicar em <strong style="color:#00f5ff;">⚡ Encurtar</strong> em qualquer link.'
            '</p>',
            unsafe_allow_html=True,
        )

        col_test, _ = st.columns([1, 2])
        with col_test:
            st.markdown('<div class="btn-wrap-cyan">', unsafe_allow_html=True)
            if st.button("⚡ Testar encurtador", use_container_width=True):
                ok, res = encurtar_link("https://lyngo.com.br/teste-tinyurl")
                if ok:
                    st.markdown(
                        f'<div class="alert-success">'
                        f'<i class="fa-solid fa-circle-check"></i> Funcionando! → {res}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f'<div class="alert-error">{res}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        # ── URL do Domínio ────────────────────────────────────────────────────
        st.markdown(
            '<div class="panel-card">'
            '<h3><i class="fa-solid fa-globe" style="margin-right:0.5rem;color:#00f5ff;"></i>'
            'URL do seu domínio</h3>'
            '<p style="font-size:0.78rem;color:#4a5a80;line-height:1.5;margin:0 0 0.8rem;">'
            'Define a URL base usada nos links rastreáveis copiados.<br>'
            'Ex: <span style="color:#00f5ff;">https://lyngoelite.com.br</span></p>',
            unsafe_allow_html=True,
        )
        _cur_base = st.session_state.get("base_url", "http://localhost:8501")
        with st.form("form_base_url"):
            _new_base = st.text_input(
                "URL base",
                value=_cur_base,
                placeholder="https://seudominio.com.br",
                label_visibility="collapsed",
            )
            _save_base = st.form_submit_button("💾 Salvar URL", use_container_width=True)
        if _save_base and _new_base.strip():
            _val = _new_base.strip().rstrip("/")
            st.session_state.base_url = _val
            _save_cfg("base_url", _val)
            st.success(f"URL base salva: {_val}")
            st.rerun()
        st.markdown(
            f'<div style="margin-top:0.4rem;font-size:0.72rem;color:#3a4a70;">'
            f'Link copiado ficará: <span style="color:#00f5ff;">{_cur_base.rstrip("/")}/?r=slug</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="panel-card"><h3><i class="fa-solid fa-sliders" style="margin-right:0.5rem;"></i>Preferências</h3>', unsafe_allow_html=True)
        st.markdown("""
        <div style="display:flex;flex-direction:column;gap:0.8rem;padding-top:0.3rem;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Notificações de venda</span>
                <span class="badge badge-cyan">Ativo</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Relatório semanal</span>
                <span class="badge badge-purple">Configurar</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Tema</span>
                <span class="badge badge-pink">Cyberpunk</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="color:#6a7aaa;font-size:0.85rem;">Plano atual</span>
                <span style="color:#34d399;font-size:0.85rem;font-weight:600;">PRO</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
