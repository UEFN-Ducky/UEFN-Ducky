import { Icons } from "../icons/Icons";

interface AssetThumbnailIconProps {
  path: string;
  /** Kept for call-site compatibility; unused while previews are disabled. */
  size?: number;
}

/** Asset previews are disabled for now — they fetched a render from UEFN and did a
 * listener online-check on every binary file in the tree/tabs. Render a plain file
 * icon with zero backend traffic. */
export function AssetThumbnailIcon({ path }: AssetThumbnailIconProps) {
  return (
    <span className="sidebar-tree-row-icon" title={path}>
      <Icons.File />
    </span>
  );
}
