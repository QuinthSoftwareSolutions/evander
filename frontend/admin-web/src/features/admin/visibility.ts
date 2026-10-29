import { useEffect, useState } from "react";

export function documentIsVisible(): boolean {
  if (typeof document === "undefined") {
    return true;
  }
  return document.visibilityState !== "hidden";
}

export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => documentIsVisible());

  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }
    const handleVisibilityChange = () => {
      setVisible(documentIsVisible());
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  return visible;
}
