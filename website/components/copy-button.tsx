"use client";

import { useState } from "react";

interface CopyButtonProps {
  value: string;
  label: string;
}

export function CopyButton({ value, label }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      className="inline-flex h-7 items-center rounded border border-white/[0.08] bg-white/[0.025] px-2 font-mono text-[9px] font-medium text-zinc-400 transition-colors hover:border-white/15 hover:text-white"
      type="button"
      onClick={copy}
      aria-label={label}
    >
      <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}
