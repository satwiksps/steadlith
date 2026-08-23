function readHttpsUrl(name: string, value: string | undefined, hostOnly = false): URL | undefined {
  const input = value?.trim();
  if (!input) {
    return undefined;
  }

  let parsed: URL;
  try {
    parsed = new URL(hostOnly ? `https://${input}` : input);
  } catch {
    throw new Error(`${name} must be an absolute HTTPS URL.`);
  }

  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    throw new Error(`${name} must be an absolute HTTPS URL without credentials.`);
  }

  return parsed;
}

const configuredSiteUrl = readHttpsUrl(
  "NEXT_PUBLIC_SITE_URL",
  process.env.NEXT_PUBLIC_SITE_URL,
);
const vercelSiteUrl = readHttpsUrl(
  "VERCEL_PROJECT_PRODUCTION_URL",
  process.env.VERCEL_PROJECT_PRODUCTION_URL,
  true,
);
const canonicalUrl = configuredSiteUrl ?? vercelSiteUrl;

export const hasCanonicalSiteUrl = canonicalUrl !== undefined;

export const siteUrl = new URL(canonicalUrl ?? "http://localhost:3000");

export const repositoryUrl = readHttpsUrl(
  "NEXT_PUBLIC_REPOSITORY_URL",
  process.env.NEXT_PUBLIC_REPOSITORY_URL ?? "https://github.com/satwiksps/steadlith",
)?.toString();

export const packageUrl = "https://pypi.org/project/steadlith/";
export const documentationUrl = "https://steadlith.readthedocs.io/en/latest/";

export const site = {
  name: "Steadlith",
  title: "Steadlith",
  description:
    "A Python CLI and library that reuses unchanged embeddings when local RAG corpora change.",
} as const;
