import { useRef, type Dispatch, RefObject, SetStateAction } from "react";
import { Icons } from "../../icons/Icons";

function splitFilename(name: string): { stem: string; ext: string } {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return { stem: name, ext: "" };
  return { stem: name.slice(0, dot), ext: name.slice(dot) };
}

export function FileRenameInput<T extends { value: string }>({
  editing,
  setEditing,
  editInputRef,
  onCommitRename,
  onCancelRename,
  splitExtension = false,
}: {
  editing: T | null;
  setEditing: Dispatch<SetStateAction<T | null>>;
  editInputRef: RefObject<HTMLInputElement>;
  onCommitRename: () => void;
  onCancelRename: () => void;
  splitExtension?: boolean;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);

  const commitOnBlur = () => {
    requestAnimationFrame(() => {
      if (!wrapperRef.current?.contains(document.activeElement)) {
        void onCommitRename();
      }
    });
  };

  const keyHandlers = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") void onCommitRename();
    if (e.key === "Escape") onCancelRename();
  };

  if (!splitExtension || !editing) {
    return (
      <input {...renameInputProps(editing, setEditing, editInputRef, onCommitRename, onCancelRename)} />
    );
  }

  const { stem, ext } = splitFilename(editing.value);

  return (
    <div
      ref={wrapperRef}
      className="sidebar-rename-split"
      onClick={(e) => e.stopPropagation()}
    >
      <input
        ref={editInputRef}
        value={stem}
        onChange={(e) =>
          setEditing((prev) => (prev ? { ...prev, value: e.target.value + ext } : prev))
        }
        onBlur={commitOnBlur}
        onKeyDown={keyHandlers}
        className="sidebar-rename-input sidebar-rename-stem"
      />
      <input
        value={ext}
        onChange={(e) => {
          let next = e.target.value;
          if (next && !next.startsWith(".")) next = `.${next}`;
          setEditing((prev) => (prev ? { ...prev, value: stem + next } : prev));
        }}
        onBlur={commitOnBlur}
        onKeyDown={keyHandlers}
        className="sidebar-rename-input sidebar-rename-ext"
        spellCheck={false}
      />
    </div>
  );
}

export function renameInputProps<T extends { value: string }>(
  editing: T | null,
  setEditing: Dispatch<SetStateAction<T | null>>,
  editInputRef: RefObject<HTMLInputElement>,
  onCommitRename: () => void,
  onCancelRename: () => void,
) {
  return {
    ref: editInputRef,
    value: editing?.value ?? "",
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setEditing((prev) => (prev ? { ...prev, value: e.target.value } : prev)),
    onClick: (e: React.MouseEvent) => e.stopPropagation(),
    onBlur: () => void onCommitRename(),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Enter") void onCommitRename();
      if (e.key === "Escape") onCancelRename();
    },
    className: "sidebar-rename-input",
  };
}

export function SidebarHoverActions({
  onRename,
  onDelete,
  canDelete = true,
  activeChat = false,
  deleteTitle = "Delete",
}: {
  onRename: () => void;
  onDelete: () => void;
  canDelete?: boolean;
  activeChat?: boolean;
  deleteTitle?: string;
}) {
  return (
    <div className="sidebar-hover-actions">
      <button
        type="button"
        className={`sidebar-action-btn${activeChat ? " is-active-chat" : ""}`}
        title="Rename"
        onClick={(e) => {
          e.stopPropagation();
          onRename();
        }}
      >
        <Icons.Pencil />
      </button>
      {canDelete && (
        <button
          type="button"
          className="sidebar-action-btn sidebar-delete-btn"
          title={deleteTitle}
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <Icons.Trash />
        </button>
      )}
    </div>
  );
}
