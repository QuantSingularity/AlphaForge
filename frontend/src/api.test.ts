import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, getAuthToken, setAuthToken } from "./api";

const TOKEN_KEY = "alphaforge.token";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetch(body: unknown = {}, status = 200) {
  // A Response body can only be read once, so build a fresh one for every call.
  const fetchMock = vi
    .fn()
    .mockImplementation(() => Promise.resolve(jsonResponse(body, status)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// The first argument is the URL, the second the request options.
function lastCall(fetchMock: ReturnType<typeof mockFetch>) {
  const calls = fetchMock.mock.calls;
  const [url, options] = calls[calls.length - 1] as [string, RequestInit];
  return { url, options };
}

beforeEach(() => {
  setAuthToken(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("auth token handling", () => {
  it("starts without a token", () => {
    expect(getAuthToken()).toBeNull();
  });

  it("keeps the token in memory and mirrors it to localStorage", () => {
    setAuthToken("abc123");

    expect(getAuthToken()).toBe("abc123");
    expect(localStorage.getItem(TOKEN_KEY)).toBe("abc123");
  });

  it("removes the token from both places when cleared", () => {
    setAuthToken("abc123");

    setAuthToken(null);

    expect(getAuthToken()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("restores a token saved by an earlier page load", async () => {
    localStorage.setItem(TOKEN_KEY, "saved-token");
    vi.resetModules();

    const fresh = await import("./api");

    expect(fresh.getAuthToken()).toBe("saved-token");
  });
});

describe("requests", () => {
  it("sends GET requests to the API with no auth header when signed out", async () => {
    const fetchMock = mockFetch({ status: "ok" });

    await api.health();

    const { url, options } = lastCall(fetchMock);
    expect(url).toBe("/api/health");
    expect(options.headers).toEqual({});
  });

  it("attaches the bearer token once one is set", async () => {
    setAuthToken("abc123");
    const fetchMock = mockFetch({});

    await api.portfolio();

    expect(lastCall(fetchMock).options.headers).toEqual({
      Authorization: "Bearer abc123",
    });
  });

  it("returns the parsed JSON body", async () => {
    mockFetch({ strategies: ["momentum", "mean_reversion"] });

    await expect(api.strategies()).resolves.toEqual({
      strategies: ["momentum", "mean_reversion"],
    });
  });

  it("posts JSON bodies with a content type", async () => {
    const fetchMock = mockFetch({ ok: true });

    await api.order({
      symbol: "AAPL",
      side: "buy",
      type: "market",
      quantity: 10,
    });

    const { url, options } = lastCall(fetchMock);
    expect(url).toBe("/api/order");
    expect(options.method).toBe("POST");
    expect(options.headers).toMatchObject({
      "Content-Type": "application/json",
    });
    expect(JSON.parse(options.body as string)).toEqual({
      symbol: "AAPL",
      side: "buy",
      type: "market",
      quantity: 10,
    });
  });

  it("sends the bearer token on posts as well", async () => {
    setAuthToken("abc123");
    const fetchMock = mockFetch({});

    await api.undo();

    expect(lastCall(fetchMock).options.headers).toMatchObject({
      Authorization: "Bearer abc123",
    });
  });
});

describe("endpoint URLs", () => {
  it("requests a symbol's bars, with an optional limit", async () => {
    const fetchMock = mockFetch({});

    await api.market("AAPL");
    expect(lastCall(fetchMock).url).toBe("/api/market/AAPL");

    await api.market("AAPL", 250);
    expect(lastCall(fetchMock).url).toBe("/api/market/AAPL?limit=250");
  });

  it("requests risk metrics at 95% confidence unless told otherwise", async () => {
    const fetchMock = mockFetch({});

    await api.risk("AAPL");
    expect(lastCall(fetchMock).url).toBe("/api/risk/AAPL?confidence=0.95");

    await api.risk("AAPL", "SPY", 0.99);
    expect(lastCall(fetchMock).url).toBe(
      "/api/risk/AAPL?confidence=0.99&benchmark=SPY",
    );
  });

  it("requests batch risk at 95% confidence unless told otherwise", async () => {
    const fetchMock = mockFetch({});

    await api.riskBatch();
    expect(lastCall(fetchMock).url).toBe("/api/risk?confidence=0.95");

    await api.riskBatch(0.9);
    expect(lastCall(fetchMock).url).toBe("/api/risk?confidence=0.9");
  });

  it("requests analytics over a 20 day window unless told otherwise", async () => {
    const fetchMock = mockFetch({});

    await api.analytics("MSFT");
    expect(lastCall(fetchMock).url).toBe("/api/analytics/MSFT?window=20");

    await api.analytics("MSFT", 60);
    expect(lastCall(fetchMock).url).toBe("/api/analytics/MSFT?window=60");
  });

  it("posts backtest requests", async () => {
    const fetchMock = mockFetch({});

    await api.backtest({
      symbol: "AAPL",
      strategy: "momentum",
      initial_capital: 50_000,
    });

    const { url, options } = lastCall(fetchMock);
    expect(url).toBe("/api/backtest");
    expect(JSON.parse(options.body as string)).toEqual({
      symbol: "AAPL",
      strategy: "momentum",
      initial_capital: 50_000,
    });
  });

  it("posts the credentials for sign in and sign up", async () => {
    const fetchMock = mockFetch({});

    await api.login("ada@firm.com", "secret");
    expect(lastCall(fetchMock).url).toBe("/api/auth/login");
    expect(JSON.parse(lastCall(fetchMock).options.body as string)).toEqual({
      email: "ada@firm.com",
      password: "secret",
    });

    await api.register("Ada", "ada@firm.com", "secret");
    expect(lastCall(fetchMock).url).toBe("/api/auth/register");
    expect(JSON.parse(lastCall(fetchMock).options.body as string)).toEqual({
      name: "Ada",
      email: "ada@firm.com",
      password: "secret",
    });
  });

  it("uses GET for the current user and POST for sign out", async () => {
    const fetchMock = mockFetch({});

    await api.me();
    expect(lastCall(fetchMock).url).toBe("/api/auth/me");
    expect(lastCall(fetchMock).options.method).toBeUndefined();

    await api.logout();
    expect(lastCall(fetchMock).url).toBe("/api/auth/logout");
    expect(lastCall(fetchMock).options.method).toBe("POST");
  });
});

describe("errors", () => {
  it("throws the error message sent by the server", async () => {
    mockFetch({ error: "insufficient buying power" }, 400);

    await expect(
      api.order({ symbol: "AAPL", side: "buy", type: "market", quantity: 1e9 }),
    ).rejects.toThrow("insufficient buying power");
  });

  it("falls back to the status code when the body has no message", async () => {
    mockFetch({}, 500);

    await expect(api.health()).rejects.toThrow("request failed (500)");
  });

  it("copes with an error response that is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Bad Gateway", { status: 502 })),
    );

    await expect(api.portfolio()).rejects.toThrow("request failed (502)");
    await expect(api.undo()).rejects.toThrow("request failed (502)");
  });

  it("surfaces server errors for posts too", async () => {
    mockFetch({ error: "invalid credentials" }, 401);

    await expect(api.login("a@b.com", "wrong")).rejects.toThrow(
      "invalid credentials",
    );
  });
});
