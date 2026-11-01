import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { api, getAuthToken } from "./api";
import { testUser } from "./test/fixtures";

// Only the network layer is faked: the real AuthProvider, router and pages run.
vi.mock("./api", () => ({
  api: { me: vi.fn(), login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  getAuthToken: vi.fn(),
  setAuthToken: vi.fn(),
}));

// The real terminal pulls in charts and a dozen API calls; routing is under test.
vi.mock("./pages/Dashboard", () => ({
  Dashboard: () => <p>terminal page</p>,
}));

const mockedGetToken = vi.mocked(getAuthToken);
const mockedMe = vi.mocked(api.me);

const LANDING = { level: 1, name: /quant trading terminal/i } as const;

// App owns its BrowserRouter, so navigation is driven through the real history.
function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

function signedIn() {
  mockedGetToken.mockReturnValue("valid-token");
  mockedMe.mockResolvedValue({ user: testUser });
}

describe("App routing", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockedGetToken.mockReturnValue(null);
    window.history.pushState({}, "", "/");
  });

  describe("anonymous visitors", () => {
    it("see the landing page on /", async () => {
      renderAt("/");

      expect(await screen.findByRole("heading", LANDING)).toBeInTheDocument();
      expect(
        screen.getAllByRole("link", { name: "Create free account" }).length,
      ).toBeGreaterThan(0);
    });

    it("can open the sign in page", async () => {
      renderAt("/sign-in");

      expect(
        await screen.findByRole("heading", { name: "Access your terminal" }),
      ).toBeInTheDocument();
    });

    it("can open the sign up page", async () => {
      renderAt("/sign-up");

      expect(
        await screen.findByRole("heading", { name: "Get started" }),
      ).toBeInTheDocument();
    });

    it("are redirected from the terminal to the sign in page", async () => {
      renderAt("/terminal");

      expect(
        await screen.findByRole("heading", { name: "Access your terminal" }),
      ).toBeInTheDocument();
      expect(window.location.pathname).toBe("/sign-in");
      expect(screen.queryByText("terminal page")).not.toBeInTheDocument();
    });

    it("are sent to the landing page for unknown paths", async () => {
      renderAt("/does-not-exist");

      expect(await screen.findByRole("heading", LANDING)).toBeInTheDocument();
      expect(window.location.pathname).toBe("/");
    });
  });

  describe("signed in users", () => {
    beforeEach(signedIn);

    it("can open the terminal", async () => {
      renderAt("/terminal");

      expect(await screen.findByText("terminal page")).toBeInTheDocument();
    });

    it("see an Open Terminal link on the landing page", async () => {
      renderAt("/");

      expect(
        (await screen.findAllByRole("link", { name: "Open Terminal" })).length,
      ).toBeGreaterThan(0);
      expect(
        screen.queryByRole("link", { name: "Create free account" }),
      ).not.toBeInTheDocument();
    });

    it.each(["/sign-in", "/sign-up"])(
      "are sent from %s straight to the terminal",
      async (path) => {
        renderAt(path);

        expect(await screen.findByText("terminal page")).toBeInTheDocument();
        expect(window.location.pathname).toBe("/terminal");
      },
    );
  });

  it("drops a stale token and treats the visitor as anonymous", async () => {
    mockedGetToken.mockReturnValue("revoked-token");
    mockedMe.mockRejectedValue(new Error("unauthorized"));

    renderAt("/terminal");

    expect(
      await screen.findByRole("heading", { name: "Access your terminal" }),
    ).toBeInTheDocument();
  });
});
