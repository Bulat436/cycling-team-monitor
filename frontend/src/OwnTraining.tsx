import { Link } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import { dateLabel, useAthleteResource } from './athleteApi'
import { RecoveryGraph } from './TrainingGraphs'
import { WeekSummary } from './TrainingSummary'
import { measure, signedMeasure, trainingApi } from './trainingApi'
import type { OwnRecovery as OwnRecoveryData } from './trainingApi'

export default function OwnTraining() {
  const query = useAthleteResource<OwnRecoveryData>(trainingApi + '/me')
  const data = query.data
  return <div className="analytics-stack">
    <header className="page-heading"><div><h1>Моя нагрузка и восстановление</h1>
      {data && <p>{data.timezone} · завершённые сутки по {dateLabel(data.completed_through)}</p>}
    </div></header>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {data && <>
      <WeekSummary week={data.load} />
      <section className="panel">
        <h2>Что показывают записи</h2>
        {data.load.delta_pct != null ? <p>
          Отклонение от вашей обычной недельной нагрузки: {signedMeasure(data.load.delta_pct, '%')}.
          {data.load.status === 'partial' && ' Сравнение учитывает дни с известной нагрузкой.'}
        </p> : <p>
          {data.load.recorded_tss != null ? `В записях недели ${measure(data.load.recorded_tss, 'TSS')}. ` : ''}
          Сравнение нагрузки с вашим обычным уровнем пока недоступно.
        </p>}
        <p className="muted">Записи дневника показывают, как менялись сон и самочувствие относительно ваших обычных значений.</p>
      </section>
      <RecoveryGraph days={data.recovery} />
      <section className="panel">
        <h2>Пропуски дневника за неделю</h2>
        {data.missing_diary_dates.length ? <ul className="training-list">
          {data.missing_diary_dates.map(date => <li key={date}><Link to={'/diary?date=' + date}>
            Заполнить за {dateLabel(date)}
          </Link></li>)}
        </ul> : <p>За все завершённые дни недели есть записи.</p>}
      </section>
    </>}
  </div>
}
