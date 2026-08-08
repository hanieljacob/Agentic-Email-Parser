import { HeadContent, Scripts, createRootRoute } from '@tanstack/react-router'
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools'
import { TanStackDevtools } from '@tanstack/react-devtools'
import Footer from '../components/Footer'
import Header from '../components/Header'
import { Toaster } from '../components/ui/sonner'

import appCss from '../styles.css?url'

const THEME_INIT_SCRIPT = `(function(){try{var stored=window.localStorage.getItem('theme');var mode=(stored==='light'||stored==='dark'||stored==='auto')?stored:'auto';var prefersDark=window.matchMedia('(prefers-color-scheme: dark)').matches;var resolved=mode==='auto'?(prefersDark?'dark':'light'):mode;var root=document.documentElement;root.classList.remove('light','dark');root.classList.add(resolved);if(mode==='auto'){root.removeAttribute('data-theme')}else{root.setAttribute('data-theme',mode)}root.style.colorScheme=resolved;}catch(e){}})();`

function NotFound() {
  return (
    <main className="page-wrap py-20 text-center">
      <p className="text-4xl font-bold text-[var(--sea-ink)] mb-3">404</p>
      <p className="text-[var(--sea-ink-soft)]/60">Page not found.</p>
    </main>
  )
}

export const Route = createRootRoute({
  head: () => ({
    meta: [
      {
        charSet: 'utf-8',
      },
      {
        name: 'viewport',
        content: 'width=device-width, initial-scale=1',
      },
      {
        title: 'Agentic Email Parser',
      },
    ],
    links: [
      {
        rel: 'stylesheet',
        href: appCss,
      },
      // SVG first for anything modern; the PNG is the fallback for browsers
      // that ignore it. There is no .ico — nothing still in use needs one.
      {
        rel: 'icon',
        type: 'image/svg+xml',
        href: '/favicon.svg',
      },
      {
        rel: 'icon',
        type: 'image/png',
        href: '/logo192.png',
      },
      {
        rel: 'apple-touch-icon',
        href: '/logo192.png',
      },
      {
        rel: 'manifest',
        href: '/manifest.json',
      },
    ],
  }),
  notFoundComponent: NotFound,
  shellComponent: RootDocument,
})

function RootDocument({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <HeadContent />
      </head>
      <body className="font-sans antialiased [overflow-wrap:anywhere] selection:bg-[rgba(37,99,235,0.20)]">
        <Header />
        {children}
        <Footer />
        {/* Top-right keeps toasts clear of the review dialog's footer
            actions, which sit bottom-right. */}
        <Toaster position="top-right" />
        {/* Opt-in, because `pnpm dev` is the path a reviewer is shown and the
            floating devtools badge sits on top of the UI. Turn it on with
            VITE_DEVTOOLS=true pnpm dev. Production builds drop it either way. */}
        {import.meta.env.VITE_DEVTOOLS === 'true' && (
          <TanStackDevtools
            config={{
              position: 'bottom-right',
            }}
            plugins={[
              {
                name: 'Tanstack Router',
                render: <TanStackRouterDevtoolsPanel />,
              },
            ]}
          />
        )}
        <Scripts />
      </body>
    </html>
  )
}
