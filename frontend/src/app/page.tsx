import type { Metadata } from "next";

import { HomeRouter } from "@/components/home-router";

export const metadata: Metadata = {
  title: { absolute: "Kiril" },
  description: "Turn long videos into viral short clips.",
};

export default function HomePage() {
  return <HomeRouter />;
}
