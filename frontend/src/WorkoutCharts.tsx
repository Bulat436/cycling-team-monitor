import { QueryFeedback } from './AthleteFeedback'
import { useAthleteResource } from './athleteApi'
import { TrendChart } from './Charts'
import type { WorkoutSeries } from './coachApi'

const sensors = [
  { key: 'power', title: 'Мощность', unit: 'Вт' },
  { key: 'heart_rate', title: 'Пульс', unit: 'уд/мин' },
  { key: 'cadence', title: 'Каденс', unit: 'об/мин' },
] as const

const clockLabel = (value: number) => {
  const second = Math.round(value)
  return Math.floor(second / 60) + ':' + String(second % 60).padStart(2, '0')
}

export default function WorkoutCharts({ workoutId }: { workoutId: number }) {
  const query = useAthleteResource<WorkoutSeries>('/workouts/' + workoutId + '/series')
  return <section className="panel">
    <h2>Показания датчиков</h2>
    <p className="muted">Время от начала записи, мин:сек. Пропуски показаний оставлены разрывами.</p>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {query.data && <div className="sensor-charts">
      {sensors.map(sensor => <div key={sensor.key} className="sensor-chart">
        <h3>{sensor.title}</h3>
        {query.data?.[sensor.key].length ? <TrendChart title={sensor.title} unit={sensor.unit} ticks={clockLabel}
          data={query.data[sensor.key].map(point => ({ x: point.second, value: point.value }))} /> :
          <p className="muted">В FIT нет записей этого датчика.</p>}
      </div>)}
    </div>}
  </section>
}
