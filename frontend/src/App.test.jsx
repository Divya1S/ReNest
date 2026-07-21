import { render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, test } from "vitest";

import RequireAuth from "./components/RequireAuth";
import { AuthContext } from "./context/AuthContext";
import LandingPage from "./pages/LandingPage";

describe("Landing page", () => {
  test("renders the unique move-out rescue message", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );

    expect(screen.getByText(/Keep usable dorm essentials out of the/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Browse listings/i })).toBeInTheDocument();
  });
});

describe("RequireAuth", () => {
  test("redirects anonymous users to the login page", () => {
    function LoginProbe() {
      const location = useLocation();
      return <div>Login page from {location.state?.from}</div>;
    }

    render(
      <AuthContext.Provider value={{ user: null, loading: false, authNotice: "Session expired." }}>
        <MemoryRouter initialEntries={["/browse"]}>
          <Routes>
            <Route element={<RequireAuth />}>
              <Route path="/browse" element={<div>Protected page</div>} />
            </Route>
            <Route path="/login" element={<LoginProbe />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(screen.getByText("Login page from /browse")).toBeInTheDocument();
  });
});
