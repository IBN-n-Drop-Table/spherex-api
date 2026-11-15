# Pool de rondas WISE vs SPHEREx: estado

Generador: [`scripts/build_game_pool.py`](../scripts/build_game_pool.py). El juego lo usa desde el
15-11-2026: `POST /api/v1/game/sessions` sortea del pool (`app/data/game_pool/manifest.json` y
`app/static/game_pool/`) y cae al Golden Path con `fallback: true` si falta o falla.

## Pool desplegado (15-11-2026, ruta HiPS)

| | Fáciles | Medias | Difíciles | Total |
|---|---|---|---|---|
| Candidatas (G ≥ 6, paralaje > 20 mas, \|b\| ≥ 15°) | 12 | 44 | 191 | 247 |
| Rondas jugables | 6 | 30 | 69 | **105** |

Descartes: 89 control de SPHEREx (casi todos difíciles: estrella y sitio vacío demasiado juntos),
38 vecina en Gaia, 14 control de WISE, 1 fallo de red (unWISE). Tamaño: 2,2 MB de PNG + 240 KB
de manifiesto. Desplazamiento medido vs esperado: mediana 1,8″, p90 3,2″, máximo 5,8″.
5–13 s por estrella al generar; `POST /game/sessions` tarda ~3 ms (36 ms la primera vez).

Mejoras respecto a la primera versión:

- Aspecto: WISE pasa por la misma pixelización que SPHEREx (remuestreo adaptativo a la rejilla
  HPX de 12,9″), las dos van a la rejilla de la ronda con interpolación bicúbica y el mismo
  suavizado gaussiano, y comparten la regla de brillo (negro = fondo − 1σ, blanco = pico de la
  estrella).
- Filtro de artefactos: ignora 20″ alrededor de las estrellas de Gaia con G ≤ G_estrella + 4;
  recuperó 7 rondas que se descartaban por estrellas reales del campo.
- Cobertura parcial: era un hueco real de los datos, no un fallo de lectura: el HiPS deja en NaN
  el núcleo saturado de las estrellas brillantes (1–4 píxeles justo sobre la estrella, G 6–10).
  Se rellena para mostrarlo como "saturado = blanco", igual que el Golden Path.
- Ampliación: candidatas con paralaje > 20 mas y rondas difíciles (3–5 píxeles nativos).

Limitaciones que quedan: en estrellas puntuales aún se adivina la forma de rombo de los píxeles
HPX; en las rondas difíciles la estrella salta más o menos el ancho de su mancha (se solapan);
en WISE algunas estrellas brillantes salen alargadas por sus espigas de difracción; solo DR1.

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

## Créditos de los datos del pool

Los mismos textos que `GET /credits` y la sección "Data used" del README
([`app/core/credits.py`](../app/core/credits.py)):

- **SPHEREx DR1 HiPS** (SPHEREx, NASA/JPL/Caltech, servido por IRSA/IPAC): cubo espectral HiPS de
  DR1 en el bucket de AWS Open Data `nasa-irsa-spherex`; DOI de SPHEREx DR1
  [10.26131/IRSA678](https://www.ipac.caltech.edu/doi/10.26131/IRSA678). Agradecimiento de SPHEREx
  en el README.
- **unWISE**: "unWISE / NASA/JPL-Caltech / D. Lang (Perimeter Institute)", Lang 2014, AJ 147, 108,
  [unwise.me](https://unwise.me/). Licencia CC BY-NC-SA 4.0 (uso no comercial, atribución obligatoria).
- **Gaia DR3** (ESA): Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1. Candidatas y respuesta
  correcta de cada ronda; consultado en el mirror del ARI Heidelberg.
- **SIMBAD** (CDS, Estrasburgo): Wenger et al. 2000, A&AS 143, 9, DOI
  [10.1051/aas:2000332](https://doi.org/10.1051/aas:2000332). Nombres de las estrellas de las rondas.
