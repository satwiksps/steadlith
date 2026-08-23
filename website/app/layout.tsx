import type { Metadata, Viewport } from "next";

import { hasCanonicalSiteUrl, site, siteUrl } from "@/lib/site";

import "./globals.css";

export const metadata: Metadata = {
  metadataBase: siteUrl,
  title: site.title,
  description: site.description,
  applicationName: site.name,
  keywords: [
    "RAG",
    "incremental indexing",
    "content-defined chunking",
    "embeddings",
    "vector index",
    "open source",
  ],
  alternates: hasCanonicalSiteUrl ? { canonical: "/" } : undefined,
  openGraph: hasCanonicalSiteUrl
    ? {
        type: "website",
        url: "/",
        title: site.title,
        description: site.description,
        siteName: site.name,
        images: [
          {
            url: "/steadlith-social-card.jpg",
            width: 1732,
            height: 908,
            alt: "Steadlith social card",
          },
        ],
      }
    : undefined,
  twitter: hasCanonicalSiteUrl
    ? {
        card: "summary_large_image",
        title: site.title,
        description: site.description,
        images: ["/steadlith-social-card.jpg"],
      }
    : undefined,
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#09090b",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
