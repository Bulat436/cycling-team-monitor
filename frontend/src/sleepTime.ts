const minuteMs = 60_000
const hourMs = 60 * minuteMs

export function offsetLabel(offset: number) {
  const absolute = Math.abs(offset)
  const hours = String(Math.floor(absolute / 60)).padStart(2, '0')
  const minutes = String(absolute % 60).padStart(2, '0')
  return `UTC${offset < 0 ? '−' : '+'}${hours}:${minutes}`
}

export function sleepPreview(date: string, timezone: string, start: string, end: string, startOffset: number | null, endOffset: number | null) {
  const empty = { minutes: null, startOffsets: [], endOffsets: [] }
  try {
    const formatter = new Intl.DateTimeFormat('en-GB', {
      timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    })
    function offsetAt(instant: number) {
      const parts = Object.fromEntries(formatter.formatToParts(instant).map(part => [part.type, part.value]))
      const wall = `${parts.year.padStart(4, '0')}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:00Z`
      return Math.round((Date.parse(wall) - instant) / minuteMs)
    }
    function boundary(day: string, clock: string, chosen: number | null) {
      if (!clock) return { instant: null, offsets: [] }
      const wall = Date.parse(day + 'T' + clock + ':00Z')
      const nearby = new Set([-36, -12, 0, 12, 36].map(hours => offsetAt(wall + hours * hourMs)))
      const offsets = [...nearby].filter(offset => offsetAt(wall - offset * minuteMs) === offset).sort((a, b) => a - b)
      const offset = chosen ?? (offsets.length === 1 ? offsets[0] : null)
      return { instant: offset != null && offsets.includes(offset) ? wall - offset * minuteMs : null, offsets }
    }
    const startDate = start && end && start > end
      ? new Date(Date.parse(date) - 24 * hourMs).toISOString().slice(0, 10) : date
    const first = boundary(startDate, start, startOffset)
    const last = boundary(date, end, endOffset)
    const minutes = first.instant != null && last.instant != null ? (last.instant - first.instant) / minuteMs : null
    return { minutes: minutes != null && minutes >= 0 && minutes <= 1440 ? minutes : null,
      startOffsets: first.offsets, endOffsets: last.offsets }
  } catch (error) {
    if (error instanceof RangeError) return empty
    throw error
  }
}
