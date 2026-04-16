import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "GemmaForge — Sovereign Edge AI",
  description:
    "Air-gapped agentic infrastructure remediation on the Dell PowerEdge XR7620. Gemma 4 + vLLM + Ralph loops.",
};

// The application chrome is intentionally minimal: brand on the left,
// hardware label on the right. Tab navigation and mode controls live
// inside the page itself (see ChromeBar.tsx) so they can react to
// per-page connection state. The Journal/About external links moved
// from the primary nav into a discreet kebab menu in the corner so the
// demo viewer's eye lands on the active dashboard, not on links that
// take them away.

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full`}
    >
      <body className="min-h-full flex flex-col bg-[#0B0D11] text-[#E8EAED] antialiased">
        <header className="border-b border-[#1C1F26] bg-[#0A0C10]">
          <div className="flex items-center justify-between px-4 h-9">
            {/* Brand only — primary navigation moved into the in-page
                ChromeBar to colocate it with the mode/speed controls. */}
            <span className="text-[12px] font-bold tracking-wide">
              GEMMA<span className="text-[#3B82F6]">FORGE</span>
            </span>

            {/* Hardware identity + external links, both quiet. */}
            <div className="flex items-center gap-3 text-[10px] text-[#6B7280]">
              <span className="font-mono uppercase tracking-wider">
                XR7620 · 4×L4
              </span>
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-[#22C55E]" />
              <span className="text-[#3F4451] mx-1">·</span>
              <a
                href="https://kenrollins.github.io/gemma-forge/journal/journey/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-[#6B7280] hover:text-[#E8EAED] transition-colors uppercase tracking-wider"
                title="Read the developer journal"
              >
                Journal&nbsp;↗
              </a>
              <a
                href="https://kenrollins.github.io/gemma-forge/about/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-[#6B7280] hover:text-[#E8EAED] transition-colors uppercase tracking-wider"
                title="Project background and the architecture brief"
              >
                About&nbsp;↗
              </a>
            </div>
          </div>
        </header>
        <main className="flex-1 overflow-hidden">{children}</main>
      </body>
    </html>
  );
}
