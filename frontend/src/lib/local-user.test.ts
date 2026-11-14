import { describe, expect, it } from "vitest";

import { LOCAL_USER_EMAIL, LOCAL_USER_ID, localSession } from "./local-user";

describe("localSession", () => {
  it("is a verified admin session for the fixed local user", () => {
    const { session, user } = localSession();

    expect(user.id).toBe(LOCAL_USER_ID);
    expect(user.email).toBe(LOCAL_USER_EMAIL);
    expect(user.emailVerified).toBe(true);
    expect(user.is_admin).toBe(true);
    expect(session.userId).toBe(LOCAL_USER_ID);
    expect(session.expiresAt.getTime()).toBeGreaterThan(Date.now());
  });
});
