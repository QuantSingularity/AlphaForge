import type { AuthUser } from "../types";

// A fully typed signed in user shared by the tests.
export const testUser: AuthUser = {
  id: "user-1",
  name: "Ada Lovelace",
  email: "ada@firm.com",
  created_at: "2026-01-01T00:00:00Z",
};
