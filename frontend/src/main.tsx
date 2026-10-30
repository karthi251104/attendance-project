import "@fontsource-variable/inter";
import "./styles.css";

import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ApiError } from "./api";
import { App } from "./App";
import { ToastProvider } from "./components/toast";
import { SessionProvider } from "./session";

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    // A 401 means the session ended: re-read the session, and the app sends the user to sign in.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) queryClient.invalidateQueries({ queryKey: ["session"] });
    },
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <SessionProvider>
            <App />
          </SessionProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
