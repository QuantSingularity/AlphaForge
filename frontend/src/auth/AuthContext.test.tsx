import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, getAuthToken, setAuthToken } from "../api";
import type { AuthUser } from "../types";
import { testUser } from "../test/fixtures";
import { AuthProvider, useAuth } from "./AuthContext";

// The context talks to the network only through ../api, so fake that module.
vi.mock("../api", () => ({
  api: {
    me: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  },
  getAuthToken: vi.fn(),
  setAuthToken: vi.fn(),
}));

const mockedApi = vi.mocked(api);
const mockedGetToken = vi.mocked(getAuthToken);
const mockedSetToken = vi.mocked(setAuthToken);

const user = testUser;

const wrapper = ({ children }: { children: ReactNode }) => (
  <AuthProvider>{children}</AuthProvider>
);

async function renderAuth() {
  const hook = renderHook(() => useAuth(), { wrapper });
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  return hook;
}

describe("AuthProvider", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockedGetToken.mockReturnValue(null);
    mockedApi.logout.mockResolvedValue({ ok: true });
  });

  describe("restoring a session", () => {
    it("is ready and signed out when there is no stored token", async () => {
      const { result } = await renderAuth();

      expect(result.current.user).toBeNull();
      expect(mockedApi.me).not.toHaveBeenCalled();
    });

    it("validates a stored token and signs the user in", async () => {
      mockedGetToken.mockReturnValue("stored-token");
      mockedApi.me.mockResolvedValue({ user });

      const { result } = await renderAuth();

      expect(mockedApi.me).toHaveBeenCalledTimes(1);
      expect(result.current.user).toEqual(user);
    });

    it("discards a stale token that the server rejects", async () => {
      mockedGetToken.mockReturnValue("revoked-token");
      mockedApi.me.mockRejectedValue(new Error("unauthorized"));

      const { result } = await renderAuth();

      expect(result.current.user).toBeNull();
      expect(mockedSetToken).toHaveBeenCalledWith(null);
    });

    it("is not ready until the token check has finished", async () => {
      mockedGetToken.mockReturnValue("stored-token");
      let finish: (value: { user: AuthUser }) => void = () => {};
      mockedApi.me.mockReturnValue(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );

      const { result } = renderHook(() => useAuth(), { wrapper });
      expect(result.current.ready).toBe(false);

      await act(async () => finish({ user }));

      expect(result.current.ready).toBe(true);
      expect(result.current.user).toEqual(user);
    });
  });

  describe("signIn", () => {
    it("stores the token and the user from the server", async () => {
      mockedApi.login.mockResolvedValue({ token: "new-token", user });
      const { result } = await renderAuth();

      await act(async () => {
        await result.current.signIn("ada@firm.com", "secret");
      });

      expect(mockedApi.login).toHaveBeenCalledWith("ada@firm.com", "secret");
      expect(mockedSetToken).toHaveBeenCalledWith("new-token");
      expect(result.current.user).toEqual(user);
    });

    it("stays signed out and rethrows when the server rejects the login", async () => {
      mockedApi.login.mockRejectedValue(new Error("invalid credentials"));
      const { result } = await renderAuth();

      await expect(
        act(async () => {
          await result.current.signIn("ada@firm.com", "wrong");
        }),
      ).rejects.toThrow("invalid credentials");

      expect(result.current.user).toBeNull();
      expect(mockedSetToken).not.toHaveBeenCalled();
    });
  });

  describe("signUp", () => {
    it("registers the account and signs the new user in", async () => {
      mockedApi.register.mockResolvedValue({ token: "reg-token", user });
      const { result } = await renderAuth();

      await act(async () => {
        await result.current.signUp(
          "Ada Lovelace",
          "ada@firm.com",
          "secret123",
        );
      });

      expect(mockedApi.register).toHaveBeenCalledWith(
        "Ada Lovelace",
        "ada@firm.com",
        "secret123",
      );
      expect(mockedSetToken).toHaveBeenCalledWith("reg-token");
      expect(result.current.user).toEqual(user);
    });

    it("rethrows registration errors", async () => {
      mockedApi.register.mockRejectedValue(
        new Error("email already registered"),
      );
      const { result } = await renderAuth();

      await expect(
        act(async () => {
          await result.current.signUp("Ada", "ada@firm.com", "secret123");
        }),
      ).rejects.toThrow("email already registered");
      expect(result.current.user).toBeNull();
    });
  });

  describe("signOut", () => {
    async function renderSignedIn() {
      mockedGetToken.mockReturnValue("stored-token");
      mockedApi.me.mockResolvedValue({ user });
      return renderAuth();
    }

    it("clears the token and the user and tells the server", async () => {
      const { result } = await renderSignedIn();

      act(() => result.current.signOut());

      expect(mockedApi.logout).toHaveBeenCalledTimes(1);
      expect(mockedSetToken).toHaveBeenLastCalledWith(null);
      expect(result.current.user).toBeNull();
    });

    it("still signs the user out when the server cannot be reached", async () => {
      mockedApi.logout.mockRejectedValue(new Error("network down"));
      const { result } = await renderSignedIn();

      act(() => result.current.signOut());

      expect(result.current.user).toBeNull();
      expect(mockedSetToken).toHaveBeenLastCalledWith(null);
    });
  });

  describe("useAuth", () => {
    it("throws outside an AuthProvider", () => {
      // React logs the thrown error; keep the test output clean.
      vi.spyOn(console, "error").mockImplementation(() => {});

      expect(() => renderHook(() => useAuth())).toThrow(
        "useAuth must be used inside an AuthProvider",
      );
    });
  });
});
