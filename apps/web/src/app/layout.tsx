import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

// Fonts are self-hosted from npm (@fontsource-variable/*): builds need no network access
// and visitors' browsers never contact a third-party font CDN.

export const metadata: Metadata = {
  title: { default: "ChainSight", template: "%s · ChainSight" },
  description: "Maritime intelligence, shipment visibility, and quantitative research.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
