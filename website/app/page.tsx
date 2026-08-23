import { CopyButton } from "@/components/copy-button";
import { MobileMenu } from "@/components/mobile-menu";
import { SteadlithMark } from "@/components/icons";
import { documentationUrl, packageUrl, repositoryUrl } from "@/lib/site";
import packageMetadata from "../package.json";

const releaseVersion = packageMetadata.version;

const capabilities = [
  {
    stage: "Identity",
    mechanism: "Content-defined chunks",
    behavior: "Normalize words, place Rabin boundaries, and hash canonical content with versioned parameters.",
    default: "Deterministic",
  },
  {
    stage: "Plan",
    mechanism: "Manifest diff",
    behavior: "Classify add, keep, move, and delete operations before any provider call or state write.",
    default: "Read only",
  },
  {
    stage: "Cache",
    mechanism: "Content-addressed embeddings",
    behavior: "Reuse vectors by chunk, provider, model, and embedding parameters.",
    default: "Local SQLite",
  },
  {
    stage: "Apply",
    mechanism: "Transactional publication",
    behavior: "Reject stale plans, tombstone removals, and publish one validated index generation.",
    default: "Explicit approval",
  },
  {
    stage: "Verify",
    mechanism: "Manifest and index checks",
    behavior: "Compare generations, record digests, active rows, and Merkle roots.",
    default: "Offline",
  },
  {
    stage: "Query",
    mechanism: "Pluggable embeddings",
    behavior: "Use deterministic lexical retrieval or an explicitly selected learned provider.",
    default: "No credentials",
  },
] as const;

const workflows = [
  {
    name: "Plan",
    title: "Inspect the complete delta",
    description: "Resolve the desired corpus and price only known cache misses without changing state.",
    file: "terminal",
    command: "steadlith plan",
  },
  {
    name: "Apply",
    title: "Publish one generation",
    description: "Reuse cached vectors, embed misses, and approve destructive operations explicitly.",
    file: "terminal",
    command: "steadlith index --allow-delete",
  },
  {
    name: "Verify",
    title: "Check committed state",
    description: "Confirm that the active index, manifest, generation, and record digests agree.",
    file: "terminal",
    command: "steadlith verify",
  },
] as const;

const architectureSteps = [
  ["01", "Chunk", "Normalize words and place content-defined Rabin boundaries."],
  ["02", "Identify", "Hash canonical content with the versioned chunking recipe."],
  ["03", "Plan", "Compare manifests and resolve reusable embedding identities."],
  ["04", "Publish", "Apply one transaction, tombstone removals, and verify state."],
] as const;

const installCommand = [
  "python -m pip install steadlith",
  "steadlith init",
  "steadlith plan",
].join("\n");

const quickStartUrl = `${documentationUrl}getting-started/quickstart/`;
const architectureUrl = `${documentationUrl}architecture/`;
const apiUrl = `${documentationUrl}reference/python-api/`;
const repositoryBaseUrl = repositoryUrl?.replace(/\/$/, "");
const securityUrl = repositoryBaseUrl ? `${repositoryBaseUrl}/blob/main/SECURITY.md` : undefined;
const contributingUrl = repositoryBaseUrl
  ? `${repositoryBaseUrl}/blob/main/CONTRIBUTING.md`
  : undefined;

export default function Home() {
  const externalLinkProps = {
    target: "_blank" as const,
    rel: "noreferrer",
  };

  return (
    <div className="min-h-screen overflow-x-hidden bg-[#09090b] text-zinc-100">
      <a
        className="fixed left-4 top-4 z-[100] -translate-y-24 rounded-md bg-white px-4 py-2 text-sm font-semibold text-zinc-950 transition-transform focus:translate-y-0"
        href="#main"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-[#09090b]/90 backdrop-blur-xl">
        <nav
          className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6 lg:px-8"
          aria-label="Main navigation"
        >
          <a className="font-semibold tracking-tight" href="#top">
            Steadlith
          </a>

          <div className="hidden items-center gap-7 text-sm text-zinc-400 md:flex">
            <a className="transition-colors hover:text-white" href="#product">How it works</a>
            <a className="transition-colors hover:text-white" href="#capabilities">Capabilities</a>
            <a className="transition-colors hover:text-white" href="#workflow">Workflow</a>
            <a className="transition-colors hover:text-white" href={documentationUrl} {...externalLinkProps}>Docs</a>
          </div>

          <div className="flex items-center gap-2">
            {repositoryUrl ? (
              <a
                className="hidden h-9 items-center rounded-md border border-white/10 bg-white/[0.035] px-3.5 text-sm font-medium text-zinc-200 transition-colors hover:border-white/20 hover:bg-white/[0.07] sm:inline-flex"
                href={repositoryUrl}
                {...externalLinkProps}
              >
                GitHub
              </a>
            ) : null}
            <MobileMenu />
          </div>
        </nav>
      </header>

      <main id="main">
        <section id="top" className="relative">
          <div className="hero-grid absolute inset-x-0 top-0 h-[720px] opacity-60" aria-hidden="true" />
          <div className="relative mx-auto max-w-7xl px-5 pb-16 pt-24 sm:px-6 sm:pt-28 lg:px-8 lg:pb-20 lg:pt-32">
            <div className="mx-auto max-w-4xl text-center">
              <p className="mb-5 font-mono text-xs font-medium uppercase tracking-[0.18em] text-emerald-300">
                Python CLI + library · local SQLite · offline by default
              </p>
              <h1 className="text-balance text-5xl font-semibold tracking-[-0.045em] text-white sm:text-6xl lg:text-[72px] lg:leading-[1.04]">
                Index only what changed.
                <span className="block text-zinc-400">Reuse everything else.</span>
              </h1>
              <p className="mx-auto mt-6 max-w-2xl text-pretty text-base leading-7 text-zinc-400 sm:text-lg sm:leading-8">
                Steadlith maintains local RAG indexes when source documents change. It gives chunks
                stable identities, shows a dry-run plan, reuses cached embeddings, then publishes one
                transactional SQLite update.
              </p>
              <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <a
                  className="inline-flex h-11 w-full items-center justify-center rounded-md bg-white px-5 text-sm font-semibold text-zinc-950 transition-colors hover:bg-zinc-200 sm:w-auto"
                  href="#get-started"
                >
                  Get started
                </a>
                {repositoryUrl ? (
                  <a
                    className="inline-flex h-11 w-full items-center justify-center rounded-md border border-white/12 bg-white/[0.035] px-5 text-sm font-medium text-zinc-200 transition-colors hover:border-white/20 hover:bg-white/[0.07] sm:w-auto"
                    href={repositoryUrl}
                    {...externalLinkProps}
                  >
                    View source
                  </a>
                ) : null}
              </div>
              <p className="mt-5 text-sm text-zinc-400">
                Plan first. No network or index write unless you explicitly allow it.
              </p>
            </div>

            <div id="product" className="mt-14 scroll-mt-24 lg:mt-16">
              <figure className="overflow-hidden rounded-xl border border-white/10 bg-[#0c0c0f] shadow-[0_32px_100px_rgba(0,0,0,0.55)]">
                <figcaption className="sr-only">Illustrative output from the Steadlith plan command</figcaption>
                <div className="flex h-12 items-center justify-between border-b border-white/[0.07] bg-[#111114] px-4 sm:px-5">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="grid size-6 shrink-0 place-items-center rounded border border-white/10 bg-white/[0.03] font-mono text-[9px] font-bold text-emerald-300">S</span>
                    <span className="truncate text-xs font-medium text-zinc-300">CLI dry run</span>
                    <span className="hidden text-xs text-zinc-400 sm:inline">/</span>
                    <span className="hidden font-mono text-[11px] text-zinc-400 sm:inline">steadlith plan</span>
                  </div>
                  <span className="flex shrink-0 items-center gap-2 font-mono text-[10px] text-emerald-300">
                    <i className="size-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
                    NO WRITES
                  </span>
                </div>

                <div className="grid lg:grid-cols-[minmax(0,1fr)_360px]">
                  <div className="min-w-0">
                    <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3 sm:px-5">
                      <span className="font-mono text-[11px] text-zinc-400">Index plan</span>
                      <span className="font-mono text-[10px] text-zinc-400">complete desired corpus</span>
                    </div>
                    <div className="overflow-x-auto py-5 font-mono text-[11px] leading-7 sm:py-7 sm:text-[13px]">
                      <div className="grid min-w-[540px] grid-cols-[110px_90px_1fr] border-y border-white/[0.06] bg-white/[0.02] px-3 text-[10px] uppercase tracking-wider text-zinc-400 sm:px-5">
                        <span>Operation</span><span className="text-right">Chunks</span><span className="pl-8">What happens</span>
                      </div>
                      <div className="grid min-w-[540px] grid-cols-[110px_90px_1fr] border-b border-emerald-400/10 bg-emerald-400/[0.04] px-3 text-zinc-300 sm:px-5">
                        <span className="text-emerald-300">add</span><span className="text-right">2</span><span className="pl-8 text-zinc-400">embed cache misses</span>
                      </div>
                      <div className="grid min-w-[540px] grid-cols-[110px_90px_1fr] border-b border-white/[0.04] px-3 text-zinc-400 sm:px-5">
                        <span>keep</span><span className="text-right">187</span><span className="pl-8">reuse existing identities</span>
                      </div>
                      <div className="grid min-w-[540px] grid-cols-[110px_90px_1fr] border-b border-blue-400/10 bg-blue-400/[0.035] px-3 text-zinc-300 sm:px-5">
                        <span className="text-blue-300">move</span><span className="text-right">1</span><span className="pl-8 text-zinc-400">reuse content at a new position</span>
                      </div>
                      <div className="grid min-w-[540px] grid-cols-[110px_90px_1fr] border-b border-amber-400/10 bg-amber-400/[0.04] px-3 text-zinc-300 sm:px-5">
                        <span className="text-amber-300">delete</span><span className="text-right">1</span><span className="pl-8 text-zinc-400">requires explicit approval</span>
                      </div>
                    </div>
                    <div className="border-t border-white/[0.07] bg-black/20 px-4 py-4 font-mono text-[11px] sm:px-5 sm:text-xs">
                      <div className="flex gap-3"><span className="select-none text-emerald-300">$</span><code className="text-zinc-300">steadlith plan</code></div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-zinc-400">
                        <span><b className="font-medium text-emerald-300">2 embeddings</b> needed</span><span>no writes</span><span>no network</span>
                      </div>
                    </div>
                  </div>

                  <aside className="border-t border-white/[0.07] bg-[#0a0a0d] lg:border-l lg:border-t-0">
                    <div className="flex h-12 items-center justify-between border-b border-white/[0.07] px-5">
                      <span className="text-xs font-medium text-zinc-300">What the plan means</span>
                      <span className="grid size-5 place-items-center rounded bg-white/[0.06] font-mono text-[10px] text-zinc-400">?</span>
                    </div>
                    <div className="p-5">
                      <div className="font-mono text-[10px] font-semibold uppercase tracking-wider text-emerald-300">Safe preview</div>
                      <p className="mt-4 text-base font-semibold text-white">Only two chunks need embeddings.</p>
                      <p className="mt-2 text-sm leading-6 text-zinc-400">The other 188 active chunks keep their identities, so their vectors do not need to be recomputed.</p>
                      <dl className="mt-5 divide-y divide-white/[0.07] border-y border-white/[0.07] text-xs">
                        <div className="flex justify-between py-3"><dt className="text-zinc-400">Embeddings</dt><dd className="font-mono text-zinc-200">2</dd></div>
                        <div className="flex justify-between py-3"><dt className="text-zinc-400">Deletes</dt><dd className="font-mono text-zinc-200">1</dd></div>
                        <div className="flex justify-between py-3"><dt className="text-zinc-400">Index writes</dt><dd className="font-mono text-zinc-200">0</dd></div>
                      </dl>
                      <div className="mt-5 border-l-2 border-emerald-400/50 pl-3">
                        <span className="font-mono text-[9px] uppercase tracking-wider text-zinc-400">Next step</span>
                        <p className="mt-1.5 text-xs leading-5 text-zinc-400">Review the complete corpus scope, then run <code>steadlith index --allow-delete</code> to apply this plan.</p>
                      </div>
                    </div>
                  </aside>
                </div>
              </figure>
            </div>

            <div aria-label="Steadlith indexing flow" className="grid gap-px border-x border-b border-white/[0.07] bg-white/[0.07] sm:grid-cols-2 lg:grid-cols-5">
              {[
                ["01 · Sources", "Markdown and text files"],
                ["02 · Chunk", "stable content identities"],
                ["03 · Plan", "add, keep, move, delete"],
                ["04 · Embed", "reuse cached vectors"],
                ["05 · Publish", "one SQLite transaction"],
              ].map(([label, detail], index) => (
                <div className="bg-[#0b0b0e] px-5 py-4" key={label}>
                  <strong className="flex items-center justify-between text-xs font-medium text-zinc-200">{label}{index < 4 ? <span className="text-zinc-700" aria-hidden="true">→</span> : null}</strong>
                  <span className="mt-1 block font-mono text-[10px] text-zinc-400">{detail}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="border-y border-white/[0.07] bg-white/[0.012]">
          <div className="mx-auto grid max-w-7xl gap-12 px-5 py-20 sm:px-6 lg:grid-cols-[0.9fr_1.1fr] lg:gap-24 lg:px-8 lg:py-28">
            <div>
              <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-emerald-300">The cost of unstable boundaries</p>
              <h2 className="mt-4 max-w-xl text-3xl font-semibold tracking-[-0.035em] text-white sm:text-4xl">A small edit should not force a full re-index.</h2>
            </div>
            <div className="space-y-8 text-base leading-7 text-zinc-400">
              <p>Offset-based chunking can shift every downstream boundary after an early insertion. The text may be unchanged while its chunk hashes and embeddings are not.</p>
              <dl className="divide-y divide-white/[0.07] border-y border-white/[0.07]">
                <div className="grid gap-2 py-4 sm:grid-cols-[150px_1fr]"><dt className="text-sm font-medium text-zinc-200">Offset chunks</dt><dd className="text-sm text-zinc-400">Where does this fixed window begin now?</dd></div>
                <div className="grid gap-2 py-4 sm:grid-cols-[150px_1fr]"><dt className="text-sm font-medium text-zinc-200">Steadlith</dt><dd className="text-sm text-zinc-400">Which content identities actually changed?</dd></div>
              </dl>
              <p className="text-sm text-zinc-400">Steadlith reports a concrete manifest delta. It does not promise that every edit changes only a fixed number of chunks.</p>
            </div>
          </div>
        </section>

        <section id="capabilities" className="scroll-mt-24">
          <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:px-8 lg:py-28">
            <div className="grid gap-6 lg:grid-cols-[1fr_420px] lg:items-end">
              <div>
                <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-emerald-300">One explicit state model</p>
                <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] text-white sm:text-4xl">Every transition stays inspectable.</h2>
              </div>
              <p className="text-sm leading-6 text-zinc-400">Chunk identity, cache identity, manifest state, and index publication remain separate so the plan can explain what will happen before it happens.</p>
            </div>

            <div className="mt-10 overflow-hidden rounded-lg border border-white/[0.08]">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] border-collapse text-left">
                  <thead className="bg-white/[0.025] font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-400">
                    <tr><th className="px-5 py-3 font-medium">Stage</th><th className="px-5 py-3 font-medium">Mechanism</th><th className="px-5 py-3 font-medium">Observable behavior</th><th className="px-5 py-3 font-medium">Default</th></tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.06] text-sm">
                    {capabilities.map((capability) => (
                      <tr className="bg-[#0b0b0e] transition-colors hover:bg-white/[0.025]" key={capability.stage}>
                        <td className="px-5 py-4 font-mono text-[11px] font-semibold text-emerald-300">{capability.stage}</td>
                        <td className="px-5 py-4 font-medium text-zinc-200">{capability.mechanism}</td>
                        <td className="max-w-xl px-5 py-4 text-xs leading-5 text-zinc-400">{capability.behavior}</td>
                        <td className="px-5 py-4 font-mono text-[10px] text-zinc-400">{capability.default}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <a className="mt-5 inline-flex text-xs font-medium text-zinc-400 underline decoration-white/20 underline-offset-4 hover:text-white" href={documentationUrl} {...externalLinkProps}>Read the complete documentation</a>
          </div>
        </section>

        <section id="workflow" className="scroll-mt-24 border-y border-white/[0.07] bg-white/[0.012]">
          <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:px-8 lg:py-28">
            <div className="max-w-2xl">
              <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-emerald-300">One workflow, three explicit stages</p>
              <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] text-white sm:text-4xl">Plan before writing. Verify after.</h2>
              <p className="mt-4 text-sm leading-6 text-zinc-400">The CLI exposes the same planner and index service available from the Python API.</p>
            </div>

            <div className="mt-10 grid gap-4 lg:grid-cols-3">
              {workflows.map((workflow) => (
                <article className="flex min-h-64 flex-col rounded-lg border border-white/[0.08] bg-[#0b0b0e] p-5" key={workflow.name}>
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-emerald-300">{workflow.name}</span>
                  <h3 className="mt-3 text-base font-semibold text-zinc-100">{workflow.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-zinc-400">{workflow.description}</p>
                  <div className="mt-auto overflow-hidden rounded-md border border-white/[0.07] bg-black/25">
                    <div className="flex h-10 items-center justify-between border-b border-white/[0.06] px-3"><span className="font-mono text-[9px] text-zinc-400">{workflow.file}</span><CopyButton value={workflow.command} label={`Copy ${workflow.name} command`} /></div>
                    <pre className="overflow-x-auto p-3 font-mono text-[11px] leading-5 text-zinc-300"><code>{workflow.command}</code></pre>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="architecture" className="scroll-mt-24">
          <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:px-8 lg:py-28">
            <div className="grid gap-12 lg:grid-cols-[0.82fr_1.18fr] lg:gap-24">
              <div>
                <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-emerald-300">Small, explicit trust boundary</p>
                <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] text-white sm:text-4xl">Pure identities. Effects at the edge.</h2>
                <p className="mt-4 text-sm leading-6 text-zinc-400">Chunking and planning stay deterministic. Files, credentials, providers, and SQLite enter through explicit application boundaries.</p>
                <a className="mt-5 inline-flex text-xs font-medium text-zinc-400 underline decoration-white/20 underline-offset-4 hover:text-white" href={architectureUrl} {...externalLinkProps}>Read the architecture</a>
              </div>
              <ol className="grid gap-px overflow-hidden rounded-lg border border-white/[0.08] bg-white/[0.08] sm:grid-cols-2">
                {architectureSteps.map(([number, title, description]) => (
                  <li className="bg-[#0b0b0e] p-5" key={number}>
                    <span className="font-mono text-[10px] text-emerald-300">{number}</span>
                    <h3 className="mt-5 text-sm font-semibold text-zinc-200">{title}</h3>
                    <p className="mt-2 text-xs leading-5 text-zinc-400">{description}</p>
                  </li>
                ))}
              </ol>
            </div>
            <div className="mt-8 flex flex-wrap gap-2 font-mono text-[10px] uppercase tracking-wider text-zinc-400">
              {["No network by default", "No implicit deletions", "No silent migrations", "No remote backend claim"].map((item) => <span className="rounded border border-white/[0.08] bg-white/[0.02] px-3 py-2" key={item}>{item}</span>)}
            </div>
          </div>
        </section>

        <section id="get-started" className="scroll-mt-24 border-t border-white/[0.07]">
          <div className="mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:px-8 lg:py-28">
            <div className="grid gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:items-center lg:gap-24">
              <div>
                <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-emerald-300">Install the release</p>
                <h2 className="mt-4 text-3xl font-semibold tracking-[-0.035em] text-white sm:text-4xl">Evaluate Steadlith on a real corpus.</h2>
                <p className="mt-4 text-sm leading-6 text-zinc-400">Install v{releaseVersion}, start with the offline provider, inspect the plan, and measure churn before connecting a paid embedding service.</p>
                <a className="mt-5 inline-flex text-xs font-medium text-zinc-400 underline decoration-white/20 underline-offset-4 hover:text-white" href={quickStartUrl} {...externalLinkProps}>Open the quick start</a>
              </div>
              <div className="overflow-hidden rounded-lg border border-white/[0.08] bg-[#0b0b0e]">
                <div className="flex h-11 items-center justify-between border-b border-white/[0.07] px-4"><span className="font-mono text-[10px] text-zinc-400">terminal</span><CopyButton value={installCommand} label="Copy installation commands" /></div>
                <pre className="overflow-x-auto p-5 font-mono text-xs leading-7 text-zinc-300"><code>{installCommand}</code></pre>
              </div>
            </div>
            <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-white/[0.07] pt-6">
              <p className="font-mono text-[10px] text-zinc-400">Apache-2.0, Python 3.10+, local SQLite reference backend</p>
              <a className="text-xs font-medium text-zinc-300 hover:text-white" href={packageUrl} {...externalLinkProps}>View on PyPI</a>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-white/[0.07] bg-[#070708]">
        <div className="mx-auto grid max-w-7xl gap-8 px-5 py-10 sm:px-6 md:grid-cols-[1fr_auto] lg:px-8">
          <div>
            <a className="inline-flex items-center gap-2.5 font-semibold tracking-tight" href="#top">
              <span className="grid size-7 place-items-center rounded border border-white/10 bg-white/[0.035]"><SteadlithMark className="h-4 w-4 text-emerald-300" /></span>
              Steadlith
            </a>
            <p className="mt-3 max-w-md text-xs leading-5 text-zinc-400">Content-defined chunk identities, cache-aware planning, and transactional indexing for RAG corpora that change.</p>
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-3 text-xs text-zinc-400 md:justify-end">
            <a className="hover:text-zinc-200" href={documentationUrl} {...externalLinkProps}>Docs</a>
            <a className="hover:text-zinc-200" href={apiUrl} {...externalLinkProps}>Python API</a>
            {securityUrl ? <a className="hover:text-zinc-200" href={securityUrl} {...externalLinkProps}>Security</a> : null}
            {contributingUrl ? <a className="hover:text-zinc-200" href={contributingUrl} {...externalLinkProps}>Contributing</a> : null}
            <a className="hover:text-zinc-200" href={packageUrl} {...externalLinkProps}>PyPI</a>
            {repositoryUrl ? <a className="hover:text-zinc-200" href={repositoryUrl} {...externalLinkProps}>GitHub</a> : null}
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-white/[0.06] pt-5 font-mono text-[10px] text-zinc-400 md:col-span-2 md:justify-between">
            <span>Apache License 2.0</span><span>v{releaseVersion}, offline by default</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
