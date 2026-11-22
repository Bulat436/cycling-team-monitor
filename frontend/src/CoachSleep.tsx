import { useState } from 'react'
import { Link } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import { dateLabel, useAthleteResource } from './athleteApi'
import { SleepSummary } from './SleepSummary'
import { isCalendarDate, sleepApi } from './sleepApi'
import type { SleepDay } from './sleepApi'
import { AttentionBadge } from './TrainingSummary'
import { athleteTrainingApi, periodLabel, trainingAthletePath } from './trainingApi'
import type { AthleteWarnings, Measurements } from './trainingApi'

export default function CoachSleep({ athleteId, today }: { athleteId: number; today: string }) {
  const [chosen, setChosen] = useState('')
  const date = chosen || today
  const day = useAthleteResource<SleepDay>(`${sleepApi(athleteId)}/${date}`, 60_000)
  const base = athleteTrainingApi(athleteId)
  const measures = useAthleteResource<Measurements>(base + '/measurements?period=28')
  const warnings = useAthleteResource<AthleteWarnings>(base + '/warnings', 60_000)
  const related = warnings.data?.warnings?.filter(warning => warning.type === 'possible_accumulated_fatigue'
    || warning.type === 'load_growth_with_worsening_recovery' || warning.type === 'recovery_after_load_block'
    || warning.facts.some(fact => ['sleep_minutes', 'sleep_quality', 'recovery'].includes(fact.metric))
    || warning.coverage.gaps.some(gap => gap === 'sparse_diary' || gap === 'insufficient_evaluable_recovery_days'))

  return <section className="panel sleep-panel" aria-label="Сон и восстановление спортсмена">
    <div className="chart-header"><h2>Сон и восстановление</h2>
      <label className="date-picker">Дата сна<input type="date" max={today} value={date}
        onChange={event => { if (isCalendarDate(event.target.value)) setChosen(event.target.value) }} /></label>
    </div>
    <QueryFeedback loading={day.isPending} error={day.error} retry={() => void day.refetch()} />
    {day.data && <>
      <p className="muted">{dateLabel(day.data.date)} · {day.data.timezone}</p>
      <SleepSummary day={day.data} comparison={measures.data?.recovery?.find(item => item.date === date)?.metrics.sleep_minutes}
        windowDays={measures.data?.rules?.recovery_window_days} />
    </>}
    <QueryFeedback loading={measures.isPending} error={measures.error} retry={() => void measures.refetch()} />
    <h3>Предупреждения восстановления</h3>
    <QueryFeedback loading={warnings.isPending} error={warnings.error} retry={() => void warnings.refetch()} />
    {related && (related.length ? <>
      <ul className="training-list">
        {related.map(warning => <li key={warning.id}>
          <strong>{warning.title}</strong>{' '}<AttentionBadge attention={warning.attention} />
          <p className="muted">{periodLabel(warning.period)}</p>
        </li>)}
      </ul>
      <Link to={`${trainingAthletePath(athleteId)}?tab=relationships&period=28`}>Подробнее о предупреждениях</Link>
    </> : <p className="muted">Активных предупреждений восстановления нет.</p>)}
  </section>
}
