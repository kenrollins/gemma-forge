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
        <header className="border-b border-[#1C1F26] bg-[#12141A]">
          <div className="flex items-center justify-between px-6 h-12">
            <div className="flex items-center gap-8">
              <span className="text-sm font-bold tracking-wide">
                GEMMA<span className="text-[#3B82F6]">FORGE</span>
              </span>
              <nav className="flex gap-6 text-xs font-medium text-[#9CA3AF]">
                <a href="/" className="hover:text-[#E8EAED] transition-colors">
                  Dashboard
                </a>
                <a
                  href="https://kenrollins.github.io/gemma-forge/journal/journey/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-[#E8EAED] transition-colors"
                >
                  Journal ↗
                </a>
                <a
                  href="https://kenrollins.github.io/gemma-forge/about/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-[#E8EAED] transition-colors"
                >
                  About ↗
                </a>
              </nav>
            </div>
            <div className="flex items-center gap-4 text-xs text-[#6B7280]">
              <span className="font-mono">XR7620 · 4×L4</span>
              <span className="inline-block w-2 h-2 rounded-full bg-[#22C55E]" />
            </div>
          </div>
        </header>
        <main className="flex-1 overflow-hidden">{children}</main>
      </body>
    </html>
  );
}
