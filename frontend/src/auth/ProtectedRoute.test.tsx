import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { testUser } from "../test/fixtures";
import type { AuthUser } from "../types";
import { useAuth } from "./AuthContext";
import { ProtectedRoute } from "./ProtectedRoute";

vi.mock("./AuthContext", () => ({ useAuth: vi.fn() }));

const mockedUseAuth = vi.mocked(useAuth);

function setAuth(state: { user: AuthUser | null; ready: boolean }) {
  mockedUseAuth.mockReturnValue(state as ReturnType<typeof useAuth>);
}

// Shows where the redirect came from, so the test can check the saved location.
function SignInProbe() {
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  return <p>sign in page, came from {from}</p>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/terminal"
          element={
            <ProtectedRoute>
              <p>terminal content</p>
            </ProtectedRoute>
          }
        />
        <Route path="/sign-in" element={<SignInProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  beforeEach(() => {
    mockedUseAuth.mockReset();
  });

  it("renders its children for a signed in user", () => {
    setAuth({ user: testUser, ready: true });
    renderAt("/terminal");

    expect(screen.getByText("terminal content")).toBeInTheDocument();
  });

  it("redirects an anonymous visitor to sign in and remembers the origin", () => {
    setAuth({ user: null, ready: true });
    renderAt("/terminal");

    expect(screen.queryByText("terminal content")).not.toBeInTheDocument();
    expect(
      screen.getByText("sign in page, came from /terminal"),
    ).toBeInTheDocument();
  });

  it("waits quietly while the session is being checked", () => {
    setAuth({ user: null, ready: false });
    renderAt("/terminal");

    expect(screen.getByText("Checking session...")).toBeInTheDocument();
    expect(screen.queryByText("terminal content")).not.toBeInTheDocument();
    expect(screen.queryByText(/sign in page/)).not.toBeInTheDocument();
  });
});
