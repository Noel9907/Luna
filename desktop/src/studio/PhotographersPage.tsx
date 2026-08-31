import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, RequestError } from '@shared/lib/api'
import { Icon } from '@shared/components/ui'
import type { CreatedMember, StudioMember } from '@shared/lib/types'

function relative(iso: string | null) {
  if (!iso) return 'Never'
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 2) return 'Just now'
  if (mins < 60) return `${mins} min ago`
  const h = Math.floor(mins / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return d === 1 ? 'Yesterday' : `${d} days ago`
}

/**
 * The generated password, shown once.
 *
 * The server never returns it again, so this panel stays until it is dismissed
 * on purpose and says plainly that it will not come back.
 */
function CredentialHandoff({ member, onDone }: { member: CreatedMember; onDone: () => void }) {
  const [copied, setCopied] = useState(false)

  const message = `Frame sign-in for ${member.name ?? member.username}
Username: ${member.username}
Password: ${member.initial_password}

You will be asked to choose your own password when you sign in.`

  return (
    <div className="cred">
      <h3 className="cred__t">Account created for {member.name ?? member.username}</h3>
      <p className="cred__d">
        Send these to them on WhatsApp. This password is shown once and cannot be looked up again.
        They will be asked to replace it the first time they sign in.
      </p>
      <div className="cred__row">
        <span className="cred__val">{member.username}</span>
        <span className="cred__val">{member.initial_password}</span>
        <button
          className="btn"
          onClick={() => {
            void navigator.clipboard.writeText(message)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          }}
        >
          {copied ? 'Copied' : 'Copy message'}
        </button>
        <button className="btn btn--sub" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  )
}

function MemberRow({
  m,
  canRemove,
  onRemove,
  removing,
}: {
  m: StudioMember
  canRemove: boolean
  onRemove: (id: string) => void
  removing: boolean
}) {
  const [confirm, setConfirm] = useState(false)
  const initials = (m.name ?? m.username)
    .split(/[\s._-]+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
    .toUpperCase()

  return (
    <tr>
      <td>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span className="av">{initials}</span>
          <div style={{ minWidth: 0 }}>
            <span className="fname">{m.name ?? m.username}</span>
            <span className="fmeta">{m.username}</span>
          </div>
        </div>
      </td>
      <td>{m.role === 'owner' ? 'Owner' : 'Photographer'}</td>
      <td>
        {/*
          Three states from two fields. `status` is whether the account works
          at all; `must_change_password` with no sign-in yet means it was set
          up and the credentials have not been used. Someone who signed in and
          then had their password reset shows as active, which is correct:
          they can still work, they just have a new password waiting.
        */}
        {m.status !== 'active' ? (
          <span className="pill pill--fail">
            <span className="pill__dot" />
            Removed
          </span>
        ) : m.must_change_password && !m.last_active_at ? (
          <span className="pill pill--wait">
            <span className="pill__dot" />
            Not signed in yet
          </span>
        ) : (
          <span className="pill pill--done">
            <span className="pill__dot" />
            Active
          </span>
        )}
      </td>
      <td className="tnum">{m.photos_uploaded.toLocaleString('en-IN')}</td>
      <td className="tnum" style={{ color: 'var(--ink4)' }}>
        {relative(m.last_active_at)}
      </td>
      <td style={{ textAlign: 'right' }}>
        {!canRemove ? (
          <span style={{ color: 'var(--ink4)', fontSize: 13 }}>Only owner</span>
        ) : confirm ? (
          <span style={{ display: 'inline-flex', gap: 8 }}>
            <button className="btn btn--dgr" disabled={removing} onClick={() => onRemove(m.user_id)}>
              Remove
            </button>
            <button className="btn" onClick={() => setConfirm(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button className="btn" onClick={() => setConfirm(true)}>
            Remove
          </button>
        )}
      </td>
    </tr>
  )
}

export function PhotographersPage() {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [username, setUsername] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<'photographer' | 'owner'>('photographer')
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<CreatedMember | null>(null)

  const { data, isLoading } = useQuery({ queryKey: ['members'], queryFn: api.members })

  const create = useMutation({
    mutationFn: () =>
      api.createMember({
        username: username.trim().toLowerCase(),
        name: name.trim() || undefined,
        role,
      }),
    onSuccess: (m) => {
      qc.invalidateQueries({ queryKey: ['members'] })
      setCreated(m)
      setUsername('')
      setName('')
      setAdding(false)
      setError(null)
    },
    onError: (e) =>
      setError(e instanceof RequestError ? e.message : 'Could not create that account.'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.removeMember(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['members'] }),
    onError: (e) =>
      setError(e instanceof RequestError ? e.message : 'Could not remove that person.'),
  })

  const members = data?.items ?? []
  const owners = members.filter((m) => m.role === 'owner').length

  return (
    <>
      <div className="topbar">
        <div className="ttl">
          Photographers
          {members.length ? <span>{members.length} in this studio</span> : null}
        </div>
        {!adding && !created ? (
          <button className="btn btn--pri" onClick={() => setAdding(true)}>
            <Icon.Plus />
            Add photographer
          </button>
        ) : null}
      </div>

      <div className="body">
        {created ? <CredentialHandoff member={created} onDone={() => setCreated(null)} /> : null}

        {adding ? (
          <div className="panel">
            <h3 style={{ margin: '0 0 4px', fontSize: 15, fontWeight: 600 }}>
              Add someone to this studio
            </h3>
            <p style={{ margin: '0 0 16px', fontSize: 14, color: 'var(--ink3)' }}>
              Pick a username for them. We generate a password you can send over WhatsApp.
              Photographs they upload belong to the studio, not to them.
            </p>
            <form
              style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}
              onSubmit={(e) => {
                e.preventDefault()
                if (username.trim()) create.mutate()
              }}
            >
              <label className="search" style={{ maxWidth: 220 }}>
                <input
                  required
                  autoFocus
                  pattern="[a-zA-Z0-9._\-]+"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  placeholder="username"
                  aria-label="Username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label className="search" style={{ maxWidth: 220 }}>
                <input
                  placeholder="Full name (optional)"
                  aria-label="Full name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <select
                className="select"
                value={role}
                aria-label="Role"
                onChange={(e) => setRole(e.target.value as 'photographer' | 'owner')}
              >
                <option value="photographer">Photographer</option>
                <option value="owner">Owner</option>
              </select>
              <button className="btn btn--pri" type="submit" disabled={create.isPending}>
                {create.isPending ? 'Creating' : 'Create account'}
              </button>
              <button
                className="btn"
                type="button"
                onClick={() => {
                  setAdding(false)
                  setError(null)
                }}
              >
                Cancel
              </button>
            </form>
          </div>
        ) : null}

        {error ? (
          <div className="note" style={{ borderColor: 'var(--red-line)', background: 'var(--red-bg)' }}>
            {error}
          </div>
        ) : null}

        {isLoading ? (
          <div className="empty">
            <div className="empty__t">Loading</div>
          </div>
        ) : members.length === 0 ? (
          <div className="empty">
            <div className="empty__t">Just you so far</div>
            <div className="empty__d">
              Add the people who shoot for you and they can upload to your events from their own
              machines.
            </div>
            <button className="btn btn--pri" onClick={() => setAdding(true)}>
              Add photographer
            </button>
          </div>
        ) : (
          <div className="tbl">
            <div className="tbl__scroll">
              <table>
                <thead>
                  <tr>
                    <th>Person</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Photographs</th>
                    <th>Last active</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <MemberRow
                      key={m.user_id}
                      m={m}
                      /* A studio with no owner would be permanently unreachable. */
                      canRemove={!(m.role === 'owner' && owners === 1)}
                      removing={remove.isPending}
                      onRemove={(id) => remove.mutate(id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
