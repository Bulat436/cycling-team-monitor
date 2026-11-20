import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { dateLabel, durationLabel, recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { WorkoutDetail, ZoneTotals } from './athleteApi'
import { QueryFeedback, SaveFeedback } from './AthleteFeedback'
import { useSession } from './session'
import { displayMeasure, RpeForm } from './Workouts'

const reasons: Record<string, string> = {
  channel_missing: 'В FIT нет записей этого датчика.',
  zones_not_configured: 'Индивидуальные зоны не настроены для этого расчёта.',
  timer_unresolved: 'По FIT не удалось определить участки с работающим таймером.',
  no_active_samples: 'Нет показаний при работающем таймере.',
  short_power_windows: 'Для NP нужно хотя бы одно полное окно длительностью 30 секунд.',
  insufficient_power_coverage: 'В ряду мощности слишком много пропусков для расчёта NP.',
  ftp_missing: 'В истории нет FTP, действовавшего на дату тренировки.',
  timer_missing: 'В FIT отсутствует надёжное время таймера.',
  speed_coverage_missing: 'В FIT нет времени движения и недостаточно показаний скорости.',
  non_cycling_activity: 'NP рассчитан только для велотренировок.',
}

const channelNames: Record<string, string> = {
  power: 'мощность', heart_rate: 'пульс', cadence: 'каденс', speed: 'скорость',
  altitude: 'высота', temperature: 'температура', distance: 'дистанция', gps: 'GPS',
}

function ZoneSection({ title, value, reason }: { title: string; value?: ZoneTotals | null; reason?: string }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {value ? (
        <dl className="measurements">
          {value.zones.map((zone, index) => <div key={index}><dt>{zone.name}</dt><dd>{durationLabel(zone.seconds)}</dd></div>)}
          <div><dt>Вне зон</dt><dd>{durationLabel(value.outside_seconds)}</dd></div>
          <div><dt>Без показаний</dt><dd>{durationLabel(value.unknown_seconds)}</dd></div>
        </dl>
      ) : <p className="muted">{reason ? reasons[reason] : 'Нет данных'}</p>}
    </section>
  )
}

function ReprocessForm({ workoutId }: { workoutId: number }) {
  const [refresh, setRefresh] = useState(false)
  const save = useSaveAthleteData()

  return (
    <form className="form-stack" onSubmit={event => {
      event.preventDefault()
      if (refresh && !window.confirm('Пересчитать тренировку с FTP и зонами из текущей истории профиля?')) return
      save.mutate({
        path: '/workouts/' + workoutId + '/reprocess',
        method: 'POST',
        body: { refresh_parameters: refresh },
      })
    }}>
      <label className="fit-checkbox"><input type="checkbox" checked={refresh} onChange={event => setRefresh(event.target.checked)} />Обновить FTP и зоны из профиля</label>
      <div className="actions"><button className="secondary" disabled={save.isPending}>Повторно обработать FIT</button></div>
      <SaveFeedback error={save.error} saved={save.isSuccess} />
    </form>
  )
}

function WorkoutDetails({ workout, coach }: { workout: WorkoutDetail; coach: boolean }) {
  const summary = workout.summary
  const calculated = workout.metrics
  const result = calculated?.result
  const perMetric = result?.reasons ?? {}
  const rpe = workout.rpe
  const measures = [
    ['Полная длительность', durationLabel(summary?.elapsed_seconds ?? null)],
    ['Время по таймеру', durationLabel(workout.timer_seconds)],
    ['Время движения', durationLabel(summary?.moving_seconds ?? null)],
    ['Дистанция', displayMeasure(summary?.distance_m, 'км', .001)],
    ['Средняя скорость', displayMeasure(summary?.avg_speed, 'км/ч', 3.6)],
    ['Максимальная скорость', displayMeasure(summary?.max_speed, 'км/ч', 3.6)],
    ['Набор высоты', displayMeasure(summary?.ascent_m, 'м')],
    ['Сброс высоты', displayMeasure(summary?.descent_m, 'м')],
    ['Минимальная высота', displayMeasure(summary?.min_altitude_m, 'м')],
    ['Максимальная высота', displayMeasure(summary?.max_altitude_m, 'м')],
    ['Средняя мощность', displayMeasure(summary?.avg_power, 'Вт')],
    ['Максимальная мощность', displayMeasure(summary?.max_power, 'Вт')],
    ['Средний пульс', displayMeasure(summary?.avg_heart_rate, 'уд/мин')],
    ['Максимальный пульс', displayMeasure(summary?.max_heart_rate, 'уд/мин')],
    ['Средний каденс', displayMeasure(summary?.avg_cadence, 'об/мин')],
    ['Максимальный каденс', displayMeasure(summary?.max_cadence, 'об/мин')],
    ['Средняя температура', displayMeasure(summary?.avg_temperature, '°C')],
    ['Максимальная температура', displayMeasure(summary?.max_temperature, '°C')],
    ['Энергозатраты', displayMeasure(summary?.calories, 'ккал')],
    ['GPS', summary?.gps ? 'Записан маршрут' : 'Нет данных'],
  ]
  const metricRows = [
    { key: 'np', name: 'NP', value: displayMeasure(workout.np, 'Вт') },
    { key: 'if', name: 'IF', value: displayMeasure(result?.if_value, '') },
    { key: 'tss', name: 'TSS', value: displayMeasure(workout.tss, '') },
  ]

  return (
    <>
      <section className="panel">
        <h2>Сводка FIT</h2>
        <dl className="measurements">
          {measures.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
        {perMetric.moving_seconds && <p className="muted">{reasons[perMetric.moving_seconds]}</p>}
      </section>
      <section className="panel">
        <h2>Мощность и нагрузка</h2>
        <dl className="measurements">
          {metricRows.map(row => <div key={row.key}>
            <dt>{row.name}</dt><dd>{row.value}</dd>
            {perMetric[row.key] && <dd className="muted">{reasons[perMetric[row.key]]}</dd>}
          </div>)}
          <div><dt>FTP для расчёта</dt><dd>{displayMeasure(calculated?.snapshot.ftp_watts, 'Вт')}</dd></div>
        </dl>
        {calculated?.snapshot.ftp_effective_from && <p className="muted">FTP действует с {dateLabel(calculated.snapshot.ftp_effective_from)}.</p>}
        {result?.quality.timing_source === 'record_gaps_verified' && <p className="muted">Паузы оценены по разрывам записи и времени таймера.</p>}
        {result?.quality.expected_seconds != null && <p className="muted">
          Покрытие мощности {durationLabel(result.quality.covered_seconds.power)} из {durationLabel(result.quality.expected_seconds)}.
        </p>}
      </section>
      <section className="panel">
        <h2>Субъективная нагрузка</h2>
        <dl className="measurements">
          <div><dt>RPE</dt><dd>{rpe ? rpe.score + ' из 10' : 'Не заполнено'}</dd></div>
          <div><dt>Нагрузка по RPE</dt><dd>{displayMeasure(rpe?.session_load, 'усл. ед.')}</dd></div>
        </dl>
        {rpe?.comment && <p className="entry-comment">{rpe.comment}</p>}
        {rpe && <p className="muted">Заполнено {recordedAt(rpe.created_at, workout.timezone)} · Изменено {recordedAt(rpe.updated_at, workout.timezone)}</p>}
        {!coach && <RpeForm workout={workout} />}
      </section>
      <ZoneSection title="Зоны мощности" value={result?.power_zones} reason={perMetric.power_zones} />
      <ZoneSection title="Зоны пульса" value={result?.heart_rate_zones} reason={perMetric.heart_rate_zones} />
      <section className="panel">
        <h2>Распределение мощности</h2>
        {result?.power_distribution ? (
          <dl className="measurements">{result.power_distribution.map(bin => (
            <div key={bin.minimum}><dt>{bin.minimum}–{bin.maximum} Вт</dt><dd>{durationLabel(bin.seconds)}</dd></div>
          ))}</dl>
        ) : <p className="muted">{reasons[perMetric.power_distribution] ?? 'Нет данных'}</p>}
      </section>
      {summary && <section className="panel">
        <h2>Исходные данные</h2>
        <p>Точек временного ряда — {summary.sample_count}.</p>
        {!!result?.quality.missing_channels.length && <p className="muted">Каналы без показаний — {result.quality.missing_channels.map(key => channelNames[key] ?? key).join(', ')}.</p>}
        <p>Источники — {[...new Set(workout.sources.map(row => row.source === 'manual' ? 'ручная загрузка' : row.source === 'onelapfit' ? 'OnelapFit' : row.source))].join(', ')}.</p>
        {workout.original_available && <div className="actions"><a className="button secondary" href={'/api/v1/workouts/' + workout.id + '/fit'}>Скачать исходный FIT</a></div>}
        {coach && workout.original_available && <ReprocessForm workoutId={workout.id} />}
      </section>}
    </>
  )
}

function DetailPage({ id, coach }: { id: number; coach: boolean }) {
  const query = useAthleteResource<WorkoutDetail>('/workouts/' + id)
  const workout = query.data
  const back = coach ? workout ? '/athletes/' + workout.athlete_id + '/workouts' : '/' : '/workouts'
  return (
    <>
      <Link className="back-link" to={back}>{coach && !workout ? 'К команде' : 'К истории тренировок'}</Link>
      <header className="page-heading"><div><h1>Тренировка</h1>{workout && <p>{recordedAt(workout.started_at, workout.timezone)}</p>}</div></header>
      <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
      {workout && <WorkoutDetails workout={workout} coach={coach} />}
    </>
  )
}

export function WorkoutDetailRoute() {
  const id = Number(useParams().workoutId)
  const coach = useSession().data?.user.role === 'coach'
  if (!Number.isSafeInteger(id) || id < 1) {
    return <section className="panel"><h1>Тренировка недоступна</h1><Link to={coach ? '/' : '/workouts'}>К списку</Link></section>
  }
  return <DetailPage key={id} id={id} coach={coach} />
}
