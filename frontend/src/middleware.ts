import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_FILE = /\.(.*)$/;

const AUTH_PAGES = ["/sign-in", "/sign-up", "/forgot-password", "/reset-password"];
const AUTH_API = ["/api/auth/sign-up", "/api/auth/sign-in", "/api/auth/forget-password", "/api/auth/reset-password"];

export function middleware(request: NextRequest) {
  if (process.env.NEXT_PUBLIC_AUTH_DISABLED === "true") {
    const { pathname } = request.nextUrl;
    if (AUTH_API.some((path) => pathname.startsWith(path))) {
      return NextResponse.json({ error: "Authentication is disabled." }, { status: 404 });
    }
    if (AUTH_PAGES.some((path) => pathname === path || pathname.startsWith(`${path}/`))) {
      const url = request.nextUrl.clone();
      url.pathname = "/";
      url.search = "";
      return NextResponse.redirect(url);
    }
  }

  const isLandingOnlyModeEnabled =
    process.env.NEXT_PUBLIC_LANDING_ONLY_MODE === "true";

  if (!isLandingOnlyModeEnabled) {
    return NextResponse.next();
  }

  const { pathname } = request.nextUrl;

  if (
    pathname === "/" ||
    pathname === "/status" ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api/billing/webhook") ||
    pathname.startsWith("/ingest/") ||
    // Opt-outs (including mailbox one-click POSTs) must work in every mode.
    pathname === "/email/product-updates/unsubscribe" ||
    PUBLIC_FILE.test(pathname)
  ) {
    return NextResponse.next();
  }

  if (pathname.startsWith("/api")) {
    return NextResponse.json(
      { error: "Kiril is in landing-page-only mode." },
      { status: 503 }
    );
  }

  const url = request.nextUrl.clone();
  url.pathname = "/";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: "/:path*",
};
