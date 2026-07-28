import { useEffect, useMemo, useState } from "react";

import { Modal, ModalActions } from "./Modal";
import { MdBlockEditor } from "./md-block-editor";
import type { PlanNode, PlanNodeKind } from "../types/panel";

export type PlaceOption = { id: string; label: string };

function flattenPlaceOptions(nodes: PlanNode[] | undefined, prefix = ""): PlaceOption[] {
  const out: PlaceOption[] = [];
  (nodes || []).forEach((node, i) => {
    const label = prefix ? `${prefix}.${i + 1}` : `${i + 1}`;
    out.push({ id: node.id, label: `${label} — ${node.content}` });
    if (node.children?.length) out.push(...flattenPlaceOptions(node.children, label));
  });
  return out;
}

export interface AddPlanNodeDialogProps {
  open: boolean;
  heading?: string;
  nodes?: PlanNode[];
  /** Prefill parent (empty = plan root). */
  initialParentId?: string;
  busy?: boolean;
  onClose: () => void;
  onSubmit: (payload: {
    content: string;
    kind: PlanNodeKind;
    parentId: string;
    body_markdown: string;
  }) => void | Promise<void>;
}

export function AddPlanNodeDialog({
  open,
  heading = "Add to plan",
  nodes,
  initialParentId = "",
  busy,
  onClose,
  onSubmit,
}: AddPlanNodeDialogProps) {
  const [kind, setKind] = useState<PlanNodeKind>("step");
  const [parentId, setParentId] = useState(initialParentId);
  const [content, setContent] = useState("");
  const [body, setBody] = useState("");

  const places = useMemo(() => flattenPlaceOptions(nodes), [nodes]);

  useEffect(() => {
    if (!open) return;
    setParentId(initialParentId);
    setKind("step");
    setContent("");
    setBody("");
  }, [open, initialParentId]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={heading}
      width={520}
      footer={
        <ModalActions
          cancelLabel="Cancel"
          confirmLabel={busy ? "Adding…" : kind === "subplan" ? "Add subplan" : "Add step"}
          confirmDisabled={busy || !content.trim()}
          onCancel={onClose}
          onConfirm={() =>
            void onSubmit({
              content: content.trim(),
              kind,
              parentId,
              body_markdown: kind === "subplan" ? body : "",
            })
          }
        />
      }
    >
      <div className="plans-add-node-form">
        <p className="plans-tab-modal-desc">
          Choose whether this is a step (a todo) or a subplan (a section with optional details), and
          where it sits in the outline.
        </p>
        <label className="plans-tab-modal-field">
          <span>Kind</span>
          <select
            className="plans-tab-search-input"
            value={kind}
            onChange={(e) => setKind(e.target.value as PlanNodeKind)}
          >
            <option value="step">Step — something to do in this plan</option>
            <option value="subplan">Subplan — section that can nest steps</option>
          </select>
        </label>
        <label className="plans-tab-modal-field">
          <span>Place under</span>
          <select
            className="plans-tab-search-input"
            value={parentId}
            onChange={(e) => setParentId(e.target.value)}
          >
            <option value="">Plan root (top level)</option>
            {places.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="plans-tab-modal-field">
          <span>Title</span>
          <input
            className="plans-tab-search-input"
            autoFocus
            value={content}
            placeholder="What should happen…"
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && content.trim() && !busy && kind === "step") {
                e.preventDefault();
                void onSubmit({
                  content: content.trim(),
                  kind,
                  parentId,
                  body_markdown: "",
                });
              }
            }}
          />
        </label>
        {kind === "subplan" ? (
          <label className="plans-tab-modal-field">
            <span>Details (optional)</span>
            <MdBlockEditor
              value={body}
              onChange={setBody}
              placeholder="Notes associated with this subplan…"
              className="plans-add-node-md"
            />
          </label>
        ) : null}
      </div>
    </Modal>
  );
}
