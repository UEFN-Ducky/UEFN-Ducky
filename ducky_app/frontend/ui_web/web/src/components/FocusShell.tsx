import { type ReactNode, useEffect } from "react";

import { WindowDrag } from "./WindowDrag";

import { WindowResize } from "./WindowResize";



interface FocusShellProps {

  header?: ReactNode;

  children: ReactNode;

}



export function FocusShell({ header, children }: FocusShellProps) {

  useEffect(() => {

    document.body.classList.add("focus-window");

    return () => document.body.classList.remove("focus-window");

  }, []);



  return (

    <>

      <WindowResize focusMode />

      <WindowDrag />

      <div className="focus-shell">
        {header}
        <div className="focus-content">{children}</div>
      </div>

    </>

  );

}

