import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../auth/AuthContext";
import { testUser } from "../test/fixtures";
import type { AuthUser } from "../types";
import { SignUp } from "./SignUp";

vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

const mockedUseAuth = vi.mocked(useAuth);
const signUp = vi.fn();

function setAuth(user: AuthUser | null = null) {
  mockedUseAuth.mockReturnValue({
    user,
    ready: true,
    signUp,
  } as unknown as ReturnType<typeof useAuth>);
}

function Destination() {
  const location = useLocation();
  return <p>arrived at {location.pathname}</p>;
}

function renderSignUp() {
  return render(
    <MemoryRouter initialEntries={["/sign-up"]}>
      <Routes>
        <Route path="/sign-up" element={<SignUp />} />
        <Route path="*" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
}

interface Form {
  name?: string;
  email?: string;
  password?: string;
  confirm?: string;
}

async function fill({
  name = "Ada Lovelace",
  email = "ada@firm.com",
  password = "secret123",
  confirm = password,
}: Form = {}) {
  await userEvent.type(screen.getByLabelText("Full name"), name);
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
  await userEvent.type(screen.getByLabelText("Confirm password"), confirm);
}

const submit = () => screen.getByRole("button", { name: "Create account" });

describe("SignUp page", () => {
  beforeEach(() => {
    signUp.mockReset();
    mockedUseAuth.mockReset();
    setAuth();
  });

  it("renders the registration form", () => {
    renderSignUp();

    expect(
      screen.getByRole("heading", { name: "Get started" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
  });

  it.each([
    [{ name: "A" }, "Enter your full name."],
    [{ email: "not-an-email" }, "Enter a valid email address."],
    [{ password: "short" }, "Password must be at least 8 characters."],
    [
      { password: "secret123", confirm: "different1" },
      "Passwords do not match.",
    ],
  ] as [Form, string][])("rejects %j with %j", async (form, message) => {
    renderSignUp();
    await fill(form);

    await userEvent.click(submit());

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(signUp).not.toHaveBeenCalled();
  });

  it("registers and opens the terminal", async () => {
    signUp.mockResolvedValue(undefined);
    renderSignUp();
    await fill();

    await userEvent.click(submit());

    expect(signUp).toHaveBeenCalledWith(
      "Ada Lovelace",
      "ada@firm.com",
      "secret123",
    );
    expect(await screen.findByText("arrived at /terminal")).toBeInTheDocument();
  });

  it("shows the server's error and stays on the page", async () => {
    signUp.mockRejectedValue(new Error("email already registered"));
    renderSignUp();
    await fill();

    await userEvent.click(submit());

    expect(
      await screen.findByText("email already registered"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/arrived at/)).not.toBeInTheDocument();
  });

  it("disables the button and shows progress while registering", async () => {
    let finish: () => void = () => {};
    signUp.mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    renderSignUp();
    await fill();

    await userEvent.click(submit());

    expect(
      screen.getByRole("button", { name: "Creating account" }),
    ).toBeDisabled();

    finish();
    expect(await screen.findByText("arrived at /terminal")).toBeInTheDocument();
  });

  it("sends a signed in user straight to the terminal", () => {
    setAuth(testUser);
    renderSignUp();

    expect(screen.getByText("arrived at /terminal")).toBeInTheDocument();
  });
});
