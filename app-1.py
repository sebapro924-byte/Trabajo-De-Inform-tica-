
"""Lectura en voz alta con IA — Español, Inglés y Guaraní.

Funciona de dos formas con este mismo archivo:
  * En línea (publicado en Streamlit Cloud).
  * Local y SIN internet, en una computadora:  python app.py
    (la primera vez, con internet, se descargan los modelos:  python app.py --descargar)
  * Local y SIN internet, usando también el CELULAR como pantalla:  python app.py --celular
    (la computadora hace el trabajo y el celular se conecta por la misma red Wi-Fi/hotspot)
"""
import html
import io
import os
import random
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# ------------------------------------------------------------------
# Configuración general
# ------------------------------------------------------------------
WHISPER_MODEL = "small"          # "base" = más rápido, "medium" = más preciso
CODIGOS = {"Español": "es", "Inglés": "en"}
IDIOMAS = ["Español", "Inglés", "Guaraní"]
NIVELES = ["Normal", "Difícil", "Profesional"]
OPCIONES_NIVEL = NIVELES + ["Progresiva"]
DESCRIPCION = {
    "Normal": "Frases cortas y sencillas. Corrección flexible.",
    "Difícil": "Frases largas. Corrección más exigente.",
    "Profesional": "Trabalenguas y vocabulario técnico. Corrección estricta.",
    "Progresiva": "Empieza fácil y sube de nivel a medida que lees bien.",
}
UMBRALES = {"Normal": 0.78, "Difícil": 0.85, "Profesional": 0.92}  # parecido mínimo para dar OK
UMBRAL_MAL = 0.50
SUBIR_CON = 70                   # promedio mínimo para subir de nivel (modo progresivo)
BASE = Path(__file__).resolve().parent
DB = str(BASE / "ranking.db")

# ------------------------------------------------------------------
# Modelos de IA (funciones puras; sirven para uso local sin internet)
# ------------------------------------------------------------------
def _dir_modelos():
    """Carpeta 'modelos' junto a app.py (así se pueden llevar y usar sin internet)."""
    try:
        d = BASE / "modelos"
        d.mkdir(exist_ok=True)
        return str(d) if os.access(d, os.W_OK) else None
    except Exception:
        return None


def _con_respaldo(fn):
    """Primero intenta SIN internet (modelo ya descargado); si no está, lo descarga."""
    try:
        return fn(True)
    except Exception:
        return fn(False)


def _crear_whisper(solo_local):
    from faster_whisper import WhisperModel
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                        download_root=_dir_modelos(), local_files_only=solo_local)


def _crear_mms(solo_local):
    from transformers import AutoProcessor, Wav2Vec2ForCTC
    kw = dict(target_lang="grn", cache_dir=_dir_modelos(), local_files_only=solo_local)
    proc = AutoProcessor.from_pretrained("facebook/mms-1b-all", **kw)
    model = Wav2Vec2ForCTC.from_pretrained("facebook/mms-1b-all",
                                           ignore_mismatched_sizes=True, **kw)
    model.eval()
    return proc, model


def descargar_modelos(con_guarani=False):
    print("Descargando el modelo de voz (español e inglés)...")
    _crear_whisper(False)
    if con_guarani:
        print("Descargando el modelo de guaraní (unos 4 GB, paciencia)...")
        _crear_mms(False)
    print("Listo. Ya puedes usar la app SIN internet.")

# ------------------------------------------------------------------
# Lanzador: 'python app.py' abre la app en una ventana propia
# ------------------------------------------------------------------
def _bajo_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        try:
            return get_script_run_ctx(suppress_warning=True) is not None
        except TypeError:
            return get_script_run_ctx() is not None
    except Exception:
        return False


def _abrir_ventana(url):
    """Abre Edge/Chrome en modo 'app' (sin barra de direcciones); si no, el navegador."""
    import shutil
    import subprocess
    import webbrowser
    candidatos = [shutil.which(n) for n in
                  ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser")]
    candidatos += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for ruta in candidatos:
        if ruta and os.path.exists(ruta):
            subprocess.Popen([ruta, f"--app={url}"])
            return
    webbrowser.open(url)


def _ip_local():
    """IP de esta computadora en la red local (no necesita internet)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _certificado(ip):
    """Certificado HTTPS propio: el celular solo deja usar el micrófono con https."""
    import ipaddress
    from datetime import timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    carpeta = BASE / "certificados"
    carpeta.mkdir(exist_ok=True)
    crt, clave = carpeta / f"{ip}.crt", carpeta / f"{ip}.key"
    if not (crt.exists() and clave.exists()):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])
        ahora = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(ahora - timedelta(days=1))
                .not_valid_after(ahora + timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address(ip)), x509.DNSName("localhost")]),
                    critical=False)
                .sign(key, hashes.SHA256()))
        clave.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(crt), str(clave)


def _puerto_libre(desde=8501):
    import socket
    for p in range(desde, desde + 20):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return 0


def _lanzar():
    import socket
    import subprocess
    import threading
    import time
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--descargar" in sys.argv:
        descargar_modelos("--guarani" in sys.argv)
        return

    celular = "--celular" in sys.argv
    extra = []
    if celular:
        ip = _ip_local()
        puerto = _puerto_libre()
        esquema = "http"
        try:
            crt, clave = _certificado(ip)
            extra = ["--server.address", "0.0.0.0",
                     "--server.sslCertFile", crt, "--server.sslKeyFile", clave]
            esquema = "https"
        except ImportError:
            extra = ["--server.address", "0.0.0.0"]
            print("\nFalta la libreria 'cryptography' (ejecuta INSTALAR.bat con internet).")
            print("Sin ella el celular NO podra usar el microfono.\n")
        url_cel = f"{esquema}://{ip}:{puerto}"
        print("=" * 60)
        print(" MODO CELULAR (sin internet)")
        print(" 1) Conecta el celular a la MISMA red Wi-Fi o hotspot que esta PC.")
        print(f" 2) En el celular abre:  {url_cel}")
        print("    (si avisa que no es seguro: Avanzado > Continuar)")
        print("=" * 60)
        try:
            import qrcode
            q = qrcode.QRCode(border=1)
            q.add_data(url_cel)
            q.print_ascii(invert=True)
        except Exception:
            pass
        if ip.startswith("127."):
            print("\nNo se detecta ninguna red. Activa el hotspot o el Wi-Fi.")
        url_pc = url_cel
    else:
        puerto = _puerto_libre() or 8501
        url_pc = f"http://localhost:{puerto}"

    def esperar_y_abrir():
        for _ in range(240):
            try:
                with socket.create_connection(("127.0.0.1", puerto), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.5)
        _abrir_ventana(url_pc)

    threading.Thread(target=esperar_y_abrir, daemon=True).start()
    print("Iniciando Lectura... (cierra esta ventana para salir)")
    subprocess.call([sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve()),
                     "--server.port", str(puerto), "--server.headless", "true",
                     "--browser.gatherUsageStats", "false", *extra])


if __name__ == "__main__" and not _bajo_streamlit():
    _lanzar()
    raise SystemExit

# ------------------------------------------------------------------
# A partir de acá es la app (Streamlit)
# ------------------------------------------------------------------
st.set_page_config(page_title="Lectura", page_icon="🎙️",
                   layout="wide", initial_sidebar_state="collapsed")

# ------------------------------------------------------------------
# Banco de frases: TEXTOS[idioma][nivel]
# (Las de guaraní conviene que las revise un hablante.)
# ------------------------------------------------------------------
TEXTOS = {
    "Español": {
        "Normal": [
            "El sol brilla sobre las colinas.",
            "Mi abuela prepara el almuerzo los domingos.",
            "Los niños juegan en el patio.",
            "Leer un buen libro es divertido.",
            "El perro corre feliz por el parque.",
            "Mañana iremos temprano a la escuela.",
            "La música nos pone alegres.",
            "Me gusta tomar tereré con mis amigos.",
        ],
        "Difícil": [
            "El sol brilla intensamente sobre las colinas por la mañana.",
            "La tecnología avanza rápidamente cambiando nuestra vida diaria.",
            "Aprender un nuevo idioma abre puertas a nuevas oportunidades.",
            "El río cruza la ciudad y refleja las luces de la noche.",
            "Cuidar el medio ambiente es responsabilidad de todos nosotros.",
            "Cuando amanece, los pájaros cantan en los árboles del jardín.",
            "Quiero ser ingeniero para crear programas que ayuden a las personas.",
            "Mañana por la tarde visitaremos el museo de historia de la ciudad.",
        ],
        "Profesional": [
            "Tres tristes tigres tragaban trigo en un trigal.",
            "El desarrollo sostenible exige equilibrar el crecimiento económico, la justicia social y la protección del medio ambiente.",
            "La inteligencia artificial transforma la medicina, la educación y la manera en que nos comunicamos.",
            "Pablito clavó un clavito. ¿Qué clavito clavó Pablito?",
            "El electrocardiograma permitió diagnosticar la arritmia cardíaca de forma rápida y precisa.",
            "Aunque la investigación científica requiere paciencia, perseverancia y rigurosidad, sus resultados benefician a toda la humanidad.",
            "El otorrinolaringólogo examinó minuciosamente al paciente antes de recomendar el tratamiento.",
            "Compré pocas copas, pocas copas compré; como compré pocas copas, pocas copas pagué.",
        ],
    },
    "Inglés": {
        "Normal": [
            "The sun shines on the hills.",
            "My grandmother cooks lunch on Sundays.",
            "The children play in the yard.",
            "Reading a good book is fun.",
            "The dog runs happily in the park.",
            "Tomorrow we will go to school early.",
            "Music makes us happy.",
            "I like to drink tea with my friends.",
        ],
        "Difícil": [
            "The quick brown fox jumps over the lazy dog.",
            "Learning a new language opens up many global opportunities.",
            "Consistency and practice are key to achieving fluency.",
            "The river crosses the city and reflects the lights at night.",
            "Taking care of the environment is everyone's responsibility.",
            "At dawn, the birds sing in the trees of the garden.",
            "I want to become an engineer and build programs that help people.",
            "Tomorrow afternoon we will visit the history museum downtown.",
        ],
        "Profesional": [
            "She sells seashells by the seashore, and the shells she sells are surely seashells.",
            "Sustainable development requires balancing economic growth, social justice, and environmental protection.",
            "Artificial intelligence is transforming medicine, education, and the way we communicate.",
            "Peter Piper picked a peck of pickled peppers.",
            "Although scientific research demands patience, perseverance, and rigor, its results benefit all of humanity.",
            "The otolaryngologist thoroughly examined the patient before recommending a treatment.",
            "Thirty-three thieves thought that they thrilled the throne throughout Thursday.",
            "The comprehensive curriculum emphasizes critical thinking, collaboration, and technological literacy.",
        ],
    },
    "Guaraní": {
        "Normal": [
            "Mba'éichapa reiko?",
            "Che réra Ana.",
            "Ahayhu che ñe'ẽ guaraní.",
            "Pe mitã ohecha peteĩ guyra.",
            "Kuarahy omimbi ko árape.",
            "Aháta eskuélape ko'ẽrõ.",
            "Ko'ágã aju eskuélagui.",
            "Ñande ñe'ẽ iporãiterei.",
        ],
        "Difícil": [
            "Che réra Ana ha aiko Paraguáipe.",
            "Ñande ñe'ẽ guaraní iporãiterei.",
            "Che ru ha che sy oñe'ẽ guaraníme.",
            "Mitãkuéra oñembosarái yvyra guýpe.",
            "Paraguay ha'e peteĩ tetã iporãva.",
            "Che aguereko peteĩ mbarakaja pyahu.",
            "Ko'ẽrõ ajapóta tembiapo ha avy'áta.",
            "Guaraní ha'e ñane ñe'ẽ teete ha jahayhu va'erã.",
        ],
        "Profesional": [
            "Che réra Ana, aiko Paraguáipe ha ahayhu che ñe'ẽ guaraní.",
            "Che ru ha che sy oñe'ẽ guaraníme, ha ko'ágã che avei añe'ẽ guaraníme.",
            "Mitãkuéra oñembosarái yvyra guýpe ha kuarahy omimbi ko árape.",
            "Aháta eskuélape ko'ẽrõ ha avy'áta che mbo'ehára ha che irũnguéra ndive.",
            "Pe mbo'ehára ombo'e mitãkuéra ohayhu haguã ñane ñe'ẽ guaraní.",
            "Ñane retã Paraguay oguereko mokõi ñe'ẽ: guaraní ha karaiñe'ẽ.",
            "Paraguay ha'e peteĩ tetã iporãva, ha ñande ñe'ẽ guaraní ha'e ñane ñe'ẽ teete.",
        ],
    },
}

# ------------------------------------------------------------------
# Estilo: minimalista pero colorido, se adapta a celular, tablet y PC
# ------------------------------------------------------------------
st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1180px; padding: .8rem 1rem 3rem; }

.encabezado { display: flex; align-items: center; gap: 14px; padding: 1.1rem 1.4rem;
  border-radius: 20px; color: #fff; margin-bottom: 1rem;
  background: linear-gradient(120deg, #4f46e5 0%, #7c3aed 55%, #ec4899 100%);
  box-shadow: 0 8px 24px rgba(79,70,229,.25); }
.encabezado .logo { font-size: 2.4rem; }
.encabezado h1 { margin: 0; font-size: clamp(1.6rem, 5vw, 2.2rem); letter-spacing: -0.02em;
  color: #fff; padding: 0; }
.encabezado p { margin: 2px 0 0; opacity: .9; font-size: .95rem; }

.panel-t { font-weight: 700; font-size: 1.05rem; margin: .2rem 0 .6rem; }
.tarjeta { border: 1px solid rgba(128,128,128,.25); border-radius: 18px;
  padding: 1rem 1.2rem; margin: .5rem 0; background: rgba(128,128,128,.05); }
.tarjeta.fuerte { border-left: 6px solid #7c3aed; }
.tarjeta h3 { margin: 0 0 .5rem; }

.chip { display: inline-block; padding: 2px 12px; border-radius: 999px; font-size: .8rem;
  font-weight: 600; margin: 0 6px 4px 0; color: #fff; background: #6b7280; }
.c-esp { background: #f97316; } .c-ing { background: #0ea5e9; } .c-gua { background: #16a34a; }
.n-normal { background: #22c55e; } .n-difícil { background: #f59e0b; }
.n-profesional { background: #ef4444; } .n-progresiva { background: #8b5cf6; }
.c-otro { background: #6b7280; }

.frase { font-size: clamp(1.3rem, 4.2vw, 2rem); line-height: 1.75; text-align: center;
  padding: .8rem .2rem; }
.w { padding: 2px 6px; border-radius: 6px; margin: 0 1px; display: inline-block; }
.w.abrev, .w.salto { background: #ffe44d; color: #222; }
.w.mal { background: #ff5c5c; color: #fff; }
.leyenda { text-align: center; font-size: .85rem; }
.leyenda .w { padding: 1px 10px; }

.paso { display: flex; gap: 10px; align-items: center; margin: .45rem 0; }
.paso b { background: #7c3aed; color: #fff; border-radius: 50%; min-width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center; font-size: .85rem; }
.fila-rank { display: flex; justify-content: space-between; align-items: center;
  padding: .5rem .2rem; border-bottom: 1px solid rgba(128,128,128,.18); gap: 8px; }
.fila-rank:last-child { border-bottom: none; }

.stButton > button { width: 100%; border-radius: 14px; min-height: 3em; font-weight: 600; }
.stButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"] {
  background: linear-gradient(120deg, #4f46e5, #7c3aed); border: none; color: #fff; }
</style>
""", unsafe_allow_html=True)

# Mejora "Agregar a pantalla de inicio" en el celular (se ejecuta una vez)
if "pwa" not in st.session_state:
    st.session_state.pwa = True
    components.html("""<script>
    const d = window.parent.document;
    const meta = (n, c) => {
      let m = d.querySelector('meta[name="' + n + '"]');
      if (!m) { m = d.createElement('meta'); m.name = n; d.head.appendChild(m); }
      m.content = c;
    };
    meta('theme-color', '#4f46e5');
    meta('mobile-web-app-capable', 'yes');
    meta('apple-mobile-web-app-capable', 'yes');
    meta('apple-mobile-web-app-title', 'Lectura');
    </script>""", height=0)

# ------------------------------------------------------------------
# Lógica de evaluación (compara lo esperado con lo dicho)
# ------------------------------------------------------------------
@dataclass
class Palabra:
    texto: str       # palabra original (para mostrar)
    estado: str      # "ok" | "abrev" | "salto" | "mal"
    dicho: str = ""  # lo que se entendió


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


def _es_abrev(orig_n: str, dicho_n: str) -> bool:
    return 2 <= len(dicho_n) < len(orig_n) and orig_n.startswith(dicho_n)


def _clasificar(orig_n: str, dicho_n: str, umbral: float) -> str:
    if _ratio(orig_n, dicho_n) >= umbral:
        return "ok"
    return "abrev" if _es_abrev(orig_n, dicho_n) else "mal"


def evaluar(original: str, dicho: str, umbral: float = 0.85):
    """Devuelve (lista de Palabra, puntaje 0-100, palabras de más)."""
    o, s = _tokens(original), _tokens(dicho)
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
                    est = _clasificar(on[i1 + k], sn[j1 + k], umbral)
                    res[i1 + k] = Palabra(o[i1 + k][0], est, s[j1 + k][0])
            else:
                ptr = 0
                for k in range(lo):
                    i = i1 + k
                    cand = [(_ratio(on[i], sn[j1 + c]), c) for c in range(ptr, ls)]
                    mejor = max(cand) if cand else (0, -1)
                    if cand and (mejor[0] >= UMBRAL_MAL or _es_abrev(on[i], sn[j1 + mejor[1]])):
                        c = mejor[1]
                        res[i] = Palabra(o[i][0], _clasificar(on[i], sn[j1 + c], umbral), s[j1 + c][0])
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
# Ranking persistente (SQLite, funciona sin internet)
# ------------------------------------------------------------------
def _db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS ranking_v2(nombre TEXT, idioma TEXT, "
                "nivel TEXT, puntaje REAL, fecha TEXT)")
    return con


def guardar_puntaje(nombre, idioma, nivel, puntaje):
    try:
        with _db() as con:
            con.execute("INSERT INTO ranking_v2 VALUES (?,?,?,?,?)",
                        (nombre, idioma, nivel, puntaje, datetime.now().isoformat()))
    except Exception:
        pass  # el ranking nunca debe romper el juego


def top(n=8, idioma=None):
    try:
        with _db() as con:
            if idioma:
                return con.execute(
                    "SELECT nombre, idioma, nivel, MAX(puntaje) p FROM ranking_v2 WHERE idioma=? "
                    "GROUP BY nombre, idioma, nivel ORDER BY p DESC LIMIT ?", (idioma, n)).fetchall()
            return con.execute(
                "SELECT nombre, idioma, nivel, MAX(puntaje) p FROM ranking_v2 "
                "GROUP BY nombre, idioma, nivel ORDER BY p DESC LIMIT ?", (n,)).fetchall()
    except Exception:
        return []

# ------------------------------------------------------------------
# Carga de modelos (una sola vez; quedan en memoria)
# ------------------------------------------------------------------
@st.cache_resource(show_spinner="Cargando modelo de voz (solo la primera vez)…")
def cargar_whisper():
    return _con_respaldo(_crear_whisper)


@st.cache_resource(show_spinner="Cargando modelo de guaraní (la primera vez tarda)…")
def cargar_mms():
    return _con_respaldo(_crear_mms)


def leer_wav(audio_bytes: bytes, sr_destino: int = 16000):
    """Lee el WAV del micrófono y lo pasa a mono 16 kHz (sin librerías extra)."""
    import wave
    import numpy as np
    with wave.open(io.BytesIO(audio_bytes), "rb") as w:
        canales, ancho, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        crudo = w.readframes(w.getnframes())
    tipo = {1: np.uint8, 2: np.int16, 4: np.int32}[ancho]
    x = np.frombuffer(crudo, dtype=tipo).astype(np.float32)
    x = (x - 128) / 128 if ancho == 1 else x / float(2 ** (8 * ancho - 1))
    if canales > 1:
        x = x.reshape(-1, canales).mean(axis=1)
    if sr != sr_destino and len(x) > 1:
        n = int(len(x) * sr_destino / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x.astype(np.float32)


def transcribir(audio_bytes: bytes, idioma: str) -> str:
    audio = leer_wav(audio_bytes)
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
# Estado y utilidades de interfaz
# ------------------------------------------------------------------
S = st.session_state
for k, v in {"vista": "inicio", "n_audio": 0, "vistas": {}, "cfg_modo": "Solo",
             "cfg_idioma": "Español", "cfg_nivel": "Normal", "cfg_nombre": "",
             "cfg_n": 2, "cfg_rondas": 3, "cfg_filtro": "Todos"}.items():
    S.setdefault(k, v)
for i in range(6):
    S.setdefault(f"jug_{i}", f"Jugador {i + 1}")
# Conserva la configuración aunque su panel no se dibuje en una pantalla
for k in [k for k in S.keys() if str(k).startswith(("cfg_", "jug_"))]:
    S[k] = S[k]


def ir(vista):
    S.vista = vista
    st.rerun()


def elegir_frase(idioma, nivel):
    """Frase que todavía no salió; al agotarlas reinicia sin repetir la última."""
    vistas = S.vistas.setdefault(f"{idioma}|{nivel}", [])
    pool = TEXTOS[idioma][nivel]
    pendientes = [f for f in pool if f not in vistas]
    if not pendientes:
        ultima = vistas[-1] if vistas else None
        vistas.clear()
        pendientes = [f for f in pool if f != ultima] or pool
    f = random.choice(pendientes)
    vistas.append(f)
    return f


def chip(texto, clase):
    return f'<span class="chip {clase}">{html.escape(texto)}</span>'


def chips(idioma, nivel):
    ci = {"Español": "c-esp", "Inglés": "c-ing", "Guaraní": "c-gua"}.get(idioma, "c-otro")
    return chip(idioma, ci) + chip(nivel, f"n-{nivel.lower()}")


def pintar(palabras) -> str:
    return "".join(f'<span class="w {p.estado}">{html.escape(p.texto)}</span> '
                   for p in palabras)


def mensaje(p):
    if p >= 85:
        return st.success, "¡Excelente! 🎉 Lectura casi perfecta."
    if p >= 60:
        return st.warning, "¡Buen intento! 👍 Repasa las palabras marcadas."
    return st.error, "Sigue practicando 💪 Prueba de nuevo, despacio."


def nivel_actual():
    return S.niveles_ronda[S.ronda] if S.modo == "Partida local" else S.nivel_actual


def frase_actual():
    return S.frases[S.ronda] if S.modo == "Partida local" else S.frase


def promedio(lista):
    return sum(lista) / len(lista) if lista else 0.0

# ------------------------------------------------------------------
# Panel izquierdo: configuración (o resumen mientras se juega)
# ------------------------------------------------------------------
def empezar_partida():
    modo = S.cfg_modo or "Solo"
    idioma = S.cfg_idioma or "Español"
    nivel = S.cfg_nivel or "Normal"
    S.modo, S.idioma, S.nivel_sel = modo, idioma, nivel
    S.turno, S.ronda = 0, 0
    if modo == "Solo":
        nombre = S.cfg_nombre.strip()
        if not nombre:
            st.error("Escribe tu nombre para comenzar.")
            return
        S.usuario = nombre
        S.jugadores = [nombre]
        S.nivel_actual = "Normal" if nivel == "Progresiva" else nivel
        S.racha = []
        S.frase = elegir_frase(idioma, S.nivel_actual)
    else:
        n, rondas = S.cfg_n or 2, S.cfg_rondas or 3
        limpios, vistos = [], {}
        for i in range(n):
            nom = S[f"jug_{i}"].strip() or f"Jugador {i + 1}"
            vistos[nom] = vistos.get(nom, 0) + 1
            limpios.append(nom if vistos[nom] == 1 else f"{nom} ({vistos[nom]})")
        if nivel == "Progresiva":
            niveles = {3: ["Normal", "Difícil", "Profesional"],
                       5: ["Normal", "Normal", "Difícil", "Difícil", "Profesional"]}[rondas]
        else:
            niveles = [nivel] * rondas
        usadas, frases = {}, []
        for niv in niveles:
            libres = [f for f in TEXTOS[idioma][niv] if f not in usadas.get(niv, [])]
            f = random.choice(libres)
            usadas.setdefault(niv, []).append(f)
            frases.append(f)
        S.jugadores, S.rondas_total = limpios, rondas
        S.niveles_ronda, S.frases = niveles, frases
        S.puntajes = {j: [] for j in limpios}
    S.n_audio += 1
    ir("jugar")


def panel_izquierdo():
    if S.vista in ("jugar", "resultado"):
        st.markdown('<div class="panel-t">🎮 Partida</div>', unsafe_allow_html=True)
        niv = nivel_actual()
        extra = chip("Progresiva", "n-progresiva") if S.nivel_sel == "Progresiva" else ""
        st.markdown(f'<div class="tarjeta">{chips(S.idioma, niv)}{extra}<br>'
                    f'<small>{S.modo}</small></div>', unsafe_allow_html=True)
        if S.modo == "Partida local":
            filas = "".join(
                f'<div class="fila-rank"><span>{html.escape(j)}</span>'
                f'<b>{promedio(p):.0f}%</b></div>' for j, p in S.puntajes.items())
            st.markdown(f'<div class="tarjeta">{filas}</div>', unsafe_allow_html=True)
        if st.button("⏹ Terminar"):
            ir("inicio")
        return

    st.markdown('<div class="panel-t">⚙️ Configuración</div>', unsafe_allow_html=True)
    st.pills("Modo", ["Solo", "Partida local"], key="cfg_modo")
    st.pills("Idioma", IDIOMAS, key="cfg_idioma")
    st.pills("Dificultad", OPCIONES_NIVEL, key="cfg_nivel")
    st.caption(DESCRIPCION.get(S.cfg_nivel or "Normal", ""))
    if (S.cfg_modo or "Solo") == "Partida local":
        st.pills("Jugadores", [2, 3, 4, 5, 6], key="cfg_n")
        for i in range(S.cfg_n or 2):
            st.text_input(f"Jugador {i + 1}", key=f"jug_{i}")
        st.pills("Rondas", [3, 5], key="cfg_rondas")
    else:
        st.text_input("Tu nombre", key="cfg_nombre")
    if st.button("▶ Empezar", type="primary"):
        empezar_partida()

# ------------------------------------------------------------------
# Panel central: bienvenida, juego, resultado y podio
# ------------------------------------------------------------------
def vista_inicio():
    st.markdown("""
<div class="tarjeta fuerte">
  <h3>¿Cómo funciona?</h3>
  <div class="paso"><b>1</b> Elige el idioma y la dificultad a la izquierda.</div>
  <div class="paso"><b>2</b> Toca <i>Empezar</i> y lee la frase en voz alta con el micrófono.</div>
  <div class="paso"><b>3</b> La IA te corrige y marca los errores con colores:</div>
  <div class="frase" style="font-size:1.2rem">
    El sol <span class="w mal">brila</span> <span class="w salto">intensamente</span> sobre las colinas.
  </div>
  <div class="leyenda"><span class="w salto">salteó o abrevió</span>
  <span class="w mal">dijo mal</span> sin marca = bien</div>
</div>
<div class="tarjeta">
  <b>Dificultad progresiva:</b> empiezas con frases cortas y, si lees bien, la app sube sola a
  frases más largas y complejas.
</div>
""", unsafe_allow_html=True)
    with st.expander("📲 Instalar como app"):
        st.markdown(
            "**En la computadora (sin internet):** ejecuta `INSTALAR.bat` una vez y usa el "
            "acceso directo **Lectura** del escritorio.\n\n"
            "**En el celular (con internet):** Android → menú ⋮ → *Instalar app* o *Agregar a "
            "pantalla principal*. iPhone → Compartir → *Agregar a inicio*.\n\n"
            "**En el celular SIN internet:** en la computadora ejecuta `INICIAR_CELULAR.bat`, "
            "conecta el celular a la misma red Wi-Fi o hotspot y abre el link (o el QR) que aparece.")


def vista_jugar():
    jugador = S.jugadores[S.turno]
    niv = nivel_actual()
    if S.modo == "Partida local":
        st.markdown(f"### Turno de {html.escape(jugador)}")
        st.markdown(chips(S.idioma, niv) + chip(f"Ronda {S.ronda + 1}/{S.rondas_total}", "c-otro"),
                    unsafe_allow_html=True)
    else:
        st.markdown(f"### Hola, {html.escape(jugador)}")
        st.markdown(chips(S.idioma, niv), unsafe_allow_html=True)

    st.markdown(f'<div class="tarjeta fuerte"><div class="frase">{html.escape(frase_actual())}</div></div>',
                unsafe_allow_html=True)
    audio = st.audio_input("Toca el micrófono, lee la frase y toca de nuevo para parar",
                           key=f"audio_{S.n_audio}")
    with st.expander("¿No funciona el micrófono? Escribe lo que leíste"):
        manual = st.text_area("Texto", label_visibility="collapsed", key=f"manual_{S.n_audio}")

    if st.button("Evaluar", type="primary"):
        if audio is None and not manual.strip():
            st.warning("Graba tu lectura primero.")
            return
        try:
            with st.spinner("Analizando tu lectura…"):
                dicho = manual.strip() if audio is None else transcribir(audio.getvalue(), S.idioma)
        except ImportError as e:
            if S.idioma == "Guaraní":
                st.error("El guaraní necesita las librerías transformers y torch. "
                         "Prueba con español o inglés.")
            else:
                st.error(f"Falta instalar una librería: {e}. Revisa requirements.txt.")
            return
        except Exception as e:
            st.error(f"No se pudo analizar el audio: {e}. Si es la primera vez en esta "
                     "computadora, necesita internet para descargar el modelo.")
            return

        palabras, puntaje, extras = evaluar(frase_actual(), dicho, UMBRALES[niv])
        aviso = ""
        if S.modo == "Solo" and S.nivel_sel == "Progresiva":
            S.racha.append(puntaje)
            if len(S.racha) >= 3:
                if promedio(S.racha) >= SUBIR_CON and S.nivel_actual != "Profesional":
                    S.nivel_actual = NIVELES[NIVELES.index(S.nivel_actual) + 1]
                    aviso = f"🚀 ¡Subes al nivel {S.nivel_actual}!"
                elif S.nivel_actual != "Profesional":
                    aviso = f"Sigues en el nivel {S.nivel_actual}: tu promedio fue {promedio(S.racha):.0f}%."
                S.racha = []
        S.resultado = dict(palabras=palabras, puntaje=puntaje, dicho=dicho,
                           extras=extras, jugador=jugador, nivel=niv, aviso=aviso)
        guardar_puntaje(jugador, S.idioma, niv, puntaje)
        if S.modo == "Partida local":
            S.puntajes[jugador].append(puntaje)
        ir("resultado")

    if S.modo == "Solo" and st.button("Otra frase"):
        S.frase = elegir_frase(S.idioma, S.nivel_actual)
        S.n_audio += 1
        st.rerun()


def vista_resultado():
    r = S.resultado
    st.markdown(f"### {html.escape(r['jugador'])}")
    st.markdown(chips(S.idioma, r["nivel"]), unsafe_allow_html=True)
    st.metric("Acierto", f"{r['puntaje']}%")
    aviso, texto = mensaje(r["puntaje"])
    aviso(texto)
    if r["aviso"]:
        st.info(r["aviso"])

    st.markdown(f'<div class="tarjeta fuerte"><div class="frase">{pintar(r["palabras"])}</div></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="leyenda"><span class="w salto">salteó / abrevió</span> '
                '<span class="w mal">dijo mal</span></div>', unsafe_allow_html=True)
    if r["extras"]:
        st.caption(f"Agregaste {r['extras']} palabra(s) que no estaban en el texto.")
    with st.expander("Lo que entendió la IA"):
        st.write(r["dicho"] or "(no se escuchó nada)")

    if S.modo == "Solo":
        c1, c2 = st.columns(2)
        if c1.button("🔄 Repetir esta frase"):
            S.n_audio += 1
            ir("jugar")
        if c2.button("➡️ Siguiente frase", type="primary"):
            S.frase = elegir_frase(S.idioma, S.nivel_actual)
            S.n_audio += 1
            ir("jugar")
    else:
        ultimo = S.turno == len(S.jugadores) - 1 and S.ronda == S.rondas_total - 1
        if st.button("🏁 Ver resultados" if ultimo else "Continuar ➡️", type="primary"):
            S.n_audio += 1
            S.turno += 1
            if S.turno >= len(S.jugadores):
                S.turno, S.ronda = 0, S.ronda + 1
            ir("final" if S.ronda >= S.rondas_total else "jugar")


def vista_final():
    st.markdown("### 🏁 Resultados de la partida")
    tabla = sorted(((promedio(p), j) for j, p in S.puntajes.items()), reverse=True)
    medallas = ["🥇", "🥈", "🥉"]
    for i, (prom, j) in enumerate(tabla):
        m = medallas[i] if i < 3 else f"#{i + 1}"
        st.markdown(f'<div class="tarjeta fuerte"><b>{m} {html.escape(j)}</b> — {prom:.1f}%</div>',
                    unsafe_allow_html=True)
    if tabla:
        st.success(f"¡Ganó {tabla[0][1]}! 🎉")
    st.caption("Cambia la configuración a la izquierda para jugar otra partida.")

# ------------------------------------------------------------------
# Panel derecho: ranking
# ------------------------------------------------------------------
def panel_derecho():
    st.markdown('<div class="panel-t">🏆 Ranking</div>', unsafe_allow_html=True)
    st.pills("Filtro", ["Todos"] + IDIOMAS, key="cfg_filtro", label_visibility="collapsed")
    filtro = S.cfg_filtro or "Todos"
    filas = top(8, None if filtro == "Todos" else filtro)
    if not filas:
        st.caption("Todavía no hay puntajes.")
    medallas = ["🥇", "🥈", "🥉"]
    html_filas = ""
    for i, (n, idi, niv, p) in enumerate(filas):
        m = medallas[i] if i < 3 else f"#{i + 1}"
        html_filas += (f'<div class="fila-rank"><span>{m} <b>{html.escape(n)}</b><br>'
                       f'{chips(idi, niv)}</span><b>{p:.0f}%</b></div>')
    if html_filas:
        st.markdown(f'<div class="tarjeta">{html_filas}</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------
# Pantalla principal
# ------------------------------------------------------------------
st.markdown('<div class="encabezado"><div class="logo">🎙️</div><div><h1>Lectura</h1>'
            '<p>Lee en voz alta y la IA te corrige · Español · English · Guaraní</p></div></div>',
            unsafe_allow_html=True)

col_izq, col_cen, col_der = st.columns([1.05, 2.1, 1.05], gap="large")
with col_izq:
    panel_izquierdo()
with col_cen:
    {"inicio": vista_inicio, "jugar": vista_jugar,
     "resultado": vista_resultado, "final": vista_final}.get(S.vista, vista_inicio)()
with col_der:
    panel_derecho()
