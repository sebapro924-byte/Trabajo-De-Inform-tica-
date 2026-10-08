"""Evaluador de lectura en voz alta — Español, Inglés y Guaraní."""
import html
import io
import random
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

import streamlit as st


# ------------------------------------------------------------------
# Configuración
# ------------------------------------------------------------------
st.set_page_config(page_title="Evaluador de Lectura", page_icon="🎙️",
                   layout="centered", initial_sidebar_state="collapsed")

WHISPER_MODEL = "small"   # "base" = más rápido, "medium" = más preciso
CODIGOS = {"Español": "es", "Inglés": "en"}
DB = "ranking.db"

TEXTOS = {
    "Español": [
        "El sol brilla intensamente sobre las colinas por la mañana.",
        "La tecnología avanza rápidamente cambiando nuestra vida diaria.",
        "Aprender un nuevo idioma abre puertas a nuevas oportunidades.",
    ],
    "Inglés": [
        "The quick brown fox jumps over the lazy dog.",
        "Learning a new language opens up many global opportunities.",
        "Consistency and practice are key to achieving fluency.",
    ],
    # Conviene que un hablante de guaraní revise estas frases.
    "Guaraní": [
        "Mba'éichapa reko, vy'apavẽ ndéve guarã ko árape.",
        "Guaraní réra ha'e ñane ñe'ẽ teete ha jahayhu va'erã.",
        "Oky guasu rire, osẽ kuarahy omhesape pára tape.",
    ],
}

st.markdown("""
<style>
.block-container { max-width: 720px; padding: 1.2rem 1rem 3rem; }
h1, h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
.stButton > button { width: 100%; border-radius: 10px; min-height: 3em; }
.frase { font-size: clamp(1.25rem, 4.5vw, 1.7rem); line-height: 1.7; margin: .5rem 0 1rem; }
.w { padding: 2px 6px; border-radius: 6px; margin: 0 1px; display: inline-block; }
.w.ok { background: transparent; }
.w.abrev, .w.salto { background: #ffe44d; color: #222; }
.w.mal { background: #ff5c5c; color: #fff; }
.leyenda span { margin-right: 14px; font-size: .9rem; }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------
# Lógica de evaluación (compara lo esperado con lo dicho)
# ------------------------------------------------------------------
UMBRAL_OK = 0.85      # parecido mínimo para dar la palabra por buena
UMBRAL_MAL = 0.50     # por debajo de esto, la palabra es "otra cosa"


@dataclass
class Palabra:
    texto: str      # palabra original (para mostrar)
    estado: str     # "ok" | "abrev" | "salto" | "mal"
    dicho: str = "" # lo que se entendió (si aplica)


def normalizar(p: str) -> str:
    """Minúsculas, sin tildes ni signos (tolerante con la transcripción)."""
    p = unicodedata.normalize("NFD", p.lower())
    p = "".join(c for c in p if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]", "", p)


def _tokens(texto: str):
    out = []
    for w in texto.split():
        n = normalizar(w)
        if n:
            out.append((w, n))
    return out


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _clasificar(orig_n: str, dicho_n: str) -> str:
    if _ratio(orig_n, dicho_n) >= UMBRAL_OK:
        return "ok"
    if 2 <= len(dicho_n) < len(orig_n) and orig_n.startswith(dicho_n):
        return "abrev"
    return "mal"


def evaluar(original: str, dicho: str):
    """Devuelve (lista de Palabra, puntaje 0-100, palabras de más)."""
    o = _tokens(original)
    s = _tokens(dicho)
    on, sn = [n for _, n in o], [n for _, n in s]
    res = [None] * len(o)
    extras = 0

    for tag, i1, i2, j1, j2 in SequenceMatcher(None, on, sn, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                res[i1 + k] = Palabra(o[i1 + k][0], "ok", s[j1 + k][0])
        elif tag == "delete":
            for i in range(i1, i2):
                res[i] = Palabra(o[i][0], "salto")
        elif tag == "insert":
            extras += j2 - j1
        else:  # replace
            lo, ls = i2 - i1, j2 - j1
            if lo == ls:
                for k in range(lo):
                    est = _clasificar(on[i1 + k], sn[j1 + k])
                    res[i1 + k] = Palabra(o[i1 + k][0], est, s[j1 + k][0])
            else:
                ptr = 0
                for k in range(lo):
                    i = i1 + k
                    cand = [(
                        _ratio(on[i], sn[j1 + c]), c) for c in range(ptr, ls)]
                    mejor = max(cand) if cand else (0, -1)
                    if mejor[0] >= UMBRAL_MAL or (
                            cand and 2 <= len(sn[j1 + mejor[1]]) < len(on[i])
                            and on[i].startswith(sn[j1 + mejor[1]])):
                        c = mejor[1]
                        res[i] = Palabra(o[i][0], _clasificar(on[i], sn[j1 + c]), s[j1 + c][0])
                        extras += c - ptr
                        ptr = c + 1
                    elif (ls - ptr) >= (lo - k):
                        res[i] = Palabra(o[i][0], "mal", s[j1 + ptr][0])
                        ptr += 1
                    else:
                        res[i] = Palabra(o[i][0], "salto")
                extras += max(0, ls - ptr)

    total = len(res) or 1
    puntos = sum({"ok": 1, "abrev": 0.5}.get(p.estado, 0) for p in res)
    return res, round(puntos / total * 100, 1), extras


# ------------------------------------------------------------------
# Ranking persistente (SQLite)
# ------------------------------------------------------------------
def _db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS ranking(nombre TEXT, idioma TEXT, "
                "puntaje REAL, fecha TEXT)")
    return con

def guardar_puntaje(nombre, idioma, puntaje):
    with _db() as con:
        con.execute("INSERT INTO ranking VALUES (?,?,?,?)",
                    (nombre, idioma, puntaje, datetime.now().isoformat()))

def top(n=5):
    with _db() as con:
        return con.execute("SELECT nombre, idioma, MAX(puntaje) p FROM ranking "
                           "GROUP BY nombre, idioma ORDER BY p DESC LIMIT ?", (n,)).fetchall()

# ------------------------------------------------------------------
# Modelos de IA (se cargan una sola vez y quedan en memoria)
# ------------------------------------------------------------------
@st.cache_resource(show_spinner="Cargando modelo de voz (solo la primera vez)…")
def cargar_whisper():
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

@st.cache_resource(show_spinner="Cargando modelo de guaraní (la primera vez tarda)…")
def cargar_mms():
    from transformers import AutoProcessor, Wav2Vec2ForCTC
    proc = AutoProcessor.from_pretrained("facebook/mms-1b-all", target_lang="grn")
    model = Wav2Vec2ForCTC.from_pretrained(
        "facebook/mms-1b-all", target_lang="grn", ignore_mismatched_sizes=True)
    model.eval()
    return proc, model

def transcribir(audio_bytes: bytes, idioma: str) -> str:
    from faster_whisper.audio import decode_audio
    audio = decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)

    if idioma == "Guaraní":          # Meta MMS: soporta guaraní (grn)
        import torch
        proc, model = cargar_mms()
        entrada = proc(audio, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            logits = model(**entrada).logits
        return proc.decode(torch.argmax(logits, dim=-1)[0])

    modelo = cargar_whisper()         # Whisper: español e inglés
    # Sin initial_prompt a propósito: así no "corrige" los errores del lector.
    segs, _ = modelo.transcribe(audio, language=CODIGOS[idioma], beam_size=1,
                                vad_filter=True, condition_on_previous_text=False)
    return " ".join(s.text for s in segs).strip()

# ------------------------------------------------------------------
# Utilidades de interfaz
# ------------------------------------------------------------------
def ir(vista):
    st.session_state.vista = vista
    st.rerun()

def pintar(palabras) -> str:
    return "".join(f'<span class="w {p.estado}">{html.escape(p.texto)}</span> '
                   for p in palabras)

def mensaje(p):
    if p >= 85: return st.success, "¡Excelente! 🎉 Lectura casi perfecta."
    if p >= 60: return st.warning, "¡Buen intento! 👍 Repasa las palabras marcadas."
    return st.error, "Sigue practicando 💪 Probá de nuevo despacio."

S = st.session_state
S.setdefault("vista", "menu")
S.setdefault("usuario", "")
S.setdefault("n_audio", 0)

# ------------------------------------------------------------------
# Vistas
# ------------------------------------------------------------------
if S.vista == "menu":
    st.title("🎙️ Evaluador de Lectura")
    st.caption("Proyecto escolar — Informática")
    nombre = st.text_input("Tu nombre", value=S.usuario)
    if st.button("Comenzar", type="primary"):
        if nombre.strip():
            S.usuario = nombre.strip()
            ir("practica")
        else:
            st.error("Escribe tu nombre para comenzar.")
    st.subheader("🏆 Ranking")
    filas = top()
    if not filas:
        st.caption("Todavía no hay puntajes.")
    for i, (n, idi, p) in enumerate(filas, 1):
        st.write(f"**#{i} {html.escape(n)}** — {p:.0f}% · {idi}")

elif S.vista == "practica":
    st.subheader(f"Hola, {S.usuario}")
    idioma = st.selectbox("Idioma", list(TEXTOS))
    if S.get("idioma_previo") != idioma or "frase" not in S:
        S.frase, S.idioma_previo = random.choice(TEXTOS[idioma]), idioma

    st.caption("Lee en voz alta:")
    st.markdown(f'<div class="frase">{html.escape(S.frase)}</div>', unsafe_allow_html=True)

    audio = st.audio_input("Toca el micrófono, lee y vuelve a tocar para parar",
                           key=f"audio_{S.n_audio}")
    with st.expander("¿No funciona el micrófono? Escribe lo que leíste"):
        manual = st.text_area("Texto", label_visibility="collapsed")

    c1, c2 = st.columns(2)
    if c1.button("Evaluar", type="primary"):
        if audio is None and not manual.strip():
            st.warning("Graba tu lectura primero.")
        else:
            try:
                with st.spinner("Analizando tu lectura…"):
                    dicho = manual.strip() if audio is None else transcribir(audio.getvalue(), idioma)
            except ImportError as e:
                st.error(f"Falta instalar una librería: {e}. Revisa requirements.txt.")
                st.stop()
            palabras, puntaje, extras = evaluar(S.frase, dicho)
            S.resultado = dict(palabras=palabras, puntaje=puntaje, dicho=dicho,
                               extras=extras, idioma=idioma)
            guardar_puntaje(S.usuario, idioma, puntaje)
            ir("resultado")
    if c2.button("Otra frase"):
        S.frase = random.choice(TEXTOS[idioma]); S.n_audio += 1; st.rerun()
    if st.button("← Menú"):
        ir("menu")

elif S.vista == "resultado":
    r = S.resultado
    st.title("📊 Resultado")
    st.metric("Acierto", f"{r['puntaje']}%")
    aviso, texto = mensaje(r["puntaje"])
    aviso(texto)

    st.markdown(f'<div class="frase">{pintar(r["palabras"])}</div>', unsafe_allow_html=True)
    st.markdown('<div class="leyenda"><span><span class="w salto">&nbsp;</span> salteó / abrevió</span>'
                '<span><span class="w mal">&nbsp;</span> dijo mal</span></div>',
                unsafe_allow_html=True)
    if r["extras"]:
        st.caption(f"Agregaste {r['extras']} palabra(s) que no estaban en el texto.")
    with st.expander("Lo que entendió la IA"):
        st.write(r["dicho"] or "(no se escuchó nada)")

    c1, c2 = st.columns(2)
    if c1.button("🔄 Intentar otra vez", type="primary"):
        S.n_audio += 1; ir("practica")
    if c2.button("🏠 Menú"):
        S.n_audio += 1; ir("menu")
