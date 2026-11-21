import { useState } from 'react'

import { QueryFeedback } from './AthleteFeedback'
import { useAthleteResource } from './athleteApi'
import type { WorkoutDetail } from './athleteApi'
import { TrendChart } from './Charts'
import type { ChartRange } from './Charts'
import type { WorkoutSeries } from './coachApi'
import { ControlEffort } from './ControlEffort'
import { effortClock } from './trainingApi'

const sensors = [
  { key: 'power', title: 'Мощность', unit: 'Вт' },
  { key: 'heart_rate', title: 'Пульс', unit: 'уд/мин' },
  { key: 'cadence', title: 'Каденс', unit: 'об/мин' },
] as const

const clockLabel = (value: number) => effortClock(Math.round(value))

export default function WorkoutCharts({ workout }: { workout: WorkoutDetail }) {
  const [range, setRange] = useState<ChartRange>()
  const query = useAthleteResource<WorkoutSeries>('/workouts/' + workout.id + '/series')
  const allowEffort = workout.original_available && workout.metrics && !!workout.summary?.sample_count
  return <section className="panel">
    <h2>Показания датчиков</h2>
    <p className="muted">Время от начала записи, мин:сек. Пропуски показаний оставлены разрывами.</p>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    <div className="sensor-charts">
      {sensors.map(sensor => <div key={sensor.key} className="sensor-chart">
        {query.data && <>
          <h3>{sensor.title}</h3>
          {query.data[sensor.key].length ? <TrendChart title={sensor.title} unit={sensor.unit} ticks={clockLabel}
            range={sensor.key === 'power' ? range : undefined}
            data={query.data[sensor.key].map(point => ({ x: point.second, value: point.value }))} /> :
            <p className="muted">В FIT нет записей этого датчика.</p>}
        </>}
        {sensor.key === 'power' && allowEffort && <ControlEffort workout={workout} onRange={setRange} />}
      </div>)}
    </div>
  </section>
}
