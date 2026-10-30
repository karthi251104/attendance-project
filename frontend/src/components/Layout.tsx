// The top bar (brand, navigation, signed-in user) around every signed-in page.

import { LogOut } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { api } from "../api";
import type { User } from "../types";
import { useToast } from "./toast";

function initials(user: User): string {
  const parts = user.name.replace(/[._@-]+/g, " ").trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

export function Layout({ user, wide = false, children }: { user: User; wide?: boolean; children: ReactNode }) {
  const toast = useToast();
  const [leaving, setLeaving] = useState(false);

  async function signOut() {
    setLeaving(true);
    try {
      const { redirect } = await api.logout();
      window.location.assign(redirect);
    } catch (error) {
      setLeaving(false);
      toast((error as Error).message, "error");
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand"><img src="/favicon.svg" alt="" /><span>Attendance Portal</span></Link>
        {user.is_hr && (
          <nav className="nav" aria-label="Main">
            <NavLink to="/hr" end>Cycles</NavLink>
            <NavLink to="/hr/upload">Upload</NavLink>
          </nav>
        )}
        <div className="topbar-end">
          <div className="user-chip">
            <div className="avatar" aria-hidden>{initials(user)}</div>
            <div className="user-meta">
              <strong>{user.name}</strong>
              <small>{user.is_hr ? "HR" : "Manager"} · {user.email}</small>
            </div>
          </div>
          <button className="btn ghost sm" onClick={signOut} disabled={leaving} title="Sign out">
            <LogOut /> <span>Sign out</span>
          </button>
        </div>
      </header>
      <main className={`page${wide ? " wide" : ""}`}>{children}</main>
    </div>
  );
}
