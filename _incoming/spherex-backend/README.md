# SPHEREx Blink API (backend)

```bash
pip install -r requirements.txt
python precompute.py                      # golden path: los 5 objetos del reto (hazlo ANTES de la demo)
PUBLIC_BASE=https://tu-servidor.com/static uvicorn app:app --host 0.0.0.0 --port 8000
```

## Flujo recomendado en el frontend
1. Usuario entra `?ra=&dec=&fov=` (fov en grados).
2. Llamada inmediata a `GET /api/v1/epochs` (1-5 s) → pinta la línea de tiempo y "procesando N imágenes…".
3. `GET /api/v1/sky-patch` → cuando llega, arranca el blink (las PNG comparten escala entre épocas).
   - Objetos del reto: respuesta en ms (snap automático a <1' de un objeto pre-calculado).
   - Coordenadas nuevas: 20-90 s en frío (depende de IRSA). Baja `max_per_det` (p.ej. 4) para ir más rápido.
4. Clic en una fuente → `GET /api/v1/spectrum`.

## Notas
- Épocas = semestres desde 2025-05-01 (`SPHEREX_EPOCH0`). Cada semestre ≈ un barrido completo del cielo.
- La resta compara la PRIMERA y la ÚLTIMA época (más desplazamiento). Píxel = 6.15": Barnard se mueve ~1.7 px/año.
- Datos nuevos: no hay que tocar nada; `query_images` siempre pregunta a IRSA. Para refrescar un parche: `&force=true`
  o borrar `static/<clave>/result.json`. Los recortes ya bajados se reutilizan desde `cache/`.
- `SPHEREX_KEEP_FLAGS` (por defecto `SOURCE`): bits de FLAGS que NO se enmascaran.
