import { connectionIconSrc } from "../connectionIcons";

interface ConnectionStatusIconProps {
  isOnline: boolean;
  isWedged?: boolean;
  size?: number;
  className?: string;
  title?: string;
}

/** Duck MCP icon for online / wedged / offline connection state. */
export function ConnectionStatusIcon({
  isOnline,
  isWedged = false,
  size = 22,
  className = "",
  title,
}: ConnectionStatusIconProps) {
  return (
    <img
      src={connectionIconSrc(isOnline, isWedged)}
      alt=""
      width={size}
      height={size}
      draggable={false}
      title={title}
      className={`connection-status-icon ${className}`.trim()}
    />
  );
}
