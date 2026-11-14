# Plan Maestro: NASA Space Apps 2026 — Reto "Planet X and SPHEREx"

## 1. El Reto y Nuestra Propuesta de Valor

**¿Qué pide la NASA?**

Desde 2025, el telescopio espacial SPHEREx mapea todo el cielo cada 6 meses en 102 bandas de luz infrarroja cercana, capturando más de mil millones de objetos. El objetivo es crear una aplicación web pública donde cualquier persona pueda introducir coordenadas celestes (Ascensión Recta y Declinación) y visualizar cómo cambia esa región del cielo con el tiempo para descubrir asteroides, cometas, enanas marrones, estrellas variables o incluso un nuevo planeta (Planeta X).

**Nuestra Visión Ganadora (Por qué vamos a destacar)**

La mayoría de los equipos construirán un simple reproductor de imágenes o GIFs. Nosotros vamos a construir una **Plataforma Gamificada de Ciencia Ciudadana Asistida por IA**. No será solo un visor pasivo, sino una estación de descubrimiento interactiva que combina los datos de múltiples telescopios, filtra firmas químicas reales y permite a cualquier ciudadano reportar hallazgos verificados.
## 2. Reglas del Juego: Qué Evalúa el Jurado de la NASA

Tanto el jurado local como los revisores científicos de la NASA califican bajo **5 criterios oficiales**. Nuestro proyecto está diseñado para puntuar al máximo en cada uno:

- **Impacto:** Transformamos una base de datos inabarcable para un solo equipo científico en una herramienta accesible para millones de ciudadanos científicos, democratizando la astronomía.
    
- **Creatividad:** Vamos más allá del "timelapse" básico añadiendo sustracción matemática de imágenes, filtros químicos por bandas infrarrojas y detección preliminar asistida por visión artificial.
    
- **Validez Científica:** Utilizamos herramientas estándar de la industria astronómica (`Astropy`, `Aladin Lite v3`, datos reales del archivo IPAC/IRSA) y documentamos nuestra metodología en Jupyter Notebooks públicos.
    
- **Relevancia:** Cumplimos el 100% de las sugerencias técnicas del reto e integramos botones de acceso directo a los **5 objetos de prueba obligatorios**: _Estrella de Barnard, Epsilon Indi B, WISE 0855, RW Aurigae y R Draconis_.
    
- **Presentación:** Interfaz de usuario en modo oscuro con estética de producto comercial moderno, repositorios impecables en GitHub y un pitch audiovisual directo al grano.

### Categorías Globales a las que Apuntamos

1. **Mejor Uso de los Datos:** Por cruzar y superponer las 102 bandas de SPHEREx con catálogos históricos de las misiones **Gaia (ESA)** y **WISE (NASA)**, además de mostrar metadatos críticos como _flags_ de contaminación.

2. **Mejor Uso de la Ciencia / Tecnología:** Por generar espectros en tiempo real (_on-the-fly_) y curvas de luz al hacer clic sobre cualquier fuente luminosa.
    
3. **Mejor Storytelling / Más Inspirador:** Por enfocar el discurso en cómo un estudiante desde su casa puede descubrir el próximo gran objeto del Sistema Solar.

### Requisitos Obligatorios (Checklist Antidescalificación)

- **Código Abierto:** Todo el código debe estar en GitHub bajo licencia abierta (MIT o Apache 2.0).

- **App Desplegada:** Los jueces no instalan proyectos en local. La web debe estar funcionando en una URL pública (Vercel/Netlify + Render/Railway).

- **Citación de Datos:** Especificar explícitamente en la web y en GitHub qué archivos y APIs de la NASA (SPHEREx, WISE, Gaia) se han utilizado.
    
- **Entregable Final:** Un video de máximo 30 segundos (o presentación de 7 diapositivas) mostrando el problema, la herramienta en acción con los objetos de prueba y el impacto global.
## 3. Funcionalidades del Producto ("Wow Factors")

### A. Núcleo del Visor (Requisitos Base)

- **Buscador de Coordenadas y FOV:** Entrada de Ascensión Recta (RA), Declinación (Dec) y selector de Campo de Visión (Field of View) para elegir entre carga rápida o rastreo de objetos veloces.
    
- **Estado en la URL (Embeddable):** Todas las búsquedas actualizan la URL (ej. `?ra=08h55m10s&dec=-7d14m42s&fov=5&band=H2O`) para poder incrustar hallazgos en foros o webs externas.
    
- **Accesos Rápidos de Demostración:** Panel destacado con los 5 objetos oficiales del reto preconfigurados para que los jueces prueben la herramienta con un solo clic.
    
- **Minimapa Espacial:** Pequeño radar en una esquina que ubica al usuario en la bóveda celeste y le indica en qué constelación está mirando.

### B. Herramientas de Detección Visual

- **Blink Comparator Interactivo:** Control manual y automático que alterna rápidamente entre imágenes de distintas épocas (ej. Mes 0 vs. Mes 6) a velocidad ajustable para que el ojo humano detecte el movimiento al instante.
    
- **Modo Resta de Imágenes (Image Subtraction):** Función que resta matemáticamente los píxeles de la Imagen A menos la Imagen B. Las estrellas fijas desaparecen en negro y solo brillan los objetos que se han movido o han cambiado de luminosidad.
    
- **Superposición Multi-Telescopio:** Interruptor para superponer capas de las misiones Gaia y WISE sobre la imagen de SPHEREx y confirmar si un objeto ya estaba catalogado.
    
### C. Explotación de las 102 Bandas Infrarrojas

- **Generador de Espectros "On-the-fly":** Al hacer clic sobre un objeto, se despliega una gráfica interactiva mostrando su intensidad lumínica a lo largo de las 102 bandas infrarrojas.
    
- **Curvas de Luz para Estrellas Variables:** Gráfica temporal que muestra cómo varía el brillo de un objeto entre cada barrido semestral (ideal para lucirse con _RW Aurigae_ y _R Draconis_).
    
- **Filtros Químicos (Falso Color RGB):** Botones preconfigurados (_"Resaltar Agua"_, _"Resaltar CO2"_, _"Resaltar Monóxido de Carbono"_) que combinan grupos de detectores para mejorar la relación señal-ruido y colorear objetos según su composición molecular.
    
### D. Ciencia Ciudadana e Inteligencia Artificial

- **Asistente de Movimiento (IA + Humano):** Script en el backend que analiza la resta de imágenes y dibuja círculos sutiles sobre píxeles sospechosos con el aviso: _"Anomalía detectada por software: requiere verificación humana"_.
    
- **Botón de Reporte y Feed Comunitario:** Permite marcar un objeto en movimiento, clasificarlo (posible asteroide, enana marrón, estrella variable) y guardarlo en un registro público de descubrimientos en tiempo real.
    
- **Tarjeta de Descubrimiento Compartible:** Generador de una imagen resumen con la captura del objeto, sus coordenadas y el nombre del descubridor lista para publicar en redes sociales.

## 4. Arquitectura Técnica y Organización en GitHub

### Stack Tecnológico Recomendado

- **Frontend:** React (Next.js o Vite), Tailwind CSS (estética espacial oscura), `Aladin Lite v3` (renderizado astronómico oficial del CDS) y `Plotly.js` o `Recharts` (espectros y curvas de luz).
    
- **Backend:** Python con `FastAPI`, `Astropy` y `Astroquery` (manejo de archivos `.fits`, coordenadas y conexión con servidores IPAC/IRSA), más `OpenCV` / `NumPy` (cálculo matricial para suma de bandas y resta de imágenes).
    
- **Base de Datos Ligera:** Supabase o Firebase (para guardar los reportes de anomalías de los usuarios en tiempo real).
    
### Estructura de nuestra Organización en GitHub

Los jueces globales evalúan la calidad de los repositorios antes de ver el código. Organizaremos nuestra cuenta en 4 repositorios limpios:

1. **`.github` (Portada de la Organización):** Contiene el `profile/README.md` público con nuestro logo, el enlace a la demo en vivo, el video de 30 segundos, la arquitectura del sistema y el índice de repositorios.
    
2. **`spherex-web` (Frontend):** Código de la interfaz. Su README incluirá capturas de pantalla, enlace a producción e instrucciones de despliegue local en 3 comandos.
    
3. **`spherex-api` (Backend):** Servidor en Python para procesamiento astronómico. Su README documentará los endpoints de la API y cómo procesamos los archivos `.fits`.
    
4. **`spherex-science-notebooks` (Validación Científica):** Repositorio con Jupyter Notebooks (`.ipynb`) mostrando las pruebas matemáticas con los 5 objetos del reto, cómo sumamos los 6 detectores para mejorar el ruido y cómo extraemos los espectros.
    

## 5. División del Equipo y Flujo de Trabajo (Estrategia "1 Claude")

Contamos con **5-6 personas y una única suscripción principal a Claude**. Para evitar cuellos de botella y código desordenado, **nosotros actuamos como Arquitectos de Sistemas y Claude como nuestro programador ejecutor**. Nadie improvisa código sin diseño previo.

### Asignación de Roles

- **Rol 1: Lead AI Developer / "AI Whisperer" (1 persona)**
    
    - Tiene el control exclusivo de la sesión de Claude y del repositorio principal.
        
    - No busca documentación ni diseña pantallas: recibe especificaciones técnicas cerradas del resto del equipo, las traduce en prompts modulares para Claude, ensambla los componentes y mantiene la aplicación desplegada sin errores.
        
        
- **Rol 2: Arquitectos de Datos y Ciencia Espacial (1-2 personas)**
    
    - Investigan en los recursos del reto cómo descargar o consultar los datos de SPHEREx, WISE y Gaia.
        
    - Usan Google Colab y otras IAs gratuitas para probar funciones de `Astropy` con los 5 objetos obligatorios. 
        
    - Entregan al _Lead AI Developer_ fragmentos de documentación oficial y scripts de Python ya validados para que Claude los convierta en endpoints de FastAPI sin alucinar funciones inexistentes.
        
        
- **Rol 3: Diseñador de Producto UX/UI (1 persona)**
    
    - Diseña en Figma (o bocetos detallados) la estructura exacta de la pantalla: posición del visor Aladin, controles del Blink Comparator, panel lateral de espectros y paleta de colores.
        
    - Entrega al _Lead AI Developer_ descripciones exactas de la interfaz para que Claude genere los componentes en React + Tailwind a la primera.
        
- **Rol 4: Product Manager, Documentación y QA (1 persona)**
      
    - Redacta y maqueta los `README.md` de toda la organización en GitHub (con diagramas en Mermaid.js, insignias y guías).
        
    - Prueba continuamente la web desplegada en busca de fallos, verifica que las URLs compartibles funcionen y asegura que cumplimos los 5 criterios de evaluación de la NASA.
        
- **Rol 5: Director de Storytelling y Producción Audiovisual (1 persona)**

    - Desde el primer día redacta el guion del pitch y estructura la presentación de 7 diapositivas.
        
    - Diseña el logo del proyecto, graba las capturas de pantalla de la herramienta en acción, locuta y edita el video final de 30 segundos con calidad profesional.

### Reglas de Oro para Programar con IA en el Hackathon

1. **Modularidad estricta:** Nunca pedir a Claude _"Haz la web completa"_. Pedir funciones aisladas: _"Crea un hook en React que sincronice las coordenadas RA y Dec con los query params de la URL"_.
    
2. **Inyección de contexto:** Antes de pedir código sobre `Aladin Lite` o `Astropy`, los Arquitectos de Datos deben copiar la documentación oficial actualizada y pegarla en el prompt de Claude.
    
3. **Mocking temprano (Datos simulados):** No esperar a tener la conexión real con la NASA para construir la web. El viernes definimos un formato JSON estándar para los espectros y las imágenes; el Frontend trabaja con ese JSON simulado mientras los Arquitectos de Datos conectan los archivos reales en el Backend.

## 6. Hoja de Ruta del Fin de Semana

- **Fase 1 — Cimientos (Viernes tarde/noche):**
      
    - Creación de los repositorios en GitHub y despliegue inicial del esqueleto web en Vercel (aunque esté vacío) para asegurar el pipeline.
        
    - Definición del contrato JSON entre Frontend y Backend.
        
    - Localización de los datasets de los 5 objetos obligatorios en los servidores de la NASA.
        
    - Diseño de la interfaz principal completado.
        
- **Fase 2 — Construcción del Núcleo (Sábado mañana y tarde):**
    
    - Integración del visor `Aladin Lite` con búsqueda por coordenadas y URLs dinámicas.

    - Programación del reproductor Blink Comparator y del endpoint de resta de imágenes en Python.
    
    - Implementación de la gráfica de espectros de 102 bandas y los filtros de falso color químico.
        
    - Redacción de los READMEs técnicos y grabación de las primeras tomas de interfaz para el video.
        
- **Fase 3 — Pulido y Storytelling (Domingo mañana):**
    
    - Congelación de código nuevo (_Code Freeze_): prohibido añadir funciones de última hora que puedan romper la demo en vivo.
        
    - Verificación exhaustiva de los 5 botones de prueba (_Barnard's Star, WISE 0855, etc._).
        
    - Cierre de edición del video de 30 segundos, subida de los Jupyter Notebooks y envío oficial del proyecto en la plataforma de NASA Space Apps.

### 7. Gestión de Riesgos, Limitaciones Técnicas y Planes de Mitigación

Un hackatón de 48 horas exige decisiones pragmáticas. Conocemos las limitaciones físicas del hardware, de los servidores gratuitos y del tiempo. A continuación, detallamos los cuellos de botella anticipados y nuestro plan para sortearlos sin sacrificar la calidad de la demo.

#### A. Cuello de Botella de Datos: Peso de los archivos `.fits`

- **El Problema:** Los archivos de imágenes astronómicas de la NASA y los cubos de datos de 102 bandas de SPHEREx son extremadamente pesados. Procesar restas de imágenes o extraer espectros en tiempo real solicitándolos a los servidores IPAC/IRSA saturará la memoria RAM de nuestros servidores gratuitos (Render/Railway) o provocará errores de _Timeout_ (tiempo de espera agotado).
    
- **Limitación del MVP:** Nuestra aplicación en vivo no podrá procesar cualquier coordenada aleatoria en tiempo real con latencia cero durante el hackatón.
    
- **Plan de Mitigación (El "Golden Path"):** Pre-procesaremos localmente en nuestros ordenadores los datos pesados de los **5 objetos obligatorios del reto**. Subiremos estos datos ya limpios, recortados y optimizados a un _bucket_ de almacenamiento (AWS S3, Firebase o similar) o los serviremos como archivos estáticos. Durante la demo, el jurado experimentará una velocidad instantánea al consultar estos 5 objetos. En la documentación aclararemos que la arquitectura en producción usaría instancias de AWS EC2 de alta capacidad para coordenadas aleatorias.
    
#### B. Caída de las APIs de la NASA por saturación

- **El Problema:** Durante el fin de semana del Space Apps, miles de equipos hacen peticiones simultáneas a las APIs de la NASA (Astropy/Astroquery), lo que frecuentemente provoca caídas del servidor o bloqueos temporales de IP (Rate Limiting).
    
- **Plan de Mitigación:** _Offline First_ para la demo. Diseñaremos la arquitectura de manera que, si la API de la NASA no responde en 5 segundos, el sistema sirva automáticamente un _mock_ (datos simulados en caché local) con una advertencia visual en la UI: "Mostrando datos en caché debido a alta latencia en servidores de origen". Esto evitará que la aplicación colapse frente a los jueces.
    

#### C. La trampa de la Inteligencia Artificial (Scope Creep)

- **El Problema:** Entrenar y desplegar un modelo de Machine Learning / Visión Artificial real para detectar objetos en movimiento requiere semanas de etiquetado y GPUs que no tenemos. Intentar hacerlo en 48 horas consumirá el 80% del tiempo del equipo y probablemente no funcione.
    
- **Limitación del MVP:** El "Asistente de Movimiento" no utilizará redes neuronales profundas (Deep Learning) en esta primera versión.
    
- **Plan de Mitigación:** Implementaremos **Detección Algorítmica Tradicional**. Usaremos operaciones de matrices simples con `OpenCV/NumPy` (resta de imágenes + umbralización + detección de contornos). Etiquetaremos la función en la interfaz como _"Detección Asistida por Software (BETA)"_. En la presentación, expondremos esto como la base algorítmica y presentaremos el uso de IA (CNNs) como el "Siguiente paso a desarrollar".
    
#### D. Desincronización entre el Código y el Pitch de 30 segundos

- **El Problema:** Llegar al domingo con un código excelente pero quedarse sin tiempo para grabar un vídeo claro. A nivel global, si el vídeo de 30 segundos no es perfecto, los jueces no leerán el código.
    
- **Plan de Mitigación:** El Rol 5 (Director de Storytelling) trabajará en paralelo. El sábado por la tarde, en cuanto el Frontend tenga algo visual (incluso si está alimentado con datos falsos de prueba), se grabarán las capturas de pantalla para el vídeo. No se esperará al domingo por la mañana para empezar a editar. El pitch manda sobre el código final.

#### E. Política de "Code Freeze" (Congelación de Código)

- **El Problema:** El temido _bug_ de última hora. Un desarrollador intenta añadir una "pequeña mejora" el domingo a las 11:00 AM y rompe la aplicación entera justo antes de entregar.
    
- **Plan de Mitigación:** Se establece un **Code Freeze inamovible el domingo a las 09:00 AM**. A partir de esa hora está estrictamente prohibido hacer _commits_ con nuevas funcionalidades. Las últimas horas se dedican exclusivamente a:
    
    1. Testear los 5 objetos de prueba obligatorios para asegurar que no fallan.

    2. Esconder/Eliminar botones de la interfaz de funciones que no se llegaron a terminar (es mejor una app con 3 funciones perfectas que con 5 rotas). 
        
    3. Redactar los READMEs de GitHub con pulcritud.