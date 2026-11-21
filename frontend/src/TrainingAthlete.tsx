import { Link, useSearchParams } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import { dateLabel, useAthleteResource } from './athleteApi'
import { RecoveryGraph, TrainingLoad } from './TrainingGraphs'
import { TrainingPerformance } from './TrainingPerformance'
import { TrainingRelationships } from './TrainingRelationships'
import { athleteTrainingApi, periodLabel, periodOptions } from './trainingApi'
import type { AthleteWarnings, Measurements, TrainingPeriod } from './trainingApi'

const tabs = [
  { id: 'load', label: 'Нагрузка' },
  { id: 'recovery', label: 'Восстановление' },
  { id: 'performance', label: 'Работоспособность' },
  { id: 'relationships', label: 'Взаимосвязи' },
] as const

export default function TrainingAthlete({ athleteId }: { athleteId: number }) {
  const [params, setParams] = useSearchParams()
  const requestedPeriod = params.get('period')
  const period = periodOptions.find(value => value === requestedPeriod) ?? '28'
  const tab = tabs.find(item => item.id === params.get('tab'))?.id ?? 'load'
  const base = athleteTrainingApi(athleteId)
  const measures = useAthleteResource<Measurements>(base + '/measurements?period=' + period)
  const advisories = useAthleteResource<AthleteWarnings>(base + '/warnings', 60_000)
  const data = measures.data

  function selectPeriod(value: TrainingPeriod) {
    setParams({ tab, period: value })
  }

  return <div className="analytics-stack">
    <header className="page-heading"><div>
      <h1>Нагрузка и восстановление</h1>
      {data && <p>{periodLabel(data.period)} · {data.timezone} · завершённые сутки по {dateLabel(data.completed_through)}</p>}
    </div></header>
    <div className="chart-header">
      <div className="period-buttons" role="group" aria-label="Период анализа">
        {periodOptions.map(value => <button key={value} type="button" className="secondary"
          aria-pressed={value === period} onClick={() => selectPeriod(value)}>{value} {value === '42' ? 'дня' : 'дней'}</button>)}
      </div>
      {tab !== 'relationships' && advisories.data && <Link to={'?tab=relationships&period=' + period}>
        Активные предупреждения: {advisories.data.warnings.length}
      </Link>}
    </div>
    <nav className="athlete-tabs training-tabs" aria-label="Вкладки нагрузки и восстановления">
      {tabs.map(item => <Link key={item.id} to={'?tab=' + item.id + '&period=' + period}
        aria-current={item.id === tab ? 'page' : undefined} className={item.id === tab ? 'active' : undefined}>
        {item.label}
      </Link>)}
    </nav>
    <QueryFeedback loading={measures.isPending} error={measures.error} retry={() => void measures.refetch()} />
    {tab === 'load' && data && <TrainingLoad data={data} />}
    {tab === 'recovery' && data && <RecoveryGraph days={data.recovery} unsupported={data.unsupported_recovery} />}
    {tab === 'performance' && data && <TrainingPerformance data={data} />}
    {tab === 'relationships' && <>
      <QueryFeedback loading={advisories.isPending} error={advisories.error} retry={() => void advisories.refetch()} />
      <TrainingRelationships data={data} warnings={advisories.data?.warnings} />
    </>}
  </div>
}
