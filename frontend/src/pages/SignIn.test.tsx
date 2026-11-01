import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../auth/AuthContext";
import { testUser } from "../test/fixtures";
import type { AuthUser } from "../types";
import { SignIn } from "./SignIn";

vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

const mockedUseAuth = vi.mocked(useAuth);
const signIn = vi.fn();

function setAuth(user: AuthUser | null = null) {
  mockedUseAuth.mockReturnValue({
    user,
    ready: true,
    signIn,
  } as unknown as ReturnType<typeof useAuth>);
}

// Stands in for whatever page sign in redirects to.
function Destination() {
  const location = useLocation();
  return <p>arrived at {location.pathname}</p>;
}

function renderSignIn(
  entry: string | { pathname: string; state: unknown } = "/sign-in",
) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/sign-in" element={<SignIn />} />
        <Route path="*" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
}

const email = () => screen.getByLabelText("Email");
const password = () => screen.getByLabelText("Password");
const submit = () => screen.getByRole("button", { name: "Sign in" });

describe("SignIn page", () => {
  beforeEach(() => {
    signIn.mockReset();
    mockedUseAuth.mockReset();
    setAuth();
  });

  it("renders the sign in form", () => {
    renderSignIn();

    expect(
      screen.getByRole("heading", { name: "Access your terminal" }),
    ).toBeInTheDocument();
    expect(email()).toHaveAttribute("type", "email");
    expect(password()).toHaveAttribute("type", "password");
    expect(
      screen.getByRole("link", { name: "Create an account" }),
    ).toHaveAttribute("href", "/sign-up");
  });

  it("asks for both fields before calling the server", async () => {
    renderSignIn();

    await userEvent.type(email(), "ada@firm.com");
    await userEvent.click(submit());

    expect(
      screen.getByText("Enter your email and password."),
    ).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });

  it("signs in and opens the terminal", async () => {
    signIn.mockResolvedValue(undefined);
    renderSignIn();

    await userEvent.type(email(), "ada@firm.com");
    await userEvent.type(password(), "secret");
    await userEvent.click(submit());

    expect(signIn).toHaveBeenCalledWith("ada@firm.com", "secret");
    expect(await screen.findByText("arrived at /terminal")).toBeInTheDocument();
  });

  it("shows the server's error and stays on the page", async () => {
    signIn.mockRejectedValue(new Error("invalid credentials"));
    renderSignIn();

    await userEvent.type(email(), "ada@firm.com");
    await userEvent.type(password(), "wrong");
    await userEvent.click(submit());

    expect(await screen.findByText("invalid credentials")).toBeInTheDocument();
    expect(screen.queryByText(/arrived at/)).not.toBeInTheDocument();
  });

  it("disables the button and shows progress while signing in", async () => {
    let finish: () => void = () => {};
    signIn.mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    renderSignIn();

    await userEvent.type(email(), "ada@firm.com");
    await userEvent.type(password(), "secret");
    await userEvent.click(submit());

    expect(screen.getByRole("button", { name: "Signing in" })).toBeDisabled();

    finish();
    expect(await screen.findByText("arrived at /terminal")).toBeInTheDocument();
  });

  it("sends a signed in user straight to the terminal", () => {
    setAuth(testUser);
    renderSignIn();

    expect(screen.getByText("arrived at /terminal")).toBeInTheDocument();
  });

  it("sends a signed in user back to the page they asked for", () => {
    setAuth(testUser);
    renderSignIn({ pathname: "/sign-in", state: { from: "/portfolio" } });

    expect(screen.getByText("arrived at /portfolio")).toBeInTheDocument();
  });
});
