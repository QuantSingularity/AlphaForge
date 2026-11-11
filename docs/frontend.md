# Web Terminal

The web terminal is a single-page application in `frontend/`. It is a thin client over the [REST API](rest-api.md): it holds no financial logic of its own and renders what the server computes.

| Item         | Value                                                              |
| :----------- | :----------------------------------------------------------------- |
| Stack        | React 18, TypeScript, Vite, React Router 6, Recharts 2             |
| Build output | `frontend/dist`, a static bundle that the C++ server serves at `/` |
| Dev server   | `http://localhost:5173`, proxying `/api` to the backend            |
| Tests        | Vitest with Testing Library on jsdom: 8 files, 95 tests            |

## Running it

### Production: served by the C++ server

```bash
cd frontend
npm install
npm run build          # type-checks, then writes frontend/dist
cd ..
code/backend/build/alphaforge_server 8080 code/backend/data frontend/dist
```

Open `http://localhost:8080`. The API and the pages come from one origin, so no proxy or CORS setup is needed.

### Development: hot reload

Run the backend as above in one terminal, and in another:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173`. The Vite dev server forwards every `/api` request to the backend, so the browser sees a single origin. The target defaults to `http://127.0.0.1:8080`. To point at a backend elsewhere, set `ALPHAFORGE_API`, either in the shell or in a `.env` file inside `frontend/`:

```bash
ALPHAFORGE_API=http://127.0.0.1:9000 npm run dev
```

### Scripts

| Script                  | What it does                                                      |
| :---------------------- | :---------------------------------------------------------------- |
| `npm run dev`           | Dev server on port 5173 with the `/api` proxy                     |
| `npm run build`         | `tsc --noEmit` then `vite build`. A type error fails the build    |
| `npm run preview`       | Serves the built bundle locally (the `/api` proxy is not applied) |
| `npm test`              | Runs the test suite once                                          |
| `npm run test:watch`    | Runs the tests in watch mode                                      |
| `npm run test:coverage` | Runs the tests with a V8 coverage summary and an `lcov` report    |

A production build currently produces a JavaScript bundle of about 631 kB (178 kB gzipped). Vite warns that it exceeds 500 kB. It is a warning, not an error, and it comes mostly from the charting library. Splitting the bundle by route is the usual fix.

## Pages and routes

| Route         | Page      | Access                                                 |
| :------------ | :-------- | :----------------------------------------------------- |
| `/`           | Home      | Public landing page                                    |
| `/sign-in`    | Sign in   | Public                                                 |
| `/sign-up`    | Sign up   | Public                                                 |
| `/terminal`   | Dashboard | Signed-in users only; others are redirected to sign in |
| anything else | none      | Redirects to `/`                                       |

The C++ server answers any non-API path it cannot find with `index.html`, so a refresh on `/terminal` loads the app rather than a 404.

## The terminal

The dashboard has a status bar, a sidebar and a content area.

**Status bar.** Shows `CONNECTED` or `OFFLINE` according to whether `/api/health` answered, then the engine version, the number of symbols loaded and the number of threads.

**Sidebar.** Navigation in three groups, and a ticker listing each instrument with its last close and its change from the previous close. The ticker is fetched once when the page loads and does not refresh on its own.

| Group    | Navigation item | Panel title           | What it shows                                                                                                                                              |
| :------- | :-------------- | :-------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Markets  | Market Data     | Price History         | Last price, last change, period return and range; a closing price area chart; a table of recent bars                                                       |
| Markets  | Analytics       | Performance Analytics | Annualized return and months observed; rolling volatility and rolling Sharpe line charts; monthly returns as a bar chart. The rolling window is adjustable |
| Trading  | Portfolio       | Account Dashboard     | Total value and total P&L; cash, holdings, realized and unrealized P&L, gross and net exposure; open positions; an allocation pie chart; the trade blotter |
| Trading  | Order Ticket    | Order Ticket          | Symbol, side, order type (market, limit, stop), quantity and prices; the account after the fill; an **Undo Last** button                                   |
| Research | Risk            | Risk Analytics        | A cross-sectional table for every instrument, computed in parallel; a single-instrument detail with beta against a chosen benchmark                        |
| Research | Strategy Lab    | Strategy Lab          | Choose an instrument, a strategy, cost and slippage; see the equity curve, headline metrics, and a strategy versus buy-and-hold table                      |

Each panel fetches its data from the API when it opens and when its inputs change. After you place or undo an order, the Portfolio panel refreshes.

The Order Ticket offers fixed choices for side and order type and sends them as the exact lower-case strings the API needs, so the spelling pitfalls described in the [REST API reference](rest-api.md#post-apiorder) cannot happen from the UI.

The meaning of every number is defined in [Risk and analytics](risk-and-analytics.md) and [Backtesting](backtesting.md).

## How sign-in works in the browser

1. **Sign up or sign in.** The page calls `POST /api/auth/register` or `/api/auth/login`. The sign-up form checks the name (2 or more characters), that the email contains `@`, and the password (8 or more characters) before sending, and shows the server's message if the request fails.
2. **Store the token.** The returned bearer token is held in memory and copied to `localStorage` under the key `alphaforge.token`, so a page refresh does not sign you out.
3. **Attach it.** The API client adds `Authorization: Bearer <token>` to every request once a token exists.
4. **Validate on load.** When the app starts and finds a stored token, it calls `GET /api/auth/me`. If that fails, for example because the server restarted and forgot all sessions, the token is removed and you are signed out.
5. **Sign out.** The client calls `POST /api/auth/logout` on a best-effort basis and clears its own state regardless, so a network error cannot leave you stuck signed in.

Because the server keeps sessions in memory, **restarting the server signs everyone out**. Accounts survive, since they are saved to disk. You sign in again with the same credentials.

**Note:** The sign-in gate protects the terminal page, not the data. The `/terminal` route is guarded in the browser, but the API behind it does not check tokens for market data, orders or the portfolio. Anyone who can reach the server can call those endpoints directly. See the [security notes](rest-api.md#security-notes) in the REST API reference.

## Source layout

```
frontend/
  index.html              entry document
  package.json            scripts and dependencies
  vite.config.ts          dev server, proxy, build and test configuration
  tsconfig.json           TypeScript settings
  src/
    main.tsx              mounts the app
    App.tsx               routes
    api.ts                the API client and token handling
    types.ts              TypeScript types for every API response
    format.ts             number, percent, money and date formatting helpers
    ui.tsx                shared components: Card, Metric, Loading, ErrorState, ChartTooltip
    index.css             all styling
    auth/                 AuthContext (session state) and ProtectedRoute
    pages/                Home, SignIn, SignUp, Dashboard
    panels/               MarketPanel, AnalyticsPanel, PortfolioPanel,
                          OrderPanel, RiskPanel, BacktestPanel
    test/setup.ts         test environment setup
```

### The API client

`src/api.ts` is the only place that talks to the server. It exposes one function per endpoint under the `api` object (`api.health()`, `api.market(symbol, limit)`, `api.order(request)`, `api.backtest(request)`, and so on), prefixes every path with `/api`, attaches the bearer token, and throws an `Error` carrying the server's message for any non-success response. Panels catch that error and show it in an error state.

If you add an endpoint, add its response type to `types.ts` and a function to `api.ts`, then call it from a panel.

## Testing

```bash
cd frontend
npm test
npm run test:coverage
```

The suite has 95 tests in 8 files covering the API client, authentication state, route protection, the sign-in and sign-up forms, formatting helpers, the shared UI components and the app's routing. It runs in a simulated browser (jsdom), so it needs no real browser and no running backend.

**Note:** The tests do not cover the six data panels or the dashboard shell. Statement coverage is therefore low, about 37%, while branch coverage is about 93% for the code that is tested. Adding panel tests with the API client mocked is the most valuable gap to fill.

## Browser support and limits

- The terminal uses current browser features and has no polyfills. Use an up-to-date Chrome, Edge, Firefox or Safari.
- The layout adapts to narrower screens with CSS breakpoints at 1100, 980, 860 and 720 pixels.
- The Market Data panel requests a symbol's full history through the API, so very long histories make a larger response and a heavier chart.
- Prices do not stream. The data is the last loaded close, so the terminal shows a historical snapshot, not live quotes.
