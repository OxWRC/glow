import {
  createBrowserRouter,
  RouterProvider,
  Navigate,
} from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { Layout } from "./routes/Layout";
import { ErrorPage } from "./routes/ErrorPage";
import { LoginPage } from "./routes/LoginPage";
import { AuthCallbackPage } from "./routes/AuthCallbackPage";
import { DashboardPage } from "./routes/DashboardPage";
import { AdminPage } from "./routes/AdminPage";
import { ClaimKeyPage } from "./routes/ClaimKeyPage";
import { PlaygroundPage } from "./routes/PlaygroundPage";
import { DEMO_MODE } from "./lib/api";

const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/en" replace /> },
  // Locale-free: the OIDC provider needs one fixed callback URL.
  { path: "/auth/callback", element: <AuthCallbackPage /> },
  {
    path: "/:locale",
    element: <Layout />,
    errorElement: <ErrorPage />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "admin", element: <AdminPage /> },
      { path: "login", element: <LoginPage /> },
      { path: "claim-key", element: <ClaimKeyPage /> },
      ...(DEMO_MODE
        ? [{ path: "playground", element: <PlaygroundPage /> }]
        : []),
    ],
  },
  { path: "*", element: <ErrorPage status={404} /> },
]);

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}
