/**
 * Honest stand-in for a section that exists in the navigation but is not built.
 *
 * Better than hiding the nav item: the photographer can see the whole shape of
 * the product, and nothing dead-ends into a router error.
 */
export function Placeholder({
  title,
  what,
  when,
}: {
  title: string
  what: string
  when: string
}) {
  return (
    <>
      <div className="topbar">
        <div className="ttl">{title}</div>
      </div>
      <div className="body">
        <div className="empty">
          <div className="empty__t">{title} is not built yet</div>
          <div className="empty__d">{what}</div>
          <span className="pill pill--wait">
            <span className="pill__dot" />
            {when}
          </span>
        </div>
      </div>
    </>
  )
}

export const ActivityPage = () => (
  <Placeholder
    title="Activity"
    what="A running log of uploads, processing failures and guest registrations across all your events."
    when="Phase 2"
  />
)
