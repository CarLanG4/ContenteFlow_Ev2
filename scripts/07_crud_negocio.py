import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pprint import pprint

from pymongo.errors import DuplicateKeyError
from db import conectar


# ============================================================
# CONTENTFLOW - PASO 7: CRUD DE NEGOCIO (IE7)
# Escenarios:
# 1. Gestion de usuarios
# 2. Ciclo de vida de una publicacion
# 3. Gestion de una campana
#
# Los documentos de prueba se eliminan al finalizar.
# Los documentos originales de ContentFlow no se modifican.
# ============================================================

# Conexion a MongoDB Atlas
client, db = conectar()

# Detectar el nombre de la coleccion de campanas.
colecciones_existentes = db.list_collection_names()

if "campañas" in colecciones_existentes:
    CAMP = "campañas"
else:
    CAMP = "campanas"

COLECCIONES = [
    "usuarios",
    "publicaciones",
    "reacciones",
    "comentarios",
    CAMP,
]

# Identificador unico para distinguir esta ejecucion.
RUN_ID = uuid.uuid4().hex[:10]
PREFIJO = f"demo_crud_{RUN_ID}"

ID_USUARIO = f"{PREFIJO}_usuario"
ID_USUARIO_DUPLICADO = f"{PREFIJO}_usuario_duplicado"
ID_PUBLICACION = f"{PREFIJO}_publicacion"
ID_CAMPANA = f"{PREFIJO}_campana"

ID_REACCIONES = [
    f"{PREFIJO}_reaccion_{i}" for i in range(1, 4)
]

ID_COMENTARIOS = [
    f"{PREFIJO}_comentario_{i}" for i in range(1, 3)
]

# Registrar exactamente lo creado para limpiar al final.
CREADOS = {
    "usuarios": [ID_USUARIO, ID_USUARIO_DUPLICADO],
    "publicaciones": [ID_PUBLICACION],
    "reacciones": ID_REACCIONES.copy(),
    "comentarios": ID_COMENTARIOS.copy(),
    CAMP: [ID_CAMPANA],
}

LOG = (
    Path(__file__).resolve().parent.parent
    / "logs"
    / "crud.log"
)
LOG.parent.mkdir(parents=True, exist_ok=True)

VERIFICACIONES = []
CONTEOS_INICIALES = {}


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def ahora():
    """Devuelve la fecha y hora actual compatible con MongoDB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def registrar_log(mensaje):
    """Guarda la actividad del CRUD en logs/crud.log."""
    with LOG.open("a", encoding="utf-8") as archivo:
        archivo.write(
            f"{ahora():%Y-%m-%d %H:%M:%S} | {mensaje}\n"
        )


def titulo(mensaje):
    print("\n" + "=" * 70)
    print(mensaje)
    print("=" * 70)
    registrar_log(mensaje)


def paso(mensaje):
    print(f"\n--- {mensaje}")
    registrar_log(mensaje)


def check(descripcion, condicion):
    """Registra y muestra el resultado de una comprobacion."""
    correcto = bool(condicion)
    VERIFICACIONES.append(correcto)

    estado = "OK" if correcto else "FALLO"

    print(f"[{estado}] {descripcion}")
    registrar_log(f"{estado} | {descripcion}")


def validar_campana(campana):
    """
    Valida que las fechas sean correctas y que todas
    las publicaciones asociadas existan en MongoDB.
    """
    errores = []

    if campana["fecha_fin"] < campana["fecha_inicio"]:
        errores.append(
            "La fecha de fin es anterior a la fecha de inicio"
        )

    publicaciones = list(
        dict.fromkeys(campana.get("publicaciones_asociadas", []))
    )

    existentes = db.publicaciones.count_documents({
        "_id": {"$in": publicaciones}
    })

    if existentes != len(publicaciones):
        errores.append(
            "Existen publicaciones asociadas que no se encuentran"
        )

    return errores


def limpiar_pruebas():
    """
    Elimina solo los registros identificados en esta ejecucion.
    Tambien comprueba las dependencias de la publicacion de prueba.
    """
    # Primero se eliminan las dependencias de la publicacion.
    db.reacciones.delete_many({
        "publicacion_id": ID_PUBLICACION
    })

    db.comentarios.delete_many({
        "publicacion_id": ID_PUBLICACION
    })

    # Despues se eliminan los documentos creados en cada coleccion.
    total = 0

    for nombre, ids in CREADOS.items():
        if ids:
            resultado = db[nombre].delete_many({
                "_id": {"$in": ids}
            })
            total += resultado.deleted_count

    return total


def obtener_referencias():
    """Obtiene usuarios y publicaciones originales de Atlas."""
    usuarios = list(
        db.usuarios.find({}, {"_id": 1}).limit(6)
    )

    publicaciones = list(
        db.publicaciones.find({}, {"_id": 1}).limit(3)
    )

    check(
        "Hay al menos seis usuarios para probar las referencias",
        len(usuarios) == 6
    )

    check(
        "Hay al menos tres publicaciones para probar las campanas",
        len(publicaciones) == 3
    )

    if len(usuarios) < 6 or len(publicaciones) < 3:
        raise RuntimeError(
            "Faltan usuarios o publicaciones en Atlas. "
            "Comprueba primero la carga de datos."
        )

    return (
        [u["_id"] for u in usuarios],
        [p["_id"] for p in publicaciones],
    )


# ============================================================
# ESCENARIO 1: GESTION DE USUARIOS
# ============================================================

def escenario_usuarios():
    titulo("ESCENARIO 1: GESTION DE USUARIOS")

    usuario = {
        "_id": ID_USUARIO,
        "nombre": "Usuario Demo CRUD",
        "correo": f"{PREFIJO}@contentflow.test",
        "pais": "Chile",
        "genero": "Otro",
        "fecha_registro": ahora(),
        "dispositivo": "Móvil",
    }

    # CREATE: crear un usuario.
    paso("CREATE: insertar un usuario de prueba")

    resultado = db.usuarios.insert_one(usuario)

    check(
        "El usuario fue creado con el ID esperado",
        resultado.inserted_id == ID_USUARIO
    )

    check(
        "El total de usuarios aumento en uno",
        db.usuarios.count_documents({})
        == CONTEOS_INICIALES["usuarios"] + 1
    )

    # Validacion: comprobar la restriccion de correo unico.
    paso("VALIDACION: intentar registrar el correo duplicado")

    try:
        db.usuarios.insert_one({
            **usuario,
            "_id": ID_USUARIO_DUPLICADO,
        })

        check(
            "MongoDB rechazo el correo duplicado",
            False
        )

    except DuplicateKeyError:
        check(
            "MongoDB rechazo el correo duplicado",
            True
        )

    # READ: consultar por correo y seleccionar campos.
    paso("READ: buscar usuario por correo y aplicar proyeccion")

    encontrado = db.usuarios.find_one(
        {"correo": usuario["correo"]},
        {
            "nombre": 1,
            "correo": 1,
            "pais": 1,
            "dispositivo": 1,
        },
    )

    pprint(encontrado, sort_dicts=False)

    check(
        "La consulta recupero al usuario correcto",
        encontrado is not None
        and encontrado["_id"] == ID_USUARIO
    )

    # UPDATE: modificar datos.
    paso("UPDATE: modificar pais y dispositivo con $set")

    resultado = db.usuarios.update_one(
        {"_id": ID_USUARIO},
        {
            "$set": {
                "pais": "Argentina",
                "dispositivo": "Tablet",
            }
        },
    )

    check(
        "La actualizacion encontro y modifico un documento",
        resultado.matched_count == 1
        and resultado.modified_count == 1
    )

    actualizado = db.usuarios.find_one({
        "_id": ID_USUARIO
    })

    check(
        "Los nuevos valores quedaron guardados",
        actualizado is not None
        and actualizado["pais"] == "Argentina"
        and actualizado["dispositivo"] == "Tablet"
    )

    # Validacion de una actualizacion repetida.
    resultado = db.usuarios.update_one(
        {"_id": ID_USUARIO},
        {"$set": {"dispositivo": "Tablet"}},
    )

    check(
        "Repetir el mismo valor no genera cambios",
        resultado.matched_count == 1
        and resultado.modified_count == 0
    )

    # Validacion de un usuario inexistente.
    resultado = db.usuarios.update_one(
        {"_id": f"{PREFIJO}_inexistente"},
        {"$set": {"pais": "Chile"}},
    )

    check(
        "Actualizar un usuario inexistente no modifica documentos",
        resultado.matched_count == 0
    )

    # DELETE: eliminar el usuario de demostracion.
    paso("DELETE: eliminar el usuario de prueba")

    resultado = db.usuarios.delete_one({
        "_id": ID_USUARIO
    })

    check(
        "Se elimino exactamente un usuario",
        resultado.deleted_count == 1
    )

    check(
        "El usuario eliminado ya no existe",
        db.usuarios.find_one({"_id": ID_USUARIO}) is None
    )


# ============================================================
# ESCENARIO 2: CICLO DE VIDA DE UNA PUBLICACION
# ============================================================

def escenario_publicacion(usuarios_ids):
    titulo("ESCENARIO 2: CICLO DE VIDA DE UNA PUBLICACION")

    autor_id = usuarios_ids[0]
    lectores = usuarios_ids[1:6]

    paso("VALIDACION: comprobar las referencias a usuarios")

    referencias = [autor_id] + lectores

    existentes = db.usuarios.count_documents({
        "_id": {"$in": referencias}
    })

    check(
        "El autor y los cinco lectores existen",
        existentes == 6
    )

    # CREATE: crear una publicacion.
    paso("CREATE: crear una nueva publicacion")

    publicacion = {
        "_id": ID_PUBLICACION,
        "usuario_id": autor_id,
        "contenido": "Lanzamiento de ContentFlow",
        "fecha_publicacion": ahora(),
        "categoria": "Tecnología",
        "hashtags": ["#innovacion"],
        "tipo": "Video",
    }

    resultado = db.publicaciones.insert_one(publicacion)

    check(
        "La publicacion fue creada",
        resultado.inserted_id == ID_PUBLICACION
    )

    # CREATE: insertar tres reacciones.
    paso("CREATE: insertar tres reacciones")

    reacciones = [
        {
            "_id": ID_REACCIONES[0],
            "usuario_id": lectores[0],
            "publicacion_id": ID_PUBLICACION,
            "tipo_reaccion": "Me gusta",
            "fecha": ahora(),
        },
        {
            "_id": ID_REACCIONES[1],
            "usuario_id": lectores[1],
            "publicacion_id": ID_PUBLICACION,
            "tipo_reaccion": "Me encanta",
            "fecha": ahora(),
        },
        {
            "_id": ID_REACCIONES[2],
            "usuario_id": lectores[2],
            "publicacion_id": ID_PUBLICACION,
            "tipo_reaccion": "Me gusta",
            "fecha": ahora(),
        },
    ]

    resultado = db.reacciones.insert_many(reacciones)

    check(
        "Se insertaron las tres reacciones",
        len(resultado.inserted_ids) == 3
    )

    # CREATE: insertar dos comentarios.
    paso("CREATE: insertar dos comentarios")

    comentarios = [
        {
            "_id": ID_COMENTARIOS[0],
            "usuario_id": lectores[3],
            "publicacion_id": ID_PUBLICACION,
            "comentario": "Muy buen contenido",
            "fecha": ahora(),
            "likes": 10,
        },
        {
            "_id": ID_COMENTARIOS[1],
            "usuario_id": lectores[4],
            "publicacion_id": ID_PUBLICACION,
            "comentario": "Gracias por compartir",
            "fecha": ahora(),
            "likes": 3,
        },
    ]

    resultado = db.comentarios.insert_many(comentarios)

    check(
        "Se insertaron los dos comentarios",
        len(resultado.inserted_ids) == 2
    )

    # UPDATE: cambiar el texto y agregar hashtags.
    paso("UPDATE: modificar el texto y los hashtags")

    resultado = db.publicaciones.update_one(
        {"_id": ID_PUBLICACION},
        {
            "$set": {
                "contenido": "Lanzamiento oficial de ContentFlow"
            },
            "$push": {
                "hashtags": "#aprendizaje"
            },
        },
    )

    check(
        "Se actualizo el contenido y se agrego un hashtag",
        resultado.matched_count == 1
        and resultado.modified_count == 1
    )

    # $addToSet impide repetir un elemento en un arreglo.
    resultado = db.publicaciones.update_one(
        {"_id": ID_PUBLICACION},
        {"$addToSet": {"hashtags": "#aprendizaje"}},
    )

    check(
        "$addToSet impidio repetir el hashtag",
        resultado.matched_count == 1
        and resultado.modified_count == 0
    )

    resultado = db.publicaciones.update_one(
        {"_id": ID_PUBLICACION},
        {"$addToSet": {"hashtags": "#futbol"}},
    )

    actual = db.publicaciones.find_one({
        "_id": ID_PUBLICACION
    })

    check(
        "Se agrego el nuevo hashtag sin duplicados",
        resultado.modified_count == 1
        and len(actual["hashtags"]) == 3
    )

    # UPDATE: aumentar en cinco los likes de cada comentario.
    paso("UPDATE: actualizar masivamente los likes con $inc")

    resultado = db.comentarios.update_many(
        {"publicacion_id": ID_PUBLICACION},
        {"$inc": {"likes": 5}},
    )

    check(
        "Se actualizaron los dos comentarios",
        resultado.matched_count == 2
        and resultado.modified_count == 2
    )

    # READ: combinar publicaciones, reacciones y comentarios.
    paso("READ: calcular metricas mediante aggregate y $lookup")

    resumen = list(db.publicaciones.aggregate([
        {
            "$match": {
                "_id": ID_PUBLICACION
            }
        },
        {
            "$lookup": {
                "from": "reacciones",
                "localField": "_id",
                "foreignField": "publicacion_id",
                "as": "reacciones",
            }
        },
        {
            "$lookup": {
                "from": "comentarios",
                "localField": "_id",
                "foreignField": "publicacion_id",
                "as": "comentarios",
            }
        },
        {
            "$project": {
                "_id": 1,
                "categoria": 1,
                "hashtags": 1,
                "cantidad_reacciones": {
                    "$size": "$reacciones"
                },
                "cantidad_comentarios": {
                    "$size": "$comentarios"
                },
                "likes_comentarios": {
                    "$sum": {
                        "$map": {
                            "input": "$comentarios",
                            "as": "comentario",
                            "in": "$$comentario.likes",
                        }
                    }
                },
            }
        },
    ]))

    pprint(resumen, sort_dicts=False)

    correcto = bool(resumen) and (
        resumen[0]["cantidad_reacciones"] == 3
        and resumen[0]["cantidad_comentarios"] == 2
        and resumen[0]["likes_comentarios"] == 23
    )

    check(
        "Metricas: tres reacciones, dos comentarios y 23 likes",
        correcto
    )

    # DELETE EN CASCADA: borrar dependencias y despues la publicacion.
    paso("DELETE EN CASCADA: eliminar reacciones y comentarios")

    eliminadas_reacciones = db.reacciones.delete_many({
        "publicacion_id": ID_PUBLICACION
    })

    eliminados_comentarios = db.comentarios.delete_many({
        "publicacion_id": ID_PUBLICACION
    })

    eliminada_publicacion = db.publicaciones.delete_one({
        "_id": ID_PUBLICACION
    })

    check(
        "Se borraron tres reacciones, dos comentarios y una publicacion",
        eliminadas_reacciones.deleted_count == 3
        and eliminados_comentarios.deleted_count == 2
        and eliminada_publicacion.deleted_count == 1
    )

    huerfanos = (
        db.reacciones.count_documents({
            "publicacion_id": ID_PUBLICACION
        })
        + db.comentarios.count_documents({
            "publicacion_id": ID_PUBLICACION
        })
    )

    check(
        "No quedaron reacciones ni comentarios huerfanos",
        huerfanos == 0
    )


# ============================================================
# ESCENARIO 3: GESTION DE UNA CAMPANA
# ============================================================

def escenario_campana(publicaciones_ids):
    titulo("ESCENARIO 3: GESTION DE UNA CAMPANA")

    inicio = ahora()

    campana = {
        "_id": ID_CAMPANA,
        "nombre": "Campaña Demo CRUD",
        "fecha_inicio": inicio,
        "fecha_fin": inicio + timedelta(days=30),
        "objetivo": "Aumentar la participacion de la audiencia",
        "publicaciones_asociadas": [],
        "metricas_objetivo": {
            "likes": 100,
            "comentarios": 50,
            "compartidos": 20,
        },
    }

    # VALIDACION: fecha de fin anterior a fecha de inicio.
    paso("VALIDACION: comprobar las fechas de la campana")

    campana_invalida = {
        **campana,
        "fecha_fin": inicio - timedelta(days=1),
    }

    errores = validar_campana(campana_invalida)

    check(
        "Se rechazo una campana con fechas incorrectas",
        len(errores) > 0
    )

    # CREATE: insertar campana.
    paso("CREATE: crear una campana")

    resultado = db[CAMP].insert_one(campana)

    check(
        "La campana fue creada",
        resultado.inserted_id == ID_CAMPANA
    )

    # VALIDACION: referencia a publicacion que no existe.
    paso("VALIDACION: comprobar publicaciones asociadas")

    campana_invalida = {
        **campana,
        "publicaciones_asociadas": [
            f"{PREFIJO}_publicacion_inexistente"
        ],
    }

    errores = validar_campana(campana_invalida)

    check(
        "Se rechazo una referencia a una publicacion inexistente",
        len(errores) > 0
    )

    # UPDATE: asociar publicaciones existentes sin repetirlas.
    paso("UPDATE: asociar publicaciones existentes con $addToSet")

    errores = validar_campana({
        **campana,
        "publicaciones_asociadas": publicaciones_ids,
    })

    check(
        "Las tres publicaciones asociadas existen",
        len(errores) == 0
    )

    resultado = db[CAMP].update_one(
        {"_id": ID_CAMPANA},
        {
            "$addToSet": {
                "publicaciones_asociadas": {
                    "$each": publicaciones_ids
                }
            }
        },
    )

    check(
        "La campana se asocio a las tres publicaciones",
        resultado.matched_count == 1
        and resultado.modified_count == 1
    )

    # UPDATE: modificar metas anidadas.
    paso("UPDATE: ajustar las metricas objetivo")

    resultado = db[CAMP].update_one(
        {"_id": ID_CAMPANA},
        {
            "$inc": {
                "metricas_objetivo.likes": 50
            },
            "$set": {
                "metricas_objetivo.comentarios": 80
            },
        },
    )

    actual = db[CAMP].find_one({
        "_id": ID_CAMPANA
    })

    metricas = actual["metricas_objetivo"]

    check(
        "Likes objetivo = 150 y comentarios objetivo = 80",
        resultado.modified_count == 1
        and metricas["likes"] == 150
        and metricas["comentarios"] == 80
    )

    # READ: consultar el avance de la campana.
    paso("READ: calcular metricas de la campana")

    avance = list(db[CAMP].aggregate([
        {
            "$match": {
                "_id": ID_CAMPANA
            }
        },
        {
            "$lookup": {
                "from": "reacciones",
                "localField": "publicaciones_asociadas",
                "foreignField": "publicacion_id",
                "as": "reacciones",
            }
        },
        {
            "$lookup": {
                "from": "comentarios",
                "localField": "publicaciones_asociadas",
                "foreignField": "publicacion_id",
                "as": "comentarios",
            }
        },
        {
            "$project": {
                "_id": 1,
                "nombre": 1,
                "cantidad_reacciones": {
                    "$size": "$reacciones"
                },
                "cantidad_comentarios": {
                    "$size": "$comentarios"
                },
                "meta_likes": "$metricas_objetivo.likes",
                "meta_comentarios": "$metricas_objetivo.comentarios",
            }
        },
    ]))

    pprint(avance, sort_dicts=False)

    check(
        "La consulta devolvio el avance de la campana",
        len(avance) == 1
        and avance[0]["meta_likes"] == 150
        and avance[0]["meta_comentarios"] == 80
    )

    # UPDATE: cerrar la campana.
    paso("UPDATE: cerrar la campana")

    resultado = db[CAMP].update_one(
        {"_id": ID_CAMPANA},
        {
            "$set": {
                "fecha_fin": ahora(),
                "estado": "cerrada",
            }
        },
    )

    cerrada = db[CAMP].find_one({
        "_id": ID_CAMPANA
    })

    check(
        "La campana quedo cerrada",
        resultado.modified_count == 1
        and cerrada.get("estado") == "cerrada"
    )

    # DELETE: eliminar la campana de demostracion.
    paso("DELETE: eliminar la campana de prueba")

    resultado = db[CAMP].delete_one({
        "_id": ID_CAMPANA
    })

    check(
        "La campana de prueba fue eliminada",
        resultado.deleted_count == 1
    )


# ============================================================
# EJECUCION PRINCIPAL, LIMPIEZA Y VALIDACION DE CONTEOS
# ============================================================

def ejecutar():
    error_inesperado = False

    try:
        titulo("CONTENTFLOW - PASO 7: CRUD DE NEGOCIO")

        # Capturar los conteos antes de ejecutar las pruebas.
        for nombre in COLECCIONES:
            CONTEOS_INICIALES[nombre] = (
                db[nombre].count_documents({})
            )

        print("Conteos iniciales:")
        pprint(CONTEOS_INICIALES)

        # Garantizar la restriccion de correo unico.
        paso("Preparacion: comprobar el indice unico de correo")

        db.usuarios.create_index(
            [("correo", 1)],
            unique=True,
            name="idx_correo_unico",
        )

        indice = db.usuarios.index_information().get(
            "idx_correo_unico", {}
        )

        check(
            "Existe el indice unico idx_correo_unico",
            indice.get("unique") is True
        )

        # Referencias dinamicas a los documentos originales.
        usuarios_ids, publicaciones_ids = obtener_referencias()

        escenario_usuarios()
        escenario_publicacion(usuarios_ids)
        escenario_campana(publicaciones_ids)

    except Exception as error:
        error_inesperado = True

        check(
            "La ejecucion termino sin errores inesperados",
            False
        )

        mensaje = f"ERROR INESPERADO: {type(error).__name__}: {error}"
        print(f"\n{mensaje}")
        registrar_log(mensaje)

    finally:
        titulo("LIMPIEZA Y VERIFICACION FINAL")

        try:
            # Solo elimina documentos identificados por este RUN_ID.
            eliminados = limpiar_pruebas()
            print(f"Documentos de prueba limpiados: {eliminados}")

            residuos = sum(
                db[nombre].count_documents({
                    "_id": {"$regex": f"^{PREFIJO}"}
                })
                for nombre in COLECCIONES
            )

            dependencias = (
                db.reacciones.count_documents({
                    "publicacion_id": ID_PUBLICACION
                })
                + db.comentarios.count_documents({
                    "publicacion_id": ID_PUBLICACION
                })
            )

            check(
                "No quedan documentos con el identificador de esta prueba",
                residuos == 0
            )

            check(
                "No quedan reacciones ni comentarios de la publicacion de prueba",
                dependencias == 0
            )

            # Los conteos finales deben coincidir con los iniciales.
            for nombre in COLECCIONES:
                final = db[nombre].count_documents({})
                inicial = CONTEOS_INICIALES.get(nombre)

                check(
                    f"{nombre}: conteo inicial = {inicial}; "
                    f"conteo final = {final}",
                    inicial is not None and final == inicial
                )

        except Exception as error:
            error_inesperado = True

            check(
                "La limpieza y verificacion final terminaron correctamente",
                False
            )

            mensaje = f"ERROR EN LIMPIEZA: {error}"
            print(mensaje)
            registrar_log(mensaje)

        finally:
            client.close()

    correctas = sum(VERIFICACIONES)
    total = len(VERIFICACIONES)

    titulo("RESUMEN FINAL")
    print(f"Verificaciones correctas: {correctas}/{total}")
    print(f"Registro de ejecucion: {LOG}")

    registrar_log(
        f"RESULTADO FINAL: {correctas}/{total} verificaciones correctas"
    )

    if error_inesperado or correctas != total:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    ejecutar()