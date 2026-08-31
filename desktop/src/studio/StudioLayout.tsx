import { Link, NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? 'ni ni--on' : 'ni')}>
      {icon}
      {label}
    </NavLink>
  )
}

export function StudioLayout() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })

  const initials =
    me?.studio?.name
      .split(' ')
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase() ?? '··'

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <span className="brand__mark">
            <Icon.Camera />
          </span>
          <span className="brand__name">Frame</span>
          <span className="brand__plan">Pro</span>
        </div>

        <nav className="nav">
          <span className="nav__grp">STUDIO</span>
          <NavItem to="/studio/events" icon={<Icon.Calendar />} label="Events" />
          <NavItem to="/studio/photographers" icon={<Icon.People />} label="Photographers" />
          <NavItem to="/studio/activity" icon={<Icon.Activity />} label="Activity" />

          <span className="nav__grp">ACCOUNT</span>
          <NavItem to="/studio/billing" icon={<Icon.Card />} label="Billing" />
          <NavItem to="/studio/branding" icon={<Icon.Brush />} label="Branding" />
          <NavItem to="/studio/password" icon={<Icon.Lock />} label="Password" />
        </nav>

        <div className="side__act">
          <Link to="/studio/events/new" className="btn btn--pri btn--wide btn--lg" style={{ textDecoration: 'none' }}>
            <Icon.Plus />
            New event
          </Link>
        </div>

        <div className="side__foot">
          <div className="ucard">
            <span className="av">{initials}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div
                style={{
                  fontSize: 13.5,
                  fontWeight: 600,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {me?.name ?? me?.username ?? 'Loading'}
              </div>
              <div style={{ fontSize: 12, color: 'var(--ink4)' }}>{me?.studio?.name ?? ''}</div>
            </div>
            <button
              className="ucard__out"
              title="Sign out"
              aria-label="Sign out"
              onClick={() => {
                api.logout()
                window.location.reload()
              }}
            >
              <svg viewBox="0 0 24 24">
                <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
                <path d="M10 8l-4 4 4 4M6 12h9" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
