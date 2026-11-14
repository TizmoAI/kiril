/**
 * Auth-disabled mode: with NEXT_PUBLIC_AUTH_DISABLED=true every request acts as
 * one fixed local admin user and the sign-in/sign-up screens are bypassed.
 * Only run this on a trusted machine; anyone who can reach the app is that user.
 */
export const authDisabled = process.env.NEXT_PUBLIC_AUTH_DISABLED === "true";

export const LOCAL_USER_ID = "local-user";
export const LOCAL_USER_EMAIL = "local@kiril.local";
const LOCAL_USER_NAME = "Local User";

export function localSession() {
  const now = new Date();
  const expiresAt = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  return {
    session: {
      id: "local-session",
      userId: LOCAL_USER_ID,
      token: "local-session",
      expiresAt,
      createdAt: now,
      updatedAt: now,
      ipAddress: null,
      userAgent: null,
    },
    user: {
      id: LOCAL_USER_ID,
      name: LOCAL_USER_NAME,
      email: LOCAL_USER_EMAIL,
      emailVerified: true,
      image: null,
      createdAt: now,
      updatedAt: now,
      is_admin: true,
    },
  };
}
