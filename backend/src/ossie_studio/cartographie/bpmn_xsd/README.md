# Schémas BPMN 2.0 (OMG)

Les cinq schémas XML officiels de BPMN 2.0 publiés par l'Object Management Group
(spécification BPMN 2.0, espace de noms `http://www.omg.org/spec/BPMN/20100524/MODEL`) :
`BPMN20.xsd` (point d'entrée), `Semantic.xsd`, `BPMNDI.xsd`, `DC.xsd`, `DI.xsd`.

Origine : https://www.omg.org/spec/BPMN/20100501/ — copie inchangée, reprise du paquet
SpiffWorkflow 3.2.0 (`SpiffWorkflow/bpmn/parser/schema/`), le site de l'OMG n'étant pas
joignable depuis l'environnement de développement.

Usage : `cartographie/process.py` vérifie qu'un fichier BPMN est conforme à BPMN 2.0
avant de l'écrire (décision de Gael, 27 novembre 2026, epic « Primitives MCP — écriture
commune par genre »). Les extensions d'autres outils (`bpmn:extensionElements`, attributs
d'un autre espace de noms) sont permises par ces schémas.
