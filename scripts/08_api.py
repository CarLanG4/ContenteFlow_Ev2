"""
08_api.py - API REST de ContentFlow (FastAPI + PyMongo) para consumir MongoDB Atlas desde Postman.

Cubre: IE3 (conectividad), IE4/IE5/IE6 (consultas con tiempo y volumen), IE7/IE8 (CRUD),
       IE9/IE10 (integración con una aplicación) e IE11 (trazabilidad y control de errores).

Ejecutar desde la raíz del proyecto, con el entorno virtual activo:
    python scripts/08_api.py
Documentación interactiva (Swagger): http://127.0.0.1:8000/docs
"""
import contextlib
import logging
import os
import secrets
import sys
import time
import uuid
from datetime import date, datetime, time as hora, timezone
from pathlib import Path
from typing import List, Literal, Optional

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Path as RutaParam, Query, Request, Response, Security
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException

from db import conectar

# ------------------------------------------------------------------ configuración
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    sys.exit("ERROR: falta API_KEY en el archivo .env (ej.: API_KEY=contentflow-clave-2026)")

PREFIJO_API = "pub_api_"      # las publicaciones creadas por esta API llevan este prefijo
PROTEGER_ORIGINALES = True    # True: PUT/PATCH/DELETE solo sobre publicaciones creadas por la API

CATEGORIAS = Literal["Tecnología", "Cultura", "Educación", "Deportes", "Entretenimiento"]
TIPOS = Literal["Imagen", "Video", "Texto"]
CAMPOS_PUBLICACION = {"usuario_id", "contenido", "fecha_publicacion", "categoria", "hashtags", "tipo"}

# ------------------------------------------------------------------ trazabilidad (IE11)
LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "api.log"
LOG_FILE.parent.mkdir(exist_ok=True)
log = logging.getLogger("contentflow.api")
log.setLevel(logging.INFO)
if not log.handlers:
    formato = logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(formato)
        log.addHandler(h)

client, db = conectar()
CAMP = "campañas" if "campañas" in db.list_collection_names() else "campanas"

app = FastAPI(title="ContentFlow API", version="1.0",
              description="API REST sobre MongoDB Atlas para la Evaluación Parcial N°2 (BDY0101).")
api_key_header = APIKeyHeader(name="x-api-key", auto_error=False, description="Clave definida en API_KEY del .env")


def ahora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def exigir_api_key(clave: Optional[str] = Security(api_key_header)):
    """Autenticación por cabecera x-api-key."""
    if not clave or not secrets.compare_digest(clave.encode(), API_KEY.encode()):
        raise HTTPException(401, "API key ausente o inválida: envía la cabecera x-api-key")


def auditar(request: Request, accion: str, detalle: str):
    log.info(f"{request.state.rid} | AUDITORIA | {accion} | {detalle}")


def error(request: Request, status: int, codigo: str, detalle, **extra):
    cuerpo = {"error": codigo, "detalle": detalle, **extra,
              "ruta": request.url.path, "request_id": getattr(request.state, "rid", "-")}
    return JSONResponse(jsonable_encoder(cuerpo), status_code=status)


CODIGOS = {400: "solicitud_invalida", 401: "no_autorizado", 403: "operacion_no_permitida",
           404: "no_encontrado", 405: "metodo_no_permitido"}


@app.middleware("http")
async def trazabilidad(request: Request, call_next):
    """Asigna un ID a cada petición y registra método, ruta, estado y tiempo."""
    request.state.rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    try:
        respuesta = await call_next(request)
    except Exception as e:  # error no previsto: se registra y se responde de forma controlada
        log.exception(f"{request.state.rid} | ERROR NO CONTROLADO | {e}")
        respuesta = error(request, 500, "error_interno", "Error interno del servidor")
    ms = (time.perf_counter() - t0) * 1000
    respuesta.headers["X-Request-ID"] = request.state.rid
    respuesta.headers["X-Process-Time-ms"] = f"{ms:.0f}"
    consulta = f"?{request.url.query}" if request.url.query else ""
    log.info(f"{request.state.rid} | {request.method} {request.url.path}{consulta} | {respuesta.status_code} | {ms:.0f} ms")
    return respuesta


@app.exception_handler(RequestValidationError)
async def al_validar(request: Request, exc: RequestValidationError):
    problemas = [{"campo": ".".join(str(p) for p in e["loc"]), "mensaje": e["msg"]} for e in exc.errors()]
    return error(request, 400, "validacion", problemas)


@app.exception_handler(HTTPException)
async def al_fallar(request: Request, exc: HTTPException):
    return error(request, exc.status_code, CODIGOS.get(exc.status_code, "error"), exc.detail)


@app.exception_handler(PyMongoError)
async def al_fallar_mongo(request: Request, exc: PyMongoError):
    log.error(f"{request.state.rid} | ERROR MONGODB | {exc}")
    return error(request, 503, "base_de_datos_no_disponible", "No se pudo completar la operación en MongoDB")


# ------------------------------------------------------------------ modelos (validación de entradas)
def _limpiar_contenido(v):
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("el contenido no puede estar vacío")
    return v


def _validar_hashtags(v):
    if v is None:
        return v
    limpios = []
    for t in v:
        t = t.strip().lower()
        if not (t.startswith("#") and len(t) > 1 and t[1:].isalnum()):
            raise ValueError(f"hashtag inválido '{t}': debe empezar con # y llevar letras o números")
        if t not in limpios:
            limpios.append(t)
    return limpios


class PublicacionEditable(BaseModel):
    """Campos que se pueden reemplazar con PUT."""
    model_config = ConfigDict(extra="forbid")
    contenido: str = Field(min_length=1, max_length=500)
    categoria: CATEGORIAS
    tipo: TIPOS
    hashtags: List[str] = Field(default_factory=list, max_length=10)

    @field_validator("contenido")
    @classmethod
    def validar_contenido(cls, v):
        return _limpiar_contenido(v)

    @field_validator("hashtags")
    @classmethod
    def validar_hashtags(cls, v):
        return _validar_hashtags(v)


class PublicacionNueva(PublicacionEditable):
    usuario_id: str = Field(pattern=r"^user_\d+$", description="Ej.: user_1")


class PublicacionParcial(BaseModel):
    """Campos opcionales para PATCH."""
    model_config = ConfigDict(extra="forbid")
    contenido: Optional[str] = Field(default=None, min_length=1, max_length=500)
    categoria: Optional[CATEGORIAS] = None
    tipo: Optional[TIPOS] = None
    hashtags: Optional[List[str]] = Field(default=None, max_length=10)

    @field_validator("contenido")
    @classmethod
    def validar_contenido(cls, v):
        return _limpiar_contenido(v)

    @field_validator("hashtags")
    @classmethod
    def validar_hashtags(cls, v):
        return _validar_hashtags(v)


IdPublicacion = RutaParam(pattern=r"^pub_\w+$", description="Ej.: pub_1 o pub_api_ab12cd34")


# ------------------------------------------------------------------ utilidades
def buscar_o_404(pub_id: str):
    doc = db.publicaciones.find_one({"_id": pub_id})
    if doc is None:
        raise HTTPException(404, f"No existe la publicación '{pub_id}'")
    return doc


def exigir_editable(pub_id: str):
    if PROTEGER_ORIGINALES and not pub_id.startswith(PREFIJO_API):
        raise HTTPException(403, f"'{pub_id}' es un dato original del análisis y está protegido: "
                                 f"solo se modifican publicaciones creadas por la API ({PREFIJO_API}...)")


@contextlib.contextmanager
def cronometro():
    """Mide el tiempo de una operación en milisegundos: with cronometro() as t: ... ; t['ms']"""
    t = {}
    t0 = time.perf_counter()
    yield t
    t["ms"] = round((time.perf_counter() - t0) * 1000, 1)


def respuesta_consulta(nombre, filtros, filas, t, **extra):
    return {"consulta": nombre, "filtros": filtros, "registros": len(filas),
            "tiempo_ms": t["ms"], **extra, "datos": filas}


# ------------------------------------------------------------------ rutas públicas
@app.get("/health", tags=["Estado"])
def health():
    """Prueba de conectividad con MongoDB Atlas (no requiere API key)."""
    with cronometro() as t:
        client.admin.command("ping")
    return {"estado": "ok", "base_de_datos": db.name, "ping_ms": t["ms"],
            "colecciones": {c: db[c].estimated_document_count()
                            for c in ["usuarios", "publicaciones", "reacciones", "comentarios", CAMP]}}


# ------------------------------------------------------------------ rutas protegidas
api = APIRouter(dependencies=[Depends(exigir_api_key)])


# ---- Consultas de negocio (IE4, IE5, IE6)
@api.get("/metricas/publicaciones", tags=["Consultas de negocio"])
def metricas_publicaciones(categoria: Optional[CATEGORIAS] = None, limite: int = Query(10, ge=1, le=100)):
    """Publicaciones con más reacciones, con sus comentarios y likes de comentarios."""
    etapas = []
    if categoria:
        ids = db.publicaciones.distinct("_id", {"categoria": categoria})
        etapas.append({"$match": {"publicacion_id": {"$in": ids}}})
    etapas += [
        {"$group": {"_id": "$publicacion_id", "reacciones": {"$sum": 1}}},
        {"$sort": {"reacciones": -1, "_id": 1}},
        {"$limit": limite},
        {"$lookup": {"from": "publicaciones", "localField": "_id", "foreignField": "_id", "as": "pub"}},
        {"$unwind": "$pub"},
        {"$lookup": {"from": "comentarios", "localField": "_id", "foreignField": "publicacion_id", "as": "c"}},
        {"$project": {"_id": 0, "publicacion": "$_id", "categoria": "$pub.categoria", "tipo": "$pub.tipo",
                      "autor": "$pub.usuario_id", "reacciones": 1, "comentarios": {"$size": "$c"},
                      "likes_comentarios": {"$sum": "$c.likes"}}},
        {"$addFields": {"interacciones": {"$add": ["$reacciones", "$comentarios"]}}},
    ]
    with cronometro() as t:
        filas = list(db.reacciones.aggregate(etapas))
    return respuesta_consulta("metricas_publicaciones", {"categoria": categoria, "limite": limite}, filas, t)


@api.get("/segmentacion/usuarios", tags=["Consultas de negocio"])
def segmentacion_usuarios(genero: Optional[Literal["Femenino", "Masculino", "Otro"]] = None,
                          dispositivo: Optional[Literal["Móvil", "Desktop", "Tablet"]] = None):
    """Segmentos de audiencia (género x dispositivo) con su tamaño y porcentaje."""
    filtro = {k: v for k, v in {"genero": genero, "dispositivo": dispositivo}.items() if v}
    etapas = [
        {"$match": filtro},
        {"$group": {"_id": {"genero": "$genero", "dispositivo": "$dispositivo"}, "usuarios": {"$sum": 1}}},
        {"$sort": {"usuarios": -1, "_id.genero": 1, "_id.dispositivo": 1}},
        {"$project": {"_id": 0, "genero": "$_id.genero", "dispositivo": "$_id.dispositivo", "usuarios": 1}},
    ]
    with cronometro() as t:
        filas = list(db.usuarios.aggregate(etapas))
    total = sum(f["usuarios"] for f in filas) or 1
    for f in filas:
        f["porcentaje"] = round(f["usuarios"] * 100 / total, 1)
    return respuesta_consulta("segmentacion_usuarios", filtro, filas, t, total_usuarios=total)


@api.get("/campanas/impacto", tags=["Consultas de negocio"])
def impacto_campanas(limite: int = Query(10, ge=1, le=100)):
    """Reacciones y comentarios logrados por campaña frente a sus metas."""
    etapas = [
        {"$lookup": {"from": "reacciones", "localField": "publicaciones_asociadas",
                     "foreignField": "publicacion_id", "as": "reacs"}},
        {"$lookup": {"from": "comentarios", "localField": "publicaciones_asociadas",
                     "foreignField": "publicacion_id", "as": "coms"}},
        {"$project": {"nombre": 1, "objetivo": 1, "reacciones": {"$size": "$reacs"},
                      "comentarios": {"$size": "$coms"}, "meta_likes": "$metricas_objetivo.likes",
                      "meta_comentarios": "$metricas_objetivo.comentarios"}},
        {"$addFields": {
            "cumplimiento_likes_pct": {"$round": [{"$multiply": [
                {"$divide": ["$reacciones", {"$max": ["$meta_likes", 1]}]}, 100]}, 1]}}},
        {"$sort": {"reacciones": -1, "_id": 1}},
        {"$limit": limite},
    ]
    with cronometro() as t:
        filas = list(db[CAMP].aggregate(etapas))
    return respuesta_consulta("impacto_campanas", {"limite": limite}, filas, t)


# ---- CRUD de publicaciones (IE7, IE8, IE9, IE10)
@api.post("/publicaciones", status_code=201, tags=["CRUD publicaciones"])
def crear_publicacion(datos: PublicacionNueva, request: Request, response: Response):
    """CREATE: inserta una publicación. Valida que el autor exista."""
    if db.usuarios.count_documents({"_id": datos.usuario_id}, limit=1) == 0:
        raise HTTPException(400, f"El usuario '{datos.usuario_id}' no existe: no se puede crear la publicación")
    doc = {"_id": f"{PREFIJO_API}{uuid.uuid4().hex[:8]}", **datos.model_dump(),
           "fecha_publicacion": ahora()}
    resultado = db.publicaciones.insert_one(doc)
    auditar(request, "CREATE", f"{resultado.inserted_id} por {datos.usuario_id}")
    response.headers["Location"] = f"/publicaciones/{resultado.inserted_id}"
    return {"mensaje": "Publicación creada", "insertedId": resultado.inserted_id, "publicacion": doc}


@api.get("/publicaciones", tags=["CRUD publicaciones"])
def listar_publicaciones(
        categoria: Optional[CATEGORIAS] = None, tipo: Optional[TIPOS] = None,
        hashtag: Optional[str] = Query(None, pattern=r"^#?\w+$", description="Ej.: cultura (el # es opcional)"),
        usuario_id: Optional[str] = Query(None, pattern=r"^user_\d+$"),
        desde: Optional[date] = None, hasta: Optional[date] = None,
        campos: Optional[str] = Query(None, description="Proyección, ej.: categoria,tipo,fecha_publicacion"),
        orden: Literal["fecha_desc", "fecha_asc"] = "fecha_desc",
        limite: int = Query(10, ge=1, le=100), pagina: int = Query(1, ge=1)):
    """READ: lista con filtros, proyección, orden y paginación."""
    if desde and hasta and desde > hasta:
        raise HTTPException(400, "'desde' no puede ser posterior a 'hasta'")
    if hashtag:
        hashtag = "#" + hashtag.lstrip("#").lower()
    filtro = {k: v for k, v in {"categoria": categoria, "tipo": tipo, "hashtags": hashtag,
                                "usuario_id": usuario_id}.items() if v}
    if desde or hasta:
        filtro["fecha_publicacion"] = {}
        if desde:
            filtro["fecha_publicacion"]["$gte"] = datetime.combine(desde, hora.min)
        if hasta:
            filtro["fecha_publicacion"]["$lte"] = datetime.combine(hasta, hora.max)
    proyeccion = None
    if campos:
        pedidos = {c.strip() for c in campos.split(",") if c.strip()}
        invalidos = pedidos - CAMPOS_PUBLICACION
        if invalidos:
            raise HTTPException(400, f"Campos no válidos: {sorted(invalidos)}. Permitidos: {sorted(CAMPOS_PUBLICACION)}")
        proyeccion = {c: 1 for c in pedidos}
    sentido = -1 if orden == "fecha_desc" else 1
    with cronometro() as t:
        total = db.publicaciones.count_documents(filtro)
        filas = list(db.publicaciones.find(filtro, proyeccion).sort(
            [("fecha_publicacion", sentido), ("_id", 1)]).skip((pagina - 1) * limite).limit(limite))
    return respuesta_consulta("publicaciones", {**jsonable_encoder(filtro), "orden": orden}, filas, t,
                              total_coincidencias=total, pagina=pagina)


@api.get("/publicaciones/{pub_id}", tags=["CRUD publicaciones"])
def obtener_publicacion(pub_id: str = IdPublicacion):
    """READ: una publicación con sus conteos de reacciones y comentarios."""
    doc = buscar_o_404(pub_id)
    doc["resumen"] = {"reacciones": db.reacciones.count_documents({"publicacion_id": pub_id}),
                      "comentarios": db.comentarios.count_documents({"publicacion_id": pub_id})}
    return doc


@api.put("/publicaciones/{pub_id}", tags=["CRUD publicaciones"])
def reemplazar_publicacion(datos: PublicacionEditable, request: Request, pub_id: str = IdPublicacion):
    """UPDATE: reemplaza contenido, categoría, tipo y hashtags (todos obligatorios)."""
    buscar_o_404(pub_id)
    exigir_editable(pub_id)
    cambios = {**datos.model_dump(), "fecha_actualizacion": ahora()}
    r = db.publicaciones.update_one({"_id": pub_id}, {"$set": cambios})
    auditar(request, "UPDATE (PUT)", f"{pub_id} campos={sorted(datos.model_dump())}")
    return {"mensaje": "Publicación actualizada", "matched_count": r.matched_count,
            "modified_count": r.modified_count, "publicacion": db.publicaciones.find_one({"_id": pub_id})}


@api.patch("/publicaciones/{pub_id}", tags=["CRUD publicaciones"])
def modificar_publicacion(datos: PublicacionParcial, request: Request, pub_id: str = IdPublicacion):
    """UPDATE parcial: cambia solo los campos enviados."""
    cambios = datos.model_dump(exclude_unset=True)
    if not cambios or any(v is None for v in cambios.values()):
        raise HTTPException(400, "Envía al menos un campo a modificar y sin valores null")
    buscar_o_404(pub_id)
    exigir_editable(pub_id)
    r = db.publicaciones.update_one({"_id": pub_id}, {"$set": {**cambios, "fecha_actualizacion": ahora()}})
    auditar(request, "UPDATE (PATCH)", f"{pub_id} campos={sorted(cambios)}")
    return {"mensaje": "Publicación modificada", "matched_count": r.matched_count,
            "modified_count": r.modified_count, "publicacion": db.publicaciones.find_one({"_id": pub_id})}


@api.delete("/publicaciones/{pub_id}", tags=["CRUD publicaciones"])
def eliminar_publicacion(request: Request, pub_id: str = IdPublicacion):
    """DELETE en cascada: borra la publicación, sus reacciones y comentarios, y la quita de las campañas."""
    buscar_o_404(pub_id)
    exigir_editable(pub_id)
    rr = db.reacciones.delete_many({"publicacion_id": pub_id})
    rc = db.comentarios.delete_many({"publicacion_id": pub_id})
    rk = db[CAMP].update_many({"publicaciones_asociadas": pub_id}, {"$pull": {"publicaciones_asociadas": pub_id}})
    rp = db.publicaciones.delete_one({"_id": pub_id})
    auditar(request, "DELETE", f"{pub_id} reacciones={rr.deleted_count} comentarios={rc.deleted_count}")
    return {"mensaje": "Publicación eliminada", "deleted_count": rp.deleted_count,
            "reacciones_eliminadas": rr.deleted_count, "comentarios_eliminados": rc.deleted_count,
            "campanas_actualizadas": rk.modified_count}


app.include_router(api)

if __name__ == "__main__":
    print("\nContentFlow API en http://127.0.0.1:8000  (documentación: /docs)  |  detener con Ctrl+C")
    print(f"Registro de actividad: {LOG_FILE}\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
