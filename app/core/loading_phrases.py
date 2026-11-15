"""Frases para las pantallas de carga (GET /api/v1/loading-phrases).

Texto visible para el usuario: en inglés y en tono ligero. Este módulo no
importa nada del resto de la app (ni base de datos, ni proveedores de IA, ni
datos preprocesados) porque el frontend lo pide mientras el servidor despierta.

Regla de contenido para frases nuevas: ninguna puede afirmar algo que no
podamos respaldar (nada de "Planet X is here", "we found something"...).
Solo ASCII, sin comillas tipográficas: lo comprueba tests/test_loading_phrases.py.
"""

from __future__ import annotations

LOADING_PHRASES: dict[str, tuple[str, ...]] = {
    "generic": (
        "Warming up the telescope. It's very cold up there.",
        "Counting photons one by one. Please hold.",
        "Asking the universe to hold still for a second.",
        "Polishing a mirror we definitely don't own.",
        "Convincing light to arrive faster. It's not listening.",
        "Dusting off 102 infrared colors.",
        "Aligning the stars. Some are being difficult.",
        "Loading the cosmos. It's a big file.",
    ),
    "cold_start": (
        "Our server is waking up. It doesn't do mornings either.",
        "Brewing coffee for the backend. Almost ready.",
        "The API is stretching. Great things need warm-ups.",
    ),
    "subtraction": (
        "Subtracting the stars that stayed. Looking for the ones that sneaked away.",
        "Spot the difference. Sometimes the difference moves.",
        "Hunting things that move while pretending not to.",
        "Making the boring stars disappear.",
    ),
    "game": (
        "Shuffling the sky. No cheating, we checked.",
        "Hiding something in the stars. Good luck.",
        "Your 1930 astronomer moment is loading.",
    ),
    "chat": (
        "Asking a very smart parrot who has read a lot of astronomy.",
        "Thinking in infrared. It takes a second.",
        'Finding words simpler than "parallax".',
    ),
}
