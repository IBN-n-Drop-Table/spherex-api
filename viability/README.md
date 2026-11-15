# Estudio de viabilidad: rondas aleatorias del juego "Encuentra lo que se mueve"

Pregunta: si el juego eligiera una posición aleatoria del cielo y ejecutara en directo el mismo
pipeline que la API (`scripts/generate_golden_path.py` con los argumentos del modo live), ¿saldría
algo que se mueve de verdad, en cuánto tiempo y con cuánta memoria?

Script: [`scripts/game_viability.py`](../scripts/game_viability.py). Posiciones uniformes en la
esfera (RA uniforme, dec = asin(2u − 1)), excluyendo |b| < 15°, FOV 5′, 1 cutout por detector y
visita, 4 descargas en paralelo, sin caché. Cada candidato se cruza con asteroides conocidos
(IMCCE SkyBoT) y con estrellas de alto movimiento propio (SIMBAD).

## Archivos

| Archivo | Contenido |
|---|---|
| `results.jsonl` | Una línea por posición de la ejecución de 30 (`--n 30 --seed 1930`), 2026-11-15 |
| `summary.json` | Resumen de esas 30 posiciones (4 terminaron) |
| `results_pilot.jsonl` | Ejecución piloto de 1 posición (`--n 1 --seed 7`), que también terminó |

## Conclusiones (tal como se midieron)

- **Muestra pequeña.** Con SPHEREx en directo terminaron 5 de 31 posiciones (4 de la ejecución de
  30 más la piloto). Las otras 26 fallaron porque IRSA SIA no respondió en 10 s (timeouts y HTTP 502):
  el servicio estaba saturado durante la prueba. Ese mismo día, horas después, una consulta SIA
  tardaba ~23 s cuando respondía.
- **Latencia.** 94–141 s por ronda, de los cuales 88–128 s son la descarga de 16–17 cutouts de IRSA.
  Pico de memoria ~187 MB por proceso (157 MB solo de importar astropy, OpenCV, etc.).
  Con un presupuesto de 8 s por ronda, el modo en directo no es viable.
- **Los candidatos "limpios" eran falsos positivos.** Las 5 posiciones dieron entre 3 y 6 candidatos
  marcados como limpios, y ninguno coincidió con un asteroide conocido ni con una estrella de alto
  movimiento propio. Al inspeccionarlos son estrellas brillantes presentes en las dos épocas, a menudo
  como parejas "aparece/desaparece" separadas ~6″ sobre la misma estrella. Ejemplo: en la posición 0,
  la pareja de 46σ/37σ cae sobre Gaia DR3 6451074589790894848 (G = 12,9; movimiento propio 0,018″/año),
  que no se mueve. El residuo supera 5σ porque el umbral de `detect_anomalies` se normaliza con el
  ruido del fondo (varianzas L2 reescaladas por la desviación global) y no incluye un término
  proporcional al flujo de la estrella. En una estrella brillante, un residuo de una fracción mínima
  de su pico ya pasa de 5σ.
- **El registro entre visitas es correcto.** En los campos del golden path (primera frente a última
  visita, PNG cacheados), las estrellas de campo se desplazan 0,03–0,41″ de mediana (p90 ≤ 0,9″
  salvo en el campo débil de WISE 0855). Las estrellas que producen los dipolos se mueven
  0,02–0,46″. Los ~6″ entre los dos lóbulos son un píxel de la rejilla de stack (~6″/px), no el
  desplazamiento real. Estas parejas aparecen también en el golden path (100+ exposiciones por
  visita), así que no se deben solo a usar 1 cutout por detector.
- **Umbral de movimiento visible.** Un objeto necesita moverse al menos ~1 píxel de SPHEREx (~6″)
  para verse en la resta. Esto se deduce de la física (píxel de 6,15″, ~365 días entre la primera y
  la última visita); no se midió directamente. Las estrellas de alto movimiento propio que se
  buscaron con SIMBAD en las 5 posiciones fueron 0, y los 4 asteroides de SkyBoT dentro de un campo
  tenían V ≈ 22,6–23,2, demasiado débiles para SPHEREx.

## Queda sin probar

- **B2 (WISE 2010 frente a SPHEREx).** Solo hubo una prueba preliminar fuera de esta rama, con 3
  estrellas del golden path, los PNG de SPHEREx ya cacheados e imágenes de unWISE. No está validada
  con descargas nuevas de SPHEREx ni con estrellas fuera del golden path.
- **El recuento de estrellas de alto movimiento propio con Gaia** por rangos (≥ 6″/año, 2–6″/año,
  0,5–2″/año). El archivo de ESA no respondía y la consulta completa al mirror del ARI Heidelberg no
  terminó; se canceló.
- La comprobación del registro sobre los cutouts nativos del modo en directo (WCS de IRSA frente a
  Gaia, exposición por exposición) no pudo ejecutarse porque IRSA SIA devolvía 502.
