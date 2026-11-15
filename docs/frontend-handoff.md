# Traspaso al frontend: qué ha cambiado en el backend

Para quien construye `spherex-web` y no ha visto el backend. Todo lo que sigue sale del código y de respuestas reales de la app, obtenidas con el cliente de pruebas de FastAPI el 2026-11-14 y repetidas con un servidor `uvicorn` local. Lo que no se ha podido probar va marcado como **sin verificar**.

- Rama del backend: `feat/english-ui` (incluye `feat/wow-backend`, que incluye `feat/offline-first-live-jobs`). Nada está desplegado todavía: **todo se probó solo en local**.
- Los ejemplos JSON son respuestas reales, pero los identificadores (`session_id`, `round_id`, nombres de recortes) cambian en cada ejecución.
- Contrato detallado en español de las funciones nuevas: [wow-contract.md](wow-contract.md). Este documento lo resume y añade el resto de cambios.

## 1. Resumen

1. El backend ya ofrece los subtítulos de distancia (`GET /targets`), el chat de IA, el juego "Encuentra lo que se mueve" y las frases de carga. Todos son aditivos.
2. **Todo texto para el usuario llega en inglés**, incluidos los campos cuyo nombre termina en `_es`. La interfaz debe estar en inglés.
3. El frontend tiene que mostrar siempre las cautelas científicas (`cautions`, `science_note`, `quality`, `saturated`, `weak_signal`, `fallback_reason`) y el aviso fijo del chat.
4. La puntuación del juego la calcula el servidor; el cliente solo envía dónde hizo clic y cuánto tardó.
5. Hay varios cambios que pueden romper código existente (sección 3) y dos pasos manuales opcionales: las claves de IA del chat y la tabla de Supabase (sección 5).

Base de las rutas: `/api/v1`. CORS abierto (`*`). Las cabeceras `Location` y `Retry-After` están expuestas al navegador.

**Formato de error (todos los endpoints):**

```json
{"detail": "Game not found.", "code": "not_found", "errors": null}
```

`detail` es siempre un texto en inglés listo para mostrar. `code` es estable para ramificar la lógica: `not_found`, `validation_error`, `job_not_found`, `http_<status>` (por ejemplo `http_409`, `http_429`), `service_unavailable`, `internal_error`. `errors` solo se rellena en los 422 de validación, con `{loc, msg, type}` por campo.

## 2. Endpoints

| Método | Ruta | Para qué | Errores posibles |
|---|---|---|---|
| GET | `/targets` | Lista de los 5 objetos, ahora con distancia y textos de subtítulo | — |
| POST | `/chat` | Asistente de IA que explica lo que se ve | 422, 429 |
| GET | `/chat/suggestions?target=` | 4 preguntas sugeridas | — |
| POST | `/game/sessions` | Empieza una partida (3 a 5 rondas) | 422, 429, 503 |
| POST | `/game/guess` | Un intento por ronda | 404, 409, 410, 422, 429 |
| POST | `/game/finish` | Guarda la puntuación (la calcula el servidor) | 404, 409, 410, 422, 429 |
| GET | `/game/leaderboard` | Los 20 mejores | — |
| GET | `/loading-phrases` | Frases para las pantallas de carga | — |
| GET | `/catalog-objects` | Objetos catalogados en SIMBAD, en píxeles sobre la imagen de `/sky-patch` (apartado 2.6) | 404, 422, 429 |
| GET | `/credits` y `/api/v1/credits` | Citas de los datos usados | — |
| GET | `/sky-patch`, `/spectrum`, `/epochs`, `/jobs/{id}` | Ya existían; campos y textos nuevos (apartado 2.5) | ver 2.5 |

### 2.1 `GET /targets`: subtítulos de distancia

Seis campos opcionales nuevos por objeto, para sustituir los datos estáticos de `src/data/targets.ts` en lo que respecta a distancia y textos. Los campos antiguos no cambian.

| Campo | Tipo | Significado |
|---|---|---|
| `distance_ly` | number | Distancia en años luz, calculada del paralaje |
| `distance_source` | string | Cita del paralaje (Gaia DR3 con `source_id`, o Kirkpatrick et al. 2021 para WISE 0855) |
| `light_left_years_ago` | int | Años que tardó la luz, redondeado. Con `distance_uncertain` va a 2 cifras significativas |
| `distance_uncertain` | bool | `true` en RW Aurigae y R Draconis: el texto ya dice "roughly" |
| `description_es` | string | 1 o 2 frases sobre qué es el objeto (**en inglés**) |
| `story_es` | string | Frase lista para el subtítulo (**en inglés**) |

Petición: `GET /api/v1/targets`. Respuesta real (2 de los 5 objetos):

```json
[
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
  },
  {
    "key": "R_DRACONIS",
    "name": "R Draconis",
    "ra_deg": 248.165,
    "dec_deg": 66.755,
    "fov_arcmin": 5.0,
    "is_cached_golden_path": true,
    "n_epochs": 3,
    "sky_patch_query": "/api/v1/sky-patch?target=R_DRACONIS",
    "distance_ly": 2607.34,
    "distance_source": "Gaia DR3 (Gaia Collaboration, Vallenari et al. 2023, A&A 674, A1), source_id 1648328016354496384: parallax 1.251 ± 0.065 mas (Mira variable: large, pulsating star, so the parallax is less reliable)",
    "light_left_years_ago": 2600,
    "distance_uncertain": true,
    "description_es": "An old red giant star that swells and shrinks: its brightness rises and falls over a cycle of several months.",
    "story_es": "The light you see left this star roughly 2,600 years ago (pulsating giants like this one are hard to measure)."
  }
]
```

Valores actuales de `light_left_years_ago`: Barnard 6, Epsilon Indi B 12, WISE 0855 7, RW Aurigae 510 (incierto), R Draconis 2600 (incierto).

### 2.2 Chat de IA

**`POST /chat`**

| Campo de entrada | Límite |
|---|---|
| `message` | obligatorio, 1 a 500 caracteres |
| `tone` | `"easy"` (por defecto) o `"technical"` |
| `context` | objeto libre, máximo 4 KB en JSON. Se detectan `science_note`, `quality: "low"`, `saturated: true`, `weak_signal: true` y `fallback_reason` a cualquier profundidad |
| `history` | hasta 20 turnos `{role: "user"|"assistant", content}`; el servidor usa los 6 últimos |

Un campo desconocido, un mensaje demasiado largo o un contexto de más de 4 KB devuelve 422. Si `context.target` (o `target_key`, `target_name`) es uno de los 5 objetos, el servidor añade por su cuenta datos fiables (distancia, descripción) al prompt.

Petición:

```json
{"message": "Why does it move?", "tone": "easy",
 "context": {"target": "BARNARDS_STAR", "blink": true, "weak_signal": true, "saturated": true,
             "quality": "low", "science_note": "The core saturates SPHEREx."}}
```

Respuesta real (sin claves de IA configuradas, por eso `provider: "canned"`):

```json
{
  "answer": "All stars move through the galaxy, but distant ones seem to stand still. Very nearby ones, like Barnard's Star or Epsilon Indi B, visibly change position in the sky within a few months: this is their 'proper motion'. Even so, the change is small, a few arcseconds, about the size of one SPHEREx pixel.",
  "provider": "canned",
  "cached": false,
  "fallback": true,
  "disclaimer": "AI assistant: it can make mistakes. Any finding requires human verification.",
  "cautions": [
    "Science note: The core saturates SPHEREx.",
    "The expected shift is smaller than one SPHEREx pixel (6.15″): the motion signal is weak.",
    "The star is saturated: it is so bright that it 'dazzles' the detector, so its brightness cannot be measured well.",
    "Part of the measurement is low quality (for example, taken outside the saturated core): no conclusions can be drawn from it."
  ]
}
```

| Campo de salida | Qué hacer con él |
|---|---|
| `answer` | Texto de la respuesta, siempre en inglés |
| `provider` | `groq`, `deepseek` o `canned` (respuesta predefinida) |
| `cached` | `true` si es una pregunta idéntica (mismo contexto, tono e historial) de la última hora |
| `fallback` | `true` = respuesta predefinida, no de IA. Se puede mostrar un indicador discreto |
| `disclaimer` | **Mostrar siempre** como aviso fijo junto al chat |
| `cautions` | **Mostrar siempre** junto a la respuesta, aunque el texto del modelo ya las mencione |

El endpoint no devuelve 5xx por culpa de los proveedores de IA: sin claves, con ambos caídos o con el tope diario agotado contesta con `fallback: true`. Los errores posibles son 422 (entrada) y 429 (demasiadas peticiones).

**`GET /chat/suggestions?target=WISE_0855`**: `target` acepta clave o nombre (`WISE_0855`, `wise-0855`, `Barnard's Star`). Sin `target` o con uno desconocido devuelve la lista genérica y `"target": null`. Respuesta real:

```json
{
  "target": "WISE_0855",
  "suggestions": [
    "What is a brown dwarf?",
    "Why can it barely be seen in visible light?",
    "How far away is it?",
    "What is SPHEREx?"
  ]
}
```

### 2.3 Juego "Encuentra lo que se mueve"

Solo usa Barnard's Star y Epsilon Indi B. El servidor guarda la respuesta correcta y solo la revela al responder la ronda.

**Flujo completo:**

1. `POST /game/sessions` (cuerpo opcional `{"rounds": 3}`, de 3 a 5, por defecto 5). Devuelve `session_id` y las rondas, ordenadas de fácil a difícil (con 5 rondas: 1 `easy`, 3 `medium`, 1 `hard`).
2. Para cada ronda, el navegador alterna `image_b_url` (visita anterior, `epoch_b`) e `image_a_url` (visita posterior, `epoch_a`): el efecto blink. Son recortes de `image_size` (300×300), cada uno con el objeto en un sitio distinto.
3. Si la ronda trae `hint_image_url` (solo en `weak_signal: true`), ofrecer un botón "Show hint": es la resta posterior − anterior del mismo recorte; un objeto que se mueve deja una pareja rojo-azul. Con `weak_signal: true` avisar de que el movimiento es menor que un píxel de SPHEREx.
4. El jugador hace clic. El frontend convierte el clic a **píxeles de la imagen** (`x = clickX * image_size.width / anchoMostrado`, origen arriba a la izquierda, +y hacia abajo) y llama a `POST /game/guess`.
5. La respuesta de `guess` trae `hit`, `distance_px`, `score`, `answer` (punto medio del camino del objeto), `answer_path` (posición en `epoch_b` y en `epoch_a`, para dibujar una flecha), `hit_radius_px` y `explanation_es` (en inglés, con la medida real). **Solo hay un intento por ronda.**
6. Tras la última ronda, `POST /game/finish` con `session_id` y `nickname`. El servidor suma las rondas jugadas y guarda la puntuación. Las rondas sin jugar cuentan 0. Hace falta haber jugado al menos una.
7. `GET /game/leaderboard` para la clasificación.

**Puntuación (por ronda, 0 a 1000, la calcula el servidor):** fallo = 0 si el intento está a más de 20 px del camino del objeto. Acierto = `500 + 300·(1 − d/20) + 200·max(0, 1 − t/30 s)`, con `d` en píxeles y `t` = `time_ms`.

**1. Empezar** (se muestran 2 de las 3 rondas; la primera es `easy` y no lleva pista):

```json
{
  "session_id": "7e383e91-8ac4-44b1-b194-63c3ee24db70",
  "expires_at": "2026-11-14T23:22:51.213892Z",
  "rounds": [
    {
      "round_id": "7ab220fc6477",
      "target_id": "BARNARDS_STAR",
      "target_name": "Barnard's Star",
      "kind": "first_vs_last",
      "epoch_a": "2025_M12",
      "epoch_b": "2025_M0",
      "date_a": "2026-08-21T15:37:09Z",
      "date_b": "2025-08-27T09:09:36Z",
      "image_a_url": "https://spherex-api.onrender.com/static/game/4787093535b90c470046ab5f.png",
      "image_b_url": "https://spherex-api.onrender.com/static/game/e07f8aa818b80a1aea2c244d.png",
      "hint_image_url": null,
      "image_size": {
        "width": 300,
        "height": 300
      },
      "weak_signal": false,
      "difficulty": "easy",
      "delta_t_days": 359.27,
      "expected_shift_arcsec": 10.22
    },
    {
      "round_id": "4d29e8f5a4ef",
      "target_id": "BARNARDS_STAR",
      "target_name": "Barnard's Star",
      "kind": "consecutive",
      "epoch_a": "2025_M12",
      "epoch_b": "2025_M7",
      "date_a": "2026-08-21T15:37:09Z",
      "date_b": "2026-03-17T16:23:42Z",
      "image_a_url": "https://spherex-api.onrender.com/static/game/a670672dfc6adc5b50e4b0b5.png",
      "image_b_url": "https://spherex-api.onrender.com/static/game/78009ea75021b64f5ef3883f.png",
      "hint_image_url": "https://spherex-api.onrender.com/static/game/82ce02015dace54bd166d6d8.png",
      "image_size": {
        "width": 300,
        "height": 300
      },
      "weak_signal": true,
      "difficulty": "medium",
      "delta_t_days": 156.97,
      "expected_shift_arcsec": 4.47
    }
  ],
  "scoring": "Miss (more than 20 pixels from the object's path): 0 points. Hit: 500 points, plus up to 300 for precision and up to 200 for speed (if you answer in under 30 s). Up to 1000 per round."
}
```

**2. Responder:**

```json
{"session_id": "<session_id>", "round_id": "<round_id>", "x": 150.0, "y": 150.0, "time_ms": 6400}
```

Respuesta real a un acierto:

```json
{
  "hit": true,
  "distance_px": 2.1,
  "score": 926,
  "answer": {
    "x": 104.8,
    "y": 59.8
  },
  "answer_path": [
    {
      "x": 104.0,
      "y": 70.0
    },
    {
      "x": 105.6,
      "y": 49.6
    }
  ],
  "hit_radius_px": 20.0,
  "explanation_es": "You found it! Barnard's Star moved about 10.2″ in 359 days (20 pixels in this image), according to its proper motion measured by Gaia DR3; its position in these SPHEREx images matches that prediction."
}
```

`x` e `y` deben estar dentro de la imagen (0 ≤ valor < 300); `time_ms` entre 0 y 600 000.

**3. Terminar:**

```json
{"session_id": "<session_id>", "nickname": "Tombaugh_30"}
```

```json
{
  "session_id": "7e383e91-8ac4-44b1-b194-63c3ee24db70",
  "nickname": "Tombaugh_30",
  "score": 926,
  "hits": 1,
  "rounds_played": 1,
  "rounds_total": 3,
  "stored_in": "memory"
}
```

El alias debe cumplir 3 a 16 caracteres entre letras (con tildes y ñ), dígitos, `_` y `-`.

**4. Clasificación** (`GET /game/leaderboard`), ordenada por puntuación y, en empate, gana la más antigua:

```json
{
  "entries": [
    {
      "nickname": "Tombaugh_30",
      "score": 926,
      "hits": 1,
      "rounds_played": 1,
      "rounds_total": 3,
      "created_at": "2026-11-14T23:07:51.230556Z"
    }
  ],
  "backend": "memory"
}
```

**Errores del juego** (los textos son los que llegan en `detail`):

| Código | Cuándo | `detail` real | Qué hacer |
|---|---|---|---|
| 404 | `session_id` desconocido (también tras reiniciarse el servidor) | `Game not found.` | Empezar partida nueva |
| 404 | `round_id` no pertenece a la sesión | `Round not found in this game.` | Error de programación |
| 409 | Segundo intento en la misma ronda | `You already answered this round: there is one attempt per round.` | Pasar a la siguiente ronda |
| 409 | Partida ya terminada, o `finish` repetido | `This game is already finished.` | Mostrar clasificación |
| 409 | `finish` sin haber jugado ninguna ronda | `Play at least one round before saving your score.` | Pedir jugar una ronda |
| 410 | Sesión caducada (15 min sin actividad) | `This game has expired. Start a new one.` | Empezar partida nueva |
| 422 | `x`/`y` fuera de la imagen | `x and y must be inside the 300x300 image` | Ajustar el clic |
| 422 | Alias inválido | `nickname: Value error, nickname: 3-16 characters, letters, digits, '_' or '-'` | Mostrar el mensaje en el campo |
| 422 | El cliente envía `score` u otro campo no permitido | `score: Extra inputs are not permitted` | No enviar nunca la puntuación |
| 429 | Demasiadas peticiones | ver 4.3 | Esperar `Retry-After` |
| 503 | Demasiadas partidas activas (2000) o faltan los datos en caché | `Too many games in progress. Try again in a minute.` / `The game is not available: the cached data is missing.` | Reintentar en un minuto |

Lancé a mano con el cliente de pruebas: 404 de sesión, 409 de segundo intento y de `finish` repetido, 422 de `x`/`y`, alias y `score`. El 410, el 429, el 404 de ronda y el 409 de `finish` sin jugar están cubiertos por tests (`tests/test_game.py`), pero no los lancé a mano: sus textos salen del código. **El 503 no tiene test: sin verificar.**

### 2.4 `GET /loading-phrases`

Responde sin depender de la base de datos, de la IA ni de los datos en caché, así que contesta aunque lo demás falle. Lleva `Cache-Control: public, max-age=3600`. Respuesta real:

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
      "Spot the difference, but the difference is a planet.",
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

Cómo usarlas: ver 4.4.

### 2.5 Endpoints que ya existían y han cambiado

Todos los cambios son aditivos, salvo los textos, que ahora están en inglés (sección 3).

| Endpoint | Cambio |
|---|---|
| `GET /sky-patch` y `GET /spectrum` | Campos nuevos: `science_note`, `quality`, `fallback_reason`, `data_credits`. `/spectrum` ya trae `saturated` y `spectrum_message` (también en `sky-patch`) |
| `GET /sky-patch` | `difference_imaging` trae `kind`, `delta_t_days`, `detectors_used`, `expected_shift_arcsec`, `weak_signal` y la lista `pairwise` (una resta por cada par de visitas consecutivas) |
| `GET /epochs` | Campo nuevo `fallback_reason` |
| `GET /jobs/{id}` | Un trabajo perdido (por reinicio del servidor o por caducidad) responde 404 con `code: "job_not_found"` |
| `GET /credits` (y `/api/v1/credits`) | Nuevo: fuentes de datos para la atribución |

Valores reales de `GET /sky-patch?target=BARNARDS_STAR` para esos campos:

```json
{
  "saturated": true,
  "quality": "low",
  "science_note": "The core saturates SPHEREx: there is no valid spectrum or light curve of the star. Its proper motion does show: between the first and the last visit its centroid moves 10.3″ north, against 10.2″ expected.",
  "spectrum_message": "The source core saturates SPHEREx (OVERFLOW/BLOOM flags): those pixels are shown in the images but excluded from the spectrum and the light curve.",
  "fallback": false,
  "fallback_message": null,
  "fallback_reason": null,
  "light_curve[0]": {
    "epoch_id": "2025_M0",
    "date": "2025-08-27",
    "flux_mjy_sr": null,
    "variance": null,
    "note": "No valid photometry: the core saturates SPHEREx (OVERFLOW/BLOOM)"
  },
  "difference_imaging (sin anomalías)": {
    "epoch_a": "2025_M12",
    "epoch_b": "2025_M0",
    "image_format": "png",
    "subtracted_image_url": "https://spherex-api.onrender.com/static/golden_path/barnardsstar_diff.png",
    "detection_label": "Software-Assisted Detection (BETA)",
    "detection_notice": "Anomaly detected by software: requires human verification",
    "kind": "first_vs_last",
    "delta_t_days": 359.27,
    "detectors_used": [
      1,
      2,
      3,
      4,
      5,
      6
    ],
    "expected_shift_arcsec": 10.22,
    "weak_signal": false
  },
  "difference_imaging.pairwise[0]": {
    "kind": "consecutive",
    "epoch_a": "2025_M7",
    "epoch_b": "2025_M0",
    "image_format": "png",
    "subtracted_image_url": "https://spherex-api.onrender.com/static/golden_path/barnardsstar_diff_epoch1_epoch2.png",
    "delta_t_days": 202.3,
    "detectors_used": [
      1,
      2,
      3,
      4,
      5,
      6
    ],
    "expected_shift_arcsec": 5.76,
    "weak_signal": true
  },
  "data_credits[0]": {
    "id": "spherex_dr1",
    "source": "SPHEREx (NASA/JPL/Caltech), served by IRSA/IPAC",
    "dataset": "SPHEREx Data Release 1, collection spherex_dr1",
    "used_for": "All images, difference images, spectra and light curves: Level 2 Spectral Images (MEF), downloaded as server-side cutouts",
    "citation": null,
    "doi": "10.26131/IRSA678",
    "url": "https://www.ipac.caltech.edu/doi/10.26131/IRSA678",
    "access": "https://irsa.ipac.caltech.edu/SIA"
  }
}
```

Para WISE 0855 (`quality` vale `"unvalidated"`, no `"low"`):

```json
{
  "quality": "unvalidated",
  "science_note": "Unvalidated data. In the broadband images WISE 0855 is blended with a neighboring fixed star (~11″ away) that dominates at 1 µm: the spectrum and light curve of this patch must not be read as a detection or as the spectrum of a Y dwarf. Its motion is confirmed only in detector 6 (4.4–5 µm): the centroid moves 7.9″ west between the first and the last visit, against 8.05″ expected. What can be said is that the patch follows its proper motion: it is centered on its position at the first visit, ~2′ west of its 2010 catalog position.",
  "light_curve[0].note": "Unvalidated: the flux is blended with a neighboring fixed star (~11″ away); it does not show the variability of WISE 0855."
}
```

Significado de los campos de cautela:

- `quality`: `null` (sin reserva), `"low"` (núcleo saturado: los números son límites inferiores) o `"unvalidated"` (WISE 0855: datos mezclados con una estrella vecina). En las bandas del espectro (`spectrum_102_bands[].quality`), lo observado fue `null` o `"low"`.
- `fallback_reason`: `null` si no hubo fallback; si no, `irsa_unavailable`, `live_processing_unavailable` o `unknown`. Con `fallback: true`, el texto para mostrar está en `fallback_message`.
- `weak_signal` (en `difference_imaging` y en cada par de `pairwise`): `true` si el desplazamiento esperado es menor que un píxel de SPHEREx (6,15″). Los pares consecutivos de los 5 objetos son siempre `true`.

### 2.6 `GET /catalog-objects`: objetos de SIMBAD sobre la imagen

Marcadores para dibujar sobre la imagen de SPHEREx. **El contrato completo, los ejemplos reales y la verificación están en [wow-contract.md, apartado 5](wow-contract.md#5-objetos-catalogados-en-simbad-get-apiv1catalog-objects).** Resumen para quien integra:

- Parámetros: `target` **o** `ra`+`dec` (como `/sky-patch`), `epoch` (visita, solo Golden Path), `max_n` (1–50, 20 por defecto), `type_filter` (`all`, `no_common_stars`, `ir_galaxies`). Orden fijo: más referencias bibliográficas primero.
- Cada objeto trae `x`, `y` en píxeles **de la imagen que se ve**, con el origen arriba a la izquierda (como el juego): `left = x * anchoMostrado / image_size.width`. No hay que invertir ningún eje.
- Las posiciones ya están movidas a la fecha de la visita con el movimiento propio de SIMBAD. Los objetos que SIMBAD no mueve llevan `proper_motion_applied: false`.
- Siempre 200 si la petición es válida. Con SIMBAD caído llega `source: "cache" | "precomputed" | "none"`, `fallback_reason` y `fallback_message` (inglés, para mostrar tal cual).
- Límite: 20 peticiones por IP y minuto (429 con `Retry-After`). La primera petición a un campo nuevo puede tardar hasta unos 5 s (SIMBAD); las repetidas salen de la caché del servidor.

```json
{
  "objects": [
    {"id": "NAME Barnard's star", "otype": "BY*", "otype_label": "BY Dra Variable", "x": 300.0, "y": 300.0,
     "mag": 9.511, "mag_band": "V", "nbref": 924,
     "simbad_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=NAME%20Barnard%27s%20star",
     "proper_motion_applied": true}
  ],
  "image_size": {"width": 600, "height": 600},
  "epoch_applied": {"epoch_id": "2025_M0", "date": "2025-08-27T09:09:36Z", "julian_year": 2025.653},
  "truncated": false,
  "source": "simbad",
  "fallback_reason": null,
  "data_credits": ["... SIMBAD, spherex_dr1, spherex_qr3 ..."],
  "notice": "Objects catalogued in SIMBAD, shown at their expected position on this image. They are not SPHEREx detections.",
  "fallback_message": null
}
```

(Un solo objeto de los devueltos y `data_credits` abreviado; la respuesta completa está en el contrato.)

## 3. Cambios que pueden romper el frontend actual

Comparado con el frontend de `main` (`210e2ba`, leído en solo lectura).

| # | Antes (lo que esperaba el frontend) | Ahora | Qué cambiar |
|---|---|---|---|
| 1 | Textos de la API en español (`fallback_message`, `message` de los jobs, `detection_label`, `detection_notice`, `spectrum_message`, `note` de la curva de luz) | Todo en inglés. Ejemplos: `Showing cached data because the source servers are responding slowly`, `Software-Assisted Detection (BETA)`, `Queued...` | No comparar textos. Ramificar con `code`, `fallback_reason` y `stage`; mostrar `detail` o `message` tal cual |
| 2 | `description_es`, `story_es` y `explanation_es` en español por su nombre | Contienen **inglés**. Los nombres no cambian para no romper el contrato | Tratarlos como texto en inglés. No traducirlos ni filtrarlos por idioma |
| 3 | La clasificación del juego como lista | `GET /game/leaderboard` devuelve `{"entries": [...], "backend": "memory"|"supabase"}` | Leer `body.entries`. Si `backend` es `"memory"`, la clasificación se pierde al reiniciar: se puede avisar |
| 4 | `hint_image_url` en todas las rondas | Solo viene en rondas `weak_signal: true`; en las demás es `null` | Mostrar el botón de pista solo si hay URL |
| 5 | `detail` de error como lista (formato FastAPI) | `detail` es siempre un texto; los campos con problema van en `errors` | `src/api/problem.ts` de `main` ya entiende los dos formatos, pero `src/api/http.ts` sigue lanzando `ApiError(res.status, await res.text())` con el JSON en bruto. **Sin verificar** que todas las pantallas pasan por `problem.ts` |
| 6 | Sin código para un trabajo perdido | 404 con `code: "job_not_found"` al consultar un job tras un reinicio | Volver a lanzar `POST /jobs` con las mismas coordenadas en vez de mostrar el error |
| 7 | Tipos de `SkyPatchResponse` sin `science_note`, `quality` (de respuesta), `fallback_reason` ni `data_credits` | Siempre presentes (pueden ser `null`) | Añadirlos a `src/types/api.ts` y mostrarlos (sección 4.1) |
| 8 | Números con coma decimal en textos (`10,2″`) | Punto decimal (`10.2″`) y miles con coma (`2,600`) | Solo afecta si se analizan los textos; no hacerlo |

## 4. Reglas de interfaz que el frontend debe respetar

### 4.1 Cautelas siempre visibles

- Mostrar `cautions` del chat junto a cada respuesta y el `disclaimer` como aviso fijo (texto real: `AI assistant: it can make mistakes. Any finding requires human verification.`).
- Mostrar `science_note` siempre que no sea `null` (panel del objeto y panel de espectro), y `quality` como etiqueta: `low` = "lower limit, do not use to claim variability"; `unvalidated` = "unvalidated data".
- Si `saturated` es `true`: indicarlo en el espectro y en la curva de luz. Los puntos de la curva con `flux_mjy_sr: null` no se dibujan como 0; su `note` explica por qué.
- Si `weak_signal` es `true`: avisar de que el movimiento es menor que un píxel de SPHEREx.
- Si `fallback` es `true`: mostrar `fallback_message` de forma visible; no son los datos de las coordenadas pedidas.
- Nunca presentar nada como descubrimiento. `detection_label` y `detection_notice` van con cualquier marcador de anomalía.
- WISE 0855, RW Aurigae y R Draconis: solo curvas de luz, brillo o imagen, nunca la resta de banda ancha ni "anomalías" (son en buena parte falsas).

### 4.2 La puntuación no se envía

`guess` y `finish` no aceptan una puntuación del cliente: si se envía `score` o cualquier campo extra, el servidor responde 422 (`Extra inputs are not permitted`). El `score` que se muestra es el de la respuesta.

### 4.3 Errores y límite de peticiones

El 429 llega con este cuerpo y la cabecera `Retry-After` (segundos):

```json
{"detail": "Too many chat requests. Wait 37 s and try again.", "code": "http_429", "errors": null}
```

(cabecera real: `Retry-After: 37`; el número cambia según cuándo se alcance el límite.)

| Código | Mensaje que llega | Qué hacer |
|---|---|---|
| 429 | `Too many chat requests. Wait N s and try again.` (también `new game`, `guess`, `score`) | Desactivar el botón y reactivarlo pasados `Retry-After` segundos. No reintentar en bucle |
| 422 | `detail` con el campo y el motivo, y `errors[]` | Mostrar `detail` junto al campo |
| 404/410 en el juego | ver la tabla de 2.3 | Ofrecer "Start a new game" |
| 503 | ver la tabla de 2.3 | Reintentar en un minuto |
| Sin respuesta (timeout del cliente, 20 s) | Texto propio del frontend | Mostrar frases `cold_start` y reintentar una vez |

Límites por IP y por minuto (valores por defecto): chat 8, nuevas partidas 10, intentos 60, `finish` 5. Tope diario global del chat: 1000 llamadas a los proveedores de IA; al llegar al tope el chat sigue respondiendo con respuestas predefinidas (`fallback: true`).

### 4.4 Frases de carga (sugerencia)

Categorías: `generic`, `cold_start`, `subtraction`, `game`, `chat`. **Esto es una propuesta, no un requisito del backend:**

| Situación | Categoría | Cuándo empezar |
|---|---|---|
| Primera petición de la sesión, el servidor gratuito puede estar dormido | `cold_start` | Si no hay respuesta pasados ~3 s |
| Carga normal de datos de un objeto | `generic` | Tras ~1 s |
| Carga de la capa de resta | `subtraction` | Al pedir la resta |
| Preparando una partida | `game` | Al llamar a `POST /game/sessions` |
| Esperando al asistente | `chat` | Al enviar la pregunta |

Elegir una al azar y cambiarla cada 4 a 5 s. Como el endpoint puede estar durmiendo, el frontend debería llevar una **copia local** de las frases (por ejemplo las de arriba) y refrescarla del servidor cuando responda. Una frase de la lista, "Spot the difference, but the difference is a planet.", sugiere que hay un planeta; si se quiere respetar la regla de no afirmar hallazgos, conviene cambiarla en `app/core/loading_phrases.py`.

### 4.5 Marcadores de SIMBAD: catálogo, no descubrimiento

1. **Rotular como catálogo.** Son objetos "catalogued in SIMBAD", **no detecciones de SPHEREx**. Mostrar `notice` (o "Catalogued in SIMBAD") junto a la capa. No usar "detected", "found" ni "discovered", y no mezclarlos con la capa de anomalías (`detected_anomalies`), que sí es una detección por software.
2. Mostrar `fallback_message` si `fallback_reason` no es `null`.
3. Citar SIMBAD/CDS con `data_credits` o `GET /credits`.
4. Agrupar marcadores a pocos píxeles: los planetas de una estrella tienen su misma posición (Barnard's Star: cuatro planetas en el píxel de la estrella).
5. `x`, `y` de un objeto sin movimiento propio (`proper_motion_applied: false`) es su posición J2000, no necesariamente la actual.

## 5. Variables de entorno y configuración (backend)

**Ninguna es obligatoria**: el backend arranca sin ellas. Lo que cambia si faltan:

| Variable | Valor por defecto | Si falta |
|---|---|---|
| `GROQ_API_KEY` | sin definir | Se salta Groq |
| `DEEPSEEK_API_KEY` | sin definir | Se salta DeepSeek. Sin ninguna de las dos claves, el chat responde solo con respuestas predefinidas (`provider: "canned"`, `fallback: true`) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Usa el valor por defecto |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Usa el valor por defecto |
| `CHAT_PROVIDER_TIMEOUT_S` | `8` | Segundos por proveedor |
| `CHAT_RATE_PER_MIN` | `8` | Peticiones de chat por IP y minuto |
| `CHAT_DAILY_PROVIDER_CAP` | `1000` | Llamadas diarias a proveedores, todos los usuarios |
| `CHAT_CACHE_TTL_S` | `3600` | Vida de la caché de respuestas |
| `GAME_SESSION_TTL_S` | `900` | Caducidad de una partida inactiva |
| `SIMBAD_TAP_URL` | `https://simbad.cds.unistra.fr/simbad/sim-tap/sync` | Servicio TAP síncrono. Con CDS caído: `https://simbad.cfa.harvard.edu/simbad/sim-tap/sync` |
| `SIMBAD_TIMEOUT_S` | `5` | Segundos hacia SIMBAD antes de servir caché o precalculado |
| `SIMBAD_RATE_PER_MIN` | `20` | Peticiones a `/catalog-objects` por IP y minuto |
| `SIMBAD_GLOBAL_QPS` | `3` | Tope global de consultas por segundo hacia SIMBAD (todos los usuarios) |
| `SIMBAD_CACHE_TTL_S` | `3600` | Vida de la caché en memoria |
| `SIMBAD_CACHE_MAX_ENTRIES` | `500` | Entradas de esa caché |
| `SIMBAD_FETCH_LIMIT` | `200` | Filas pedidas a SIMBAD por consulta |
| `GAME_SESSIONS_PER_MIN`, `GAME_GUESSES_PER_MIN`, `GAME_FINISH_PER_MIN` | `10`, `60`, `5` | Límites por IP |
| `GAME_CROP_PX` | `300` | Lado de las imágenes de ronda (el frontend lo lee de `image_size`) |
| `GAME_SALT` | aleatoria en cada arranque | Fija los nombres de los recortes entre reinicios |
| `SUPABASE_URL`, `SUPABASE_KEY` | sin definir | La clasificación y los reportes se guardan en memoria y se pierden al reiniciar (`backend: "memory"`) |
| `PUBLIC_BASE` | sin definir | Las URLs absolutas de las imágenes se construyen con el host de cada petición. Funciona detrás del proxy de Render con `--proxy-headers`; **sin verificar en Render** |

Para el frontend, la variable que ya existe es `VITE_API_BASE_URL` (la URL del backend). Las claves de IA **solo** van en el backend, nunca en el navegador.

**Paso manual de Supabase** (solo si se quiere que la clasificación sobreviva a los reinicios): pegar `supabase/game_scores.sql` en Supabase Dashboard → SQL Editor → Run, y **reiniciar la API** (comprueba la tabla una sola vez al arrancar; si no existe, usa memoria). El script es idempotente. Aviso: la clave `anon` permite insertar filas directamente; mantenerla solo en el servidor. El propio SQL trae un bloque opcional, comentado, para restringir la inserción a `service_role`. **Sin verificar:** nunca se ha ejecutado contra un proyecto Supabase real.

## 6. Qué puede construir ya el frontend

| Pantalla o componente | ¿Listo en el backend? | Notas |
|---|---|---|
| Subtítulos de distancia por objeto | Sí | `GET /targets`: `story_es` + `description_es`. Si `distance_uncertain`, el texto ya está redactado como aproximado |
| Chat de IA con aviso fijo | Sí | `POST /chat` con el JSON de contexto. Mostrar `cautions` y `disclaimer` |
| Selector de tono ("explain it simply" / "technical") | Sí (solo falta la interfaz) | El backend ya acepta `tone: "easy" | "technical"` |
| Preguntas sugeridas | Sí | `GET /chat/suggestions?target=` devuelve 4 por objeto |
| Botón "Explain what I see" | Sí | Es un `POST /chat` con el contexto de la pantalla; el frontend monta la pregunta y el contexto |
| Juego completo con clasificación | Sí | Flujo de 2.3. Falta la interfaz de blink, el cronómetro y la pista |
| Frases de carga | Sí | Sección 4.4 |
| Tarjeta compartible "Star Hunter" | **No** | El backend no genera imágenes de tarjeta. `finish` devuelve `nickname`, `score`, `hits` y `rounds_*`; la tarjeta se dibuja en el navegador (el generador actual, `shareCard.ts`, está atado a un `CitizenReport`: hace falta otra función de dibujo) |
| Cortinilla visible/infrarrojo | No hace falta backend | Son dos capas HiPS de Aladin. El 2026-11-14 comprobé con `curl` que `properties` de DSS2 color y de AllWISE en el mirror de IRSA responden 200 (el de CDS daba 404). **No repetido hoy** |
| "Apunta tu móvil al cielo", sonificación | No hace falta backend | Son funciones del navegador |

## 7. Hallazgos del frontend (auditoría de `spherex-web`, solo lectura)

Revisado el `main` remoto (`210e2ba`); mi clon local estaba desactualizado, así que usé una descarga aparte.

| Dónde | Hallazgo | Arreglo |
|---|---|---|
| `src/api/http.ts:40`, `:60`, `:85` | `` `Sin respuesta del servidor en ${timeoutMs} ms` ``: mensaje visible en español | Pasarlo a inglés, por ejemplo `` `No response from the server in ${timeoutMs} ms` `` |
| `src/components/SphereXView.tsx:28`, `:98` | `toLocaleDateString(undefined, ...)` usa el idioma del navegador | Fijar `'en-US'` |
| `src/components/LightCurvePanel.tsx:31`, `:39` | Igual | Fijar `'en-US'` |
| `src/lib/shareCard.ts:176` | Igual (la fecha sale en la tarjeta compartida) | Fijar `'en-US'` |
| `src/lib/reports.ts:27` | `new Date(iso).toLocaleDateString()` sin argumentos | Fijar `'en-US'` |
| `src/types/api.ts:88-89` | Comentarios con ejemplos en español | Solo comentarios, no es visible |
| `src/api/http.ts:43`, `:63`, `:88` | `ApiError(res.status, await res.text())` guarda el JSON de error en bruto | Pasar por `src/api/problem.ts` para mostrar `detail` (ver 3, fila 5). Sin verificar si ya se hace en cada pantalla |

El resto de la interfaz que revisé (texto de componentes) ya estaba en inglés.

## 8. Límites conocidos y sin verificar

**Sin verificar:**
- **Llamadas reales a Groq y DeepSeek:** no hay claves en este entorno. Los nombres de modelo y los parámetros (`reasoning_effort`, `include_reasoning`, `thinking: disabled`) salen de la documentación oficial de cada proveedor. No se comprobó que respondan en inglés a una pregunta en español.
- **Supabase real:** ni la tabla `game_scores` ni el modo `backend: "supabase"`.
- **Despliegue en Render:** todo se probó en local. Los recortes del juego se escriben en `app/static/game/` (disco efímero) y las partidas están en memoria: tras un reinicio, una partida en curso responde 404 (`Game not found.`).
- **El frontend contra estos cambios:** no se ha ejecutado.
- **Error 503 del juego:** sin test.

**Límites conocidos:**
- **Límite por IP aproximado:** está en memoria, de una sola instancia, y se reinicia con cada arranque. Con `--proxy-headers`, falsear `X-Forwarded-For` permite saltárselo. El tope diario global del chat sí protege el gasto.
- **`time_ms` lo envía el cliente:** se puede falsear. Como mucho aporta 200 de los 1000 puntos de una ronda.
- **El objeto puede caer en el centro:** cada ronda coloca el objeto en una de 25 posiciones (rejilla 5×5), y una de ellas es el centro, así que en aproximadamente 1 de cada 25 rondas lo está. En mi prueba con `curl`, un clic en el centro (150, 150) acertó por esa casualidad.
- **La clave `anon` de Supabase** permite insertar filas sin pasar por la API; guardarla solo en el servidor.
- **Respuestas predefinidas del chat:** se eligen por palabras clave (inglés y español). Una pregunta que no encaja recibe un texto genérico con `fallback: true`.
- **Clasificación en memoria** si no hay Supabase: se pierde al reiniciar.
- **El chat no recuerda nada entre peticiones:** el frontend debe reenviar `history` (máximo 6 turnos usados).
- **Barnard es muy brillante** en los recortes, así que sus rondas son más fáciles de ver.

## 9. Cómo probar en local

```bash
# Backend (desde la raíz de spherex-api; sin claves de IA ni Supabase funciona todo)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
LIVE_WARMUP=0 uvicorn app.main:app --port 8000

# Frontend, en otra terminal (el proxy de Vite apunta a localhost:8000)
cd ../spherex-web && npm ci && npm run dev

# Tests del backend (sin red)
pytest -q
```

Cinco llamadas listas para pegar. Las verifiqué contra un `uvicorn` local; sustituye `<...>` por los valores de la respuesta anterior.

```bash
B=http://localhost:8000

# 1. Subtítulos: distancias y frases de los 5 objetos
curl -s $B/api/v1/targets | python3 -m json.tool | head -30

# 2. Frases de carga (mira la cabecera Cache-Control)
curl -si $B/api/v1/loading-phrases | head -12

# 3. Chat (sin claves de IA devuelve provider "canned", fallback true)
curl -s -X POST $B/api/v1/chat -H 'content-type: application/json' \
  -d '{"message":"What is a brown dwarf?","tone":"easy","context":{"target":"WISE_0855"}}'

# 4. Juego: empezar, responder y terminar
S=$(curl -s -X POST $B/api/v1/game/sessions -H 'content-type: application/json' -d '{"rounds":3}')
SID=$(echo "$S" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
RID=$(echo "$S" | python3 -c "import sys,json; print(json.load(sys.stdin)['rounds'][0]['round_id'])")
curl -s -X POST $B/api/v1/game/guess -H 'content-type: application/json' \
  -d "{\"session_id\":\"$SID\",\"round_id\":\"$RID\",\"x\":120,\"y\":90,\"time_ms\":5000}"
curl -s -X POST $B/api/v1/game/finish -H 'content-type: application/json' \
  -d "{\"session_id\":\"$SID\",\"nickname\":\"Demo_Player\"}"
curl -s $B/api/v1/game/leaderboard

# 5. Cautelas científicas: R Draconis está saturada
curl -s "$B/api/v1/sky-patch?target=R_DRACONIS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['saturated'], d['quality']); print(d['science_note'])"
```

Para forzar un 429 sin esperar: `CHAT_RATE_PER_MIN=1 LIVE_WARMUP=0 uvicorn app.main:app --port 8000` y repetir dos veces la llamada 3 (la segunda devuelve 429 con `Retry-After`).
