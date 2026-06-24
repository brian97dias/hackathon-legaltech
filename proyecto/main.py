# main.py
# API FastAPI: subir PDF, listar documentos/capítulos/artículos. Sirve /ui con tu index.html

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sqlite3
from pathlib import Path
import tempfile, shutil

from ingest import save_to_db

DB_PATH = str(Path(__file__).with_name("app.db"))

app = FastAPI(title="SST Ingest API", version="0.2")

# CORS abierto para pruebas (restringe en producción)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sirve archivos estáticos desde ./web en /ui (coloca ahí tu index.html)
static_dir = Path(__file__).with_name("web")
if static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

# ----- Utilidad SQLite -----
def q(sql: str, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

# ----- Modelos -----
class Document(BaseModel):
    id: str
    title: str
    source_path: str
    created_at: str

class Chapter(BaseModel):
    id: str
    roman: str
    title: str | None

class Article(BaseModel):
    id: str
    number: int
    title: str | None
    chapter_roman: str | None
    content: str

# ----- Rutas básicas -----
@app.get("/")
def root():
    return {"ok": True, "message": "SST Ingest API lista. Usa /docs o /ui"}

@app.get("/health")
def health():
    return {"status": "up"}

# ----- Subida de PDF -----
@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...), title: str = Form(None)):
    # Guarda temporal y corre ingest
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    doc_id, n_ch, n_art = save_to_db(tmp_path, title_hint=title or file.filename)
    return {"document_id": doc_id, "chapters": n_ch, "articles": n_art}

# ----- Lectura -----
@app.get("/documents", response_model=list[Document])
def list_documents():
    return q("SELECT id, title, source_path, created_at FROM documents ORDER BY created_at DESC")

@app.get("/documents/{doc_id}/chapters", response_model=list[Chapter])
def list_chapters(doc_id: str):
    rows = q(
        """
        SELECT id, roman, title
        FROM chapters
        WHERE document_id=?
        ORDER BY rowid
        """,
        (doc_id,),
    )
    if not rows:
        doc = q("SELECT id FROM documents WHERE id=?", (doc_id,))
        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
    return rows

@app.get("/documents/{doc_id}/articles", response_model=list[Article])
def list_articles(doc_id: str, limit: int = 50, offset: int = 0):
    rows = q(
        """
        SELECT id, number, title, chapter_roman, content
        FROM articles
        WHERE document_id=?
        ORDER BY number
        LIMIT ? OFFSET ?
        """,
        (doc_id, limit, offset),
    )
    if not rows:
        doc = q("SELECT id FROM documents WHERE id=?", (doc_id,))
        if not doc:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
    return rows

@app.get("/documents/{doc_id}/articles/{number}", response_model=Article)
def get_article(doc_id: str, number: int):
    rows = q(
        """
        SELECT id, number, title, chapter_roman, content
        FROM articles
        WHERE document_id=? AND number=?
        LIMIT 1
        """,
        (doc_id, number),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Artículo no encontrado")
    return rows[0]
