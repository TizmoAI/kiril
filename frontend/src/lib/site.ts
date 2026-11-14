export const SITE_NAME = "Kiril";
export const DEFAULT_SITE_URL = "http://localhost:3107";

export function getSiteUrl() {
  try {
    return new URL(process.env.NEXT_PUBLIC_APP_URL || DEFAULT_SITE_URL).origin;
  } catch {
    return DEFAULT_SITE_URL;
  }
}
