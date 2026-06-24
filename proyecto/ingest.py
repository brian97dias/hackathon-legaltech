# ingest.py
# Extrae texto de PDF, limpia headers/pies, detecta CAPÍTULOS y ARTÍCULOS, y guarda en SQLite

import re, sys, uuid, sqlite3
import fitz  # PyMuPDF
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = str(Path(__file__).with_name("app.db"))

# Regex ANCLADOS al inicio de línea para evitar falsos positivos dentro de párrafos
CAP_RE = re.compile(r"(?m)^\s*CAP[IÍ]TULO\s+([IVXLCDM]+)\s*(?:\n+([^\n]+))?")
ART_RE = re.compile(r"(?m)^\s*(?:Art[íi]culo|ART[ÍI]CULO)\s+(\d+)\s*[º°\.]?\s*(.*)$")

def read_pdf_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    pages = [p.get_text("text") for p in doc]
    text = "\n".join(pages)
    text = strip_headers_footers(text)  # Limpia cabeceras/pies y numeración de página
    return normalize_text(text)

def strip_headers_footers(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        t = ln.strip()
        # Cabeceras/pies típicos del PDF del Decreto
        if t in {"EVA - Gestor Normativo", "Decreto 1443 de 2014"}:
            continue
        if t.startswith("Departamento Administrativo de la Función Pública"):
            continue
        # Números de página sueltos tipo "3", "12"
        if re.fullmatch(r"\d{1,3}", t):
            continue
        lines.append(ln)
    return "\n".join(lines)

def normalize_text(text: str) -> str:
    # Unir palabras cortadas por guion al final de línea: "ex-\n traordinario" -> "extraordinario"
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)
    # Colapsar espacios múltiples
    text = re.sub(r"[ \t]+", " ", text)
    # Normalizar saltos excesivos
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def find_blocks(text: str, pattern: re.Pattern):
    matches = list(pattern.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((start, end, m))
    return blocks

def parse_outline(text: str):
    # 1) Capítulos
    chapter_blocks = find_blocks(text, CAP_RE)
    chapters = []
    for start, end, m in chapter_blocks:
        roman = m.group(1).strip()
        title = (m.group(2) or "").strip()
        chapters.append({"roman": roman, "title": title, "start": start, "end": end})

    chapter_starts = sorted([b[0] for b in chapter_blocks])

    # 2) Artículos (candidatos)
    art_blocks_all = find_blocks(text, ART_RE)
    cand_articles = []
    for i, (start, end, m) in enumerate(art_blocks_all):
        num = int(m.group(1))
        title = (m.group(2) or "").strip()

        # Cortar el cuerpo por el próximo Artículo o el próximo Capítulo, lo que ocurra primero
        next_art_start = art_blocks_all[i + 1][0] if i + 1 < len(art_blocks_all) else len(text)
        next_chap_start = min((cs for cs in chapter_starts if cs > start), default=len(text))
        true_end = min(next_art_start, next_chap_start)
        body = text[m.end():true_end].strip()

        chap_roman = None
        for ch in chapters:
            if start >= ch["start"] and start < ch["end"]:
                chap_roman = ch["roman"]
                break

        # Limpia puntos/guiones iniciales en el título
        title = re.sub(r"^[\s\.:·-]+", "", title)

        cand_articles.append({
            "number": num,
            "title": title,
            "chapter_roman": chap_roman,
            "content": body,
        })

    # 3) Filtro y deduplicación
    def is_citation(t: str) -> bool:
        t0 = (t or "").strip().lower()
        return t0.startswith(("de la ley", "del decreto", "de la constitución"))

    def score(a):
        s = 0
        if a["chapter_roman"]:
            s += 2
        if not is_citation(a["title"]):
            s += 1
        s += min(len(a["content"]) // 500, 2)  # prioriza cuerpo más sustancioso
        return s

    seen = {}
    for a in cand_articles:
        # Ignora números imposibles (citas) → permitimos 1..200 por seguridad
        if not (1 <= a["number"] <= 200):
            continue
        cur = seen.get(a["number"])
        if cur is None or score(a) > score(cur):
            seen[a["number"]] = a

    # Este decreto tiene 38 artículos; forzamos 1..38
    max_expected = 38
    articles = [seen[n] for n in range(1, max_expected + 1) if n in seen]

    return chapters, articles

def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS documents(
            id TEXT PRIMARY KEY,
            title TEXT,
            source_path TEXT,
            created_at TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chapters(
            id TEXT PRIMARY KEY,
            document_id TEXT,
            roman TEXT,
            title TEXT,
            start_idx INTEGER,
            end_idx INTEGER,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS articles(
            id TEXT PRIMARY KEY,
            document_id TEXT,
            number INTEGER,
            title TEXT,
            chapter_roman TEXT,
            content TEXT,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );
        """
    )
    conn.commit()

def save_to_db(pdf_path: str, title_hint: str | None = None):
    text = read_pdf_text(pdf_path)
    chapters, articles = parse_outline(text)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    cur = conn.cursor()

    doc_id = str(uuid.uuid4())
    title = title_hint or Path(pdf_path).name
    cur.execute(
        "INSERT INTO documents(id, title, source_path, created_at) VALUES(?,?,?,?)",
        (doc_id, title, str(pdf_path), datetime.now(timezone.utc).isoformat()),
    )

    for ch in chapters:
        cur.execute(
            "INSERT INTO chapters(id, document_id, roman, title, start_idx, end_idx) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), doc_id, ch["roman"], ch["title"], ch["start"], ch["end"]),
        )

    for art in articles:
        cur.execute(
            "INSERT INTO articles(id, document_id, number, title, chapter_roman, content) VALUES(?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                doc_id,
                art["number"],
                art["title"],
                art["chapter_roman"],
                art["content"],
            ),
        )
    conn.commit()

    # resumen rápido
    cur.execute("SELECT COUNT(*) FROM chapters WHERE document_id=?", (doc_id,))
    n_ch = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM articles WHERE document_id=?", (doc_id,))
    n_art = cur.fetchone()[0]
    conn.close()
    return doc_id, n_ch, n_art

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python ingest.py <ruta_al_pdf> [titulo_opcional]")
        sys.exit(1)
    pdf_path = sys.argv[1]
    title_hint = sys.argv[2] if len(sys.argv) >= 3 else None
    doc_id, n_ch, n_art = save_to_db(pdf_path, title_hint)
    print(f"OK. document_id={doc_id} capítulos={n_ch} artículos={n_art} DB={DB_PATH}")
