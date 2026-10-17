import type { Metadata, Viewport } from "next"
import { Geist, JetBrains_Mono } from "next/font/google"

import { Providers } from "@/components/providers"
import { THEME_INIT_SCRIPT } from "@/lib/theme"

import "./globals.css"

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] })
const jetbrainsMono = JetBrains_Mono({ variable: "--font-jetbrains-mono", subsets: ["latin"] })

export const metadata: Metadata = {
  title: { default: "FlowForge", template: "%s · FlowForge" },
  description: "FlowForge — Orchestrate. Execute. Observe. Distributed task execution with a live dashboard.",
  applicationName: "FlowForge",
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#070b18" },
    { media: "(prefers-color-scheme: light)", color: "#eef2fa" },
  ],
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${jetbrainsMono.variable}`}
      data-theme="dark"
      suppressHydrationWarning
    >
      <head>
        {/* Applies the saved theme before the first paint (no flash of the wrong theme). */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-dvh font-sans">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[100] focus:rounded-lg focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
