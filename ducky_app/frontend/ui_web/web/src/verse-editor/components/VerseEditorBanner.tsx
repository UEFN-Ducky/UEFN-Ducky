interface VerseEditorBannerProps {

  message: string;

  variant?: "info" | "warn";

}



export function VerseEditorBanner({ message, variant = "info" }: VerseEditorBannerProps) {

  if (!message) return null;

  return (

    <div

      className={`verse-editor-banner ${variant === "warn" ? "is-warn" : "is-info"}`}

    >

      {message}

    </div>

  );

}


