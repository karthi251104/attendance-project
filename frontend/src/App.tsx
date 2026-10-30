import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AttendancePage } from "./pages/Attendance";
import { ChangePasswordPage } from "./pages/ChangePassword";
import { ChangesPage } from "./pages/Changes";
import { CycleReviewPage } from "./pages/CycleReview";
import { HomePage, NotFoundPage } from "./pages/Home";
import { HrCyclesPage } from "./pages/HrCycles";
import { LoginPage } from "./pages/Login";
import { LoginsPage } from "./pages/Logins";
import { UploadPage } from "./pages/Upload";
import { RequireUser } from "./session";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RequireUser>{(user) => <Layout user={user}><HomePage user={user} /></Layout>}</RequireUser>} />
      {/* The link in the manager email; HR can open it too. */}
      <Route path="/team/:cycleId" element={<RequireUser>{(user) => <Layout user={user} wide><AttendancePage hrView={false} /></Layout>}</RequireUser>} />
      <Route path="/hr" element={<RequireUser hr>{(user) => <Layout user={user}><HrCyclesPage /></Layout>}</RequireUser>} />
      <Route path="/hr/upload" element={<RequireUser hr>{(user) => <Layout user={user}><UploadPage /></Layout>}</RequireUser>} />
      <Route path="/hr/cycles/:cycleId" element={<RequireUser hr>{(user) => <Layout user={user}><CycleReviewPage /></Layout>}</RequireUser>} />
      <Route path="/hr/cycles/:cycleId/employees" element={<RequireUser hr>{(user) => <Layout user={user} wide><AttendancePage hrView /></Layout>}</RequireUser>} />
      <Route path="/hr/cycles/:cycleId/changes" element={<RequireUser hr>{(user) => <Layout user={user}><ChangesPage /></Layout>}</RequireUser>} />
      <Route path="/hr/logins" element={<RequireUser hr>{(user) => <Layout user={user}><LoginsPage /></Layout>}</RequireUser>} />
      <Route path="/account/password" element={<RequireUser>{(user) => <Layout user={user}><ChangePasswordPage /></Layout>}</RequireUser>} />
      <Route path="*" element={<RequireUser>{(user) => <Layout user={user}><NotFoundPage /></Layout>}</RequireUser>} />
    </Routes>
  );
}
