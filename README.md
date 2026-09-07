# Nexo Ontológico

Interfaz web para hacer preguntas en lenguaje natural sobre la ontología OWL/XML incluida en `ontologia/`. La aplicación responde con los hechos disponibles y muestra una traza sencilla del proceso de inferencia.

## Qué incluye

- Carga automática del primer archivo `.owx` dentro de `ontologia/`.
- Resolución de etiquetas `rdfs:label` para trabajar con nombres legibles.
- Consultas sobre relaciones como `imparte`, `inscrito_En` y `TomaClaseCon`.
- Inferencia de tipos mediante dominios de propiedades y cierre transitivo de `rdfs:subClassOf`.
- Tabla con los resultados y explicación por pasos.
- Motor completamente local: no requiere Ollama ni una API externa.

## Instalación

Se recomienda Python 3.10 o superior.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Ejecución

Desde la raíz del proyecto:

```powershell
streamlit run app.py
```

Streamlit abrirá la interfaz en `http://localhost:8501`.

## Ejemplos de preguntas

- `¿Qué materias imparte Juan?`
- `¿En qué materias está inscrito Itz?`
- `¿Con quién toma clase Diana?`
- `¿Qué profesores existen?`

La ontología contiene principalmente aserciones de propiedades de objeto. Por eso una pregunta sobre una propiedad de datos puede indicar que el concepto existe, pero que no hay un valor explícito almacenado para esa persona.

## Arquitectura

`app.py` contiene dos partes: `OntologyEngine`, que carga y consulta el grafo RDF, y la vista Streamlit, que presenta la pregunta, la respuesta y la traza de inferencia. La selección de consultas es determinista y auditable; el lenguaje natural se mapea a las etiquetas y alias conocidos de la ontología.