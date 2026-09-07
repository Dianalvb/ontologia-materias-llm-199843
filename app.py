from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import streamlit as st
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef


ROOT = Path(__file__).parent
ONTOLOGY_PATH = next(ROOT.glob("ontologia/**/*.owx"), None)
WEBPROTEGE = Namespace("http://webprotege.stanford.edu/")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", value.lower())


@dataclass
class InferenceResult:
    answer: str
    steps: list[dict[str, str]]
    rows: list[dict[str, str]]


class OntologyEngine:
    def __init__(self, path: Path):
        self.graph = Graph()
        self._load_owlxml(path)
        self.labels = {
            str(subject): str(label)
            for subject, label in self.graph.subject_objects(RDFS.label)
        }
        self.label_index = {normalize(label): URIRef(uri) for uri, label in self.labels.items()}
        self._subclasses = self._build_subclass_closure()

    @staticmethod
    def _iri(element: ElementTree.Element) -> URIRef | None:
        if element is None:
            return None
        value = element.attrib.get("IRI") or element.attrib.get("abbreviatedIRI")
        if not value and element.tag.rsplit("}", 1)[-1] == "IRI":
            value = element.text
        if not value:
            return None
        prefixes = {
            "owl:": str(OWL),
            "rdf:": str(RDF),
            "rdfs:": str(RDFS),
            "xsd:": "http://www.w3.org/2001/XMLSchema#",
        }
        for prefix, namespace in prefixes.items():
            if value.startswith(prefix):
                return URIRef(namespace + value[len(prefix):])
        return URIRef(value)

    def _load_owlxml(self, path: Path) -> None:
        """Translate the OWL/XML functional serialization into RDF triples."""
        root = ElementTree.parse(path).getroot()
        for element in root:
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "Declaration" and len(element):
                declared = element[0]
                uri = self._iri(declared)
                if uri is None:
                    continue
                kind = declared.tag.rsplit("}", 1)[-1]
                rdf_type = {
                    "Class": OWL.Class,
                    "ObjectProperty": OWL.ObjectProperty,
                    "DataProperty": OWL.DatatypeProperty,
                    "NamedIndividual": OWL.NamedIndividual,
                }.get(kind)
                if rdf_type:
                    self.graph.add((uri, RDF.type, rdf_type))
            elif tag in {"SubClassOf", "SubObjectPropertyOf", "SubDataPropertyOf"} and len(element) >= 2:
                child = self._iri(element[0])
                parent = self._iri(element[1])
                predicate = {
                    "SubClassOf": RDFS.subClassOf,
                    "SubObjectPropertyOf": RDFS.subPropertyOf,
                    "SubDataPropertyOf": RDFS.subPropertyOf,
                }[tag]
                if child and parent:
                    self.graph.add((child, predicate, parent))
            elif tag in {"ObjectPropertyDomain", "ObjectPropertyRange", "DataPropertyDomain", "DataPropertyRange"} and len(element) >= 2:
                property_uri = self._iri(element[0])
                value_uri = self._iri(element[1])
                predicate = RDFS.domain if tag.endswith("Domain") else RDFS.range
                if property_uri and value_uri:
                    self.graph.add((property_uri, predicate, value_uri))
            elif tag == "ClassAssertion" and len(element) >= 2:
                class_uri = self._iri(element[0])
                individual_uri = self._iri(element[-1])
                if class_uri and individual_uri and element[0].tag.rsplit("}", 1)[-1] == "Class":
                    self.graph.add((individual_uri, RDF.type, class_uri))
            elif tag == "ObjectPropertyAssertion" and len(element) >= 3:
                predicate = self._iri(element[0])
                subject = self._iri(element[1])
                object_uri = self._iri(element[2])
                if predicate and subject and object_uri:
                    self.graph.add((subject, predicate, object_uri))
            elif tag == "DataPropertyAssertion" and len(element) >= 3:
                predicate = self._iri(element[0])
                subject = self._iri(element[1])
                literal_element = element[2]
                if predicate and subject:
                    datatype = self._iri(literal_element) if literal_element.attrib.get("datatype") else None
                    self.graph.add((subject, predicate, Literal(literal_element.text or "", datatype=datatype)))
            elif tag == "AnnotationAssertion" and len(element) >= 3:
                predicate = self._iri(element[0])
                subject = self._iri(element[1])
                value = element[2].text or ""
                if predicate and subject:
                    self.graph.add((subject, predicate, Literal(value)))

    def name(self, value: URIRef | Literal | str) -> str:
        text = str(value)
        return self.labels.get(text, text.rsplit("/", 1)[-1].rsplit("#", 1)[-1])

    def _build_subclass_closure(self) -> dict[URIRef, set[URIRef]]:
        closure: dict[URIRef, set[URIRef]] = {}
        for child, parent in self.graph.subject_objects(RDFS.subClassOf):
            closure.setdefault(child, set()).add(parent)
        changed = True
        while changed:
            changed = False
            for child, parents in list(closure.items()):
                expanded = set(parents)
                for parent in parents:
                    expanded.update(closure.get(parent, set()))
                if expanded != parents:
                    closure[child] = expanded
                    changed = True
        return closure

    def individuals_of(self, class_uri: URIRef) -> list[URIRef]:
        accepted = {class_uri} | {child for child, parents in self._subclasses.items() if class_uri in parents}
        values = {
            subject
            for subject, _, asserted_class in self.graph.triples((None, RDF.type, None))
            if asserted_class in accepted
        }
        return sorted(values, key=lambda value: self.name(value).lower())

    def find_label(self, text: str) -> URIRef | None:
        compact = normalize(text)
        if compact in self.label_index:
            return self.label_index[compact]
        for key, uri in self.label_index.items():
            if key in compact or compact in key:
                return uri
        return None

    def find_property(self, question: str) -> URIRef | None:
        question_key = normalize(question)
        candidates = []
        for uri, label in self.labels.items():
            if (uri.startswith(str(WEBPROTEGE)) and
                    any(self.graph.triples((URIRef(uri), RDF.type, kind)) for kind in (RDF.Property,))):
                candidates.append((normalize(label), URIRef(uri)))
        # OWL/XML does not always explicitly type declarations as rdf:Property.
        for uri in set(self.graph.subjects(RDFS.domain, None)) | set(self.graph.subjects(RDFS.range, None)):
            if uri in self.labels:
                candidates.append((normalize(self.labels[str(uri)]), uri))
        aliases = {
            "imparte": "imparte",
            "inscrito": "inscritoen",
            "inscrita": "inscritoen",
            "toma clase": "tomaclasecon",
            "clase": "tomaclasecon",
            "edad": "tieneedad",
            "genero": "tienegenero",
            "nombre": "tienenombre",
        }
        for phrase, target in aliases.items():
            if normalize(phrase) in question_key:
                for label_key, uri in candidates:
                    if label_key == target:
                        return uri
        for label_key, uri in candidates:
            if label_key in question_key:
                return uri
        return None

    def infer_types(self, subject: URIRef) -> set[URIRef]:
        types = {obj for obj in self.graph.objects(subject, RDF.type)}
        for predicate in self.graph.predicates(subject, None):
            for domain in self.graph.objects(predicate, RDFS.domain):
                types.add(domain)
        expanded = set(types)
        for type_uri in types:
            expanded.update(self._subclasses.get(type_uri, set()))
        return expanded

    def answer(self, question: str) -> InferenceResult:
        clean = question.strip()
        steps: list[dict[str, str]] = [
            {"title": "1. Interpretar", "text": f"Pregunta recibida: \"{clean}\""},
            {"title": "2. Buscar conceptos", "text": "Se comparan entidades y propiedades con sus etiquetas de la ontología."},
        ]
        relation = self.find_property(clean)
        subject = None
        for label in sorted(self.labels.values(), key=len, reverse=True):
            if normalize(label) in normalize(clean) and self.find_label(label) != relation:
                candidate = self.find_label(label)
                candidate_types = set(self.graph.objects(candidate, RDF.type)) if candidate else set()
                property_types = {OWL.Class, OWL.ObjectProperty, URIRef(str(OWL) + "DatatypeProperty")}
                if candidate and candidate_types.isdisjoint(property_types):
                    subject = candidate
                    break

        if relation and subject:
            matches = list(self.graph.objects(subject, relation))
            if matches:
                rows = [{"Resultado": self.name(value), "IRI": str(value)} for value in matches]
                steps.append({"title": "3. Hechos encontrados", "text": f"{self.name(subject)} --{self.name(relation)}--> {len(matches)} resultado(s)."})
                steps.append({"title": "4. Inferencia", "text": "La respuesta se deriva directamente de una aserción de la ontología."})
                answer = ", ".join(self.name(value) for value in matches)
                return InferenceResult(answer, steps, rows)
            steps.append({"title": "3. Hechos encontrados", "text": f"No hay un hecho explícito para {self.name(subject)} y {self.name(relation)}."})

        class_uri = next((uri for uri, label in self.labels.items() if normalize(label) in normalize(clean) and uri in {str(item) for item in self.graph.subjects(RDFS.subClassOf, None)}), None)
        if class_uri:
            individuals = self.individuals_of(URIRef(class_uri))
            if individuals:
                rows = [{"Resultado": self.name(value), "IRI": str(value)} for value in individuals]
                steps.append({"title": "3. Aplicar jerarquía", "text": f"Se calcula el cierre de subclases para {self.labels[class_uri]}."})
                steps.append({"title": "4. Resultado", "text": f"La clase contiene {len(individuals)} individuo(s), incluyendo tipos heredados."})
                return InferenceResult(", ".join(self.name(value) for value in individuals), steps, rows)

        if subject:
            types = sorted({self.name(uri) for uri in self.infer_types(subject)})
            if types:
                steps.append({"title": "3. Inferir tipos", "text": f"Se recorren los tipos de {self.name(subject)} y sus superclases."})
                steps.append({"title": "4. Resultado", "text": "No se encontró esa propiedad; sí se pudieron inferir sus tipos."})
                return InferenceResult(f"{self.name(subject)} pertenece a: {', '.join(types)}.", steps, [{"Tipo inferido": value} for value in types])

        steps.append({"title": "3. Sin coincidencia", "text": "No se encontró una combinación de entidad, propiedad o clase compatible."})
        return InferenceResult("No pude responder con los hechos disponibles en la ontología.", steps, [])


st.set_page_config(page_title="Nexo Ontológico", page_icon="◈", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root { --ink:#182426; --muted:#607173; --paper:#f5f1e8; --mint:#b8ddcc; --coral:#e47d61; --line:#d8d1c3; }
html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; color: var(--ink); }
.stApp { background: radial-gradient(circle at 88% 8%, #dcefe5 0, transparent 28%), var(--paper); }
.hero { padding: 2.5rem 0 1.4rem; border-bottom: 1px solid var(--line); }
.eyebrow { font: 500 .75rem 'DM Mono', monospace; letter-spacing: .08em; text-transform: uppercase; color: #477967; }
.hero h1 { font-size: clamp(2.2rem, 5vw, 4.6rem); line-height: .96; margin: .65rem 0; max-width: 780px; letter-spacing: 0; }
.hero p { font-size: 1.05rem; color: var(--muted); max-width: 700px; }
.step { border-left: 3px solid var(--mint); padding: .75rem 1rem; margin: .65rem 0; background: rgba(255,255,255,.46); }
.step strong { display:block; font-size:.86rem; color:#477967; }
.step span { color: var(--ink); }
.answer { background: var(--ink); color: #f8f5ec; padding: 1.25rem 1.4rem; margin: 1rem 0 1.4rem; font-size: 1.18rem; }
div[data-testid="stTextInput"] input { border: 1px solid var(--line); border-radius: 0; background: #fffdf8; }
button[kind="primary"] { border-radius: 0; background: var(--coral); border: 0; }
section[data-testid="stSidebar"] { background: #e2eee7; border-right: 1px solid var(--line); }
.mono { font-family:'DM Mono',monospace; font-size:.78rem; color:var(--muted); }
</style>
""", unsafe_allow_html=True)


if ONTOLOGY_PATH is None:
    st.error("No se encontró ningún archivo .owx dentro de la carpeta ontologia.")
    st.stop()

@st.cache_resource
def load_engine(path: str) -> OntologyEngine:
    return OntologyEngine(Path(path))


engine = load_engine(str(ONTOLOGY_PATH))
with st.sidebar:
    st.markdown("### NEXO / ONTOLOGÍA")
    st.caption("Consulta explicable sobre conocimiento OWL")
    st.divider()
    st.metric("Triples cargados", len(engine.graph))
    st.metric("Etiquetas legibles", len(engine.labels))
    st.markdown("**Ejemplos**")
    examples = ["¿Qué materias imparte Juan?", "¿En qué materias está inscrito Itz?", "¿Con quién toma clase Diana?", "¿Qué profesores existen?"]
    for example in examples:
        if st.button(example, use_container_width=True):
            st.session_state.question = example
    st.divider()
    st.markdown('<span class="mono">Motor local · sin API externa</span>', unsafe_allow_html=True)


st.markdown('<div class="hero"><div class="eyebrow">Laboratorio de agentes inteligentes / 01</div><h1>Pregunta a tu conocimiento.</h1><p>Escribe una pregunta en lenguaje natural y observa cómo la ontología construye la respuesta, paso a paso.</p></div>', unsafe_allow_html=True)
question = st.text_input("Pregunta", value=st.session_state.get("question", ""), placeholder="Ej. ¿Qué materias imparte Juan?", label_visibility="collapsed")
ask = st.button("Consultar  →", type="primary", use_container_width=False)

if ask and question.strip():
    result = engine.answer(question)
    left, right = st.columns([1.05, 1.45], gap="large")
    with left:
        st.markdown("#### Respuesta")
        st.markdown(f'<div class="answer">{result.answer}</div>', unsafe_allow_html=True)
        if result.rows:
            st.dataframe(result.rows, use_container_width=True, hide_index=True)
    with right:
        st.markdown("#### Traza de inferencia")
        for step in result.steps:
            st.markdown(f'<div class="step"><strong>{step["title"]}</strong><span>{step["text"]}</span></div>', unsafe_allow_html=True)
elif not question.strip():
    st.info("Escribe una pregunta o elige un ejemplo de la barra lateral para comenzar.")