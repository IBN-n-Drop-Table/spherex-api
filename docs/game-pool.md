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

## Ruta HiPS DR1 en S3 (`--spherex-source hips`): validada el 15-11-2026

Sin IRSA: las imágenes de SPHEREx salen del cubo HiPS de DR1 del bucket público
`nasa-irsa-spherex` (`dr1/hips5_cube/hips_20261026/Norder5`), leyendo con peticiones `Range`
solo las filas que necesita cada ronda. Solo rondas fáciles y medias (desplazamiento ≥ 31″,
`--min-shift-px 5`), porque el píxel del HiPS es de ~12,9″.

- Canales: D2 + D3 (1,1–2,4 µm), uno de cada dos (17), combinados por mediana. Las candidatas
  (enanas M y blancas cercanas) son más brillantes ahí y el fondo zodiacal térmico crece por
  encima de ~3,5 µm.
- Fechas: cada tesela mezcla las visitas entre `MJD-BEG` y `MJD-END`. La ronda guarda ese
  intervalo (`date_a_range`); la respuesta es Gaia propagado al punto medio, y la tolerancia
  del control de calidad suma pm × (intervalo / 2).
- Control de calidad, en las dos imágenes: estrella visible (≥ 5σ), centroide dentro de la
  tolerancia de Gaia, el sitio de la otra fecha vacío y, en SPHEREx, nada fuera de 25″ de las
  dos posiciones con más del 50 % del pico de la estrella (descarta las franjas de los HiPS).

| Prueba | Estrellas | Jugables | Notas |
|---|---|---|---|
| Validación | 18 (14 aisladas) | 7 (50 % de las aisladas) | desplazamiento medido vs esperado: error 3–5″ |
| Pool completo (pm ≥ 2,05″/año, G ≥ 6, paralaje > 50 mas) | 47 | 18 (4 fáciles, 14 medias) | 4–15 s por estrella (mediana 5 s); error mediano 2,7″, máximo 5,0″ |

Descartes en el pool: 9 vecina en Gaia (binarias, compañeras), 8 control de SPHEREx (sobre
todo franjas/desorden), 7 cobertura parcial del HiPS, 5 control de WISE (saturación o mezcla).
Tamaño: 408 KB de PNG (36 imágenes de 300 × 300) + 44 KB de manifiesto.

Limitaciones: 18 rondas es poco (el objetivo era 60–150) porque la consulta de candidatas solo
cubre paralaje > 50 mas; los descartes por cobertura parcial (15 %) no se han investigado; las
estrellas se ven como manchas romboidales (píxel de 12,9″, proyección HPX girada 45°); solo DR1.

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
