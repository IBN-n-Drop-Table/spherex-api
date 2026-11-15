# Pool de rondas WISE vs SPHEREx: estado

Generador: [`scripts/build_game_pool.py`](../scripts/build_game_pool.py). Todavía no lo usa el
juego ni la API.

## Estado: la parte de SPHEREx NO está validada

La validación con 10 estrellas nuevas (15-11-2026, 11:54–12:24) quedó bloqueada por IRSA:
el servicio SIA respondió HTTP 502 a todos los intentos (60 s de margen cada uno) durante
30 minutos, así que no se descargó ni se comprobó ninguna imagen de SPHEREx.

Lo que sí se comprobó con esas 10 estrellas:

| Resultado | Estrellas | Detalle |
|---|---|---|
| Pasan Gaia + WISE; fallan en SPHEREx por IRSA 502 | 6 | G = 7,9–18,0; movimiento propio 1,3–5,1″/año |
| Descartadas: demasiado brillantes | 3 | G = 2,7–4,2: saturan unWISE, centroide a 11–19″ de Gaia |
| Descartada: vecina en Gaia | 1 | G = 20,7 con una fuente de G = 19,3 a 8″ |

- Candidatas: 202 estrellas de Gaia DR3 con paralaje > 50 mas, movimiento propio ≥ 1,2″/año
  (≥ 3 píxeles de SPHEREx entre 2010 y 2025-26) y |b| ≥ 15°. Consulta acotada en el mirror de
  Gaia del ARI Heidelberg (1,3 s); el archivo de ESA no respondía.
- WISE: imágenes de unWISE "allwise" (misión original, 2010–2011; no es IRSA), ~1,4–2 s por
  estrella. Licencia CC BY-NC-SA; atribución obligatoria: "unWISE / NASA/JPL-Caltech / D. Lang
  (Perimeter Institute)".
- Sin validar: que la estrella se vea en SPHEREx, el límite de brillo por abajo en SPHEREx, la
  latencia de las descargas de SPHEREx y la tasa de descarte final.

## Principio de la respuesta

La posición correcta de cada ronda sale de Gaia DR3 (posición + movimiento propio) propagada a
la fecha de cada imagen. Las imágenes solo se comprueban (QA): la estrella debe verse donde dice
Gaia y el sitio de la otra fecha debe estar vacío. No se usa el detector de anomalías.

## Acceso alternativo a SPHEREx (documentación de IRSA, comprobado el 15-11-2026)

- **Copia en la nube: existe.** Bucket público de AWS `nasa-irsa-spherex` (us-east-1), con los
  prefijos `dr1/`, `qr3/` y `qr2/`
  ([IRSA cloud access](https://irsa.ipac.caltech.edu/cloud_access/#spherex)). Responde y se puede
  listar sin credenciales (`dr1/level2/` y `qr3/level2/` existen). Limitaciones: los cutouts solo
  existen en los servidores de IRSA, así que desde S3 hay que bajar el frame completo; y para saber
  qué frames cubren una posición hace falta SIA o TAP, que son de IRSA.
- **TAP: existe**, con las tablas `spherex.artifact` y `spherex.plane`
  ([tutorial de cutouts de IRSA](https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts/)).
  Pasa por el mismo proxy que SIA: respondió 502 en la prueba.
- **Directorios navegables y cutouts por URL**:
  `https://irsa.ipac.caltech.edu/ibe/data/spherex/` y `?center=RA,DEC&size=DEG`
  ([acceso a datos SPHEREx](https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-access/)).
  Mismo servidor que SIA.
