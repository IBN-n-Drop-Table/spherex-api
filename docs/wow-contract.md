# Contrato de las funciones "wow" (rama `feat/wow-backend`)

Para el frontend (`spherex-web`). Todo es **aditivo**: ningún campo existente cambia.
Los ejemplos salen de la API real (proveedores de IA sin claves, Supabase sin configurar).
Errores: el formato de siempre, `{"detail", "code", "errors"}`. Las URLs de imagen son absolutas.

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/api/v1/targets` | Subtítulos: distancia y textos de los 5 objetos (campos nuevos) |
| POST | `/api/v1/chat` | Chat IA que explica lo que se ve |
| GET | `/api/v1/chat/suggestions?target=` | 4 preguntas sugeridas |
| POST | `/api/v1/game/sessions` | Empezar partida (3–5 rondas) |
| POST | `/api/v1/game/guess` | Un intento por ronda |
| POST | `/api/v1/game/finish` | Guardar la puntuación (calculada en el servidor) |
| GET | `/api/v1/game/leaderboard` | Top 20 |
| GET | `/api/v1/loading-phrases` | Frases para las pantallas de carga |
| GET | `/api/v1/catalog-objects` | Objetos catalogados en SIMBAD, ya proyectados a píxeles sobre la imagen de `/sky-patch` |

---

## 1. Subtítulos: `GET /api/v1/targets`

Seis campos opcionales nuevos por objeto:

| Campo | Tipo | Uso |
|---|---|---|
| `distance_ly` | number | Distancia en años luz, calculada del paralaje |
| `distance_source` | string | Cita del paralaje (Gaia DR3 con `source_id`, o Kirkpatrick et al. 2021 para WISE 0855) |
| `light_left_years_ago` | int | Años que tardó la luz, redondeado (2 cifras significativas si `distance_uncertain`) |
| `distance_uncertain` | bool | `true` en R Draconis y RW Aurigae: el texto dice "roughly" |
| `description_es` | string | 1–2 frases en lenguaje llano |
| `story_es` | string | Frase lista para el subtítulo |

```json
{
  "key": "BARNARDS_STAR",
  "name": "Barnard's Star",
  "ra_deg": 269.452075,
  "dec_deg": 4.693391,
  "fov_arcmin": 5.0,
  "is_cached_golden_path": true,
  "n_epochs": 3,
  "sky_patch_query": "/api/v1/sky-patch?target=BARNARDS_STAR",
  "distance_ly": 5.96,
  "distance_source": "Gaia DR3 (Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1), source_id 4472832130942575872: parallax 546.976 ± 0.040 mas",
  "light_left_years_ago": 6,
  "distance_uncertain": false,
  "description_es": "A small, cool red dwarf, one of the closest stars to the Sun. It is the star that moves fastest across our sky.",
  "story_es": "The light you see left this star about 6 years ago."
}
```

Valores actuales: Barnard 6 años, Epsilon Indi B 12, WISE 0855 7, RW Aurigae ≈510 (incierto), R Draconis ≈2600 (incierto).

**Idioma.** Todos los textos para el usuario (`description_es`, `story_es`, `explanation_es`, `scoring`, `answer`, `cautions`, `disclaimer`, sugerencias, errores) están en **inglés**. Los campos conservan su nombre con `_es` para no romper el contrato.

---

## 2. Chat IA

### `POST /api/v1/chat`

Entrada:

```json
{
  "message": "Why does it move?",
  "tone": "easy",
  "context": {
    "target": "BARNARDS_STAR",
    "band": "base",
    "epochs": ["2025_M0", "2025_M7"],
    "blink": true,
    "difference": false,
    "saturated": true,
    "weak_signal": true,
    "science_note": null,
    "fallback_reason": null
  },
  "history": [
    {"role": "user", "content": "What is this?"},
    {"role": "assistant", "content": "This is Barnard's Star..."}
  ]
}
```

| Campo | Límite |
|---|---|
| `message` | 1–500 caracteres (obligatorio) |
| `tone` | `"easy"` (por defecto) o `"technical"` |
| `context` | objeto libre, ≤ 4 KB en JSON. Las claves `science_note`, `quality: "low"`, `saturated: true`, `weak_signal: true` y `fallback_reason` se detectan **a cualquier profundidad** |
| `history` | ≤ 20 turnos `user`/`assistant`; se usan los 6 últimos, cada uno cortado a 1000 caracteres |

Si el contexto trae `target`, `target_key` o `target_name` de uno de los 5 objetos, el servidor añade al prompt sus datos fiables (distancia, descripción). Un campo de más o un límite superado devuelve 422.

Salida (siempre 200, salvo 422 o 429):

```json
{
  "answer": "All stars move through the galaxy, but distant ones seem to stand still. ...",
  "provider": "canned",
  "cached": false,
  "fallback": true,
  "disclaimer": "AI assistant: it can make mistakes. Any finding requires human verification.",
  "cautions": [
    "The expected shift is smaller than one SPHEREx pixel (6.15″): the motion signal is weak.",
    "The star is saturated: it is so bright that it 'dazzles' the detector, so its brightness cannot be measured well."
  ]
}
```

- `provider`: `groq`, `deepseek` o `canned`. `fallback: true` significa respuesta predefinida, porque no hay claves, fallaron los dos proveedores o se alcanzó el tope diario.
- `cached: true` indica que es la misma pregunta con el mismo contexto, tono e historial en la última hora.
- `cautions` es un campo nuevo: muéstralas siempre junto a la respuesta, aunque el modelo ya las mencione.
- `disclaimer` va fijo en la interfaz.
- Error 429 por exceso de peticiones de una IP: lee la cabecera `Retry-After` (en segundos).

```json
{"detail": "Too many chat requests. Wait 37 s and try again.", "code": "http_429", "errors": null}
```

### `GET /api/v1/chat/suggestions?target=BARNARDS_STAR`

`target` acepta la clave o el nombre (`Barnard's Star`, `wise-0855`…). Sin `target`, o con uno desconocido, devuelve la lista genérica y `"target": null`.

```json
{
  "target": "BARNARDS_STAR",
  "suggestions": [
    "Why does this star move?",
    "What is the blink comparator?",
    "How far away is it?",
    "What is image subtraction?"
  ]
}
```

---

## 3. Juego "Encuentra lo que se mueve"

Las rondas solo usan **Barnard's Star y Epsilon Indi B**, con pares consecutivos y el par primera-vs-última, y nunca el detector de anomalías. La respuesta correcta no sale del servidor hasta que se responde la ronda.

**Coordenadas.** `x`, `y` son posiciones **continuas** en la imagen de la ronda: el origen está arriba a la izquierda, +y va hacia abajo y el píxel *i* ocupa [*i*, *i*+1). Las dos imágenes de una ronda comparten tamaño (`image_size`, 300×300). Si la imagen se pinta escalada, convierte con `x = clickX * image_size.width / anchoMostrado`.

**Blink.** Alterna `image_b_url` (visita anterior, `epoch_b`) con `image_a_url` (visita posterior, `epoch_a`). Cada ronda es un recorte distinto, así que el objeto no está en el centro.

**Pista.** `hint_image_url` solo aparece en rondas `weak_signal: true`. Es la resta (posterior − anterior) del mismo recorte: un objeto que se mueve deja una pareja rojo-azul. Si `weak_signal` es true, avisa en inglés, por ejemplo: "The shift is smaller than one SPHEREx pixel".

### `POST /api/v1/game/sessions` → 201

El cuerpo es opcional: `{"rounds": 5}`, con un valor entre 3 y 5 (5 por defecto). Las rondas van de fácil a difícil: 1 `easy`, luego `medium` y al final 1 `hard`.

```json
{
  "session_id": "36504625-c0bd-4381-bddd-aee678de7a61",
  "expires_at": "2026-11-14T22:47:51.042642Z",
  "rounds": [
    {
      "round_id": "2219bbf14602",
      "target_id": "BARNARDS_STAR",
      "target_name": "Barnard's Star",
      "kind": "first_vs_last",
      "epoch_a": "2025_M12",
      "epoch_b": "2025_M0",
      "date_a": "2026-08-21T15:37:09Z",
      "date_b": "2025-08-27T09:09:36Z",
      "image_a_url": "https://spherex-api.onrender.com/static/game/4aead6ab2eb812954f6599cf.png",
      "image_b_url": "https://spherex-api.onrender.com/static/game/dba1657f2c1d8b024cb91f93.png",
      "hint_image_url": null,
      "image_size": {"width": 300, "height": 300},
      "weak_signal": false,
      "difficulty": "easy",
      "delta_t_days": 359.27,
      "expected_shift_arcsec": 10.22
    },
    {
      "round_id": "df8db0bbf9ce",
      "target_id": "EPSILON_INDI_B",
      "target_name": "Epsilon Indi B",
      "kind": "consecutive",
      "epoch_a": "2025_M5",
      "epoch_b": "2025_M0",
      "date_a": "2025-10-06T07:59:24Z",
      "date_b": "2025-04-30T02:01:13Z",
      "image_a_url": "https://spherex-api.onrender.com/static/game/55ae688f55199b66d7da7afe.png",
      "image_b_url": "https://spherex-api.onrender.com/static/game/0044c3ebcb46aedc5447dbb5.png",
      "hint_image_url": "https://spherex-api.onrender.com/static/game/553b14f78fb9a223b92dc9dc.png",
      "image_size": {"width": 300, "height": 300},
      "weak_signal": true,
      "difficulty": "hard",
      "delta_t_days": 159.25,
      "expected_shift_arcsec": 2.04
    }
  ],
  "scoring": "Miss (more than 20 pixels from the object's path): 0 points. Hit: 500 points, plus up to 300 for precision and up to 200 for speed (if you answer in under 30 s). Up to 1000 per round."
}
```

La sesión caduca a los 15 minutos sin actividad; cada intento renueva ese plazo.

### `POST /api/v1/game/guess` → 200

```json
{"session_id": "36504625-c0bd-4381-bddd-aee678de7a61", "round_id": "2219bbf14602", "x": 198.6, "y": 51.6, "time_ms": 6400}
```

```json
{
  "hit": true,
  "distance_px": 3.2,
  "score": 910,
  "answer": {"x": 194.8, "y": 59.8},
  "answer_path": [{"x": 194.0, "y": 70.0}, {"x": 195.6, "y": 49.6}],
  "hit_radius_px": 20.0,
  "explanation_es": "You found it! Barnard's Star moved about 10.2″ in 359 days (20 pixels in this image), according to its proper motion measured by Gaia DR3; its position in these SPHEREx images matches that prediction."
}
```

- `answer` es el punto medio del camino del objeto.
- `answer_path` es un campo nuevo con la posición en `epoch_b` y luego en `epoch_a`. Sirve para dibujar la flecha.
- `distance_px` es la distancia del intento a ese camino.

**Puntuación** (0–1000 por ronda, calculada en el servidor):
- Fallo, con `d` > 20 px: 0 puntos.
- Acierto: `500 + 300·(1 − d/20) + 200·max(0, 1 − t/30 s)`.

**Errores:**

| Código | Causa |
|---|---|
| 404 | Sesión o ronda desconocida |
| 409 | Segundo intento en la misma ronda, o partida ya terminada |
| 410 | Sesión caducada |
| 422 | `x`/`y` fuera de la imagen, o `time_ms` fuera de 0–600 000 |
| 429 | Demasiados intentos; lee `Retry-After` |

### `POST /api/v1/game/finish` → 200

```json
{"session_id": "36504625-c0bd-4381-bddd-aee678de7a61", "nickname": "Tombaugh_30"}
```

```json
{
  "session_id": "36504625-c0bd-4381-bddd-aee678de7a61",
  "nickname": "Tombaugh_30",
  "score": 910,
  "hits": 1,
  "rounds_played": 1,
  "rounds_total": 3,
  "stored_in": "memory"
}
```

- `score` es la suma de las rondas jugadas, recalculada en el servidor. Si el cliente envía un `score`, la petición se rechaza con 422.
- Alias: 3–16 caracteres, solo letras (con tildes y ñ), dígitos, `_` y `-`. Cualquier otro devuelve 422.
- 409 si no se jugó ninguna ronda o si se intenta guardar dos veces.
- Las rondas sin jugar cuentan 0.

### `GET /api/v1/game/leaderboard` → 200

```json
{
  "entries": [
    {"nickname": "Tombaugh_30", "score": 910, "hits": 1, "rounds_played": 1, "rounds_total": 3, "created_at": "2026-11-14T22:32:51.050723Z"}
  ],
  "backend": "memory"
}
```

Muestra el top 20 por puntuación; en caso de empate gana la más antigua. Con `backend: "memory"` la clasificación se pierde al reiniciar el servidor.

---

## 4. Frases de carga: `GET /api/v1/loading-phrases`

Frases ligeras en inglés para las pantallas de carga, agrupadas por categoría. Elige una al azar de la categoría que toque:

| Categoría | Cuándo usarla |
|---|---|
| `generic` | Cualquier espera |
| `cold_start` | El servidor tarda en despertar (Render gratuito) |
| `subtraction` | Mientras carga la resta de imágenes |
| `game` | Mientras se prepara una partida |
| `chat` | Mientras responde el asistente |

El endpoint no depende de la base de datos, de los proveedores de IA ni de los datos en caché, así que responde aunque todo lo demás falle. Lleva `Cache-Control: public, max-age=3600`, así que el navegador puede guardarlo una hora: pídelo una vez al arrancar y ten una copia local en el frontend por si el servidor aún no ha despertado.

```json
{
  "phrases": {
    "generic": [
      "Warming up the telescope. It's very cold up there.",
      "Counting photons one by one. Please hold.",
      "Asking the universe to hold still for a second.",
      "Polishing a mirror we definitely don't own.",
      "Convincing light to arrive faster. It's not listening.",
      "Dusting off 102 infrared colors.",
      "Aligning the stars. Some are being difficult.",
      "Loading the cosmos. It's a big file."
    ],
    "cold_start": [
      "Our server is waking up. It doesn't do mornings either.",
      "Brewing coffee for the backend. Almost ready.",
      "The API is stretching. Great things need warm-ups."
    ],
    "subtraction": [
      "Subtracting the stars that stayed. Looking for the ones that sneaked away.",
      "Spot the difference. Sometimes the difference moves.",
      "Hunting things that move while pretending not to.",
      "Making the boring stars disappear."
    ],
    "game": [
      "Shuffling the sky. No cheating, we checked.",
      "Hiding something in the stars. Good luck.",
      "Your 1930 astronomer moment is loading."
    ],
    "chat": [
      "Asking a very smart parrot who has read a lot of astronomy.",
      "Thinking in infrared. It takes a second.",
      "Finding words simpler than \"parallax\"."
    ]
  }
}
```

---

## 5. Objetos catalogados en SIMBAD: `GET /api/v1/catalog-objects`

Devuelve los objetos que **SIMBAD tiene catalogados** dentro de la imagen de SPHEREx, ya en píxeles, para que el frontend dibuje marcadores encima. Son entradas de un catálogo: **no son detecciones de SPHEREx** (ver "Reglas de interfaz").

### Parámetros

| Parámetro | Por defecto | Significado |
|---|---|---|
| `target` | | Igual que en `/sky-patch`: `BARNARDS_STAR`, `WISE_0855`... Usa la imagen en caché de ese objeto |
| `ra`, `dec` | | Igual que en `/sky-patch` (grados o sexagesimal). Si caen dentro de uno de los 5 objetos, se usa su imagen en caché; si no, la del recorte vivo (centrado ahí, 600 × 600 px) |
| `fov` | 5 | Arcominutos. Solo cuenta fuera del Golden Path (máximo 15 ahí; los 5 objetos son siempre 5′) |
| `epoch` | primera visita | Solo Golden Path: `2025_M0`, `2025_M7`... Es la visita a cuya fecha se mueven las posiciones. Fuera del Golden Path se usa la fecha de hoy (UTC) y `epoch` da 422 |
| `max_n` | 20 | De 1 a 50 |
| `type_filter` | `all` | `all`; `no_common_stars` (oculta estrellas sueltas, múltiples, de secuencia principal, variables genéricas y binarias espectroscópicas: `*`, `**`, `MS*`, `V*`, `SB*`); `ir_galaxies` (galaxias y núcleos activos: `G`, `GiC`, `BiC`, `GiG`, `GiP`, `LSB`, `bCG`, `SBG`, `H2G`, `EmG`, `rG`, `AGN`, `LIN`, `SyG`, `Sy1`, `Sy2`, `QSO`, `Bla`, `BLL`) |

Hay que dar `target`, o `ra` y `dec`. Orden: **solo por relevancia**, es decir, número de referencias bibliográficas (`nbref`) de mayor a menor. No hay orden ni filtro por brillo.

### Coordenadas de los marcadores

`x`, `y` son posiciones **continuas sobre el PNG que ve el usuario** (la misma convención que el juego): origen **arriba a la izquierda**, +x a la derecha, +y **hacia abajo**, el píxel *i* ocupa [*i*, *i*+1). `image_size` es el tamaño de esa imagen (600 × 600). Si la imagen se pinta escalada: `left = x * anchoMostrado / image_size.width` (igual para `y`).

En el cielo, el norte está arriba y **el este a la izquierda**, porque así es el WCS del PNG (`patch.wcs` de `/sky-patch`: proyección TAN, `cd1_1 < 0`). El eje vertical de FITS (abajo→arriba) ya está invertido en el servidor: **no se vuelve a invertir en el cliente**. Los objetos cuyo píxel cae fuera de la imagen no se devuelven.

### Cómo se calcula (para entender los resultados)

1. Consulta síncrona al servicio TAP de SIMBAD (ADQL, salida JSON): `CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', centro, radio)) = 1`, centrada en el centro de la imagen y con radio igual a la **mitad de la diagonal** del recorte (3,5′ para 5′). Pide también `pmra`, `pmdec` y el tipo de objeto.
2. SIMBAD da posiciones **J2000**; SPHEREx observa en 2025-2026. Cada posición se mueve a la **fecha de la visita** con su movimiento propio de SIMBAD. Si SIMBAD no tiene movimiento propio para el objeto, no se mueve y sale `proper_motion_applied: false`. Esto importa: Barnard's Star se desplaza unos 6′ desde J2000, más que el radio de la imagen, así que el cono de búsqueda se ensancha según el movimiento propio posible y se descarta lo que, con **su propio** movimiento, no llegaría a la imagen.
3. Se proyecta con la WCS del PNG servido (no la del FITS original) y se descarta lo que cae fuera.

### Respuesta

| Campo | Significado |
|---|---|
| `objects[].id` | Identificador principal de SIMBAD, tal cual (`NAME Barnard's star`, `V* RW Aur`...). Puede llevar el prefijo `NAME ` |
| `objects[].otype`, `otype_label` | Código de tipo de SIMBAD (`BY*`) y su descripción oficial (`BY Dra Variable`) |
| `objects[].x`, `y` | Posición en la imagen (ver arriba) |
| `objects[].mag`, `mag_band` | Magnitud y banda (`V`, o la primera que exista de `G`, `R`, `J`, `H`, `K`). `null` si SIMBAD no tiene ninguna |
| `objects[].nbref` | Referencias bibliográficas en SIMBAD (la relevancia) |
| `objects[].simbad_url` | Página del objeto en SIMBAD |
| `objects[].proper_motion_applied` | `true`: posición movida a `epoch_applied`. `false`: SIMBAD no da movimiento propio y se usa la posición J2000 |
| `image_size` | `{width, height}` de la imagen donde se proyectó |
| `epoch_applied` | `epoch_id` (o `null`), `date` (UTC) y `julian_year` a los que se movieron las posiciones |
| `truncated` | `true` si había más objetos en la imagen que `max_n` (o más de los que SIMBAD devolvió: tope de 200 filas por consulta) |
| `source` | `simbad` (en vivo), `cache` (memoria del servidor, TTL de 1 h), `precomputed` (fichero del Golden Path) o `none` (nada respondió: `objects` vacío) |
| `fallback_reason` | `null`, o `simbad_unavailable` (no respondió en 5 s o dio error) o `simbad_rate_limited` (**nuestro servidor** está espaciando sus consultas a SIMBAD: cubo de fichas global, ráfaga de 10 y 3/s de media; SIMBAD no ha rechazado nada) |
| `fallback_message` | Texto en inglés para mostrar cuando hay `fallback_reason` |
| `notice` | Texto en inglés para mostrar junto a los marcadores |
| `data_credits` | SIMBAD/CDS y SPHEREx (DR1, QR3) |

Siempre 200 si la petición es válida: un campo vacío devuelve `"objects": []`. Con SIMBAD caído se sirve, por este orden, el resultado en caché de la misma consulta (aunque haya caducado), el fichero precalculado del objeto (`app/data/simbad/`, generado con `scripts/generate_simbad_golden.py`), o una lista vacía.

### Ejemplo real: Barnard's Star, sin estrellas comunes (SIMBAD en vivo)

Respuesta obtenida de SIMBAD (a través de su espejo de Harvard) el 2026-11-15 con `?target=BARNARDS_STAR&max_n=5&type_filter=no_common_stars`. Se muestran 2 de los 5 objetos y solo el crédito de SIMBAD dentro de `data_credits` (el real trae también `spherex_dr1` y `spherex_qr3`). La estrella cae en (300, 300): el centro de su propia imagen en la primera visita.

```json
{
  "objects": [
    {
      "id": "NAME Barnard's star",
      "otype": "BY*",
      "otype_label": "BY Dra Variable",
      "x": 300.0,
      "y": 300.0,
      "mag": 9.511,
      "mag_band": "V",
      "nbref": 924,
      "simbad_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=NAME%20Barnard%27s%20star",
      "proper_motion_applied": true
    },
    {
      "id": "NAME Barnard's Star b",
      "otype": "Pl",
      "otype_label": "Extra-solar Planet",
      "x": 300.0,
      "y": 300.0,
      "mag": null,
      "mag_band": null,
      "nbref": 34,
      "simbad_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=NAME%20Barnard%27s%20Star%20b",
      "proper_motion_applied": true
    }
  ],
  "image_size": {
    "width": 600,
    "height": 600
  },
  "epoch_applied": {
    "epoch_id": "2025_M0",
    "date": "2025-08-27T09:09:36Z",
    "julian_year": 2025.653
  },
  "truncated": false,
  "source": "simbad",
  "fallback_reason": null,
  "data_credits": [
    {
      "id": "simbad",
      "source": "SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France",
      "dataset": "SIMBAD",
      "used_for": "Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic references) marked on the SPHEREx images, with positions propagated to the observation date",
      "citation": "Wenger et al. 2000, A&AS 143, 9",
      "doi": "10.1051/aas:2000332",
      "url": "https://simbad.cds.unistra.fr/simbad/",
      "access": "https://simbad.cds.unistra.fr/simbad/sim-tap"
    }
  ],
  "notice": "Objects catalogued in SIMBAD, shown at their expected position on this image. They are not SPHEREx detections.",
  "fallback_message": null
}
```

Los cuatro planetas de Barnard (`Pl`) tienen **la misma posición que la estrella**: SIMBAD les da sus coordenadas. Caen en el mismo píxel.

### Ejemplo real: coordenadas fuera del Golden Path (`?ra=83.8221&dec=-5.3911&max_n=5`)

Imagen de 5′ centrada ahí, fecha de hoy. Aquí `truncated` es `true` y `NAME Orion-KL` no tiene movimiento propio en SIMBAD (`proper_motion_applied: false`). Se muestran 2 de los 5 objetos.

```json
{
  "objects": [
    {
      "id": "M 42",
      "otype": "HII",
      "otype_label": "HII Region",
      "x": 314.25,
      "y": 274.82,
      "mag": null,
      "mag_band": null,
      "nbref": 4512,
      "simbad_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=M%2042",
      "proper_motion_applied": true
    },
    {
      "id": "NAME Orion-KL",
      "otype": "SFR",
      "otype_label": "Star Forming Region",
      "x": 393.91,
      "y": 167.08,
      "mag": null,
      "mag_band": null,
      "nbref": 2441,
      "simbad_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=NAME%20Orion-KL",
      "proper_motion_applied": false
    }
  ],
  "image_size": {
    "width": 600,
    "height": 600
  },
  "epoch_applied": {
    "epoch_id": null,
    "date": "2026-11-15T00:01:00Z",
    "julian_year": 2026.871
  },
  "truncated": true,
  "source": "simbad",
  "fallback_reason": null,
  "data_credits": [
    {
      "id": "simbad",
      "source": "SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France",
      "dataset": "SIMBAD",
      "used_for": "Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic references) marked on the SPHEREx images, with positions propagated to the observation date",
      "citation": "Wenger et al. 2000, A&AS 143, 9",
      "doi": "10.1051/aas:2000332",
      "url": "https://simbad.cds.unistra.fr/simbad/",
      "access": "https://simbad.cds.unistra.fr/simbad/sim-tap"
    }
  ],
  "notice": "Objects catalogued in SIMBAD, shown at their expected position on this image. They are not SPHEREx detections.",
  "fallback_message": null
}
```

### Ejemplo real: SIMBAD no responde, se sirve el precalculado

Con la URL de SIMBAD inalcanzable. `x`, `y` son los mismos, pero `source` y `fallback_*` avisan. Se muestra 1 de los 5 objetos.

```json
{
  "objects": [
    {
      "id": "NAME Barnard's star",
      "otype": "BY*",
      "otype_label": "BY Dra Variable",
      "x": 300.0,
      "y": 300.0,
      "mag": 9.511,
      "mag_band": "V",
      "nbref": 924,
      "simbad_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=NAME%20Barnard%27s%20star",
      "proper_motion_applied": true
    }
  ],
  "image_size": {
    "width": 600,
    "height": 600
  },
  "epoch_applied": {
    "epoch_id": "2025_M0",
    "date": "2025-08-27T09:09:36Z",
    "julian_year": 2025.653
  },
  "truncated": false,
  "source": "precomputed",
  "fallback_reason": "simbad_unavailable",
  "data_credits": [
    {
      "id": "simbad",
      "source": "SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France",
      "dataset": "SIMBAD",
      "used_for": "Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic references) marked on the SPHEREx images, with positions propagated to the observation date",
      "citation": "Wenger et al. 2000, A&AS 143, 9",
      "doi": "10.1051/aas:2000332",
      "url": "https://simbad.cds.unistra.fr/simbad/",
      "access": "https://simbad.cds.unistra.fr/simbad/sim-tap"
    }
  ],
  "notice": "Objects catalogued in SIMBAD, shown at their expected position on this image. They are not SPHEREx detections.",
  "fallback_message": "SIMBAD did not answer in time. Showing a precomputed list for this target."
}
```

### Ejemplo real: SIMBAD no responde y no hay nada guardado (coordenadas nuevas)

```json
{
  "objects": [],
  "image_size": {
    "width": 600,
    "height": 600
  },
  "epoch_applied": {
    "epoch_id": null,
    "date": "2026-11-15T00:01:01Z",
    "julian_year": 2026.871
  },
  "truncated": false,
  "source": "none",
  "fallback_reason": "simbad_unavailable",
  "data_credits": [
    {
      "id": "simbad",
      "source": "SIMBAD astronomical database, CDS, Strasbourg Astronomical Observatory, France",
      "dataset": "SIMBAD",
      "used_for": "Catalogued objects (identifier, type, position, proper motion, magnitude, bibliographic references) marked on the SPHEREx images, with positions propagated to the observation date",
      "citation": "Wenger et al. 2000, A&AS 143, 9",
      "doi": "10.1051/aas:2000332",
      "url": "https://simbad.cds.unistra.fr/simbad/",
      "access": "https://simbad.cds.unistra.fr/simbad/sim-tap"
    }
  ],
  "notice": "Objects catalogued in SIMBAD, shown at their expected position on this image. They are not SPHEREx detections.",
  "fallback_message": "SIMBAD did not answer in time and no saved result exists for this field, so no catalogued objects are shown."
}
```

El mismo caso con `simbad_rate_limited` dice: `We are slowing down our queries to SIMBAD for a moment and no saved result exists for this field yet, so no catalogued objects are shown. Try again in a few seconds.` Con caché o Golden Path disponibles, se sirven ellos ("Showing the last result saved for this field." / "Showing a precomputed list for this target."); un cubo vacío nunca es un error.

### Campo vacío

Si SIMBAD responde y no hay objetos, es un 200 con `"objects": []`, `"truncated": false`, `"source": "simbad"` y `"fallback_reason": null`. Estructura (se vació la lista de un ejemplo real):

```json
{
  "image_size": {
    "width": 600,
    "height": 600
  },
  "epoch_applied": {
    "epoch_id": null,
    "date": "2026-11-15T00:01:00Z",
    "julian_year": 2026.871
  },
  "truncated": false,
  "source": "simbad",
  "fallback_reason": null,
  "notice": "Objects catalogued in SIMBAD, shown at their expected position on this image. They are not SPHEREx detections.",
  "fallback_message": null,
  "objects": [],
  "data_credits": "..."
}
```

### Errores

| Código | Cuándo | `detail` |
|---|---|---|
| 404 | `target` desconocido y sin `ra`/`dec` | `Unknown target. Try WISE_0855, or give ra and dec.` |
| 422 | Ni `target` ni `ra`/`dec` | `Give a target, or ra and dec.` |
| 422 | `max_n` fuera de 1–50, `type_filter` no válido, `fov` fuera de rango | el campo y el motivo, con `errors[]` |
| 422 | `epoch` desconocido, o dado fuera del Golden Path | `Unknown epoch '2099_M1'. Available: 2025_M0, 2025_M7, 2025_M12.` / `epoch is only available for Golden Path targets.` |
| 422 | `fov` mayor de 15 fuera del Golden Path | `fov: at most 15 arcmin outside the Golden Path targets.` |
| 429 | Más de 20 peticiones por IP y minuto | `Too many catalog-objects requests. Wait N s and try again.` con `Retry-After` |

### Reglas de interfaz

1. **Rotular siempre como catálogo, nunca como descubrimiento.** Los marcadores son objetos "catalogued in SIMBAD", no cosas que SPHEREx haya detectado. Mostrar `notice` junto a la capa (o un texto equivalente: "Catalogued in SIMBAD") y no usar palabras como "detected", "found" o "discovered". La capa de anomalías de `/sky-patch` es otra cosa y no debe mezclarse.
2. Mostrar `fallback_message` cuando `fallback_reason` no sea `null`, y no presentar como actual un resultado de `cache` o `precomputed` con `fallback_reason`.
3. Citar SIMBAD/CDS: usar `data_credits` (o `GET /credits`) en los créditos de la página.
4. Los marcadores pueden **solaparse** (los planetas de una estrella caen en su píxel). Agrupar los que estén a menos de unos píxeles y enseñar la lista al pulsar.
5. `proper_motion_applied: false` quiere decir "posición J2000 sin mover", no "posición exacta". Para las estrellas de movimiento propio alto, el marcador de un objeto sin movimiento propio puede estar desplazado respecto a donde está hoy.
6. Los objetos extensos (nubes, regiones HII, cúmulos) se marcan en el punto que SIMBAD da como posición, no cubren su extensión.
7. Cambiar `max_n` reutiliza la caché del servidor (1 h). Cambiar `type_filter`, o pedir una época más de un cuarto de año distinta, hace una consulta nueva a SIMBAD; mejor no pedir las tres épocas a la vez.

### Qué está verificado

Contra el servicio real de SIMBAD (espejo de Harvard, datos ingeridos hasta 2026-10-14; el host de CDS tenía un incidente de seguridad y no respondía): nombres de tabla y columnas (`basic`, `otypedef`, `allfluxes`), la lista de 226 códigos de tipo, el formato JSON, la sintaxis ADQL y el movimiento propio de los 5 objetos. Con el movimiento propio de SIMBAD, el objeto de cada recorte cae a 0,00 px (Barnard), 0,13 px (Epsilon Indi B), 0,03 px (RW Aurigae), 0,78 px (WISE 0855) y 7,4 px (R Draconis; su posición en `targets.py` parece estar redondeada a 0,001°, 3,6″, lo que explica la diferencia, pero no se ha comprobado) del centro de su imagen en la primera visita. **Sin verificar:** el host principal de CDS (`simbad.cds.unistra.fr`), el límite real de peticiones por segundo de SIMBAD (se usa un cubo de fichas con ráfaga de 10 y 3/s de media por prudencia; la documentación de SIMBAD que da ese límite no se pudo leer: 403/404). La cita de SIMBAD (Wenger et al. 2000, A&AS 143, 9) sí se comprobó después: la da la página de SIMBAD (espejo de Harvard) y Crossref confirma el DOI 10.1051/aas:2000332.
