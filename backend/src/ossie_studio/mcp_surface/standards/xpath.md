# XPath (BPMN)

The paths of patch_document operations on a BPMN process: XPath 1.0 with BPMN prefixes, one node each.

A BPMN process is XML: `patch_document` designates what it changes with **XPath 1.0**. Prefixes
always available: `bpmn:` (model), `bpmndi:` (diagram), `dc:`, `di:`, `urban:` (Ossie's
attachment), plus those the file declares.

- Each path designates **exactly one node** (an element, an attribute, a text); zero or several
  nodes are refused (`write_path_not_single`). An unreadable XPath: `write_xpath_invalid`.
- `set` on an element: replace it by the XML fragment in `value`; on an attribute or a text: set
  the text in `value`.
- `add` on an element: append the XML fragment in `value` as its last child; on
  `<element path>/@name`: add an attribute that does not exist yet.
- `remove`: remove the element, attribute or text.
- A fragment uses the prefixes above (`<bpmn:task id="Task_2" name="Contrôler"/>`).

The modified file is checked whole (BPMN 2.0 schema, diagram, attachments) before it is written;
the file keeps its prefixes, comments and other tools' extensions.

```yaml example-patch
kind: processus
document: |
  <?xml version="1.0" encoding="UTF-8"?>
  <bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:urban="https://github.com/gaelgael5/urban-bpmn/schema/1.0" id="PRC-PRISE-COMMANDE-DETAIL" targetNamespace="http://bpmn.io/schema/bpmn">
    <bpmn:process id="Process_1" name="Prise de commande" isExecutable="false">
      <bpmn:extensionElements><urban:reference cle="PRC-PRISE-COMMANDE" type="processus-metier"/></bpmn:extensionElements>
      <bpmn:startEvent id="StartEvent_1"/>
    </bpmn:process>
    <bpmndi:BPMNDiagram id="BPMNDiagram_1">
      <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
        <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1"><dc:Bounds x="180" y="160" width="36" height="36"/></bpmndi:BPMNShape>
      </bpmndi:BPMNPlane>
    </bpmndi:BPMNDiagram>
  </bpmn:definitions>
operations:
  - {op: set, path: "//bpmn:process/@name", value: Prise de commande en ligne}
  - {op: add, path: "//bpmn:process", value: '<bpmn:task id="Task_1" name="Saisir la commande"/>'}
  - {op: add, path: "//bpmn:task[@id='Task_1']/@completionQuantity", value: "1"}
```
