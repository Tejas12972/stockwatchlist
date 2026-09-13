import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "Options Analytics",
  description:
    "Personal options analytics with self-computed Black-Scholes greeks and a locally accumulated implied-volatility history.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col">
        <header className="border-b border-slate-800">
          <nav className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-4">
            <Link href="/" className="font-semibold tracking-tight text-slate-100">
              Options Analytics
            </Link>
            <Link href="/" className="text-sm text-slate-400 hover:text-slate-200">
              Watchlist
            </Link>
            <Link href="/payoff" className="text-sm text-slate-400 hover:text-slate-200">
              Spread builder
            </Link>
            <span className="ml-auto text-xs text-slate-500">
              greeks computed locally
            </span>
          </nav>
        </header>

        <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">{children}</main>

        <footer className="border-t border-slate-800">
          <div className="mx-auto max-w-7xl px-6 py-5 text-xs leading-relaxed text-slate-500">
            <p className="font-medium text-slate-400">
              Not investment advice. A personal analytics tool.
            </p>
            <p className="mt-1">
              Market data is delayed and comes from an unofficial source. Prices
              and greeks are model estimates: Black-Scholes assumes European
              exercise, but US equity options are American and can be exercised
              early. Nothing here is a recommendation to trade.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
