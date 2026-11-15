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

#### Datos del catálogo SIMBAD en el chat (campo opcional `sources`)

Si el contexto trae uno de los 5 objetos, el servidor añade al prompt un bloque `<simbad_catalog>` con datos **ya guardados** en `app/data/simbad/` (el chat nunca consulta SIMBAD en vivo): el objeto (identificador, tipo, referencias bibliográficas, magnitud) y hasta 6 objetos catalogados cercanos, más citados primero, en 2.500 caracteres como máximo. El modelo debe citar "according to SIMBAD" y no presentar nada como detección de SPHEREx.

- `sources` es **opcional**: vale `["SIMBAD"]` cuando se usó ese bloque (también en respuestas en caché y en la predefinida de abajo) y **la clave no existe** en caso contrario. El resto del contrato no cambia, y las `cautions` siguen yendo aparte.
- Sin claves de IA, la pregunta "What else is near this object?" (o con "nearby", "near this", "around it") se responde desde los mismos datos, con `provider: "canned"`, `fallback: true` y `sources: ["SIMBAD"]`. No está entre las 4 sugerencias: el frontend puede ofrecerla como texto fijo.
- Los 4 planetas de Barnard comparten posición con la estrella; el bloque los presenta como planetas catalogados, no como objetos aparte. Son entradas de catálogo, no detecciones.

Respuesta real (sin claves de IA, `context.target = "BARNARDS_STAR"`):

```json
{
  "answer": "According to SIMBAD, NAME Barnard's star is catalogued as: BY Dra Variable. SIMBAD also lists planets catalogued around this star (NAME Barnard's Star b, NAME Barnard's Star c, NAME Barnard's Star d, NAME Barnard's Star e); they are planets, not separate objects. Other catalogued objects in the same patch of sky, most-cited first: ATO J269.3920+04.7625 (Eclipsing Binary); GAT 771 (Star); GES J17574778+0443542 (Star); Gaia DR3 4472837804589123072 (White Dwarf Candidate). These are catalogue entries, not SPHEREx detections.",
  "provider": "canned",
  "cached": false,
  "fallback": true,
  "disclaimer": "AI assistant: it can make mistakes. Any finding requires human verification.",
  "cautions": [],
  "sources": ["SIMBAD"]
}
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
