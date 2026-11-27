# BPMN 2.0 process (processus)

A BPMN 2.0 process (XML, diagram included) detailing a business process of the map, as Ossie expects it.

Identity: `key` (uppercase letters, digits, dashes), the process's own key, e.g.
`PRC-PRISE-COMMANDE-DETAIL` (a business process may have several BPMN details). The file's
`bpmn:definitions` id is set to this key.

Content — the BPMN 2.0 XML, **as text**:

- Standard BPMN 2.0 XML, conforming to the official OMG schema, **with its diagram**
  (`bpmndi:BPMNDiagram`): a file without diagram is refused. Other tools' extensions are kept.
- **Attachment to the map**: Ossie's extension `<urban:reference cle="..." type="..."/>` inside
  `bpmn:extensionElements`, namespace `https://github.com/gaelgael5/urban-bpmn/schema/1.0` (prefix
  `urban`). A process written by an agent **must** detail a business process: put a reference to a
  `BusinessProcess` object of the map on the `bpmn:process` (`write_process_unattached` otherwise).
- On a `bpmn:collaboration`: one `BusinessCollaboration` object. On a participant, a lane, a data
  object reference or a data store reference: the object it stands for (actor, role, business
  object, application). Every referenced key must exist in the map.
- Elements attached to objects the business process does not relate to (not assigned, not
  accessed, not serving) only produce **warnings**: the process is written.
- Modify a process with `patch_document` and XPath (`describe_standard xpath`).

```yaml example
kind: processus
identity: {key: PRC-PRISE-COMMANDE-DETAIL}
content: |
  <?xml version="1.0" encoding="UTF-8"?>
  <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:urban="https://github.com/gaelgael5/urban-bpmn/schema/1.0" id="PRC-PRISE-COMMANDE-DETAIL" targetNamespace="http://bpmn.io/schema/bpmn">
    <bpmn:process id="Process_1" name="Prise de commande — détail" isExecutable="false">
      <bpmn:extensionElements><urban:reference cle="PRC-PRISE-COMMANDE" type="processus-metier"/></bpmn:extensionElements>
      <bpmn:startEvent id="Start_1"/>
      <bpmn:task id="Task_1" name="Saisir la commande"/>
      <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_1"/>
      <bpmn:dataObjectReference id="Data_1" name="Commande" dataObjectRef="DataObject_1">
        <bpmn:extensionElements><urban:reference cle="OBJ-COMMANDE" type="document-metier"/></bpmn:extensionElements>
      </bpmn:dataObjectReference>
      <bpmn:dataObject id="DataObject_1"/>
    </bpmn:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1">
      <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
        <bpmndi:BPMNShape id="Start_1_di" bpmnElement="Start_1"><dc:Bounds x="180" y="160" width="36" height="36"/></bpmndi:BPMNShape>
        <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1"><dc:Bounds x="270" y="138" width="100" height="80"/></bpmndi:BPMNShape>
        <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1"><di:waypoint x="216" y="178"/><di:waypoint x="270" y="178"/></bpmndi:BPMNEdge>
        <bpmndi:BPMNShape id="Data_1_di" bpmnElement="Data_1"><dc:Bounds x="302" y="275" width="36" height="50"/></bpmndi:BPMNShape>
      </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
  </bpmn:definitions>
```
