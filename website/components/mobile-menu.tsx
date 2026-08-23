"use client";

import { useEffect, useRef, useState } from "react";

import { documentationUrl, repositoryUrl } from "@/lib/site";

const links = [
  ["Product", "#product"],
  ["Capabilities", "#capabilities"],
  ["Workflow", "#workflow"],
  ["Architecture", "#architecture"],
] as const;

export function MobileMenu() {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && open) {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }

    function closeOutside(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOutside);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOutside);
    };
  }, [open]);

  return (
    <div className="relative md:hidden" ref={menuRef}>
      <button
        ref={triggerRef}
        className="inline-flex h-11 items-center rounded-md border border-white/10 bg-white/[0.035] px-3 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:bg-white/[0.07]"
        type="button"
        aria-expanded={open}
        aria-controls="mobile-navigation"
        onClick={() => setOpen((current) => !current)}
      >
        {open ? "Close" : "Menu"}
      </button>
      {open ? (
        <div
          className="absolute right-0 top-12 w-56 overflow-hidden rounded-lg border border-white/10 bg-[#111713] p-1.5 shadow-2xl shadow-black/50"
          id="mobile-navigation"
        >
          {links.map(([label, href]) => (
            <a
              className="block rounded-md px-3 py-2.5 text-sm text-zinc-400 transition-colors hover:bg-white/[0.05] hover:text-white"
              href={href}
              key={href}
              onClick={() => setOpen(false)}
            >
              {label}
            </a>
          ))}
          <a
            className="mt-1 block border-t border-white/[0.07] px-3 py-3 text-sm font-medium text-zinc-200"
            href={documentationUrl}
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
          >
            Documentation
          </a>
          {repositoryUrl ? (
            <a
              className="block px-3 py-3 text-sm font-medium text-zinc-200"
              href={repositoryUrl}
              target="_blank"
              rel="noreferrer"
              onClick={() => setOpen(false)}
            >
              Repository
            </a>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
