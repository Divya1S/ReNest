import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

export default function RequireAuth() {
  const { user, loading, authNotice } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="paper-panel flex min-h-[40vh] items-center justify-center p-12 text-center">
        <div>
          <p className="label-title">Loading</p>
          <h2 className="mt-3 text-[22px] font-bold tracking-[-0.02em]">Checking your ReNest session...</h2>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{
          from: `${location.pathname}${location.search}${location.hash}`,
          reason: authNotice || undefined,
        }}
      />
    );
  }

  return <Outlet />;
}
