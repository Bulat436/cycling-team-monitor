import { explain } from './api'

export function QueryFeedback({ loading, error, retry }: { loading: boolean; error: Error | null; retry: () => void }) {
  if (loading) return <p role="status">Загрузка…</p>
  if (error) return <div className="panel"><p role="alert" className="error">{explain(error)}</p><button className="secondary" onClick={retry}>Повторить</button></div>
  return null
}

export function SaveFeedback({ error, saved }: { error: Error | null; saved: boolean }) {
  if (error) return <p role="alert" className="error">{explain(error)}</p>
  if (saved) return <p role="status" className="success">Изменения сохранены.</p>
  return null
}

export function Pager({ offset, size, hasMore, onChange }: {
  offset: number; size: number; hasMore: boolean; onChange: (offset: number) => void
}) {
  if (!offset && !hasMore) return null
  return (
    <div className="actions">
      {offset > 0 && <button className="secondary" onClick={() => onChange(offset - size)}>Новые записи</button>}
      {hasMore && <button className="secondary" onClick={() => onChange(offset + size)}>Ранние записи</button>}
    </div>
  )
}
