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

const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/en" replace /> },
  {
    path: "/:locale",
    element: <Layout />,
    errorElement: <ErrorPage />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "admin", element: <AdminPage /> },
      { path: "login", element: <LoginPage /> },
      { path: "auth/callback", element: <AuthCallbackPage /> },
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
