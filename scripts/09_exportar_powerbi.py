import csv
import json
from datetime import datetime
from pathlib import Path

from db import conectar


# Carpeta donde se guardaran los CSV
RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "powerbi_data"
SALIDA.mkdir(parents=True, exist_ok=True)


def aplanar(documento, prefijo=""):
    """
    Convierte documentos MongoDB a filas planas.
    Los subdocumentos se convierten en columnas.
    Las listas se guardan como texto JSON.
    """
    fila = {}

    for clave, valor in documento.items():
        nombre = f"{prefijo}_{clave}" if prefijo else clave

        if isinstance(valor, dict):
            fila.update(aplanar(valor, nombre))

        elif isinstance(valor, list):
            fila[nombre] = json.dumps(
                valor, ensure_ascii=False, default=str
            )

        elif isinstance(valor, datetime):
            fila[nombre] = valor.isoformat(sep=" ")

        elif valor is None:
            fila[nombre] = ""

        elif valor.__class__.__name__ == "ObjectId":
            fila[nombre] = str(valor)

        else:
            fila[nombre] = valor

    return fila


def guardar_csv(nombre_archivo, filas, columnas_fijas=None):
    ruta = SALIDA / f"{nombre_archivo}.csv"

    if columnas_fijas is not None:
        columnas = columnas_fijas
    else:
        columnas = sorted({
            columna
            for fila in filas
            for columna in fila
        })

        if not columnas:
            columnas = ["_id"]

    with ruta.open(
        "w", newline="", encoding="utf-8-sig"
    ) as archivo:
        escritor = csv.DictWriter(
            archivo,
            fieldnames=columnas,
            extrasaction="ignore",
        )
        escritor.writeheader()
        escritor.writerows(filas)

    print(f"[OK] {nombre_archivo}.csv: {len(filas)} registros")

def ejecutar():
    client, db = conectar()

    try:
        colecciones = db.list_collection_names()

        campana_db = (
            "campañas"
            if "campañas" in colecciones
            else "campanas"
        )

        # Exportar las colecciones principales.
        tablas = [
            ("usuarios", "usuarios"),
            ("publicaciones", "publicaciones"),
            ("reacciones", "reacciones"),
            ("comentarios", "comentarios"),
            ("campanas", campana_db),
        ]

        for archivo, coleccion in tablas:
            documentos = list(db[coleccion].find({}))
            filas = [aplanar(d) for d in documentos]
            guardar_csv(archivo, filas)

        # Crear tabla intermedia para relacionar campañas
        # con las publicaciones que tiene asociadas.
        campanas = list(db[campana_db].find({}))
        relacion = []

        for campana in campanas:
            campana_id = str(campana["_id"])

            for publicacion_id in (
                campana.get("publicaciones_asociadas") or []
            ):
                relacion.append({
                    "campana_id": campana_id,
                    "publicacion_id": str(publicacion_id),
                })

        guardar_csv(
    "campana_publicacion",
    relacion,
    columnas_fijas=["campana_id", "publicacion_id"]
)

        # Exportar las métricas de rendimiento, si existen.
        if "metricas_de_rendimiento" in colecciones:
            documentos = list(
                db.metricas_de_rendimiento.find({})
            )
            filas = [aplanar(d) for d in documentos]
            guardar_csv("metricas_de_rendimiento", filas)

        print("\nExportacion finalizada.")
        print(f"Archivos guardados en: {SALIDA}")

    finally:
        client.close()


if __name__ == "__main__":
    ejecutar()