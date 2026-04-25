import { Link } from '@tanstack/react-router'
import { Mail } from 'lucide-react'
import ThemeToggle from './ThemeToggle'

export default function Header() {
  return (
    <header className="sticky top-0 z-50 border-b border-[var(--line)] bg-[var(--header-bg)] px-4 backdrop-blur-lg">
      <nav className="page-wrap flex flex-wrap items-center gap-x-3 gap-y-2 py-3 sm:py-4">
        {/* brand */}
        <h2 className="m-0 flex-shrink-0 text-base font-semibold tracking-tight">
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-full border border-[var(--chip-line)] bg-[var(--chip-bg)] px-3 py-1.5 text-sm text-[var(--sea-ink)] no-underline shadow-[0_2px_8px_rgba(0,0,0,0.08)] sm:px-4 sm:py-2"
          >
            <Mail className="h-3.5 w-3.5 text-[var(--lagoon-deep)]" />
            Email Parser
          </Link>
        </h2>

        {/* nav links */}
        <div className="order-3 flex w-full flex-wrap items-center gap-x-4 gap-y-1 pb-1 text-sm font-semibold sm:order-2 sm:w-auto sm:flex-nowrap sm:pb-0">
          <Link
            to="/"
            className="nav-link"
            activeProps={{ className: 'nav-link is-active' }}
          >
            Compose
          </Link>
          <Link
            to="/review"
            className="nav-link"
            activeProps={{ className: 'nav-link is-active' }}
          >
            Review
          </Link>
        </div>

        {/* actions */}
        <div className="ml-auto flex items-center gap-1.5 sm:ml-0 sm:gap-2">
          <ThemeToggle />
        </div>
      </nav>
    </header>
  )
}
