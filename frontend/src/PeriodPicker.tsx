import { useState } from 'react'
import type { FormEvent } from 'react'

import { dateLabel } from './athleteApi'
import type { DateRange, Preset, SelectedPeriod } from './coachApi'

const options: { key: Preset; name: string }[] = [
  { key: '7', name: '7 дней' }, { key: '28', name: '28 дней' }, { key: '90', name: '90 дней' },
  { key: 'custom', name: 'Свой период' },
]

export function PeriodCaption({ period }: { period: DateRange }) {
  return <p className="muted">{dateLabel(period.start)} — {dateLabel(period.end)}</p>
}

export function PeriodPicker({ value, onChange, actual }: {
  value: SelectedPeriod; onChange: (value: SelectedPeriod) => void; actual?: DateRange
}) {
  const [custom, setCustom] = useState(value.preset === 'custom')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const start = String(data.get('start'))
    const end = String(data.get('end'))
    onChange({ preset: 'custom', start, end })
  }

  return (
    <div className="form-stack">
      <div className="period-buttons" role="group" aria-label="Период">
        {options.map(option => (
          <button key={option.key} type="button" className="secondary"
            aria-pressed={option.key === 'custom' ? custom : !custom && value.preset === option.key}
            onClick={() => {
              setCustom(option.key === 'custom')
              if (option.key !== 'custom') onChange({ preset: option.key })
            }}>{option.name}</button>
        ))}
      </div>
      {custom && <form className="date-range-form" onSubmit={submit}>
        <label>Начало периода<input type="date" name="start" required min="1990-01-01" max="2200-12-31"
          defaultValue={value.preset === 'custom' ? value.start : actual?.start} /></label>
        <label>Конец периода<input type="date" name="end" required min="1990-01-01" max="2200-12-31"
          defaultValue={value.preset === 'custom' ? value.end : actual?.end} /></label>
        <button type="submit" className="secondary">Применить период</button>
      </form>}
    </div>
  )
}
