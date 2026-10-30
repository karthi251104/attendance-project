// Toast messages in the bottom-right corner; each disappears after a few seconds.

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { Alert } from "./ui";

type Tone = "info" | "warn" | "error" | "success";
interface Toast { id: number; tone: Tone; message: string }

const ToastContext = createContext<(message: string, tone?: Tone) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const show = useCallback((message: string, tone: Tone = "success") => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all, { id, tone, message }]);
    window.setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), tone === "error" ? 8000 : 4500);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className="toast"><Alert tone={t.tone}>{t.message}</Alert></div>)}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
